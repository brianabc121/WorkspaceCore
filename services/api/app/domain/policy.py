"""工作空间权限策略（docs/DEVELOPMENT_SPEC.md 第 9.2 节；契约 ADR 第 5、6 节）。

所有工作空间范围的读写都经过 require_role：
- 不是成员：404 NOT_FOUND（不区分“不存在”和“无权访问”，防止枚举）；
- 是成员但角色不够：403 FORBIDDEN。
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import Workspace
from app.repositories import workspaces as workspace_repo
from app.schemas.workspaces import Role

_RANK = {Role.viewer: 0, Role.member: 1, Role.admin: 2, Role.owner: 3}


@dataclass(frozen=True)
class WorkspaceAccess:
    workspace: Workspace
    role: Role
    # 当前用户；只属于用户自己的数据（通知）按它过滤
    user_id: uuid.UUID | None = None


def has_role(role: Role, minimum: Role) -> bool:
    return _RANK[role] >= _RANK[minimum]


def not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "资源不存在")


async def require_role(
    db: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID, minimum: Role
) -> WorkspaceAccess:
    found = await workspace_repo.get_for_member(db, workspace_id=workspace_id, user_id=user_id)
    if found is None:
        raise not_found()
    workspace, role = found
    if not has_role(role, minimum):
        raise ApiError(403, "FORBIDDEN", "没有权限执行此操作")
    return WorkspaceAccess(workspace=workspace, role=role, user_id=user_id)
