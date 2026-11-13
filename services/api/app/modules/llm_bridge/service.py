"""大模型会话桥模块的业务逻辑（docs/v2/LLM_BRIDGE_PLAN.md 第 4.2、8、13 节）。

- 会话桥的登记、登录、环境、设置：admin 能看，只有 owner 能改；改动都记审计。
- 按实例的权限（例外九）：管理员给实例打开后，实例的 llm_bridge_* 工具才列出、才能调用。
- 工具转发：工作台认出实例、检查权限，再带着“代表哪个实例、有哪些权限范围”转给会话桥；
  会话的归属、排队、限额、账号一致性由会话桥负责。
"""

from __future__ import annotations

import uuid
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
from app.modules.llm_bridge.client import BridgeCallError, BridgeClient, bridge_client
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeGrant
from app.modules.llm_bridge.schemas import (
    LlmBridgeAccountOut,
    LlmBridgeChannelOut,
    LlmBridgeGrantIn,
    LlmBridgeGrantOut,
    LlmBridgeIn,
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


class LlmBridgeService:
    def __init__(self, db: AsyncSession, settings: Settings, runtime: ConnectorRuntime) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime

    # ---- 会话桥登记 ----
    @staticmethod
    def bridge_out(row: LlmBridge) -> LlmBridgeOut:
        return LlmBridgeOut(
            id=row.id,
            name=row.name,
            base_url=row.base_url,
            enabled=row.enabled,
            has_token=bool(row.token_ref),
            created_at=row.created_at,
            updated_at=row.updated_at,
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

    async def create_bridge(self, access: WorkspaceAccess, body: LlmBridgeIn) -> LlmBridgeOut:
        workspace_id = access.workspace.id
        name = body.name.strip()
        await self._name_free(workspace_id, name, skip=None)
        bridge_id = uuid.uuid4()
        row = LlmBridge(
            id=bridge_id,
            workspace_id=workspace_id,
            name=name,
            base_url=clean_url(body.base_url),
            token_ref=self.runtime.credentials().seal(
                body.token, context=secret_context(workspace_id, bridge_id)
            ),
        )
        self.db.add(row)
        await self.db.flush()
        await self._audit(
            access, "bridge_created", row.id, {"name": name, "base_url": row.base_url}
        )
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

    async def delete_bridge(self, access: WorkspaceAccess, bridge_id: uuid.UUID) -> None:
        row = await self.bridge(access.workspace.id, bridge_id)
        await self._audit(access, "bridge_deleted", row.id, {"name": row.name})
        await self.db.delete(row)
        await self.db.commit()

    def client(self, row: LlmBridge) -> BridgeClient:
        token = (
            self.runtime.credentials()
            .resolve(row.token_ref, context=secret_context(row.workspace_id, row.id))
            .get_secret_value()
        )
        return bridge_client(self.runtime, row.base_url, token)

    # ---- 转给会话桥的管理操作 ----
    async def admin(self, row: LlmBridge, method: str, path: str, body: Any = None) -> Any:
        try:
            return await self.client(row).admin(method, path, body)
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
            data = await self.client(row).admin("GET", "/status")
        except BridgeCallError as error:
            return LlmBridgeStatusOut(reachable=False, error=error.message)
        except ConnectorError as error:
            return LlmBridgeStatusOut(reachable=False, error=unavailable(error).message)
        accounts = [
            LlmBridgeAccountOut(slot=slot, **data["accounts"][slot])
            for slot in SLOTS
            if slot in data.get("accounts", {})
        ]
        return LlmBridgeStatusOut(
            reachable=True,
            version=str(data.get("version", "")),
            accounts=accounts,
            channels=[
                LlmBridgeChannelOut(**c)
                for c in data.get("channels", [])
                if c["platform"] != "fake"
            ],
            queued=int(data.get("queued", 0)),
            running=int(data.get("running", 0)),
            settings=LlmBridgeSettings(**data["settings"]),
        )

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
    def _grant_out(instance: AgentInstance, row: LlmBridgeGrant | None) -> LlmBridgeGrantOut:
        return LlmBridgeGrantOut(
            instance_id=instance.id,
            instance_name=instance.name,
            enabled=bool(row and row.enabled),
            can_write=bool(row and row.can_write),
            can_account=bool(row and row.can_account),
            can_takeover=bool(row and row.can_takeover),
            platforms=list(row.platforms) if row else [],
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
            },
        )
        await self.db.commit()
        return self._grant_out(instance, row)
