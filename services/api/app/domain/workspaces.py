"""工作空间详情与设置。权限一律经过 app/domain/policy.py。"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess, require_role
from app.schemas.workspaces import Role, WorkspaceOut


def _to_out(access: WorkspaceAccess) -> WorkspaceOut:
    workspace = access.workspace
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        role=access.role,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


class WorkspaceService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.audit = AuditTrail(db, settings)

    async def get(self, context: AuthContext, workspace_id: uuid.UUID) -> WorkspaceOut:
        access = await require_role(
            self.db, user_id=context.user.id, workspace_id=workspace_id, minimum=Role.viewer
        )
        return _to_out(access)

    async def update(
        self, context: AuthContext, workspace_id: uuid.UUID, *, name: str
    ) -> WorkspaceOut:
        access = await require_role(
            self.db, user_id=context.user.id, workspace_id=workspace_id, minimum=Role.admin
        )
        workspace = access.workspace
        if workspace.name != name:
            old = workspace.name
            workspace.name = name
            await self.db.flush()
            await self.audit.record(
                "workspace.updated",
                workspace_ids=[workspace.id],
                actor_id=context.user.id,
                resource_type="workspace",
                resource_id=workspace.id,
                metadata={"name": {"old": old, "new": name}},
                client_ip=context.client_ip,
            )
            await self.db.commit()
        return _to_out(access)
