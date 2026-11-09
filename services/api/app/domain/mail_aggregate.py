"""多邮箱聚合读取（T-0170，四页改造规范第 7 节，实施计划 Task 5）。

`GET /api/v1/mail/messages`：不指定邮箱时读当前用户有权读取的全部已连接邮箱，按 Gmail internalDate
合并成一批；指定 account_id 时只读这一个邮箱。只读：复用 MailService 的逐账号读取
（拿令牌、401 刷新、授权失效标记、错误归类），不新增 Gmail 写范围，也不建邮件镜像表。

- 并发有上限：邮箱 worker 最多 3 个，每个邮箱的元数据读取最多 2 个，总 HTTP 最多 6 个；
  每个 worker 用自己的数据库会话，不让多个协程共用一个 AsyncSession。
  请求自己的会话只在开头读邮箱清单，读完就结束事务，网络阶段不占着连接。
- 一批的预算 12 秒：到时返回已经读到的，没读完 / 没轮到的邮箱标 pending；下一批先读没轮到的邮箱，
  慢邮箱排到后面（不饥饿）。
- 一个邮箱失败不影响其他邮箱：每个邮箱返回自己的状态（ok / needs_reconnect / rate_limited /
  unavailable / pending）；整体结果区分正常（可能为空）、部分失败、全部失败、全部需要重新连接。
- 游标：不透明的随机句柄，状态放 Redis 15 分钟。只存用户 / 工作空间、邮箱范围指纹、查询条件的 HMAC、
  每个邮箱的 pageToken / 还没读的消息 ID / 是否读完 / Gmail 估算数 / 收件箱未读数 / 限流到期时间；
  **不存邮件内容和任何头部**。输入的游标不消耗（同一个游标重试得到同样的结果），每一批生成新游标；
  每次翻页都重新校验用户、工作空间、角色（路由依赖）与邮箱的连接状态。
- 日志只记数量，不记搜索词、游标、主题、摘要、正文。
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import mail_format as fmt
from app.domain.connectors import ConnectorRuntime
from app.domain.mail import METADATA_HEADERS, MailError, MailService, can_read
from app.integrations import gmail
from app.integrations.base import ConnectorError
from app.models import MailAccount

logger = logging.getLogger("app.mail")

BATCH_BUDGET = 12.0  # 一批的预算（秒）；测试里会调小
ACCOUNT_WORKERS = 3  # 同时读几个邮箱
ACCOUNT_FETCHES = 2  # 每个邮箱同时取几封的元数据
TOTAL_HTTP = 6  # 一批里同时进行的 Gmail 请求
CURSOR_TTL = 900
DEFAULT_LIMIT = 10
MAX_LIMIT = 20
# 全部邮箱模式只接受 Gmail 的系统标签（核验：Gmail API “Manage labels” 指南的系统标签表）；
# 自定义标签（Label_123）只在所属邮箱里有意义，必须指定单个邮箱
SYSTEM_LABELS = frozenset(
    {
        "INBOX",
        "UNREAD",
        "STARRED",
        "IMPORTANT",
        "SENT",
        "CATEGORY_PERSONAL",
        "CATEGORY_SOCIAL",
        "CATEGORY_PROMOTIONS",
        "CATEGORY_UPDATES",
        "CATEGORY_FORUMS",
    }
)
_CURSOR_PREFIX = "mail_cursor:"
Status = Literal["ok", "needs_reconnect", "rate_limited", "unavailable", "pending"]
Outcome = Literal["ok", "partial", "pending", "all_failed", "no_access", "no_accounts"]
_STATE_VERSION = 1


def _err(status: int, code: str, message: str) -> MailError:
    return MailError(status, code, message)


@dataclass(frozen=True)
class AccountRef:
    """邮箱的快照：开头读一次数据库，之后不再碰请求的会话。"""

    id: uuid.UUID
    email: str
    display_name: str
    status: str
    readable: bool

    @classmethod
    def of(cls, account: MailAccount) -> "AccountRef":
        return cls(
            id=account.id,
            email=account.email,
            display_name=account.display_name,
            status=account.status,
            readable=can_read(account),
        )


@dataclass
class AccountCursor:
    """一个邮箱在当前查询下读到哪儿了（只有位置，没有内容）。"""

    started: bool = False
    token: str | None = None  # Gmail 的 nextPageToken
    pending: list[str] = field(default_factory=list)  # 已列出、还没取元数据的消息 ID
    done: bool = False
    estimate: int | None = None  # 最近一次 messages.list 的 resultSizeEstimate
    unread: int | None = None  # 收件箱未读数（labels.get INBOX）
    retry_at: float | None = None  # 限流到期（epoch 秒）


@dataclass
class CursorState:
    user: str
    workspace: str
    scope: str
    query: str
    salt: str
    limit: int
    order: list[str]
    accounts: dict[str, AccountCursor]

    def dump(self) -> str:
        data = asdict(self)
        data["v"] = _STATE_VERSION
        return json.dumps(data, separators=(",", ":"))


def _parse_state(raw: bytes | str) -> CursorState | None:
    """Redis 里的游标状态；格式不对一律当作过期。"""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("v") != _STATE_VERSION:
            return None
        accounts: dict[str, AccountCursor] = {}
        for key, value in dict(data["accounts"]).items():
            pending = [str(x) for x in value["pending"]]
            token = value["token"]
            estimate, unread, retry_at = value["estimate"], value["unread"], value["retry_at"]
            accounts[str(key)] = AccountCursor(
                started=bool(value["started"]),
                token=str(token) if token else None,
                pending=pending,
                done=bool(value["done"]),
                estimate=int(estimate) if isinstance(estimate, int) else None,
                unread=int(unread) if isinstance(unread, int) else None,
                retry_at=float(retry_at) if isinstance(retry_at, int | float) else None,
            )
        order = [str(x) for x in data["order"]]
        if sorted(order) != sorted(accounts):
            return None
        return CursorState(
            user=str(data["user"]),
            workspace=str(data["workspace"]),
            scope=str(data["scope"]),
            query=str(data["query"]),
            salt=str(data["salt"]),
            limit=int(data["limit"]),
            order=order,
            accounts=accounts,
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


@dataclass
class Turn:
    """一个邮箱在这一批里的结果。"""

    messages: list[fmt.ParsedMessage] = field(default_factory=list)
    finished: bool = False
    status: Status = "pending"
    error_code: str | None = None
    first: bool = False  # 这个邮箱在当前查询下的第一轮（读收件箱未读数）
    unread_read: bool = False


@dataclass(frozen=True)
class AccountResult:
    ref: AccountRef
    status: Status
    error_code: str | None
    loaded: int
    exhausted: bool
    unread: int | None
    retry_at: datetime | None


@dataclass(frozen=True)
class AggregatePage:
    items: list[tuple[AccountRef, fmt.ParsedMessage]]
    accounts: list[AccountResult]
    outcome: Outcome
    partial: bool
    next_cursor: str | None
    total_estimate: int | None


def sort_key(entry: tuple[AccountRef, fmt.ParsedMessage]) -> tuple[int, float, str, str]:
    """internalDate 倒序；同一时间按 account_id、message_id；时间未知的放最后。"""
    ref, message = entry
    when = message.internal_date
    return (
        1 if when is None else 0,
        -when.timestamp() if when is not None else 0.0,
        str(ref.id),
        message.message_id,
    )


def _status_of(error: ApiError) -> Status:
    if error.code == "RATE_LIMITED":
        return "rate_limited"
    if error.code in ("MAIL_RECONNECT", "MAIL_SCOPE_MISSING"):
        return "needs_reconnect"
    return "unavailable"


def outcome_of(statuses: Sequence[Status]) -> Outcome:
    if not statuses:
        return "no_accounts"
    if all(s == "ok" for s in statuses):
        return "ok"
    if any(s == "ok" for s in statuses):
        return "partial"
    if all(s == "needs_reconnect" for s in statuses):
        return "no_access"
    if any(s == "pending" for s in statuses):
        return "pending"
    return "all_failed"


class MailAggregateService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        redis: Redis,
        runtime: ConnectorRuntime,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.redis = redis
        self.runtime = runtime
        self._slots = asyncio.Semaphore(TOTAL_HTTP)

    # ------------------------------------------------------------ 游标

    def _query_hash(self, salt: str, query: str | None, label: str | None, limit: int) -> str:
        """查询条件的 HMAC（Redis 里不放搜索词原文）。"""
        secret = self.settings.session_secret
        key = secret.get_secret_value().encode() if secret else b"mail-aggregate-cursor"
        canonical = json.dumps([query or "", label or "", limit], ensure_ascii=False)
        return hmac.new(key, f"{salt}\n{canonical}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _fingerprint(single: bool, ids: Sequence[uuid.UUID]) -> str:
        text = ("one:" if single else "all:") + ",".join(sorted(str(i) for i in ids))
        return hashlib.sha256(text.encode()).hexdigest()

    async def _load(self, cursor: str) -> CursorState | None:
        raw = await self.redis.get(_CURSOR_PREFIX + cursor)
        return None if raw is None else _parse_state(raw)

    async def _save(self, state: CursorState) -> str:
        handle = secrets.token_urlsafe(24)
        await self.redis.set(_CURSOR_PREFIX + handle, state.dump(), ex=CURSOR_TTL)
        return handle

    # ------------------------------------------------------------ 读一批

    async def batch(
        self,
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        account_id: uuid.UUID | None,
        query: str | None,
        label: str | None,
        cursor: str | None,
        limit: int,
    ) -> AggregatePage:
        if account_id is None and label and label not in SYSTEM_LABELS:
            raise _err(
                422,
                "MAIL_LABEL_NEEDS_ACCOUNT",
                "自定义标签只在所属邮箱里有效：先选一个邮箱再按这个标签查看",
            )
        # 数据库阶段：用请求的会话读邮箱清单，读完结束事务，网络阶段不占连接
        service = MailService(db, self.settings, self.redis, self.runtime)
        rows = (
            [await service.account(workspace_id, account_id)]
            if account_id is not None
            else await service.accounts(workspace_id)
        )
        refs = [AccountRef.of(a) for a in rows]
        await db.rollback()
        by_id = {str(r.id): r for r in refs}
        scope = self._fingerprint(account_id is not None, [r.id for r in refs])

        if cursor:
            state = await self._load(cursor)
            if state is None:
                raise _err(422, "MAIL_CURSOR_EXPIRED", "这一页的游标已过期，请从第一页重新加载")
            if (
                state.user != str(user_id)
                or state.workspace != str(workspace_id)
                or state.scope != scope
                or state.limit != limit
                or not hmac.compare_digest(
                    state.query, self._query_hash(state.salt, query, label, limit)
                )
            ):
                raise _err(
                    422,
                    "MAIL_CURSOR_MISMATCH",
                    "邮箱范围或查询条件变了，旧的游标不能再用：请从第一页重新加载",
                )
        else:
            salt = secrets.token_hex(8)
            state = CursorState(
                user=str(user_id),
                workspace=str(workspace_id),
                scope=scope,
                query=self._query_hash(salt, query, label, limit),
                salt=salt,
                limit=limit,
                order=list(by_id),
                accounts={aid: AccountCursor() for aid in by_id},
            )

        now = time.time()
        fixed: dict[str, Turn] = {}
        runnable: list[str] = []
        for aid in state.order:
            ref, entry = by_id[aid], state.accounts[aid]
            if not ref.readable:
                code = "MAIL_RECONNECT" if ref.status != "active" else "MAIL_SCOPE_MISSING"
                fixed[aid] = Turn(finished=True, status="needs_reconnect", error_code=code)
            elif entry.done:
                fixed[aid] = Turn(finished=True, status="ok")
            elif entry.retry_at is not None and entry.retry_at > now:
                fixed[aid] = Turn(finished=True, status="rate_limited", error_code="RATE_LIMITED")
            else:
                runnable.append(aid)

        turns, attempted = await self._run(
            runnable, by_id, state, workspace_id=workspace_id, query=query, label=label
        )
        # 轮转：这一批没轮到的放最前，读过（包括没读完）的放最后
        skipped = [aid for aid in runnable if aid not in attempted]
        state.order = (
            skipped
            + [aid for aid in state.order if aid not in skipped and aid not in attempted]
            + attempted
        )

        results: list[AccountResult] = []
        items: list[tuple[AccountRef, fmt.ParsedMessage]] = []
        for ref in refs:
            aid = str(ref.id)
            entry = state.accounts[aid]
            turn = fixed.get(aid) or turns.get(aid) or Turn()
            status: Status = turn.status if turn.finished else "pending"
            items.extend((ref, m) for m in turn.messages)
            results.append(
                AccountResult(
                    ref=ref,
                    status=status,
                    error_code=turn.error_code if status != "pending" else None,
                    loaded=len(turn.messages),
                    exhausted=entry.done,
                    unread=entry.unread,
                    retry_at=datetime.fromtimestamp(entry.retry_at, UTC)
                    if status == "rate_limited" and entry.retry_at
                    else None,
                )
            )
        items.sort(key=sort_key)

        # 还有没读完的邮箱才给下一页；需要重新连接的邮箱读不动，不算
        more = any(not r.exhausted and r.status != "needs_reconnect" for r in results)
        next_cursor = await self._save(state) if more else None
        statuses = [r.status for r in results]
        estimates = [state.accounts[str(r.ref.id)].estimate for r in results]
        total = (
            sum(e for e in estimates if e is not None)
            if results and all(s == "ok" for s in statuses) and None not in estimates
            else None
        )
        logger.info(
            "mail aggregate batch",
            extra={
                "accounts": len(refs),
                "items": len(items),
                "not_ok": sum(1 for s in statuses if s != "ok"),
                "first_page": cursor is None,
            },
        )
        return AggregatePage(
            items=items,
            accounts=results,
            outcome=outcome_of(statuses),
            partial=any(s != "ok" for s in statuses),
            next_cursor=next_cursor,
            total_estimate=total,
        )

    async def _run(
        self,
        runnable: list[str],
        by_id: dict[str, AccountRef],
        state: CursorState,
        *,
        workspace_id: uuid.UUID,
        query: str | None,
        label: str | None,
    ) -> tuple[dict[str, Turn], list[str]]:
        """最多 3 个 worker 依次领邮箱；预算到了就取消还在读的，已读到的照样返回。"""
        turns = {aid: Turn() for aid in runnable}
        queue = list(runnable)
        attempted: list[str] = []

        async def worker() -> None:
            while queue:
                aid = queue.pop(0)
                attempted.append(aid)
                await self._turn(
                    by_id[aid],
                    state.accounts[aid],
                    turns[aid],
                    workspace_id=workspace_id,
                    query=query,
                    label=label,
                    limit=state.limit,
                )

        workers = [asyncio.create_task(worker()) for _ in range(min(ACCOUNT_WORKERS, len(queue)))]
        if workers:
            done, late = await asyncio.wait(workers, timeout=BATCH_BUDGET)
            for task in late:
                task.cancel()
            await asyncio.gather(*late, return_exceptions=True)
            for task in done:
                task.result()  # worker 里的意外错误（不是邮箱的读取错误）照常抛出
        return turns, attempted

    async def _turn(
        self,
        ref: AccountRef,
        entry: AccountCursor,
        turn: Turn,
        *,
        workspace_id: uuid.UUID,
        query: str | None,
        label: str | None,
        limit: int,
    ) -> None:
        """读一个邮箱的一批：自己的数据库会话、自己的 MailService。"""
        async with self.sessionmaker() as db:
            service = MailService(db, self.settings, self.redis, self.runtime)

            async def action(client: gmail.GmailClient) -> None:
                await self._fetch(client, entry, turn, query=query, label=label, limit=limit)

            turn.first = not entry.started
            try:
                # 重新校验：邮箱还在这个工作空间、没断开、授权有效
                account = await service.account(workspace_id, ref.id)
                await service.read_with(account, action)
            except ApiError as error:
                # MailError 与部署层面的错误（例如凭证密钥缺失）：只算这个邮箱失败
                turn.status, turn.error_code = _status_of(error), error.code
                if error.code == "RATE_LIMITED":
                    retry = error.details.get("retry_after")
                    entry.retry_at = time.time() + (int(retry) if isinstance(retry, int) else 60)
                turn.finished = True
                return
        turn.status, turn.finished = "ok", True

    async def _fetch(
        self,
        client: gmail.GmailClient,
        entry: AccountCursor,
        turn: Turn,
        *,
        query: str | None,
        label: str | None,
        limit: int,
    ) -> None:
        """列一页（还有没取的 ID 时先取完）→ 取元数据。401 重跑时接着上次的进度。"""
        if not entry.started or (not entry.pending and entry.token):
            async with self._slots:
                page = await client.list_messages(
                    query=query or None,
                    label_ids=[label] if label else [],
                    max_results=limit,
                    page_token=entry.token,
                )
            refs = [m for m in page.get("messages") or [] if isinstance(m, dict) and m.get("id")]
            token = page.get("nextPageToken")
            estimate = page.get("resultSizeEstimate")
            entry.started = True
            entry.pending = [str(m["id"]) for m in refs[:limit]]
            entry.token = str(token) if token else None
            entry.estimate = estimate if isinstance(estimate, int) else None
        if turn.first and not turn.unread_read:
            entry.unread = await self._unread(client)
            turn.unread_read = True
        await self._metadata(client, entry, turn, limit)
        entry.done = not entry.pending and entry.token is None
        entry.retry_at = None

    async def _unread(self, client: gmail.GmailClient) -> int | None:
        """收件箱未读数：Gmail 给了才有；读不到不影响列表。"""
        try:
            async with self._slots:
                data = await client.label("INBOX")
        except ConnectorError as error:
            if error.kind == "AUTH_FAILED" and not error.resource_level:
                raise  # 交给 MailService 刷新令牌后重跑
            return None
        value = data.get("messagesUnread")
        return value if isinstance(value, int) and value >= 0 else None

    async def _metadata(
        self, client: gmail.GmailClient, entry: AccountCursor, turn: Turn, limit: int
    ) -> None:
        """每个邮箱最多 2 个并发取元数据；取到一封就从 pending 里去掉（被取消时进度不丢）。"""
        todo = list(entry.pending[:limit])
        failures: list[ConnectorError] = []

        async def puller() -> None:
            while todo and not failures:
                message_id = todo.pop(0)
                try:
                    async with self._slots:
                        data = await client.get_message(
                            message_id, format_="metadata", headers=METADATA_HEADERS
                        )
                except ConnectorError as error:
                    if error.kind == "NOT_FOUND":  # 列出之后被删掉了
                        entry.pending.remove(message_id)
                        continue
                    failures.append(error)
                    return
                turn.messages.append(fmt.parse(data))
                entry.pending.remove(message_id)

        await asyncio.gather(*(puller() for _ in range(ACCOUNT_FETCHES)))
        if failures:
            raise failures[0]


__all__ = [
    "ACCOUNT_FETCHES",
    "ACCOUNT_WORKERS",
    "BATCH_BUDGET",
    "CURSOR_TTL",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "SYSTEM_LABELS",
    "TOTAL_HTTP",
    "AccountRef",
    "AccountResult",
    "AggregatePage",
    "MailAggregateService",
    "outcome_of",
    "sort_key",
]
