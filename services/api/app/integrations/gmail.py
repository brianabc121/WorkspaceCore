"""Google OAuth 2.0 与 Gmail API v1 客户端（T-0128，docs/v2/MAIL_PLAN.md 第 2 节）。

接口按核验记录 `docs/decisions/2026-11-06-gmail-verification.md`（Gmail API discovery 文档
revision 20261102、Google OAuth 2.0 网页服务器流程、配额与错误处理的官方文档）实现：

- 授权：`https://accounts.google.com/o/oauth2/v2/auth`，`response_type=code`、`access_type=offline`、
  `prompt=consent`、`include_granted_scopes=true`、`state`、PKCE `S256`；
- 令牌：`POST https://oauth2.googleapis.com/token`（`authorization_code` / `refresh_token`），
  响应 `access_token`、`expires_in`、`refresh_token`（只在 offline 时）、`scope`、`token_type`；
- 撤销：`POST https://oauth2.googleapis.com/revoke?token=…`，成功 200；
- Gmail：`gmail/v1/users/me/{profile,labels,messages,messages/{id},threads/{id},
  messages/{id}/attachments/{id},messages/send}`。

错误归类：401 → AUTH_FAILED；403 的 `rateLimitExceeded` / `userRateLimitExceeded` /
`dailyLimitExceeded` 与 429 → RATE_LIMITED（带 Retry-After）；其他 403 → AUTH_FAILED
（这个资源无权）；404 → NOT_FOUND；400 → VALIDATION_FAILED；5xx → UNAVAILABLE
（只有读请求在 ConnectorHttp 里重试）。
发信是写操作：超时归为 TIMEOUT_UNKNOWN，从不自动重试。错误对象不保存对方的响应原文。
"""

import base64
import hashlib
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - 公开的端点地址，不是密钥
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API_URL = "https://gmail.googleapis.com"
SCOPE_READ = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPES = (SCOPE_READ, SCOPE_SEND)
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded"})
_FORM = "application/x-www-form-urlencoded"


# ---------------------------------------------------------------- OAuth


def pkce_pair() -> tuple[str, str]:
    """PKCE：返回（code_verifier, code_challenge）。

    challenge = BASE64URL(SHA256(verifier))，不带 '='（RFC 7636 的 S256）。
    """
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(
    *,
    auth_url: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: Sequence[str] = SCOPES,
    login_hint: str | None = None,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{auth_url}?{urlencode(params)}"


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_in: int
    refresh_token: str | None
    scopes: frozenset[str]

    @property
    def can_read(self) -> bool:
        return SCOPE_READ in self.scopes

    @property
    def can_send(self) -> bool:
        return SCOPE_SEND in self.scopes


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _token_error(response: httpx.Response) -> ConnectorError:
    data = _json(response)
    error = str(data.get("error") or "")
    if error == "invalid_grant":
        return ConnectorError(
            "AUTH_FAILED", "Google 授权已失效，需要重新连接", status=400, reason="invalid_grant"
        )
    if error == "admin_policy_enforced":
        return ConnectorError(
            "AUTH_FAILED", "Google 管理员限制了这个应用", status=400, reason="admin_policy_enforced"
        )
    if error in ("invalid_client", "unauthorized_client"):
        return ConnectorError(
            "AUTH_FAILED", "OAuth 客户端 ID 或密钥不对", status=response.status_code, reason=error
        )
    if response.status_code >= 500:
        return ConnectorError("UNAVAILABLE", "Google 暂时不可用", status=response.status_code)
    return ConnectorError(
        "VALIDATION_FAILED",
        "Google 拒绝了这次授权请求",
        status=response.status_code,
        reason=error or "token_request_rejected",
    )


def _tokens(data: Mapping[str, Any]) -> TokenSet:
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise ConnectorError("UNAVAILABLE", "Google 返回的令牌不完整", reason="invalid_response")
    expires = data.get("expires_in")
    refresh = data.get("refresh_token")
    scope = data.get("scope")
    return TokenSet(
        access_token=access,
        expires_in=int(expires)
        if isinstance(expires, int | float | str) and str(expires).isdigit()
        else 3600,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        scopes=frozenset(str(scope).split()) if isinstance(scope, str) else frozenset(),
    )


class GoogleOAuth:
    def __init__(
        self,
        http: ConnectorHttp,
        *,
        client_id: str,
        client_secret: str,
        token_url: str = GOOGLE_TOKEN_URL,
        revoke_url: str = GOOGLE_REVOKE_URL,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._revoke_url = revoke_url

    async def _post_token(self, form: Mapping[str, str]) -> TokenSet:
        response = await self._http.request(
            "POST",
            self._token_url,
            headers={},
            content_type=_FORM,
            content=urlencode(form).encode(),
        )
        if response.status_code != 200:
            raise _token_error(response)
        return _tokens(_json(response))

    async def exchange(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        return await self._post_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
        )

    async def refresh(self, refresh_token: str) -> TokenSet:
        return await self._post_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        )

    async def revoke(self, token: str) -> None:
        response = await self._http.request(
            "POST",
            self._revoke_url,
            headers={},
            params={"token": token},
            content_type=_FORM,
            content=b"",
        )
        if response.status_code == 200:
            return
        if response.status_code == 400:
            # 令牌已经失效或已撤销：对断开来说结果一样
            return
        raise ConnectorError("UNAVAILABLE", "Google 暂时不可用", status=response.status_code)


# ---------------------------------------------------------------- Gmail


def _reason(data: Mapping[str, Any]) -> str:
    error = data.get("error")
    if not isinstance(error, dict):
        return ""
    errors = error.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("reason") or "")
    details = error.get("status")
    return str(details or "")


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    if value and value.isdigit():
        return int(value)
    return None


