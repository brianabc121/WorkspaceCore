"""Gmail 邮件（T-0128，docs/v2/MAIL_PLAN.md）。

页面接口（app/api/v1/mail.py）与智能体的 MCP 工具（app/domain/mail_tools.py）都经过这里：

- OAuth 客户端：每个工作空间一条，client_secret 加密；
- 连接：授权地址带 state（Redis，10 分钟、一次性、绑定发起的用户与工作空间）与 PKCE S256；
  回调换令牌、读 `getProfile` 拿邮箱地址，按**实际授权**的范围记下能读、能发（M2）；
- 令牌：refresh token 加密存数据库；access token 加密后放 Redis，按 `expires_in` 提前 60 秒过期；
  刷新返回 `invalid_grant` → 邮箱标为需要重新连接并通知管理员；Gmail 返回 401 时刷新一次再试；
- 读：实时调 Gmail API，不缓存、不落库正文（M5）；
- 发：先写发件记录（sending），再调 `messages.send`；超时 / 连接中断记为 unknown，不自动重发。

外部错误归类见 app/integrations/gmail.py；这里把它们映射成接口错误（MailError）。
"""

import json
import logging
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import mail_format as fmt
from app.domain import notifications, secret_scan
from app.domain.connectors import ConnectorRuntime
from app.domain.credentials import CredentialBox, CredentialError
from app.integrations import gmail
from app.integrations.base import ConnectorError
from app.models import MailAccount, MailOAuthClient, MailSend

logger = logging.getLogger("app.mail")

STATE_TTL = 600
MAX_RECIPIENTS = 20
SUBJECT_LIMIT = 200
BODY_LIMIT = 20_000
LIST_LIMIT = 50
THREAD_LIMIT = 20
ATTACHMENT_LIMIT = 25 * 1024 * 1024
_METADATA = ("From", "To", "Cc", "Subject", "Date")
METADATA_HEADERS = _METADATA


class MailError(ApiError):
    """邮件相关的接口错误；MCP 工具把 code 与 message 原样交给智能体。"""


def _err(status: int, code: str, message: str, **details: Any) -> MailError:
    return MailError(status, code, message, details or None)


def _now() -> datetime:
    return datetime.now(UTC)


def client_context(workspace_id: uuid.UUID) -> str:
    return f"mail_oauth_client:{workspace_id}"


def refresh_context(account_id: uuid.UUID) -> str:
    return f"mail_account:{account_id}"


def access_context(account_id: uuid.UUID) -> str:
    return f"mail_access:{account_id}"


def can_read(account: MailAccount) -> bool:
    return account.status == "active" and gmail.SCOPE_READ in (account.scopes or [])


def can_send(account: MailAccount) -> bool:
    return account.status == "active" and gmail.SCOPE_SEND in (account.scopes or [])


@dataclass(frozen=True)
class Sender:
    """谁在发：人（user_id）或智能体（instance_id）。"""

    kind: str
    name: str
    user_id: uuid.UUID | None = None
    instance_id: uuid.UUID | None = None


@dataclass
class Listing:
    messages: list[fmt.ParsedMessage]
    next_page_token: str | None
    estimate: int


