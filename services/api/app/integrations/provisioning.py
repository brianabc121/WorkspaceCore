"""部署脚本自动接入组件时用的初始化接口（T-0076）。

只在 `app.commands.connect` 里用，平时的同步不走这里。

Vikunja 2.7.1（核验 ADR 第 3 节；请求与响应格式见存档的 OpenAPI）：
- `POST /api/v2/login` `{username, password}` → `{token}`：短期 JWT，只用来创建下面的 API Token；
  用户名或密码不对返回 403；
- `POST /api/v2/tokens`（Bearer JWT）`{title, permissions, expires_at}` → `{id, token}`：
  明文 `tk_` Token 只在创建时返回一次。

n8n 2.45.7：建 owner、登录、建 API Key 与凭据只有内部 REST（`/rest/...`，不在 Public API 里），
在锁定版本上实测（docs/decisions/2026-11-02-deploy-connect.md 第 3 节）：
- `GET /rest/settings` → `data.userManagement.showSetupOnFirstLoad`：true 表示还没有 owner；
- `POST /rest/owner/setup` `{email, firstName, lastName, password}`：只能成功一次，之后返回 400；
- `POST /rest/login` `{emailOrLdapLoginId, password}` → Cookie `n8n-auth`；密码不对返回 401；
- `POST /rest/api-keys` `{label, scopes, expiresAt}` → `data.rawApiKey`：明文只返回一次；
- `POST /rest/credentials` `{name, type: "httpHeaderAuth", data: {name, value}}` → `data.id`。

密码、JWT、Cookie 与新建的 Token 不写日志，也不放进错误信息；错误对象不保存对方的响应原文。
写操作不重试（ConnectorHttp 只重试读请求）：结果未知时由调用方提示到对方界面里检查，不盲目重建。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp

# 工作台需要的 Vikunja 权限：核验 ADR 第 3 节的最小权限；
# projects_webhooks 用于自动注册 Webhook（实时同步）
VIKUNJA_PERMISSIONS: dict[str, list[str]] = {
    "projects": ["read_all", "read_one"],
    "tasks": ["create", "read_all", "read_one", "update", "delete"],
    "projects_webhooks": ["create", "read_all", "update", "delete"],
}
# 工作台只接受这四个只读 scope（核验 ADR 第 5.3 节）
N8N_READ_SCOPES: tuple[str, ...] = (
    "workflow:list",
    "workflow:read",
    "execution:list",
    "execution:read",
)
N8N_AUTH_COOKIE = "n8n-auth"


def _invalid(service: str) -> ConnectorError:
    return ConnectorError(
        "UNAVAILABLE", f"{service} 返回了无法识别的响应", reason="invalid_response"
    )


def _json(response: httpx.Response, service: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise _invalid(service) from error
    if not isinstance(body, dict):
        raise _invalid(service)
    return body


def _error(response: httpx.Response, service: str, *, login: bool = False) -> ConnectorError:
    status = response.status_code
    if status == 429:
        return ConnectorError("RATE_LIMITED", f"{service} 限流，稍后再试", status=status)
    if login and status in {400, 401, 403, 412}:
        # 用户名或密码不对：Vikunja 2.7.1 返回 403，n8n 2.45.7 返回 401（实测）
        return ConnectorError(
            "AUTH_FAILED", f"{service} 用户名或密码不对", status=status, reason="login_failed"
        )
    if status in {401, 403}:
        return ConnectorError(
            "AUTH_FAILED", f"{service} 拒绝了请求", status=status, reason="forbidden"
        )
    if status in {400, 422}:
        return ConnectorError(
            "VALIDATION_FAILED", f"{service} 拒绝了请求内容", status=status, reason="rejected"
        )
    return ConnectorError(
        "UNAVAILABLE", f"{service} 暂时不可用", status=status, reason="upstream_error"
    )


@dataclass(frozen=True)
class CreatedToken:
    token: str
    token_id: int | None
    expires_at: datetime


class VikunjaProvisioner:
    def __init__(self, http: ConnectorHttp, base_url: str) -> None:
        self._http = http
        self._api = f"{base_url.rstrip('/')}/api/v2"

    async def login(self, username: str, password: str) -> str:
        response = await self._http.request(
            "POST",
            f"{self._api}/login",
            headers={},
            json={"username": username, "password": password, "long_token": False},
        )
        if response.status_code >= 400:
            raise _error(response, "Vikunja", login=True)
        token = _json(response, "Vikunja").get("token")
        if not isinstance(token, str) or not token:
            raise _invalid("Vikunja")
        return token

    async def create_token(
        self, session_token: str, *, title: str, expires_at: datetime
    ) -> CreatedToken:
        response = await self._http.request(
            "POST",
            f"{self._api}/tokens",
            headers={"Authorization": f"Bearer {session_token}"},
            json={
                "title": title,
                "permissions": VIKUNJA_PERMISSIONS,
                "expires_at": expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            },
        )
        if response.status_code >= 400:
            raise _error(response, "Vikunja")
        body = _json(response, "Vikunja")
        token = body.get("token")
        if not isinstance(token, str) or not token.startswith("tk_"):
            raise _invalid("Vikunja")
        token_id = body.get("id")
        return CreatedToken(
            token=token,
            token_id=token_id if isinstance(token_id, int) else None,
            expires_at=expires_at,
        )

    async def revoke_token(self, session_token: str, token_id: int) -> bool:
        """尽力吊销（`DELETE /api/v2/tokens/{id}`）；失败只返回 False。"""
        try:
            response = await self._http.request(
                "DELETE",
                f"{self._api}/tokens/{token_id}",
                headers={"Authorization": f"Bearer {session_token}"},
            )
        except ConnectorError:
            return False
        return response.status_code < 400


class N8nProvisioner:
    def __init__(self, http: ConnectorHttp, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def _post(
        self, path: str, body: dict[str, Any], *, cookie: str | None = None, login: bool = False
    ) -> httpx.Response:
        headers = {"Cookie": f"{N8N_AUTH_COOKIE}={cookie}"} if cookie else {}
        response = await self._http.request(
            "POST", f"{self._base}{path}", headers=headers, json=body
        )
        if response.status_code >= 400:
            raise _error(response, "n8n", login=login)
        return response

    async def owner_exists(self) -> bool:
        response = await self._http.request("GET", f"{self._base}/rest/settings", headers={})
        if response.status_code >= 400:
            raise _error(response, "n8n")
        data = _json(response, "n8n").get("data")
        management = data.get("userManagement") if isinstance(data, dict) else None
        if not isinstance(management, dict) or not isinstance(
            management.get("showSetupOnFirstLoad"), bool
        ):
            raise _invalid("n8n")
        return not management["showSetupOnFirstLoad"]

    async def setup_owner(self, *, email: str, password: str) -> None:
        await self._post(
            "/rest/owner/setup",
            {"email": email, "firstName": "Workspace", "lastName": "Admin", "password": password},
        )

    async def login(self, *, email: str, password: str) -> str:
        response = await self._post(
            "/rest/login", {"emailOrLdapLoginId": email, "password": password}, login=True
        )
        cookie = response.cookies.get(N8N_AUTH_COOKIE)
        if not cookie:
            raise _invalid("n8n")
        return cookie

    async def create_api_key(self, cookie: str, *, label: str) -> str:
        response = await self._post(
            "/rest/api-keys",
            {"label": label, "scopes": list(N8N_READ_SCOPES), "expiresAt": None},
            cookie=cookie,
        )
        data = _json(response, "n8n").get("data")
        key = data.get("rawApiKey") if isinstance(data, dict) else None
        scopes = data.get("scopes") if isinstance(data, dict) else None
        if not isinstance(key, str) or not key:
            raise _invalid("n8n")
        if not isinstance(scopes, list) or sorted(scopes) != sorted(N8N_READ_SCOPES):
            # 拿到的 Key 权限和请求的不一致：不用它（工作台只接受只读 Key）
            raise ConnectorError(
                "VALIDATION_FAILED",
                "n8n 创建的 API Key 权限与请求的不一致",
                reason="scope_mismatch",
            )
        return key

    async def create_header_credential(
        self, cookie: str, *, name: str, header: str, value: str
    ) -> str:
        response = await self._post(
            "/rest/credentials",
            {"name": name, "type": "httpHeaderAuth", "data": {"name": header, "value": value}},
            cookie=cookie,
        )
        data = _json(response, "n8n").get("data")
        credential_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(credential_id, str) or not credential_id:
            raise _invalid("n8n")
        return credential_id
