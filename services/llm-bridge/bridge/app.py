"""HTTP 入口：`POST /mcp`（MCP 工具）、`/admin/*`（管理接口，给工作台的设置页用）、`/healthz`。

认证（Authorization: Bearer）：
- 管理令牌（BRIDGE_ADMIN_TOKEN）：可以调用 /admin/*；调用 /mcp 时必须用请求头说明代表哪个调用方：
  X-Bridge-Caller（调用方编号）、X-Bridge-Caller-Label（显示名，URL 编码）、
  X-Bridge-Scopes（逗号分隔）。
  这是“经工作台调用”的接法：工作台认出实例并决定它的权限。
- 调用方令牌（管理接口发放）：只能调用 /mcp，身份和权限以桥里登记的为准。这是 OpenClaw 直连的接法。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from urllib.parse import unquote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from bridge import __version__
from bridge.channels import CHANNELS, FAKE_CHANNELS, SLOT_LABELS, find
from bridge.config import Settings, get_settings
from bridge.drivers.base import Driver
from bridge.drivers.cli_stream import CliStreamDriver, claude_profile, codebuddy_profile
from bridge.drivers.codex import CodexDriver
from bridge.drivers.fake import FakeDriver
from bridge.engine import BridgeError, Caller, Engine, env_view
from bridge.mcp_server import CALLER_STATE_KEY, McpRunner
from bridge.store import Store

logger = logging.getLogger("bridge")

SCOPES = ("write", "account", "takeover")
_CALLER_ID_RE = re.compile(r"^[\w.\-:]{1,100}$")
_STATUS = {
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "VALIDATION_FAILED": 422,
    "CONFLICT": 409,
    "RATE_LIMITED": 429,
    "NOT_SUPPORTED": 422,
    "NOT_LOGGED_IN": 409,
    "ACCOUNT_MISMATCH": 409,
    "UNAVAILABLE": 503,
}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def build_drivers(settings: Settings) -> dict[str, Driver]:
    drivers: dict[str, Driver] = {
        "openai": CodexDriver(
            settings.codex_bin, settings.home_dir / "codex", rpc_timeout=settings.rpc_timeout
        ),
        "claude": CliStreamDriver(
            claude_profile(settings.claude_bin),
            settings.home_dir / "claude",
            settings.secrets_dir,
            rpc_timeout=settings.rpc_timeout,
        ),
        "workbuddy": CliStreamDriver(
            codebuddy_profile(settings.codebuddy_bin),
            settings.home_dir / "codebuddy",
            settings.secrets_dir,
            rpc_timeout=settings.rpc_timeout,
        ),
    }
    if settings.enable_fake:
        drivers["fake"] = FakeDriver()
    return drivers


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "需要 Bearer 令牌")
    return token.strip()


def _is_admin(request: Request, token: str) -> bool:
    settings: Settings = request.app.state.settings
    return hmac.compare_digest(token.encode(), settings.admin_token.encode())


def require_admin(request: Request) -> None:
    if not _is_admin(request, _bearer(request)):
        raise HTTPException(401, "令牌不正确")


def resolve_caller(request: Request) -> Caller:
    token = _bearer(request)
    if _is_admin(request, token):
        caller_id = request.headers.get("x-bridge-caller", "")
        if not _CALLER_ID_RE.match(caller_id):
            raise HTTPException(400, "用管理令牌调用 /mcp 时必须带 X-Bridge-Caller")
        label = unquote(request.headers.get("x-bridge-caller-label", ""))[:80] or caller_id
        raw = request.headers.get("x-bridge-scopes", "")
        scopes = frozenset(s for s in (part.strip() for part in raw.split(",")) if s in SCOPES)
        return Caller(caller_id, label, scopes)
    store: Store = request.app.state.store
    row = store.caller_by_token(token_hash(token))
    if row is None:
        raise HTTPException(401, "令牌不正确")
    store.touch_caller(row["id"])
    return Caller(row["id"], row["label"], frozenset(s for s in row["scopes"] or [] if s in SCOPES))


Admin = Annotated[None, Depends(require_admin)]


class _McpResponse(Response):
    def __init__(self, caller: Caller) -> None:
        super().__init__()
        self.caller = caller

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        manager = scope["app"].state.mcp.manager
        state = {**scope.get("state", {}), CALLER_STATE_KEY: self.caller}
        await manager.handle_request({**scope, "state": state}, receive, send)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(_Body):
    method: str
    api_key: str | None = None
    oauth_token: str | None = None


class EnvironmentBody(_Body):
    platform: str
    mode: Literal["dev", "cloud"]
    name: str = Field(min_length=1, max_length=60)
    repo_url: str | None = Field(default=None, max_length=500)
    branch: str | None = Field(default=None, max_length=200)
    sandbox: Literal["read_only", "workspace_write", "exec"] = "read_only"
    network: bool = False
    description: str = Field(default="", max_length=300)
    enabled: bool = True


class SessionPatchBody(_Body):
    title: str = Field(min_length=1, max_length=200)


class CallerBody(_Body):
    label: str = Field(min_length=1, max_length=80)
    scopes: list[Literal["write", "account", "takeover"]] = []


def _env_config(body: EnvironmentBody) -> dict[str, Any]:
    if body.repo_url and not body.repo_url.startswith("https://"):
        raise BridgeError("VALIDATION_FAILED", "仓库地址只支持 https://")
    if body.branch and not re.fullmatch(r"[\w./\-]{1,200}", body.branch):
        raise BridgeError("VALIDATION_FAILED", "分支名不合法")
    return {
        "repo_url": body.repo_url or None,
        "branch": body.branch or None,
        "sandbox": body.sandbox,
        "network": body.network,
        "description": body.description,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        for path in (settings.data_dir, settings.home_dir, settings.work_dir, settings.secrets_dir):
            path.mkdir(parents=True, exist_ok=True)
        settings.secrets_dir.chmod(0o700)
        settings.home_dir.chmod(0o700)
        store = Store(settings.db_path)
        channels = CHANNELS + (FAKE_CHANNELS if settings.enable_fake else ())
        engine = Engine(store, build_drivers(settings), channels, settings)
        runner = McpRunner()
        app.state.settings, app.state.store, app.state.engine, app.state.mcp = (
            settings,
            store,
            engine,
            runner,
        )
        await runner.start()
        await engine.startup()
        try:
            yield
        finally:
            await engine.shutdown()
            await runner.stop()
            store.close()

    app = FastAPI(
        title="LLM Bridge", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.exception_handler(BridgeError)
    async def bridge_error(request: Request, error: BridgeError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": error.code, "message": error.message}},
            status_code=_STATUS.get(error.code, 400),
        )

    def engine_of(request: Request) -> Engine:
        engine: Engine = request.app.state.engine
        return engine

    def slot_of(request: Request, slot: str) -> str:
        if slot not in engine_of(request).drivers:
            raise BridgeError("NOT_FOUND", "没有这个账号槽位")
        return slot

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/mcp", include_in_schema=False)
    async def mcp_endpoint(request: Request) -> Response:
        return _McpResponse(resolve_caller(request))

    # ---- 管理接口 ----
    @app.get("/admin/status")
    async def admin_status(request: Request, _: Admin) -> dict[str, Any]:
        engine = engine_of(request)
        overview = await engine.overview()
        return {"version": __version__, **overview, "settings": engine.settings_view()}

    @app.post("/admin/accounts/{slot}/refresh")
    async def admin_refresh(request: Request, slot: str, _: Admin) -> dict[str, Any]:
        account = await engine_of(request).account(slot_of(request, slot), refresh=True)
        return {
            "state": account["state"],
            "identity": account.get("identity_label"),
            "detail": account["detail"],
        }

    @app.post("/admin/accounts/{slot}/login")
    async def admin_login(request: Request, slot: str, body: LoginBody, _: Admin) -> dict[str, Any]:
        payload = {
            k: v for k, v in (("api_key", body.api_key), ("oauth_token", body.oauth_token)) if v
        }
        return await engine_of(request).login(slot_of(request, slot), body.method, payload)

    @app.get("/admin/accounts/{slot}/login")
    async def admin_login_state(request: Request, slot: str, _: Admin) -> dict[str, Any]:
        return {"login": await engine_of(request).login_state(slot_of(request, slot))}

    @app.post("/admin/accounts/{slot}/logout")
    async def admin_logout(request: Request, slot: str, _: Admin) -> dict[str, bool]:
        await engine_of(request).logout(slot_of(request, slot))
        return {"ok": True}

    @app.post("/admin/accounts/{slot}/reset")
    async def admin_reset(request: Request, slot: str, _: Admin) -> dict[str, int]:
        return {"archived_sessions": await engine_of(request).reset_account(slot_of(request, slot))}

    @app.put("/admin/settings")
    async def admin_settings(request: Request, body: dict[str, Any], _: Admin) -> dict[str, Any]:
        return engine_of(request).update_settings(body)

    @app.get("/admin/environments")
    async def admin_envs(request: Request, _: Admin) -> dict[str, Any]:
        store: Store = request.app.state.store
        return {"environments": [env_view(e) for e in store.list_environments()]}

    @app.post("/admin/environments")
    async def admin_env_create(request: Request, body: EnvironmentBody, _: Admin) -> dict[str, Any]:
        engine, store = engine_of(request), request.app.state.store
        if find(engine.channels, body.platform, body.mode) is None:
            raise BridgeError("VALIDATION_FAILED", f"没有这个通道：{body.platform}.{body.mode}")
        if any(e["name"] == body.name for e in store.list_environments(body.platform, body.mode)):
            raise BridgeError("CONFLICT", "这个通道下已经有同名的环境")
        env = store.create_environment(body.platform, body.mode, body.name, _env_config(body))
        if not body.enabled:
            store.update_environment(env["id"], enabled=0)
        return env_view(store.get_environment(env["id"]))

    @app.put("/admin/environments/{env_id}")
    async def admin_env_update(
        request: Request, env_id: str, body: EnvironmentBody, _: Admin
    ) -> dict[str, Any]:
        store: Store = request.app.state.store
        env = store.get_environment(env_id)
        if env is None:
            raise BridgeError("NOT_FOUND", "没有这个环境")
        if (env["platform"], env["mode"]) != (body.platform, body.mode):
            raise BridgeError("VALIDATION_FAILED", "不能改环境所属的平台和模式")
        store.update_environment(
            env_id, name=body.name, config=_env_config(body), enabled=int(body.enabled)
        )
        return env_view(store.get_environment(env_id) or env)

    @app.delete("/admin/environments/{env_id}")
    async def admin_env_delete(request: Request, env_id: str, _: Admin) -> dict[str, bool]:
        if not request.app.state.store.delete_environment(env_id):
            raise BridgeError("NOT_FOUND", "没有这个环境")
        return {"ok": True}

    @app.get("/admin/callers")
    async def admin_callers(request: Request, _: Admin) -> dict[str, Any]:
        rows = request.app.state.store.list_callers()
        keys = ("id", "label", "scopes", "created_at", "last_seen_at")
        return {"callers": [{k: row[k] for k in keys} for row in rows]}

    @app.post("/admin/callers")
    async def admin_caller_create(request: Request, body: CallerBody, _: Admin) -> dict[str, Any]:
        token = "lb_" + secrets.token_urlsafe(32)
        row = request.app.state.store.create_caller(
            body.label, token_hash(token), list(body.scopes)
        )
        # 令牌只在这一次返回，桥里只存哈希
        return {"id": row["id"], "label": row["label"], "scopes": row["scopes"], "token": token}

    @app.delete("/admin/callers/{caller_id}")
    async def admin_caller_delete(request: Request, caller_id: str, _: Admin) -> dict[str, bool]:
        if not request.app.state.store.delete_caller(caller_id):
            raise BridgeError("NOT_FOUND", "没有这个调用方")
        return {"ok": True}

    @app.get("/admin/sessions")
    async def admin_sessions(
        request: Request,
        _: Admin,
        limit: int = 50,
        offset: int = 0,
        sync: bool = False,
        q: str = "",
        platform: str = "",
        status: Literal["active", "archived", "all"] = "all",
    ) -> dict[str, Any]:
        engine = engine_of(request)
        note = ""
        if sync:
            # 只读这座桥控制的平台的官方程序记录
            slots = [slot for slot in engine.drivers if engine.slot_enabled(slot)]
            failed = await engine.sync_external(slots)
            note = "、".join(SLOT_LABELS.get(s, s) for s in failed)
        rows = engine.store.list_sessions(
            include_archived=True,
            status=None if status == "all" else status,
            platform=platform or None,
            query=q.strip()[:120] or None,
            limit=min(max(limit, 1), 200) + 1,
            offset=max(offset, 0),
        )
        size = min(max(limit, 1), 200)
        busy = engine.store.busy_sessions()
        return {
            "sessions": [engine.admin_session_view(r, busy) for r in rows[:size]],
            "next_offset": max(offset, 0) + size if len(rows) > size else None,
            "sync_failed": note,
        }

    @app.get("/admin/sessions/{session_id}")
    async def admin_session(request: Request, session_id: str, _: Admin) -> dict[str, Any]:
        return await engine_of(request).conversation(session_id)

    @app.get("/admin/sessions/{session_id}/events")
    async def admin_session_events(
        request: Request, session_id: str, _: Admin, after: int = 0, wait_seconds: float = 0
    ) -> dict[str, Any]:
        return await engine_of(request).session_events(session_id, max(after, 0), wait_seconds)

    @app.patch("/admin/sessions/{session_id}")
    async def admin_session_rename(
        request: Request, session_id: str, body: SessionPatchBody, _: Admin
    ) -> dict[str, Any]:
        return engine_of(request).rename_session(session_id, body.title)

    @app.get("/admin/sessions/{session_id}/turns")
    async def admin_turns(request: Request, session_id: str, _: Admin) -> dict[str, Any]:
        engine = engine_of(request)
        if engine.store.get_session(session_id) is None:
            raise BridgeError("NOT_FOUND", "没有这个会话")
        turns = engine.store.list_turns(session_id=session_id, limit=200, newest_first=True)
        return {"turns": [{**engine.turn_view(t), "text": t["text"]} for t in turns]}

    return app