class MailService:
    def __init__(
        self, db: AsyncSession, settings: Settings, redis: Redis, runtime: ConnectorRuntime
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.runtime = runtime

    # ------------------------------------------------------------ OAuth 客户端

    def box(self) -> CredentialBox:
        return self.runtime.credentials()

    def redirect_uri(self) -> str:
        return self.settings.public_base_url.rstrip("/") + "/api/v1/mail/oauth/callback"

    async def oauth_client(self, workspace_id: uuid.UUID) -> MailOAuthClient | None:
        found: MailOAuthClient | None = await self.db.scalar(
            select(MailOAuthClient).where(MailOAuthClient.workspace_id == workspace_id)
        )
        return found

    async def put_client(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        client_id: str,
        client_secret: str | None,
        version: int | None,
    ) -> MailOAuthClient:
        current = await self.oauth_client(workspace_id)
        if current is None:
            if not client_secret:
                raise _err(422, "VALIDATION_FAILED", "第一次设置要填 client_secret")
            current = MailOAuthClient(
                workspace_id=workspace_id,
                client_id=client_id,
                client_secret_ref=self.box().seal(
                    client_secret, context=client_context(workspace_id)
                ),
                updated_by=user_id,
            )
            self.db.add(current)
            await self.db.flush()
            return current
        if version is not None and version != current.version:
            raise _err(409, "VERSION_CONFLICT", "设置已被别人修改，请刷新后再试")
        current.client_id = client_id
        if client_secret:
            current.client_secret_ref = self.box().seal(
                client_secret, context=client_context(workspace_id)
            )
        current.updated_by = user_id
        current.version += 1
        await self.db.flush()
        return current

    async def _oauth(self, workspace_id: uuid.UUID) -> tuple[gmail.GoogleOAuth, MailOAuthClient]:
        client = await self.oauth_client(workspace_id)
        if client is None:
            raise _err(409, "MAIL_NOT_CONFIGURED", "还没有设置 Google OAuth 客户端（设置 → 邮件）")
        try:
            secret = self.box().resolve(
                client.client_secret_ref, context=client_context(workspace_id)
            )
        except CredentialError as error:
            raise _err(503, "EXTERNAL_UNAVAILABLE", "OAuth 客户端密钥无法解密") from error
        oauth = gmail.GoogleOAuth(
            self.runtime.http(f"mail:{workspace_id}"),
            client_id=client.client_id,
            client_secret=secret.get_secret_value(),
            token_url=self.settings.google_oauth_token_url,
            revoke_url=self.settings.google_oauth_revoke_url,
        )
        return oauth, client

    # ------------------------------------------------------------ 连接与回调

    async def start(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID, *, login_hint: str | None
    ) -> str:
        client = await self.oauth_client(workspace_id)
        if client is None:
            raise _err(409, "MAIL_NOT_CONFIGURED", "还没有设置 Google OAuth 客户端（设置 → 邮件）")
        verifier, challenge = gmail.pkce_pair()
        state = secrets.token_urlsafe(32)
        payload = {"w": str(workspace_id), "u": str(user_id), "v": verifier}
        await self.redis.set(f"mail_oauth_state:{state}", json.dumps(payload), ex=STATE_TTL)
        return gmail.authorize_url(
            auth_url=self.settings.google_oauth_auth_url,
            client_id=client.client_id,
            redirect_uri=self.redirect_uri(),
            state=state,
            code_challenge=challenge,
            login_hint=login_hint,
        )

    async def take_state(self, state: str) -> dict[str, str] | None:
        """取出并删除 state（一次性）；没有或过期返回 None。"""
        if not state or len(state) > 200:
            return None
        key = f"mail_oauth_state:{state}"
        raw = await self.redis.getdel(key)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def finish(self, data: dict[str, str], *, code: str) -> MailAccount:
        workspace_id, user_id = uuid.UUID(data["w"]), uuid.UUID(data["u"])
        oauth, _ = await self._oauth(workspace_id)
        try:
            tokens = await oauth.exchange(
                code=code, redirect_uri=self.redirect_uri(), code_verifier=data["v"]
            )
            if not tokens.can_read:
                raise _err(
                    422,
                    "MAIL_SCOPE_MISSING",
                    "没有授权读取邮件：重新连接时请勾选“查看您的电子邮件和设置”",
                )
            profile = await gmail.GmailClient(
                self.runtime.http(f"mail:{workspace_id}"),
                tokens.access_token,
                self.settings.gmail_api_url,
            ).profile()
        except ConnectorError as error:
            raise self._connector_error(error) from error
        email = str(profile.get("emailAddress") or "").strip().lower()
        if not email:
            raise _err(502, "EXTERNAL_UNAVAILABLE", "Google 没有返回邮箱地址")
        account = await self.db.scalar(
            select(MailAccount).where(
                MailAccount.workspace_id == workspace_id, MailAccount.email == email
            )
        )
        if account is None:
            account = MailAccount(workspace_id=workspace_id, email=email, display_name=email)
            self.db.add(account)
            await self.db.flush()
        if not tokens.refresh_token and not account.refresh_token_ref:
            raise _err(
                422,
                "MAIL_NO_REFRESH_TOKEN",
                "Google 没有返回长期令牌：请在 Google 账号里移除这个应用的授权后重新连接",
            )
        if tokens.refresh_token:
            account.refresh_token_ref = self.box().seal(
                tokens.refresh_token, context=refresh_context(account.id)
            )
        account.scopes = sorted(tokens.scopes & set(gmail.SCOPES))
        account.status = "active"
        account.last_error = None
        account.connected_by = user_id
        account.connected_at = _now()
        account.version += 1
        await self._cache_access(account, tokens)
        await self.db.flush()
        return account

    # ------------------------------------------------------------ 令牌

    async def _cache_access(self, account: MailAccount, tokens: gmail.TokenSet) -> None:
        ttl = max(60, tokens.expires_in - 60)
        sealed = self.box().seal(tokens.access_token, context=access_context(account.id))
        await self.redis.set(f"mail_at:{account.id}", sealed, ex=ttl)

    async def _access_token(self, account: MailAccount, *, fresh: bool = False) -> str:
        if not fresh:
            sealed = await self.redis.get(f"mail_at:{account.id}")
            if sealed:
                text = sealed.decode() if isinstance(sealed, bytes) else str(sealed)
                try:
                    token = self.box().resolve(text, context=access_context(account.id))
                    return token.get_secret_value()
                except CredentialError:
                    await self.redis.delete(f"mail_at:{account.id}")
        if account.status != "active" or not account.refresh_token_ref:
            raise _err(409, "MAIL_RECONNECT", f"邮箱 {account.email} 需要重新连接")
        oauth, _ = await self._oauth(account.workspace_id)
        try:
            refresh = self.box().resolve(
                account.refresh_token_ref, context=refresh_context(account.id)
            )
            tokens = await oauth.refresh(refresh.get_secret_value())
        except CredentialError as error:
            raise _err(503, "EXTERNAL_UNAVAILABLE", "邮箱令牌无法解密") from error
        except ConnectorError as error:
            if error.kind == "AUTH_FAILED":
                await self._needs_reconnect(account, error.reason or "auth_failed")
            raise self._connector_error(error) from error
        if tokens.scopes:
            account.scopes = sorted(tokens.scopes & set(gmail.SCOPES))
        await self._cache_access(account, tokens)
        return tokens.access_token

    async def _needs_reconnect(self, account: MailAccount, reason: str) -> None:
        """授权失效：标记、清掉缓存的令牌、通知管理员。调用方的事务回滚也要留下标记，所以单独提交。"""
        account.status = "needs_reconnect"
        account.last_error = reason[:100]
        await self.redis.delete(f"mail_at:{account.id}")
        await notifications.notify_admins(
            self.db,
            account.workspace_id,
            type_="mail.reconnect",
            severity="warning",
            title=f"Gmail 邮箱 {account.email} 需要重新连接",
            body="Google 授权已失效（撤销、过期或改了密码）。在 设置 → 邮件 里重新连接。",
            link="/settings/mail",
            key=str(account.id),
        )
        await self.db.commit()

    async def _call(self, account: MailAccount, action: Any) -> Any:
        """用 access token 调 Gmail；401 时刷新一次再试。

        熔断按邮箱计数（T-0170）：一个邮箱连续失败不连累同一工作空间的其他邮箱。
        """
        http = self.runtime.http(f"mail:{account.workspace_id}:{account.id}")
        token = await self._access_token(account)
        try:
            return await action(gmail.GmailClient(http, token, self.settings.gmail_api_url))
        except ConnectorError as error:
            if error.kind == "AUTH_FAILED" and not error.resource_level:
                token = await self._access_token(account, fresh=True)
                try:
                    return await action(gmail.GmailClient(http, token, self.settings.gmail_api_url))
                except ConnectorError as again:
                    if again.kind == "AUTH_FAILED" and not again.resource_level:
                        await self._needs_reconnect(account, "unauthorized")
                    raise self._connector_error(again) from again
            raise self._connector_error(error) from error

    def _connector_error(self, error: ConnectorError) -> MailError:
        if error.kind == "RATE_LIMITED":
            return _err(
                429,
                "RATE_LIMITED",
                "Gmail 限流或达到每日上限，稍后再试",
                retry_after=error.retry_after or 60,
            )
        if error.kind == "NOT_FOUND":
            return _err(404, "NOT_FOUND", "没有这封邮件或会话")
        if error.kind == "AUTH_FAILED":
            if error.reason in ("invalid_client", "unauthorized_client"):
                return _err(409, "MAIL_CLIENT_INVALID", "Google OAuth 客户端 ID 或密钥不对")
            if error.resource_level:
                return _err(403, "MAIL_FORBIDDEN", "Gmail 拒绝了这次访问")
            return _err(409, "MAIL_RECONNECT", "Gmail 授权已失效，需要重新连接")
        if error.kind == "VALIDATION_FAILED":
            return _err(422, "VALIDATION_FAILED", "Gmail 不接受这个请求（检查搜索条件或收件人）")
        if error.kind == "TIMEOUT_UNKNOWN":
            return _err(504, "TIMEOUT_UNKNOWN", "Gmail 没有及时响应，结果未知")
        return _err(503, "EXTERNAL_UNAVAILABLE", "Gmail 暂时不可用")

    # ------------------------------------------------------------ 账号

    async def accounts(self, workspace_id: uuid.UUID) -> list[MailAccount]:
        return list(
            await self.db.scalars(
                select(MailAccount)
                .where(
                    MailAccount.workspace_id == workspace_id, MailAccount.status != "disconnected"
                )
                .order_by(MailAccount.created_at)
            )
        )

    async def account(self, workspace_id: uuid.UUID, account_id: uuid.UUID) -> MailAccount:
        found: MailAccount | None = await self.db.scalar(
            select(MailAccount).where(
                MailAccount.id == account_id,
                MailAccount.workspace_id == workspace_id,
                MailAccount.status != "disconnected",
            )
        )
        if found is None:
            raise _err(404, "NOT_FOUND", "没有这个邮箱")
        return found

    async def disconnect(self, account: MailAccount) -> None:
        """撤销 Google 授权（尽力而为）并删掉本地令牌；发件记录保留。"""
        if account.refresh_token_ref:
            try:
                oauth, _ = await self._oauth(account.workspace_id)
                refresh = self.box().resolve(
                    account.refresh_token_ref, context=refresh_context(account.id)
                )
                await oauth.revoke(refresh.get_secret_value())
            except (ConnectorError, CredentialError, ApiError) as error:
                logger.warning(
                    "mail revoke failed",
                    extra={"account_id": str(account.id), "error": repr(error)},
                )
        await self.redis.delete(f"mail_at:{account.id}")
        account.refresh_token_ref = ""
        account.status = "disconnected"
        account.scopes = []
        account.version += 1
        await self.db.flush()

    # ------------------------------------------------------------ 读

    async def labels(self, account: MailAccount) -> list[dict[str, Any]]:
        self._need_read(account)

        async def action(client: gmail.GmailClient) -> list[dict[str, Any]]:
            return await client.labels()

        labels: list[dict[str, Any]] = await self._call(account, action)
        return labels

    async def search(
        self,
        account: MailAccount,
        *,
        query: str | None,
        label: str | None,
        max_results: int,
        page_token: str | None,
    ) -> Listing:
        self._need_read(account)
        size = max(1, min(LIST_LIMIT, max_results))

        async def action(client: gmail.GmailClient) -> Listing:
            page = await client.list_messages(
                query=query or None,
                label_ids=[label] if label else [],
                max_results=size,
                page_token=page_token or None,
            )
            refs = [m for m in page.get("messages") or [] if isinstance(m, dict) and m.get("id")]
            messages = [
                fmt.parse(
                    await client.get_message(str(m["id"]), format_="metadata", headers=_METADATA)
                )
                for m in refs[:size]
            ]
            token = page.get("nextPageToken")
            return Listing(
                messages=messages,
                next_page_token=str(token) if token else None,
                estimate=int(page.get("resultSizeEstimate") or len(messages)),
            )

        listing: Listing = await self._call(account, action)
        return listing

    async def message(self, account: MailAccount, message_id: str) -> fmt.ParsedMessage:
        self._need_read(account)

        async def action(client: gmail.GmailClient) -> fmt.ParsedMessage:
            return fmt.parse(await client.get_message(message_id, format_="full"))

        parsed: fmt.ParsedMessage = await self._call(account, action)
        return parsed

    async def thread(self, account: MailAccount, thread_id: str) -> list[fmt.ParsedMessage]:
        self._need_read(account)

        async def action(client: gmail.GmailClient) -> list[fmt.ParsedMessage]:
            data = await client.get_thread(thread_id, format_="full")
            items = [m for m in data.get("messages") or [] if isinstance(m, dict)]
            return [fmt.parse(m) for m in items[-THREAD_LIMIT:]]

        messages: list[fmt.ParsedMessage] = await self._call(account, action)
        return messages

    async def attachment(self, account: MailAccount, message_id: str, attachment_id: str) -> bytes:
        self._need_read(account)

        async def action(client: gmail.GmailClient) -> bytes:
            data = await client.attachment(message_id, attachment_id)
            size = int(data.get("size") or 0)
            if size > ATTACHMENT_LIMIT:
                raise _err(413, "PAYLOAD_TOO_LARGE", "附件太大，请在 Gmail 里下载")
            return fmt.b64url_decode(str(data.get("data") or ""))

        content: bytes = await self._call(account, action)
        return content

    async def read_with(self, account: MailAccount, action: Any) -> Any:
        """给多邮箱聚合（mail_aggregate.py）用的小粒度读取：检查能读，再按 `_call` 的规则调 Gmail。

        action 只拿到 GmailClient，不碰数据库会话；拿令牌、401 时刷新、授权失效时标记，
        都在这个服务自己的会话里依次进行。action 可能因为 401 被重跑一次，要能接着上次的进度做。
        """
        self._need_read(account)
        return await self._call(account, action)

    def _need_read(self, account: MailAccount) -> None:
        if account.status != "active":
            raise _err(409, "MAIL_RECONNECT", f"邮箱 {account.email} 需要重新连接")
        if not can_read(account):
            raise _err(409, "MAIL_SCOPE_MISSING", "这个邮箱没有授权读取邮件，请重新连接并勾选")

    # ------------------------------------------------------------ 发

    @staticmethod
    def clean_recipients(values: Sequence[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            address = value.strip()
            if not fmt.valid_address(address):
                raise _err(422, "VALIDATION_FAILED", f"收件人地址不对：{address[:80]}")
            if address.lower() not in out:
                out.append(address.lower())
        return out

    async def send(
        self,
        account: MailAccount,
        sender: Sender,
        *,
        to: Sequence[str],
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
        subject: str,
        body: str,
        reply_to_message_id: str | None = None,
        client_key: str | None = None,
        footer: str | None = None,
    ) -> MailSend:
        if not can_send(account):
            raise _err(
                409,
                "MAIL_SCOPE_MISSING",
                "这个邮箱没有授权发信（重新连接时勾选“代表您发送电子邮件”）",
            )
        to_list, cc_list, bcc_list = (
            self.clean_recipients(to),
            self.clean_recipients(cc),
            self.clean_recipients(bcc),
        )
        if not to_list:
            raise _err(422, "VALIDATION_FAILED", "至少要有一个收件人")
        if len(to_list) + len(cc_list) + len(bcc_list) > MAX_RECIPIENTS:
            raise _err(422, "VALIDATION_FAILED", f"收件人合计最多 {MAX_RECIPIENTS} 个")
        subject = " ".join(subject.split())[:SUBJECT_LIMIT]
        if len(body) > BODY_LIMIT:
            raise _err(422, "VALIDATION_FAILED", f"正文最多 {BODY_LIMIT} 字")
        if secret_scan.scan(subject, body):
            raise _err(
                422,
                "SECRET_BLOCKED",
                "内容里像是有令牌、私钥一类的密钥，没有发出；去掉之后再发",
            )
        reply: fmt.Reply | None = None
        if reply_to_message_id:

            async def original(client: gmail.GmailClient) -> fmt.ParsedMessage:
                return fmt.parse(
                    await client.get_message(
                        reply_to_message_id,
                        format_="metadata",
                        headers=("Subject", "Message-ID", "References"),
                    )
                )

            reply = fmt.reply_of(await self._call(account, original))
            if not subject:
                subject = fmt.reply_subject(reply.subject)[:SUBJECT_LIMIT]
        if not subject:
            raise _err(422, "VALIDATION_FAILED", "要写主题")
        text = body if not footer else f"{body.rstrip()}\n\n{footer}"
        record = MailSend(
            workspace_id=account.workspace_id,
            account_id=account.id,
            actor_kind=sender.kind,
            user_id=sender.user_id,
            instance_id=sender.instance_id,
            actor_name=sender.name[:200],
            sender=account.email,
            to_addresses=to_list,
            cc_addresses=cc_list,
            bcc_count=len(bcc_list),
            subject=subject,
            body_chars=len(text),
            reply_to_message_id=reply_to_message_id,
            client_key=client_key,
            status="sending",
        )
        self.db.add(record)
        # 先提交“发送中”：进程在发信途中退出时，记录仍在（之后标为结果未知，人去 Gmail 里确认）
        await self.db.commit()
        raw = fmt.build(
            sender=account.email,
            sender_name=account.display_name if account.display_name != account.email else None,
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            subject=subject,
            body=text,
            reply=reply,
            domain=account.email.partition("@")[2] or "myworkspace.local",
        )

        async def action(client: gmail.GmailClient) -> dict[str, Any]:
            return await client.send(raw, thread_id=reply.thread_id if reply else None)

        try:
            sent: dict[str, Any] = await self._call(account, action)
        except MailError as error:
            record.status = "unknown" if error.code == "TIMEOUT_UNKNOWN" else "failed"
            record.error_code = error.code
            record.finished_at = _now()
            await self.db.commit()
            raise
        record.status = "sent"
        record.gmail_message_id = str(sent.get("id") or "") or None
        record.gmail_thread_id = str(sent.get("threadId") or "") or None
        record.finished_at = _now()
        await self.db.flush()
        return record

    async def sends(
        self, workspace_id: uuid.UUID, *, instance_id: uuid.UUID | None = None, limit: int = 50
    ) -> list[MailSend]:
        stmt = select(MailSend).where(MailSend.workspace_id == workspace_id)
        if instance_id is not None:
            stmt = stmt.where(MailSend.instance_id == instance_id)
        return list(await self.db.scalars(stmt.order_by(MailSend.created_at.desc()).limit(limit)))


__all__ = [
    "LIST_LIMIT",
    "MAX_RECIPIENTS",
    "METADATA_HEADERS",
    "Listing",
    "MailError",
    "MailService",
    "Sender",
    "can_read",
    "can_send",
]