def classify(response: httpx.Response) -> ConnectorError:
    status = response.status_code
    reason = _reason(_json(response))
    if status == 401:
        return ConnectorError("AUTH_FAILED", "Gmail 授权失效", status=status, reason="unauthorized")
    if status == 429 or (status == 403 and reason in _RATE_REASONS):
        return ConnectorError(
            "RATE_LIMITED",
            "Gmail 限流或达到每日上限",
            status=status,
            retry_after=_retry_after(response) or 60,
            reason=reason or "rate_limited",
        )
    if status == 403:
        return ConnectorError(
            "AUTH_FAILED",
            "Gmail 拒绝访问",
            status=status,
            resource_level=True,
            reason=reason or "forbidden",
        )
    if status == 404:
        return ConnectorError("NOT_FOUND", "邮件不存在", status=status, reason="not_found")
    if status == 400:
        return ConnectorError(
            "VALIDATION_FAILED",
            "Gmail 不接受这个请求",
            status=status,
            reason=reason or "bad_request",
        )
    return ConnectorError(
        "UNAVAILABLE", "Gmail 暂时不可用", status=status, reason=reason or "server_error"
    )


class GmailClient:
    def __init__(
        self, http: ConnectorHttp, access_token: str, base_url: str = GMAIL_API_URL
    ) -> None:
        self._http = http
        self._base = base_url.rstrip("/") + "/gmail/v1/users/me"
        self._headers = {"Authorization": f"Bearer {access_token}", "User-Agent": "my-workspace"}

    async def _get(
        self, path: str, params: Sequence[tuple[str, Any]] | None = None
    ) -> dict[str, Any]:
        response = await self._http.request(
            "GET", self._base + path, headers=self._headers, params=list(params or [])
        )
        if response.status_code != 200:
            raise classify(response)
        data = _json(response)
        if not data and response.content:
            raise ConnectorError(
                "UNAVAILABLE", "Gmail 返回的数据无法解析", reason="invalid_response"
            )
        return data

    async def profile(self) -> dict[str, Any]:
        return await self._get("/profile")

    async def labels(self) -> list[dict[str, Any]]:
        data = await self._get("/labels")
        labels = data.get("labels")
        return [x for x in labels if isinstance(x, dict)] if isinstance(labels, list) else []

    async def list_messages(
        self,
        *,
        query: str | None,
        label_ids: Sequence[str] = (),
        max_results: int = 20,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params: list[tuple[str, Any]] = [("maxResults", max_results)]
        if query:
            params.append(("q", query))
        params.extend(("labelIds", label) for label in label_ids)
        if page_token:
            params.append(("pageToken", page_token))
        return await self._get("/messages", params)

    async def get_message(
        self, message_id: str, *, format_: str = "full", headers: Sequence[str] = ()
    ) -> dict[str, Any]:
        params: list[tuple[str, Any]] = [("format", format_)]
        params.extend(("metadataHeaders", name) for name in headers)
        return await self._get(f"/messages/{_safe_id(message_id)}", params)

    async def get_thread(self, thread_id: str, *, format_: str = "full") -> dict[str, Any]:
        return await self._get(f"/threads/{_safe_id(thread_id)}", [("format", format_)])

    async def attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        return await self._get(
            f"/messages/{_safe_id(message_id)}/attachments/{_safe_id(attachment_id)}"
        )

    async def send(self, raw: str, *, thread_id: str | None = None) -> dict[str, Any]:
        """发信：从不重试；超时等请求已发出后的错误由 ConnectorHttp 归为 TIMEOUT_UNKNOWN。"""
        body: dict[str, Any] = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        response = await self._http.request(
            "POST", self._base + "/messages/send", headers=self._headers, json=body
        )
        if response.status_code != 200:
            raise classify(response)
        return _json(response)


def _safe_id(value: str) -> str:
    """Gmail 的消息、会话、附件 ID 是 URL 安全的字符；其他字符一律拒绝，免得拼出别的路径。"""
    if not value or len(value) > 512 or not all(c.isalnum() or c in "-_" for c in value):
        raise ConnectorError("VALIDATION_FAILED", "邮件 ID 不对", reason="invalid_id")
    return value


__all__ = [
    "GMAIL_API_URL",
    "GOOGLE_AUTH_URL",
    "GOOGLE_REVOKE_URL",
    "GOOGLE_TOKEN_URL",
    "SCOPES",
    "SCOPE_READ",
    "SCOPE_SEND",
    "GmailClient",
    "GoogleOAuth",
    "TokenSet",
    "authorize_url",
    "classify",
    "pkce_pair",
]
