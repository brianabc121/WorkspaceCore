"""IM 群、通讯录与分组（V2 OC-3）。

契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 5.6、5.7 节：

- **群**：群主是当前用户（`groupType=2`）；成员只能是本工作空间的实例（至少一个，最多 50 个）和
  其他 owner / admin（最多 20 个）。`ws_notice` 建群时一起拉进去，用来发“自动接力已暂停”等提示；
  调用者 imAdmin 不入群（`internal/rpc/group/group.go` 的 CreateGroup 只让群主、管理员、成员入群，
  真实实例已核实）。
  建群必须带 Idempotency-Key：群 ID 由键推出（`ws` + 32 位十六进制），OpenIM 已有同 ID 的群时
  返回 1202，就当作上次已经建好（结果未知时重发同一个键即可）。
  改名与公告、邀请、移出（不能移出群主）、解散（只有群主）先调用 OpenIM，失败时 503、本地不改。
- **防刷屏设置**：乐观并发；`paused` 取 Redis 里的连续条数（app/domain/im_callbacks.py）。
- **通讯录**：本工作空间的全部实例，停用的也在（显示为 disabled）——没有删除好友。
- **分组**：属于工作空间，名称唯一；成员整体替换；删除分组不影响实例与好友关系。
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import events, idempotency
from app.domain.audit import AuditTrail
from app.domain.im import IM_ROLES, ImAccounts, admin_im_user_id, im_unavailable
from app.domain.im_callbacks import STREAK_KEY
from app.domain.policy import WorkspaceAccess
from app.integrations.base import ConnectorError
from app.integrations.openim import OpenIMClient, group_conversation_id, single_conversation_id
from app.models import (
    AgentInstance,
    ImContactGroup,
    ImContactGroupMember,
    ImConversation,
    ImGroup,
    ImGroupMember,
    User,
    WorkspaceMember,
)
from app.schemas.im import (
    ImContactGroupCreate,
    ImContactGroupList,
    ImContactGroupMembersReplace,
    ImContactGroupOut,
    ImContactGroupUpdate,
    ImContactList,
    ImContactOut,
    ImGroupCreate,
    ImGroupMemberOut,
    ImGroupMembersAdd,
    ImGroupOut,
    ImGroupPolicyOut,
    ImGroupPolicyUpdate,
    ImGroupUpdate,
)

logger = logging.getLogger("app.im")

NOTICE_USER_ID = "ws_notice"
RELAY_PAUSED_TEXT = "自动接力已暂停：实例连续发言达到上限，管理员发言后恢复。"


def group_id_for(key: uuid.UUID) -> str:
    """建群的群 ID 由 Idempotency-Key 推出：重发同一个键时 OpenIM 返回 1202 而不会再建一个。"""
    return f"ws{key.hex}"


def _not_found(what: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what}不存在")


def _version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current},
    )


def _invalid(loc: list[str], message: str) -> ApiError:
    return ApiError(
        422, "VALIDATION_FAILED", message, details={"errors": [{"loc": loc, "msg": message}]}
    )


class ImGroupService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        redis: Redis | None,
        client: OpenIMClient | None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.client = client
        self.audit = AuditTrail(db, settings)

    @staticmethod
    def _user_id(access: WorkspaceAccess) -> uuid.UUID:
        if access.user_id is None:  # pragma: no cover - 管理员接口总是带当前用户
            raise ApiError(401, "UNAUTHENTICATED", "未登录")
        return access.user_id

    def _require_client(self) -> OpenIMClient:
        if self.client is None:
            raise im_unavailable()
        return self.client

    # ---------------------------------------------------------------- 成员校验

    async def _instances(
        self, access: WorkspaceAccess, instance_ids: Sequence[uuid.UUID]
    ) -> list[AgentInstance]:
        """本工作空间、IM 已就绪、没有停用的实例；不存在 404，未就绪 409。"""
        if not instance_ids:
            return []
        found = {
            i.id: i
            for i in await self.db.scalars(
                select(AgentInstance).where(
                    AgentInstance.id.in_(instance_ids),
                    AgentInstance.workspace_id == access.workspace.id,
                )
            )
        }
        missing = [str(i) for i in instance_ids if i not in found]
        if missing:
            raise ApiError(404, "NOT_FOUND", "实例不存在", details={"instance_ids": missing})
        not_ready = [str(i.id) for i in found.values() if not i.im_ready or i.status == "disabled"]
        if not_ready:
            raise ApiError(
                409,
                "IM_NOT_READY",
                "有实例的 IM 账号还没就绪或已停用",
                details={"instance_ids": not_ready},
            )
        return [found[i] for i in dict.fromkeys(instance_ids)]

    async def _admins(self, access: WorkspaceAccess, user_ids: Sequence[uuid.UUID]) -> list[User]:
        """本工作空间状态正常的 owner / admin；不是的一律 404（不区分不存在与角色不够）。"""
        if not user_ids:
            return []
        found = {
            u.id: u
            for u in await self.db.scalars(
                select(User)
                .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                .where(
                    User.id.in_(user_ids),
                    User.status == "active",
                    WorkspaceMember.workspace_id == access.workspace.id,
                    WorkspaceMember.role.in_(IM_ROLES),
                )
            )
        }
        missing = [str(u) for u in user_ids if u not in found]
        if missing:
            raise ApiError(
                404, "NOT_FOUND", "成员不存在或不是管理员", details={"user_ids": missing}
            )
        return [found[u] for u in dict.fromkeys(user_ids)]

    async def _group(self, access: WorkspaceAccess, group_id: str) -> ImGroup:
        group = await self.db.scalar(
            select(ImGroup).where(
                ImGroup.group_id == group_id, ImGroup.workspace_id == access.workspace.id
            )
        )
        if group is None:
            raise _not_found("群")
        return group

    async def _live_group(self, access: WorkspaceAccess, group_id: str) -> ImGroup:
        group = await self._group(access, group_id)
        if group.dismissed_at is not None:
            raise ApiError(409, "CONFLICT", "群已解散")
        return group

    async def _add_conversations(self, group: ImGroup, user_ids: Sequence[uuid.UUID]) -> None:
        for user_id in user_ids:
            await self.db.execute(
                pg_insert(ImConversation)
                .values(
                    workspace_id=group.workspace_id,
                    owner_user_id=user_id,
                    conversation_id=group_conversation_id(group.group_id),
                    kind="group",
                    group_pk=group.id,
                )
                .on_conflict_do_nothing(index_elements=["owner_user_id", "conversation_id"])
            )

    # ---------------------------------------------------------------- 输出

    async def _paused(self, group: ImGroup) -> bool:
        if self.redis is None:
            return False
        streak = int(await self.redis.get(STREAK_KEY.format(group_id=group.group_id)) or 0)
        return streak >= group.max_agent_streak

    async def _policy_out(self, group: ImGroup) -> ImGroupPolicyOut:
        return ImGroupPolicyOut(
            relay_enabled=group.relay_enabled,
            max_agent_streak=group.max_agent_streak,
            max_per_minute=group.max_per_minute,
            paused=await self._paused(group),
            version=group.version,
        )

    async def group_out(self, group: ImGroup) -> ImGroupOut:
        members = (
            await self.db.scalars(
                select(ImGroupMember)
                .where(ImGroupMember.group_pk == group.id)
                .order_by(ImGroupMember.joined_at, ImGroupMember.im_user_id)
            )
        ).all()
        instance_ids = [m.instance_id for m in members if m.instance_id]
        user_ids = [m.user_id for m in members if m.user_id]
        instances = {
            i.id: i
            for i in await self.db.scalars(
                select(AgentInstance).where(AgentInstance.id.in_(instance_ids))
            )
        }
        users = {u.id: u for u in await self.db.scalars(select(User).where(User.id.in_(user_ids)))}
        out = []
        for member in members:
            instance = instances.get(member.instance_id) if member.instance_id else None
            user = users.get(member.user_id) if member.user_id else None
            if instance is not None:
                nickname = instance.im_nickname or instance.name
            elif user is not None:
                nickname = user.display_name
            else:
                nickname = member.im_user_id
            out.append(
                ImGroupMemberOut(
                    im_user_id=member.im_user_id,
                    instance_id=member.instance_id,
                    user_id=member.user_id,
                    nickname=nickname,
                    role="owner" if member.role == "owner" else "member",
                    online=(instance.status == "online") if instance is not None else None,
                )
            )
        return ImGroupOut(
            group_id=group.group_id,
            conversation_id=group_conversation_id(group.group_id),
            name=group.name,
            announcement=group.announcement,
            owner_im_id=admin_im_user_id(group.owner_user_id) if group.owner_user_id else "",
            members=out,
            policy=await self._policy_out(group),
            dismissed=group.dismissed_at is not None,
            created_at=group.created_at,
        )

    # ---------------------------------------------------------------- 群

    async def create_group(
        self, access: WorkspaceAccess, body: ImGroupCreate, key: uuid.UUID, *, path: str
    ) -> tuple[int, dict[str, Any], bool]:
        """返回 (状态码, 响应体, 是否重放)。"""
        user_id = self._user_id(access)
        instances = await self._instances(access, body.instance_ids)
        others = [u for u in await self._admins(access, body.user_ids) if u.id != user_id]
        begun = await idempotency.begin(
            self.db,
            workspace_id=access.workspace.id,
            actor_id=user_id,
            key=key,
            request_fingerprint=idempotency.fingerprint(
                access.workspace.id, "POST", path, body.model_dump(mode="json")
            ),
            context={"operation": "im.group.create"},
        )
        if begun.state == "replay":
            status_code, stored = idempotency.replay(begun.record)
            return status_code, stored, True
        record_id = begun.record.id
        group_id = group_id_for(key)
        try:
            client = self._require_client()
            accounts = ImAccounts(self.db, client)
            me = await self.db.get(User, user_id)
            if me is None:  # pragma: no cover
                raise _not_found("用户")
            owner_im = await accounts.ensure_user(me)
            member_ims = [i.im_user_id for i in instances]
            member_ims += [await accounts.ensure_user(u) for u in others]
            member_ims.append(await accounts.ensure_notice_user())
            try:
                await client.create_group(
                    owner=owner_im,
                    name=body.name,
                    members=member_ims,
                    group_id=group_id,
                    notification=body.announcement,
                )
            except ConnectorError as error:
                # 1202：同一个群 ID 已经存在——上次的请求已经建好了群
                if error.kind != "CONFLICT":
                    raise
        except ConnectorError as error:
            record = await self.db.get(type(begun.record), record_id, with_for_update=True)
            # 群 ID 由键推出，重发同一个键不会多建群：除了结果未知，都删掉记录允许直接重试
            if error.not_sent or error.kind != "TIMEOUT_UNKNOWN":
                if record is not None:
                    await idempotency.discard(self.db, record)
                await self.db.commit()
                raise im_unavailable() from error
            if record is not None:
                idempotency.mark_unknown(record)
            await self.db.commit()
            raise ApiError(
                504,
                "RESULT_UNKNOWN",
                "建群结果未知：请用同一个 Idempotency-Key 重新发送",
                details={"retry_with_same_key": True},
            ) from error
        except ApiError:
            record = await self.db.get(type(begun.record), record_id, with_for_update=True)
            if record is not None:
                await idempotency.discard(self.db, record)
            await self.db.commit()
            raise
        group = await self.db.scalar(select(ImGroup).where(ImGroup.group_id == group_id))
        if group is None:
            group = ImGroup(
                workspace_id=access.workspace.id,
                group_id=group_id,
                name=body.name,
                announcement=body.announcement,
                owner_user_id=user_id,
                relay_enabled=body.relay_enabled,
            )
            self.db.add(group)
            await self.db.flush()
            self.db.add(
                ImGroupMember(group_pk=group.id, im_user_id=owner_im, user_id=user_id, role="owner")
            )
            for instance in instances:
                self.db.add(
                    ImGroupMember(
                        group_pk=group.id, im_user_id=instance.im_user_id, instance_id=instance.id
                    )
                )
            for user in others:
                self.db.add(
                    ImGroupMember(
                        group_pk=group.id, im_user_id=admin_im_user_id(user.id), user_id=user.id
                    )
                )
            await self.db.flush()
            await self._add_conversations(group, [user_id, *[u.id for u in others]])
        await self.audit.record(
            "im.group.created",
            workspace_ids=[access.workspace.id],
            actor_id=user_id,
            resource_type="im_group",
            resource_id=group_id,
            metadata={"instances": len(instances), "users": len(others)},
        )
        out = (await self.group_out(group)).model_dump(mode="json")
        record = await self.db.get(type(begun.record), record_id, with_for_update=True)
        if record is not None:
            idempotency.succeed(record, 201, out)
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()
        return 201, out, False

    async def get_group(self, access: WorkspaceAccess, group_id: str) -> ImGroupOut:
        return await self.group_out(await self._group(access, group_id))

    async def update_group(
        self, access: WorkspaceAccess, group_id: str, body: ImGroupUpdate
    ) -> ImGroupOut:
        group = await self._live_group(access, group_id)
        try:
            await self._require_client().set_group_info(
                group.group_id, name=body.name, notification=body.announcement
            )
        except ConnectorError as error:
            raise im_unavailable() from error
        if body.name is not None:
            group.name = body.name
        if body.announcement is not None:
            group.announcement = body.announcement
        group.version += 1
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()
        await self.db.refresh(group)
        return await self.group_out(group)

    async def dismiss_group(self, access: WorkspaceAccess, group_id: str) -> None:
        group = await self._group(access, group_id)
        if group.owner_user_id != self._user_id(access):
            raise ApiError(403, "FORBIDDEN", "只有群主能解散群")
        if group.dismissed_at is not None:
            return
        try:
            await self._require_client().dismiss_group(group.group_id)
        except ConnectorError as error:
            if error.kind != "NOT_FOUND":  # 已经解散：照常记下
                raise im_unavailable() from error
        group.dismissed_at = datetime.now(UTC)
        group.version += 1
        await self.audit.record(
            "im.group.dismissed",
            workspace_ids=[access.workspace.id],
            actor_id=self._user_id(access),
            resource_type="im_group",
            resource_id=group.group_id,
        )
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()

    async def add_members(
        self, access: WorkspaceAccess, group_id: str, body: ImGroupMembersAdd
    ) -> ImGroupOut:
        group = await self._live_group(access, group_id)
        instances = await self._instances(access, body.instance_ids)
        users = await self._admins(access, body.user_ids)
        existing = set(
            (
                await self.db.scalars(
                    select(ImGroupMember.im_user_id).where(ImGroupMember.group_pk == group.id)
                )
            ).all()
        )
        new_instances = [i for i in instances if i.im_user_id not in existing]
        new_users = [u for u in users if admin_im_user_id(u.id) not in existing]
        if len(existing) + len(new_instances) + len(new_users) > 71:  # 群主 + 50 + 20
            raise _invalid(["body"], "群成员超过上限")
        if new_instances or new_users:
            client = self._require_client()
            try:
                accounts = ImAccounts(self.db, client)
                ims = [i.im_user_id for i in new_instances]
                ims += [await accounts.ensure_user(u) for u in new_users]
                await client.invite_to_group(group.group_id, ims)
            except ConnectorError as error:
                raise im_unavailable() from error
            for instance in new_instances:
                self.db.add(
                    ImGroupMember(
                        group_pk=group.id, im_user_id=instance.im_user_id, instance_id=instance.id
                    )
                )
            for user in new_users:
                self.db.add(
                    ImGroupMember(
                        group_pk=group.id, im_user_id=admin_im_user_id(user.id), user_id=user.id
                    )
                )
            await self.db.flush()
            await self._add_conversations(group, [u.id for u in new_users])
            events.mark(self.db, access.workspace.id, "im")
            await self.db.commit()
        return await self.group_out(group)

    async def remove_member(self, access: WorkspaceAccess, group_id: str, im_user_id: str) -> None:
        group = await self._live_group(access, group_id)
        member = await self.db.scalar(
            select(ImGroupMember).where(
                ImGroupMember.group_pk == group.id, ImGroupMember.im_user_id == im_user_id
            )
        )
        if member is None:
            raise _not_found("成员")
        if member.role == "owner" or member.user_id == group.owner_user_id:
            raise _invalid(["path", "im_user_id"], "不能移出群主")
        try:
            await self._require_client().kick_from_group(group.group_id, [im_user_id])
        except ConnectorError as error:
            if error.kind != "NOT_FOUND":  # 已经不在群里：照常删除本地记录
                raise im_unavailable() from error
        await self.db.delete(member)
        if member.user_id is not None:
            # 被移出的管理员不再有这个群的会话
            await self.db.execute(
                delete(ImConversation).where(
                    ImConversation.owner_user_id == member.user_id,
                    ImConversation.conversation_id == group_conversation_id(group.group_id),
                )
            )
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()

    async def update_policy(
        self, access: WorkspaceAccess, group_id: str, body: ImGroupPolicyUpdate
    ) -> ImGroupPolicyOut:
        group = await self._group(access, group_id)
        if group.version != body.version:
            raise _version_conflict(group.version)
        group.relay_enabled = body.relay_enabled
        group.max_agent_streak = body.max_agent_streak
        group.max_per_minute = body.max_per_minute
        group.version += 1
        await self.audit.record(
            "im.group.policy_updated",
            workspace_ids=[access.workspace.id],
            actor_id=self._user_id(access),
            resource_type="im_group",
            resource_id=group.group_id,
            metadata=body.model_dump(exclude={"version"}),
        )
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()
        await self.db.refresh(group)
        return await self._policy_out(group)

    # ---------------------------------------------------------------- 通讯录与分组

    async def _contact_group(
        self, access: WorkspaceAccess, contact_group_id: uuid.UUID
    ) -> ImContactGroup:
        group = await self.db.scalar(
            select(ImContactGroup).where(
                ImContactGroup.id == contact_group_id,
                ImContactGroup.workspace_id == access.workspace.id,
            )
        )
        if group is None:
            raise _not_found("分组")
        return group

    async def contact_group_instances(
        self, access: WorkspaceAccess, contact_group_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """分组里的实例（会话列表按分组筛选用）；分组不存在 404。"""
        group = await self._contact_group(access, contact_group_id)
        return list(
            (
                await self.db.scalars(
                    select(ImContactGroupMember.instance_id).where(
                        ImContactGroupMember.contact_group_id == group.id
                    )
                )
            ).all()
        )

    async def list_contacts(
        self, access: WorkspaceAccess, *, contact_group_id: uuid.UUID | None, q: str | None
    ) -> ImContactList:
        me = admin_im_user_id(self._user_id(access))
        stmt = select(AgentInstance).where(AgentInstance.workspace_id == access.workspace.id)
        if contact_group_id is not None:
            stmt = stmt.where(
                AgentInstance.id.in_(await self.contact_group_instances(access, contact_group_id))
            )
        if q:
            needle = q.lower()
            stmt = stmt.where(
                (func.strpos(func.lower(AgentInstance.name), needle) > 0)
                | (func.strpos(func.lower(AgentInstance.im_nickname), needle) > 0)
            )
        instances = (
            await self.db.scalars(stmt.order_by(AgentInstance.name, AgentInstance.id))
        ).all()
        memberships: dict[uuid.UUID, list[uuid.UUID]] = {i.id: [] for i in instances}
        for group_pk, instance_id in await self.db.execute(
            select(ImContactGroupMember.contact_group_id, ImContactGroupMember.instance_id).where(
                ImContactGroupMember.instance_id.in_(list(memberships))
            )
        ):
            memberships[instance_id].append(group_pk)
        return ImContactList(
            items=[
                ImContactOut(
                    instance_id=i.id,
                    im_user_id=i.im_user_id,
                    nickname=i.im_nickname or i.name,
                    status=i.status,
                    contact_group_ids=sorted(memberships[i.id]),
                    conversation_id=single_conversation_id(me, i.im_user_id),
                )
                for i in instances
            ]
        )

    async def _contact_group_out(self, group: ImContactGroup) -> ImContactGroupOut:
        ids = (
            await self.db.scalars(
                select(ImContactGroupMember.instance_id)
                .where(ImContactGroupMember.contact_group_id == group.id)
                .order_by(ImContactGroupMember.instance_id)
            )
        ).all()
        return ImContactGroupOut(
            id=group.id,
            name=group.name,
            sort_order=group.sort_order,
            instance_ids=list(ids),
            version=group.version,
        )

    async def list_contact_groups(self, access: WorkspaceAccess) -> ImContactGroupList:
        groups = (
            await self.db.scalars(
                select(ImContactGroup)
                .where(ImContactGroup.workspace_id == access.workspace.id)
                .order_by(ImContactGroup.sort_order, ImContactGroup.name)
            )
        ).all()
        return ImContactGroupList(items=[await self._contact_group_out(g) for g in groups])

    @staticmethod
    def _name_taken() -> ApiError:
        return ApiError(409, "CONTACT_GROUP_EXISTS", "已有同名的分组")

    async def create_contact_group(
        self, access: WorkspaceAccess, body: ImContactGroupCreate
    ) -> ImContactGroupOut:
        group = ImContactGroup(
            workspace_id=access.workspace.id, name=body.name, sort_order=body.sort_order
        )
        self.db.add(group)
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            raise self._name_taken() from None
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()
        await self.db.refresh(group)
        return await self._contact_group_out(group)

    async def update_contact_group(
        self, access: WorkspaceAccess, contact_group_id: uuid.UUID, body: ImContactGroupUpdate
    ) -> ImContactGroupOut:
        group = await self._contact_group(access, contact_group_id)
        if group.version != body.version:
            raise _version_conflict(group.version)
        if body.name is not None:
            group.name = body.name
        if body.sort_order is not None:
            group.sort_order = body.sort_order
        group.version += 1
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            raise self._name_taken() from None
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()
        await self.db.refresh(group)
        return await self._contact_group_out(group)

    async def delete_contact_group(
        self, access: WorkspaceAccess, contact_group_id: uuid.UUID, version: int
    ) -> None:
        group = await self._contact_group(access, contact_group_id)
        if group.version != version:
            raise _version_conflict(group.version)
        await self.db.delete(group)
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()

    async def replace_contact_group_members(
        self,
        access: WorkspaceAccess,
        contact_group_id: uuid.UUID,
        body: ImContactGroupMembersReplace,
    ) -> ImContactGroupOut:
        group = await self._contact_group(access, contact_group_id)
        if group.version != body.version:
            raise _version_conflict(group.version)
        wanted = list(dict.fromkeys(body.instance_ids))
        found = set(
            (
                await self.db.scalars(
                    select(AgentInstance.id).where(
                        AgentInstance.id.in_(wanted),
                        AgentInstance.workspace_id == access.workspace.id,
                    )
                )
            ).all()
        )
        missing = [str(i) for i in wanted if i not in found]
        if missing:
            raise ApiError(404, "NOT_FOUND", "实例不存在", details={"instance_ids": missing})
        await self.db.execute(
            delete(ImContactGroupMember).where(ImContactGroupMember.contact_group_id == group.id)
        )
        for instance_id in wanted:
            self.db.add(ImContactGroupMember(contact_group_id=group.id, instance_id=instance_id))
        group.version += 1
        events.mark(self.db, access.workspace.id, "im")
        await self.db.commit()
        await self.db.refresh(group)
        return await self._contact_group_out(group)


async def send_relay_paused_notice(client: OpenIMClient, group_id: str) -> None:
    """群里由 ws_notice 提示“自动接力已暂停”（契约第 10 节）。失败只记日志。"""
    try:
        await client.send_text(send_id=NOTICE_USER_ID, group_id=group_id, text=RELAY_PAUSED_TEXT)
    except ConnectorError as error:
        logger.warning(
            "relay paused notice failed", extra={"group_id": group_id, "error_kind": error.kind}
        )
