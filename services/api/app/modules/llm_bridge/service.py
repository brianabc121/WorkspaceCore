"""大模型会话桥模块的业务逻辑（docs/v2/LLM_BRIDGE_PLAN.md 第 4.2、8、13 节）。

- 会话桥的登记、登录、环境、设置：admin 能看，只有 owner 能改；改动都记审计。
- 按实例的权限（例外九）：管理员给实例打开后，实例的 llm_bridge_* 工具才列出、才能调用。
- 工具转发：工作台认出实例、检查权限，再带着“代表哪个实例、有哪些权限范围”转给会话桥；
  会话的归属、排队、限额、账号一致性由会话桥负责。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit import AuditTrail
from app.domain.connectors import ConnectorRuntime
from app.domain.policy import WorkspaceAccess
from app.integrations.base import ConnectorError
from app.models import AgentInstance
from app.modules.llm_bridge import deploy_bundle, tunnel
from app.modules.llm_bridge.client import BridgeCallError, BridgeClient, bridge_client
from app.modules.llm_bridge.models import (
    LlmBridge,
    LlmBridgeGrant,
    LlmBridgeInstall,
    LlmBridgeSession,
)
from app.modules.llm_bridge.schemas import (
    ALL_PLATFORMS,
    PLATFORM_ALIASES,
    LlmBridgeAccountOut,
    LlmBridgeChannelOut,
    LlmBridgeConnectivityOut,
    LlmBridgeGrantIn,
    LlmBridgeGrantOut,
    LlmBridgeIn,
    LlmBridgeIndexOwnerOut,
    LlmBridgeIndexSessionOut,
    LlmBridgeInstallOut,
    LlmBridgeLoadOut,
    LlmBridgeLocalIn,
    LlmBridgeLocalOut,
    LlmBridgeLoginOut,
    LlmBridgeLoginStepOut,
    LlmBridgeOut,
    LlmBridgePatch,
    LlmBridgeQuotaList,
    LlmBridgeQuotaOut,
    LlmBridgeSearchHitOut,
    LlmBridgeSearchOut,
    LlmBridgeSettings,
    LlmBridgeStatusOut,
    LlmBridgeUsageOut,
    LlmBridgeUsageRowOut,
    canonical_platform,
    canonical_platforms,
)

SLOTS = ("openai", "claude", "workbuddy")
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


def secret_context(workspace_id: uuid.UUID, bridge_id: uuid.UUID) -> str:
    """凭证加密的用途：密文挪到别的工作空间或别的桥上会解密失败。"""
    return f"llm_bridge:{workspace_id}:{bridge_id}:token"


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": ["body", field], "msg": message, "type": "value_error"}]},
    )


def clean_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise _invalid("base_url", "地址要以 http:// 或 https:// 开头")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise _invalid("base_url", "地址里不能带账号、查询参数或 #")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


# 登录进度转发时的上限（T-0276）：会话桥自己也限长，这里再兜底
LOGIN_STEPS_MAX = 12
LOGIN_LOG_LINES = 200
LOGIN_LINE_CHARS = 500


def _short(value: Any, limit: int = LOGIN_LINE_CHARS) -> str | None:
    return None if value is None else str(value)[:limit]


def login_out(data: Any) -> LlmBridgeLoginOut:
    """会话桥的登录进度 → 接口的返回：步骤与运行信息限长，不认识的字段丢掉；没有进度时是 none。"""
    if not isinstance(data, dict):
        return LlmBridgeLoginOut(state="none")
    steps = [
        LlmBridgeLoginStepOut(
            key=str(step["key"])[:40],
            status=str(step.get("status") or "pending")[:20],
            detail=str(step.get("detail") or "")[:LOGIN_LINE_CHARS],
            started_at=_short(step.get("started_at"), 40),
            finished_at=_short(step.get("finished_at"), 40),
        )
        for step in (data.get("steps") or [])[:LOGIN_STEPS_MAX]
        if isinstance(step, dict) and step.get("key")
    ]
    return LlmBridgeLoginOut(
        state=str(data.get("state") or "none")[:20],
        verification_url=_short(data.get("verification_url"), 2048),
        user_code=_short(data.get("user_code"), 100),
        detail=str(data.get("detail") or "")[:LOGIN_LINE_CHARS],
        steps=steps,
        log=[str(line)[:LOGIN_LINE_CHARS] for line in (data.get("log") or [])][-LOGIN_LOG_LINES:],
        started_at=_short(data.get("started_at"), 40),
        finished_at=_short(data.get("finished_at"), 40),
    )


def unavailable(error: ConnectorError) -> ApiError:
    if error.kind == "AUTH_FAILED":
        return ApiError(503, "BRIDGE_AUTH_FAILED", "会话桥不接受登记的令牌，请检查令牌")
    return ApiError(503, "EXTERNAL_UNAVAILABLE", "会话桥暂时连不上")


def from_bridge(error: BridgeCallError) -> ApiError:
    return ApiError(_STATUS.get(error.code, 502), error.code, error.message or "会话桥返回了错误")


def install_out(row: LlmBridgeInstall, current_tag: str | None) -> LlmBridgeInstallOut:
    """current_tag：这个工作台带的构建文件的版本；和部署的不一样就可以重新部署来升级。"""
    return LlmBridgeInstallOut(
        target_kind=row.target_kind,
        target_id=row.server_id if row.target_kind == "agent_server" else row.runner_host_id,
        server_name=row.server_name,
        mode=row.mode,
        port=row.port,
        needs_sudo=row.needs_sudo,
        outdated=bool(current_tag and row.source_tag and row.source_tag != current_tag),
        deployed_at=row.deployed_at,
    )


async def remove_bridge(db: AsyncSession, row: LlmBridge) -> None:
    """去掉一座桥的登记（不提交）。

    只允许用这座桥的实例，之后一座都不能用（列表变空），不会变成“都能用”。
    """
    grants = (
        await db.scalars(
            select(LlmBridgeGrant).where(
                LlmBridgeGrant.workspace_id == row.workspace_id,
                LlmBridgeGrant.bridge_ids.is_not(None),
            )
        )
    ).all()
    for grant in grants:
        if grant.bridge_ids is not None and str(row.id) in grant.bridge_ids:
            grant.bridge_ids = [i for i in grant.bridge_ids if i != str(row.id)]
    await db.delete(row)


def _owner_kind(owner_id: Any) -> str:
    """管理器里的调用方编号是谁：没有的是官方程序里已有的会话，user: 开头是人，其余是实例。"""
    if not isinstance(owner_id, str) or not owner_id:
        return "external"
    return "user" if owner_id.startswith("user:") else "agent"


class LlmBridgeService:
    def __init__(self, db: AsyncSession, settings: Settings, runtime: ConnectorRuntime) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime

    # ---- 会话桥登记 ----
    @staticmethod
    def bridge_out(row: LlmBridge, deployment: LlmBridgeInstallOut | None = None) -> LlmBridgeOut:
        return LlmBridgeOut(
            id=row.id,
            name=row.name,
            base_url=row.base_url,
            enabled=row.enabled,
            has_token=bool(row.token_ref),
            deployment=deployment,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def installs(self, workspace_id: uuid.UUID) -> dict[uuid.UUID, LlmBridgeInstallOut]:
        """工作台部署出去的那些桥（T-0234）：桥的 id → 部署在哪台服务器上。"""
        rows = (
            await self.db.scalars(
                select(LlmBridgeInstall).where(LlmBridgeInstall.workspace_id == workspace_id)
            )
        ).all()
        tag = deploy_bundle.current_tag()
        return {row.bridge_id: install_out(row, tag) for row in rows}

    async def install(self, bridge_id: uuid.UUID) -> LlmBridgeInstall | None:
        return await self.db.scalar(
            select(LlmBridgeInstall).where(LlmBridgeInstall.bridge_id == bridge_id)
        )

    async def bridges(
        self, workspace_id: uuid.UUID, *, only_enabled: bool = False
    ) -> list[LlmBridge]:
        query = select(LlmBridge).where(LlmBridge.workspace_id == workspace_id)
        if only_enabled:
            query = query.where(LlmBridge.enabled)
        return list(
            (await self.db.scalars(query.order_by(LlmBridge.created_at, LlmBridge.id))).all()
        )

    async def bridge(self, workspace_id: uuid.UUID, bridge_id: uuid.UUID) -> LlmBridge:
        row = await self.db.scalar(
            select(LlmBridge).where(
                LlmBridge.id == bridge_id, LlmBridge.workspace_id == workspace_id
            )
        )
        if row is None:
            raise ApiError(404, "NOT_FOUND", "没有这座会话桥")
        return row

    async def _name_free(
        self, workspace_id: uuid.UUID, name: str, *, skip: uuid.UUID | None
    ) -> None:
        taken = await self.db.scalar(
            select(LlmBridge.id).where(
                LlmBridge.workspace_id == workspace_id, LlmBridge.name == name
            )
        )
        if taken is not None and taken != skip:
            raise ApiError(409, "NAME_TAKEN", "已经有同名的会话桥")

    async def _audit(
        self, access: WorkspaceAccess, action: str, resource_id: uuid.UUID, metadata: dict[str, Any]
    ) -> None:
        await AuditTrail(self.db, self.settings).record(
            f"llm_bridge.{action}",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="llm_bridge",
            resource_id=resource_id,
            metadata=metadata,
        )

    async def audit(
        self, access: WorkspaceAccess, action: str, resource_id: uuid.UUID, metadata: dict[str, Any]
    ) -> None:
        """记一条审计（动作名前面自动加 llm_bridge.）；不提交事务。"""
        await self._audit(access, action, resource_id, metadata)

    async def create_bridge(self, access: WorkspaceAccess, body: LlmBridgeIn) -> LlmBridgeOut:
        workspace_id = access.workspace.id
        name = body.name.strip()
        await self._name_free(workspace_id, name, skip=None)
        base_url = clean_url(body.base_url)
        platforms: list[str] | None = None
        if body.platforms is not None:
            # 把“控制的平台”写进会话桥；连不上或令牌不对就不登记
            platforms = await self._apply_platforms(base_url, body.token, body.platforms)
        bridge_id = uuid.uuid4()
        row = LlmBridge(
            id=bridge_id,
            workspace_id=workspace_id,
            name=name,
            base_url=base_url,
            token_ref=self.runtime.credentials().seal(
                body.token, context=secret_context(workspace_id, bridge_id)
            ),
        )
        self.db.add(row)
        await self.db.flush()
        metadata: dict[str, Any] = {"name": name, "base_url": row.base_url}
        if platforms is not None:
            metadata["platforms"] = platforms
        await self._audit(access, "bridge_created", row.id, metadata)
        await self.db.commit()
        return self.bridge_out(row)

    # ---- 本机部署（T-0238）：随工作台一起启动的那个 MCP 管理器 ----
    def _local(self) -> tuple[str, str] | None:
        """本机管理器的地址和令牌；没有随带时为 None。令牌只在服务端用，不给页面。"""
        url, token = self.settings.llm_bridge_local_url, self.settings.llm_bridge_local_token
        if not url or token is None or len(token.get_secret_value()) < 32:
            return None
        return clean_url(url), token.get_secret_value()

    async def local_state(self, workspace_id: uuid.UUID) -> LlmBridgeLocalOut:
        local = self._local()
        if local is None:
            return LlmBridgeLocalOut(available=False, bridge_id=None, taken=False)
        rows = (
            await self.db.scalars(select(LlmBridge).where(LlmBridge.base_url == local[0]))
        ).all()
        mine = next((row for row in rows if row.workspace_id == workspace_id), None)
        return LlmBridgeLocalOut(
            available=True,
            bridge_id=mine.id if mine is not None else None,
            taken=mine is None and bool(rows),
        )

    async def add_local(self, access: WorkspaceAccess, body: LlmBridgeLocalIn) -> LlmBridgeOut:
        """把随工作台启动的 MCP 管理器登记进这个工作空间：不用填地址、令牌，也不登录服务器。"""
        local = self._local()
        if local is None:
            raise ApiError(
                409,
                "LOCAL_UNAVAILABLE",
                "这个工作台没有随带本机的 MCP 管理器：升级到带它的版本并重新启动工作台后再试",
            )
        state = await self.local_state(access.workspace.id)
        if state.bridge_id is not None:
            raise ApiError(409, "LOCAL_EXISTS", "本机的 MCP 管理器已经添加过了")
        if state.taken:
            # 一个管理器的账号、会话只归一个工作空间：不让第二个工作空间共用
            raise ApiError(409, "LOCAL_TAKEN", "本机的 MCP 管理器已经被另一个工作空间添加")
        url, token = local
        return await self.create_bridge(
            access, LlmBridgeIn(name=body.name, base_url=url, token=token, platforms=body.platforms)
        )

    async def update_bridge(
        self, access: WorkspaceAccess, bridge_id: uuid.UUID, body: LlmBridgePatch
    ) -> LlmBridgeOut:
        row = await self.bridge(access.workspace.id, bridge_id)
        changed: list[str] = []
        if body.name is not None and body.name.strip() != row.name:
            await self._name_free(access.workspace.id, body.name.strip(), skip=row.id)
            row.name = body.name.strip()
            changed.append("name")
        moved = body.base_url is not None and clean_url(body.base_url) != row.base_url
        local = self._local()
        if (moved or body.token is not None) and local is not None and row.base_url == local[0]:
            raise ApiError(
                409,
                "BRIDGE_MANAGED",
                "这是随工作台启动的本机 MCP 管理器：地址和令牌由工作台管理，不能在这里改",
            )
        if (moved or body.token is not None) and await self.install(row.id) is not None:
            raise ApiError(
                409,
                "BRIDGE_MANAGED",
                "这个 MCP 管理器是工作台部署的：地址和令牌由部署管理，不能在这里改",
            )
        if body.base_url is not None and clean_url(body.base_url) != row.base_url:
            row.base_url = clean_url(body.base_url)
            changed.append("base_url")
        if body.token is not None:
            row.token_ref = self.runtime.credentials().seal(
                body.token, context=secret_context(row.workspace_id, row.id)
            )
            changed.append("token")
        if body.enabled is not None and body.enabled != row.enabled:
            row.enabled = body.enabled
            changed.append("enabled")
        await self.db.flush()
        await self._audit(access, "bridge_updated", row.id, {"name": row.name, "changed": changed})
        await self.db.commit()
        return self.bridge_out(row)

    async def _apply_platforms(
        self, base_url: str, token: str, platforms: Sequence[str]
    ) -> list[str]:
        client = bridge_client(self.runtime, base_url, token)
        try:
            saved = await client.admin(
                "PUT", "/settings", {"platforms": list(dict.fromkeys(platforms))}
            )
        except BridgeCallError as error:
            if error.code == "VALIDATION_FAILED" and "没有这个设置" in error.message:
                raise ApiError(
                    409,
                    "BRIDGE_OUTDATED",
                    "这座会话桥的版本太旧，不支持选择控制的平台：请先升级会话桥再登记",
                ) from error
            raise from_bridge(error) from error
        except ConnectorError as error:
            raise unavailable(error) from error
        chosen = saved.get("platforms") if isinstance(saved, dict) else None
        if not isinstance(chosen, list):
            raise ApiError(
                409,
                "BRIDGE_OUTDATED",
                "这座会话桥的版本太旧，不支持选择控制的平台：请先升级会话桥再登记",
            )
        return [str(p) for p in chosen]

    async def delete_bridge(self, access: WorkspaceAccess, bridge_id: uuid.UUID) -> None:
        row = await self.bridge(access.workspace.id, bridge_id)
        if await self.install(row.id) is not None:
            raise ApiError(
                409,
                "BRIDGE_MANAGED",
                "这个 MCP 管理器是工作台部署的：用“卸载”把服务器上的容器一起处理"
                "（服务器连不上时，卸载里可以选“只删除登记”）",
            )
        await self._audit(access, "bridge_deleted", row.id, {"name": row.name})
        await remove_bridge(self.db, row)
        await self.db.commit()

    async def client(self, row: LlmBridge) -> BridgeClient:
        """这座桥的客户端。

        工作台部署到别的服务器上的管理器只监听那台服务器的本机地址：经专用的 SSH 通道访问
        （tunnel.py）。通道在发请求时才建立，连不上按“会话桥暂时连不上”处理。
        """
        token = (
            self.runtime.credentials()
            .resolve(row.token_ref, context=secret_context(row.workspace_id, row.id))
            .get_secret_value()
        )
        install = await self.install(row.id)
        spec = tunnel.spec_of(install) if install is not None else None
        if spec is None:
            return bridge_client(self.runtime, row.base_url, token)
        settings = self.settings

        async def resolve() -> str:
            return await tunnel.POOL.url(settings, spec)

        return bridge_client(
            self.runtime,
            row.base_url,
            token,
            resolve=resolve,
            scope=f"llm_bridge:install:{spec.install_id}",
        )

    # ---- 转给会话桥的管理操作 ----
    async def admin(
        self,
        row: LlmBridge,
        method: str,
        path: str,
        body: Any = None,
        *,
        read_timeout: float | None = None,
    ) -> Any:
        try:
            return await (await self.client(row)).admin(
                method, path, body, read_timeout=read_timeout
            )
        except BridgeCallError as error:
            raise from_bridge(error) from error
        except ConnectorError as error:
            raise unavailable(error) from error

    async def admin_write(
        self,
        access: WorkspaceAccess,
        row: LlmBridge,
        action: str,
        method: str,
        path: str,
        body: Any = None,
        metadata: dict[str, Any] | None = None,
        *,
        read_timeout: float | None = None,
    ) -> Any:
        """改动类的管理操作：成功后记审计（不记密钥）。"""
        result = await self.admin(row, method, path, body, read_timeout=read_timeout)
        await self._audit(access, action, row.id, {"bridge": row.name, **(metadata or {})})
        await self.db.commit()
        return result

    async def status(self, row: LlmBridge) -> LlmBridgeStatusOut:
        try:
            data = await (await self.client(row)).admin("GET", "/status")
        except BridgeCallError as error:
            return LlmBridgeStatusOut(reachable=False, error=error.message)
        except ConnectorError as error:
            return LlmBridgeStatusOut(reachable=False, error=unavailable(error).message)
        accounts = [
            LlmBridgeAccountOut(slot=slot, **data["accounts"][slot])
            for slot in SLOTS
            if slot in data.get("accounts", {})
        ]
        # 旧版本的会话桥不报告 platforms：按“全部”处理；
        # 没升级的版本还单列 chatgpt，按 codex 算（T-0280）
        platforms = data.get("platforms")
        if not isinstance(platforms, list):
            platforms = list(ALL_PLATFORMS)
        return LlmBridgeStatusOut(
            reachable=True,
            version=str(data.get("version", "")),
            platforms=[p for p in canonical_platforms(platforms) if p in ALL_PLATFORMS],
            accounts=accounts,
            # 旧版本单列的 chatgpt.chat 就是 codex.chat：不再列出
            channels=[
                LlmBridgeChannelOut(**c)
                for c in data.get("channels", [])
                if c["platform"] not in ("fake", *PLATFORM_ALIASES)
            ],
            queued=int(data.get("queued", 0)),
            running=int(data.get("running", 0)),
            load={
                platform: LlmBridgeLoadOut(**row)
                for platform, row in (data.get("load") or {}).items()
                if platform in ALL_PLATFORMS
            },
            quota=self.quota_out(data.get("quota")),
            settings=self.settings_out(data["settings"]),
        )

    @staticmethod
    def quota_out(data: Any) -> dict[str, LlmBridgeQuotaOut]:
        if not isinstance(data, dict):
            return {}
        return {
            platform: LlmBridgeQuotaOut(**row)
            for platform, row in data.items()
            if platform in ALL_PLATFORMS and isinstance(row, dict)
        }

    async def connectivity(self, row: LlmBridge, *, refresh: bool) -> LlmBridgeConnectivityOut:
        """管理器所在的服务器到各平台能不能用、多慢。由管理器去测：要看的是它那台服务器的网络。"""
        path = f"/connectivity?refresh={'true' if refresh else 'false'}"
        try:
            data = await (await self.client(row)).admin("GET", path)
        except BridgeCallError as error:
            raise from_bridge(error) from error
        except ConnectorError as error:
            if error.reason == "invalid_response":  # 旧版本的管理器没有这个接口
                return LlmBridgeConnectivityOut(supported=False, slots={})
            raise unavailable(error) from error
        slots = data.get("slots") if isinstance(data, dict) else None
        return LlmBridgeConnectivityOut(
            supported=True, slots=slots if isinstance(slots, dict) else {}
        )

    async def quota(self, row: LlmBridge, *, refresh: bool) -> LlmBridgeQuotaList:
        """各平台的额度；refresh 时让管理器向能随时查的平台实查一次。"""
        data = await self.admin(row, "GET", f"/quota?refresh={'true' if refresh else 'false'}")
        return LlmBridgeQuotaList(platforms=self.quota_out(data.get("platforms")))

    @staticmethod
    def settings_out(data: dict[str, Any]) -> LlmBridgeSettings:
        values = dict(data)
        if isinstance(values.get("platforms"), list):
            values["platforms"] = [
                p for p in canonical_platforms(values["platforms"]) if p in ALL_PLATFORMS
            ]
        return LlmBridgeSettings(**values)

    # ---- 按实例的权限 ----
    # ---- 会话索引（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 3.4） ----
    async def sync_index(self, workspace_id: uuid.UUID) -> list[str]:
        """现在就把各个管理器上变了的会话同步进索引；返回没同步上的管理器的名字。"""
        from app.modules.llm_bridge import jobs  # jobs 导入本模块

        targets = [(b.id, b.name) for b in await self.bridges(workspace_id, only_enabled=True)]
        failed: list[str] = []
        for bridge_id, name in targets:
            try:
                await jobs.sync_bridge(self.db, self.settings, self.runtime, bridge_id)
            except (BridgeCallError, ConnectorError):
                await self.db.rollback()
                failed.append(name)
        return failed

    async def index(
        self,
        workspace_id: uuid.UUID,
        *,
        bridge_id: uuid.UUID | None = None,
        owner_kind: str | None = None,
        owner_id: str | None = None,
        platform: str | None = None,
        mode: str | None = None,
        status: str | None = None,
        problem: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
        q: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LlmBridgeIndexSessionOut], int | None]:
        query = (
            select(LlmBridgeSession, LlmBridge.name)
            .join(LlmBridge, LlmBridge.id == LlmBridgeSession.bridge_id)
            .where(LlmBridgeSession.workspace_id == workspace_id)
        )
        if bridge_id is not None:
            query = query.where(LlmBridgeSession.bridge_id == bridge_id)
        if owner_kind is not None:
            query = query.where(LlmBridgeSession.owner_kind == owner_kind)
        if owner_id is not None:
            query = query.where(LlmBridgeSession.owner_id == owner_id)
        if platform is not None:
            query = query.where(LlmBridgeSession.platform == platform)
        if mode is not None:
            query = query.where(LlmBridgeSession.mode == mode)
        if status is not None:
            query = query.where(LlmBridgeSession.status == status)
        if problem:  # 有失败、结果未知或中断的回合
            query = query.where(
                (LlmBridgeSession.turns_failed > 0) | (LlmBridgeSession.turns_interrupted > 0)
            )
        if since is not None:
            query = query.where(LlmBridgeSession.updated_at >= since)
        if until is not None:
            query = query.where(LlmBridgeSession.updated_at < until)
        if q.strip():
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(LlmBridgeSession.title.ilike(f"%{escaped}%", escape="\\"))
        rows = (
            await self.db.execute(
                query.order_by(LlmBridgeSession.updated_at.desc(), LlmBridgeSession.id)
                .limit(limit + 1)
                .offset(offset)
            )
        ).all()
        items = [self._index_out(row, name) for row, name in rows[:limit]]
        return items, offset + limit if len(rows) > limit else None

    @staticmethod
    def _index_out(row: LlmBridgeSession, name: str) -> LlmBridgeIndexSessionOut:
        return LlmBridgeIndexSessionOut(
            bridge_id=row.bridge_id,
            bridge_name=name,
            session_id=row.session_id,
            platform=row.platform,
            mode=row.mode,
            model=row.model,
            title=row.title,
            status=row.status,
            origin=row.origin,
            owner_kind=row.owner_kind,
            owner_id=row.owner_id,
            owner=row.owner_label,
            source=row.source,
            busy=row.busy,
            turns_total=row.turns_total,
            turns_failed=row.turns_failed,
            turns_interrupted=row.turns_interrupted,
            tokens=row.tokens,
            created_at=row.created_at,
            updated_at=row.updated_at,
            last_turn_at=row.last_turn_at,
        )

    async def index_owners(self, workspace_id: uuid.UUID) -> list[LlmBridgeIndexOwnerOut]:
        """索引里出现过的发起人（筛选用）：每个人或实例一行，名字取最近一次用的。"""
        rows = (
            await self.db.execute(
                select(
                    LlmBridgeSession.owner_kind,
                    LlmBridgeSession.owner_id,
                    func.max(LlmBridgeSession.owner_label),
                    func.count(),
                )
                .where(LlmBridgeSession.workspace_id == workspace_id)
                .group_by(LlmBridgeSession.owner_kind, LlmBridgeSession.owner_id)
                .order_by(func.count().desc())
            )
        ).all()
        return [
            LlmBridgeIndexOwnerOut(
                owner_kind=kind, owner_id=owner_id, owner=label or "", sessions=n
            )
            for kind, owner_id, label, n in rows
        ]

    async def _fan_out(
        self, workspace_id: uuid.UUID, path: str
    ) -> tuple[list[tuple[LlmBridge, Any]], list[str]]:
        """向每个启用的管理器读同一个管理接口；连不上或版本太旧的记下名字，不影响别的。"""
        bridges = await self.bridges(workspace_id, only_enabled=True)
        clients = [(b, await self.client(b)) for b in bridges]
        await self.db.commit()  # 等管理器回答时不占着事务

        async def one(client: BridgeClient) -> Any:
            try:
                return await client.admin("GET", path)
            except (BridgeCallError, ConnectorError):
                return None

        results = await asyncio.gather(*(one(client) for _, client in clients))
        ok = [(b, data) for (b, _), data in zip(clients, results, strict=True) if data is not None]
        failed = [b.name for (b, _), data in zip(clients, results, strict=True) if data is None]
        return ok, failed

    async def search(self, workspace_id: uuid.UUID, q: str, limit: int) -> LlmBridgeSearchOut:
        """在所有管理器的提问和回答里找这段文字。内容不经过工作台的数据库：现问现答。"""
        results, failed = await self._fan_out(
            workspace_id, f"/search?{urlencode({'q': q, 'limit': limit})}"
        )
        found: list[tuple[uuid.UUID, str, dict[str, Any]]] = []
        for bridge, data in results:
            hits = data.get("hits") if isinstance(data, dict) else None
            for hit in hits if isinstance(hits, list) else []:
                if isinstance(hit, dict) and isinstance(hit.get("session_id"), str):
                    found.append((bridge.id, bridge.name, hit))
        found.sort(key=lambda item: float(item[2].get("at") or 0), reverse=True)
        found = found[:limit]
        known: dict[tuple[uuid.UUID, str], LlmBridgeSession] = {}
        if found:
            rows = await self.db.scalars(
                select(LlmBridgeSession).where(
                    LlmBridgeSession.workspace_id == workspace_id,
                    LlmBridgeSession.session_id.in_({hit["session_id"] for _, _, hit in found}),
                )
            )
            known = {(row.bridge_id, row.session_id): row for row in rows}
        out = []
        for bridge_id, name, hit in found:
            row = known.get((bridge_id, hit["session_id"]))
            out.append(
                LlmBridgeSearchHitOut(
                    session=self._index_out(row, name) if row is not None else None,
                    bridge_id=bridge_id,
                    bridge_name=name,
                    session_id=hit["session_id"],
                    turn_id=hit.get("turn_id"),
                    role=str(hit.get("role") or ""),
                    snippet=str(hit.get("snippet") or "")[:400],
                    at=hit.get("at"),
                )
            )
        return LlmBridgeSearchOut(hits=out, failed=failed)

    async def usage(self, workspace_id: uuid.UUID, days: int) -> LlmBridgeUsageOut:
        """最近 days 天的用量：各管理器按天、平台、发起人汇总的回合数和 token。"""
        results, failed = await self._fan_out(workspace_id, f"/usage?days={days}")
        rows: list[LlmBridgeUsageRowOut] = []
        for bridge, data in results:
            items = data.get("rows") if isinstance(data, dict) else None
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                owner_id = item.get("owner_id")
                kind = "system" if owner_id == "system" else _owner_kind(owner_id)
                rows.append(
                    LlmBridgeUsageRowOut(
                        day=str(item.get("day") or ""),
                        bridge_id=bridge.id,
                        bridge_name=bridge.name,
                        platform=canonical_platform(str(item.get("platform") or "")),
                        owner_kind=kind,
                        owner_id=owner_id,
                        owner=str(item.get("owner") or "")[:80],
                        turns=int(item.get("turns") or 0),
                        failed=int(item.get("failed") or 0),
                        interrupted=int(item.get("interrupted") or 0),
                        tokens=int(item.get("tokens") or 0),
                        credits=_credits(item.get("credits")),
                    )
                )
        rows.sort(key=lambda r: (r.day, r.tokens), reverse=True)
        return LlmBridgeUsageOut(days=days, rows=rows, failed=failed)

    async def grant(self, instance_id: uuid.UUID) -> LlmBridgeGrant | None:
        return await self.db.get(LlmBridgeGrant, instance_id)

    async def grants(self, workspace_id: uuid.UUID) -> list[LlmBridgeGrantOut]:
        instances = (
            await self.db.scalars(
                select(AgentInstance)
                .where(AgentInstance.workspace_id == workspace_id)
                .order_by(AgentInstance.name, AgentInstance.id)
            )
        ).all()
        rows = {
            g.instance_id: g
            for g in (
                await self.db.scalars(
                    select(LlmBridgeGrant).where(LlmBridgeGrant.workspace_id == workspace_id)
                )
            ).all()
        }
        return [self._grant_out(i, rows.get(i.id)) for i in instances]

    @staticmethod
    def allowed(grant: LlmBridgeGrant, bridges: list[LlmBridge]) -> list[LlmBridge]:
        """这个实例能用的会话桥：bridge_ids 为空（NULL）表示都能用。"""
        if grant.bridge_ids is None:
            return bridges
        ids = set(grant.bridge_ids)
        return [b for b in bridges if str(b.id) in ids]

    @staticmethod
    def _grant_out(instance: AgentInstance, row: LlmBridgeGrant | None) -> LlmBridgeGrantOut:
        return LlmBridgeGrantOut(
            instance_id=instance.id,
            instance_name=instance.name,
            enabled=bool(row and row.enabled),
            can_write=bool(row and row.can_write),
            can_account=bool(row and row.can_account),
            can_takeover=bool(row and row.can_takeover),
            platforms=list(row.platforms) if row else [],
            bridge_ids=(
                [uuid.UUID(i) for i in row.bridge_ids]
                if row is not None and row.bridge_ids is not None
                else None
            ),
            environment_ids=list(row.environment_ids or []) if row else [],
        )

    async def set_grant(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, body: LlmBridgeGrantIn
    ) -> LlmBridgeGrantOut:
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id, AgentInstance.workspace_id == access.workspace.id
            )
        )
        if instance is None:
            raise ApiError(404, "NOT_FOUND", "没有这个实例")
        row = await self.grant(instance_id)
        if row is None:
            row = LlmBridgeGrant(instance_id=instance_id, workspace_id=access.workspace.id)
            self.db.add(row)
        row.enabled = body.enabled
        # 总开关关掉时其余权限一并收回
        row.can_write = body.enabled and body.can_write
        row.can_account = body.enabled and body.can_account
        row.can_takeover = body.enabled and body.can_takeover
        row.platforms = sorted(set(body.platforms))
        if body.bridge_ids is None:
            row.bridge_ids = None
        else:
            known = {b.id for b in await self.bridges(access.workspace.id)}
            if any(i not in known for i in body.bridge_ids):
                raise _invalid("bridge_ids", "没有这个 MCP 管理器")
            row.bridge_ids = sorted({str(i) for i in body.bridge_ids})
        # 没有写权限就用不上开发环境：一并收回
        row.environment_ids = sorted(set(body.environment_ids)) if row.can_write else []
        await self.db.flush()
        await self._audit(
            access,
            "grant_updated",
            instance_id,
            {
                "instance": instance.name,
                "enabled": row.enabled,
                "can_write": row.can_write,
                "can_account": row.can_account,
                "can_takeover": row.can_takeover,
                "platforms": row.platforms,
                "bridge_ids": row.bridge_ids,
                "environment_ids": row.environment_ids,
            },
        )
        await self.db.commit()
        return self._grant_out(instance, row)


def _credits(value: Any) -> float | None:
    """管理器报的积分：是数才要，旧版本的管理器没有这一项。"""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return round(float(value), 2)
    return None
