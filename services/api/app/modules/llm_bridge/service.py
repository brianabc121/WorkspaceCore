"""大模型会话桥模块的业务逻辑（docs/v2/LLM_BRIDGE_PLAN.md 第 4.2、8、13 节）。

- 会话桥的登记、登录、环境、设置：admin 能看，只有 owner 能改；改动都记审计。
- 按实例的权限（例外九）：管理员给实例打开后，实例的 llm_bridge_* 工具才列出、才能调用。
- 工具转发：工作台认出实例、检查权限，再带着“代表哪个实例、有哪些权限范围”转给会话桥；
  会话的归属、排队、限额、账号一致性由会话桥负责。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
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
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeGrant, LlmBridgeInstall
from app.modules.llm_bridge.schemas import (
    ALL_PLATFORMS,
    LlmBridgeAccountOut,
    LlmBridgeChannelOut,
    LlmBridgeGrantIn,
    LlmBridgeGrantOut,
    LlmBridgeIn,
    LlmBridgeInstallOut,
    LlmBridgeOut,
    LlmBridgePatch,
    LlmBridgeSettings,
    LlmBridgeStatusOut,
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
        """工作台部署出去的那些桥（T-0233）：桥的 id → 部署在哪台服务器上。"""
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
    async def admin(self, row: LlmBridge, method: str, path: str, body: Any = None) -> Any:
        try:
            return await (await self.client(row)).admin(method, path, body)
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
    ) -> Any:
        """改动类的管理操作：成功后记审计（不记密钥）。"""
        result = await self.admin(row, method, path, body)
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
        # 旧版本的会话桥不报告 platforms：按“全部”处理
        platforms = data.get("platforms")
        if not isinstance(platforms, list):
            platforms = list(ALL_PLATFORMS)
        return LlmBridgeStatusOut(
            reachable=True,
            version=str(data.get("version", "")),
            platforms=[p for p in platforms if p in ALL_PLATFORMS],
            accounts=accounts,
            channels=[
                LlmBridgeChannelOut(**c)
                for c in data.get("channels", [])
                if c["platform"] != "fake"
            ],
            queued=int(data.get("queued", 0)),
            running=int(data.get("running", 0)),
            settings=self.settings_out(data["settings"]),
        )

    @staticmethod
    def settings_out(data: dict[str, Any]) -> LlmBridgeSettings:
        values = dict(data)
        if isinstance(values.get("platforms"), list):
            values["platforms"] = [p for p in values["platforms"] if p in ALL_PLATFORMS]
        return LlmBridgeSettings(**values)

    # ---- 按实例的权限 ----
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
            },
        )
        await self.db.commit()
        return self._grant_out(instance, row)
