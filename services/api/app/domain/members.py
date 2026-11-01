"""工作空间成员管理（docs/DEVELOPMENT_SPEC.md 第 9.2 节“admin 管理集成和成员”；T-0064）。

规则：
- 列出与管理成员需要 admin 及以上；
- admin 只能添加、修改、移除 member / viewer；授予 admin、收回 admin、移除 admin 只能由 owner 操作；
- owner 不能被修改或移除（不支持转让），也不能通过这里授予；
- 已有账号按邮箱加入；没有账号时必须同时给出显示名与初始密码，创建账号后加入；
- 所有改动写审计日志（不记录密码），在锁住工作空间行的事务里进行，并发的修改依次生效。

移除成员后，下一次请求的权限检查就会拒绝它（权限每次都按成员关系判断）；
它登记的 OpenClaw 实例以它的身份执行，之后的工具调用因没有角色而被拒绝。
"""

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import security
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess, require_role
from app.models import User, WorkspaceMember
from app.repositories import users as user_repo
from app.repositories import workspaces as workspace_repo
from app.schemas.members import MemberCreate, MemberOut, MemberUpdate
from app.schemas.workspaces import Role


def _member_not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "成员不存在")


def _owner_only() -> ApiError:
    return ApiError(403, "FORBIDDEN", "只有 owner 可以授予、收回或移除 admin")


class MemberService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.audit = AuditTrail(db, settings)

    async def _access(self, context: AuthContext, workspace_id: uuid.UUID) -> WorkspaceAccess:
        return await require_role(
            self.db, user_id=context.user.id, workspace_id=workspace_id, minimum=Role.admin
        )

    def _out(self, context: AuthContext, member: WorkspaceMember, user: User) -> MemberOut:
        return MemberOut(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=Role(member.role),
            joined_at=member.created_at,
            you=user.id == context.user.id,
        )

    async def members(self, context: AuthContext, workspace_id: uuid.UUID) -> list[MemberOut]:
        await self._access(context, workspace_id)
        rows = await self.db.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.created_at, User.email)
        )
        return [self._out(context, member, user) for member, user in rows]

    @staticmethod
    def _check_target(access: WorkspaceAccess, current: Role, new: Role | None = None) -> None:
        """owner 不能被改动；涉及 admin（原来是或将要是）只能由 owner 操作。"""
        if current == Role.owner:
            raise ApiError(403, "FORBIDDEN", "不能修改或移除 owner")
        if access.role != Role.owner and Role.admin in (current, new):
            raise _owner_only()

    async def _locked_member(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[WorkspaceMember, User]:
        row = (
            await self.db.execute(
                select(WorkspaceMember, User)
                .join(User, User.id == WorkspaceMember.user_id)
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                )
                .with_for_update(of=WorkspaceMember)
                .execution_options(populate_existing=True)
            )
        ).first()
        if row is None:
            raise _member_not_found()
        member, user = row
        return member, user

    async def add(
        self, context: AuthContext, workspace_id: uuid.UUID, body: MemberCreate
    ) -> tuple[MemberOut, bool]:
        """返回成员与是否新建了账号。"""
        access = await self._access(context, workspace_id)
        role = Role(body.role)
        self._check_target(access, Role.member, role)
        await workspace_repo.lock(self.db, workspace_id)
        user = await user_repo.get_by_email(self.db, body.email)
        created = False
        if user is None:
            if body.display_name is None or body.password is None:
                raise ApiError(
                    404,
                    "USER_NOT_FOUND",
                    "没有这个邮箱的账号：同时填写显示名与初始密码即可创建账号并加入",
                )
            password_hash = await asyncio.to_thread(
                security.hash_password, body.password.get_secret_value()
            )
            try:
                async with self.db.begin_nested():
                    user = await user_repo.create(
                        self.db,
                        email=body.email,
                        password_hash=password_hash,
                        display_name=body.display_name,
                    )
            except IntegrityError as error:
                raise ApiError(409, "MEMBER_EXISTS", "这个邮箱刚被注册，请重试") from error
            created = True
        elif body.password is not None:
            # 不允许借“加入成员”修改已有账号的密码
            raise ApiError(409, "USER_EXISTS", "这个邮箱已有账号：直接按邮箱加入，不要填写初始密码")
        existing = await self.db.get(WorkspaceMember, (workspace_id, user.id))
        if existing is not None:
            raise ApiError(409, "MEMBER_EXISTS", "这个账号已经是本工作空间的成员")
        member = WorkspaceMember(workspace_id=workspace_id, user_id=user.id, role=role.value)
        self.db.add(member)
        await self.db.flush()
        await self.db.refresh(member)
        if created:
            await self.audit.record(
                "user.created",
                workspace_ids=[workspace_id],
                actor_id=context.user.id,
                resource_type="user",
                resource_id=user.id,
                client_ip=context.client_ip,
            )
        await self.audit.record(
            "workspace.member_added",
            workspace_ids=[workspace_id],
            actor_id=context.user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={"role": role.value, "new_account": created},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        return self._out(context, member, user), created

    async def update(
        self,
        context: AuthContext,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        body: MemberUpdate,
    ) -> MemberOut:
        access = await self._access(context, workspace_id)
        await workspace_repo.lock(self.db, workspace_id)
        member, user = await self._locked_member(workspace_id, user_id)
        current, new = Role(member.role), Role(body.role)
        self._check_target(access, current, new)
        if current != new:
            member.role = new.value
            await self.db.flush()
            await self.audit.record(
                "workspace.member_role_changed",
                workspace_ids=[workspace_id],
                actor_id=context.user.id,
                resource_type="user",
                resource_id=user.id,
                metadata={"role": {"old": current.value, "new": new.value}},
                client_ip=context.client_ip,
            )
            await self.db.commit()
        return self._out(context, member, user)

    async def remove(
        self, context: AuthContext, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        access = await self._access(context, workspace_id)
        await workspace_repo.lock(self.db, workspace_id)
        member, user = await self._locked_member(workspace_id, user_id)
        current = Role(member.role)
        self._check_target(access, current)
        await self.db.delete(member)
        await self.audit.record(
            "workspace.member_removed",
            workspace_ids=[workspace_id],
            actor_id=context.user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={"role": current.value},
            client_ip=context.client_ip,
        )
        await self.db.commit()
