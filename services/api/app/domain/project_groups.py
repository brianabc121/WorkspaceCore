"""项目成员（智能体）与项目群（T-0110，docs/v2/AGENT_OPS_PLAN.md 第 2.1、2.2 节）。

- **项目成员**沿用知识库的 project_agents：加入项目 = 能读写项目知识库、收到项目简报，
  有项目群时也在群里。成员最多 50 个（OpenIM 群里实例的上限，见 app/domain/im_groups.py）。
- **项目群**是普通的 IM 群（im_groups），多了 project_id：
  - 群名是项目名，群公告是项目说明的前 200 字，归档后公告前面标注“（项目已归档）”；
  - 群主是 projects.im_group_owner_id（新建项目的人，或点“建项目群”的人）；
    那个人不在了或不再是 owner / admin 时，改为工作空间的 owner；
  - 智能体成员跟随项目成员：加入时邀请，移出项目时移出群；停用的实例不移出，也不新邀请；
    IM 还没就绪的实例等就绪后再邀请。在 IM 里直接拉进项目群的智能体，下次同步时会被移出
    （项目群的成员以项目页为准）。
- **建群与同步**：项目的 im_group_status 为 pending 时要建群或同步，Worker 每分钟试一次，
  写接口提交后也立即试一次。
  - 群 ID 由（项目、第几个群）推出：重试时 OpenIM 返回 1202，就当作上次已经建好；
    群被解散后再建，会是新的群 ID。
  - 同步时锁住项目这一行（SKIP LOCKED），同一个项目不会同时被两处建群。
  - OpenIM 不可用时状态保持 pending，im_group_error 记下错误类别（不记第三方返回的原文）。
- **删除项目**时先解散群；解散失败只记日志，群留在 IM 里，由群主处理。
"""

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.audit import AuditTrail
from app.domain.im import IM_ROLES, ImAccounts
from app.domain.im_groups import group_id_for
from app.integrations.base import ConnectorError
from app.integrations.openim import OpenIMClient, group_conversation_id
from app.models import (
    AgentInstance,
    ImConversation,
    ImGroup,
    ImGroupMember,
    Project,
    ProjectAgent,
    User,
    WorkspaceMember,
)

logger = logging.getLogger("app.project_groups")

MAX_PROJECT_AGENTS = 50
NAME_MAX = 64
ANNOUNCEMENT_MAX = 200
ARCHIVED_MARK = "（项目已归档）"
SYNC_BATCH = 20
# 群 ID 的命名空间：uuid5(命名空间, "<项目 ID>:<第几个群>") → group_id_for
_GROUP_NAMESPACE = uuid.UUID("6f1d8a52-0c7b-4f0e-9a43-2a1f3b7d5e10")


def project_group_id(project_id: uuid.UUID, generation: int) -> str:
    return group_id_for(uuid.uuid5(_GROUP_NAMESPACE, f"{project_id}:{generation}"))


def group_name(project: Project) -> str:
    return project.title.strip()[:NAME_MAX] or "项目群"


def group_announcement(project: Project) -> str:
    text = project.description.strip()[:ANNOUNCEMENT_MAX]
    return f"{ARCHIVED_MARK}{text}" if project.archived else text


@dataclass(frozen=True)
class Member:
    instance: AgentInstance
    access: str


async def members(
    db: AsyncSession, project_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[Member]]:
    """各项目的智能体成员（按名字排序）。"""
    ids = list(dict.fromkeys(project_ids))
    found: dict[uuid.UUID, list[Member]] = {pid: [] for pid in ids}
    if not ids:
        return found
    rows = await db.execute(
        select(ProjectAgent.project_id, ProjectAgent.access, AgentInstance)
        .join(AgentInstance, AgentInstance.id == ProjectAgent.instance_id)
        .where(ProjectAgent.project_id.in_(ids))
        .order_by(AgentInstance.name, AgentInstance.id)
    )
    for project_id, access, instance in rows.all():
        found[project_id].append(Member(instance, access))
    return found


async def live_groups(
    db: AsyncSession, project_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ImGroup]:
    ids = list(dict.fromkeys(project_ids))
    if not ids:
        return {}
    rows = await db.scalars(
        select(ImGroup).where(ImGroup.project_id.in_(ids), ImGroup.dismissed_at.is_(None))
    )
    return {g.project_id: g for g in rows.all() if g.project_id is not None}


async def group_instance_ids(
    db: AsyncSession, group_pks: Iterable[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    pks = list(dict.fromkeys(group_pks))
    found: dict[uuid.UUID, set[uuid.UUID]] = {pk: set() for pk in pks}
    if not pks:
        return found
    rows = await db.execute(
        select(ImGroupMember.group_pk, ImGroupMember.instance_id).where(
            ImGroupMember.group_pk.in_(pks), ImGroupMember.instance_id.is_not(None)
        )
    )
    for pk, instance_id in rows.all():
        if instance_id is not None:
            found[pk].add(instance_id)
    return found


def mark_dirty(project: Project) -> None:
    """项目名、说明、归档或成员变了：已有项目群的要同步（没有项目群的不变）。"""
    if project.im_group_status == "ready":
        project.im_group_status = "pending"


def request_group(project: Project, owner_user_id: uuid.UUID) -> None:
    """要一个项目群（新建项目时勾选，或旧项目补建）。已经在建或建好的只是重新同步。"""
    if project.im_group_status == "none":
        project.im_group_owner_id = owner_user_id
    project.im_group_status = "pending"
    project.im_group_error = None


async def add_agents(
    db: AsyncSession,
    project: Project,
    instance_ids: Sequence[uuid.UUID],
    *,
    added_by: uuid.UUID | None,
) -> list[uuid.UUID]:
    """让这些实例加入项目（项目知识库读写）；已经是成员的保留原来的权限。调用方提交。

    实例必须属于项目所在的工作空间（复合外键也会拦住）。返回新加入的实例。
    """
    wanted = list(dict.fromkeys(instance_ids))
    if not wanted:
        return []
    existing = set(
        (
            await db.scalars(
                select(ProjectAgent.instance_id).where(ProjectAgent.project_id == project.id)
            )
        ).all()
    )
    added = [i for i in wanted if i not in existing]
    for instance_id in added:
        await db.execute(
            pg_insert(ProjectAgent)
            .values(
                workspace_id=project.workspace_id,
                project_id=project.id,
                instance_id=instance_id,
                access="read_write",
                added_by=added_by,
            )
            .on_conflict_do_nothing()
        )
    if added:
        mark_dirty(project)
    return added


async def remove_agent(db: AsyncSession, project: Project, instance_id: uuid.UUID) -> bool:
    result = await db.execute(
        delete(ProjectAgent).where(
            ProjectAgent.project_id == project.id, ProjectAgent.instance_id == instance_id
        )
    )
    removed = bool(getattr(result, "rowcount", 0))
    if removed:
        mark_dirty(project)
    return removed


async def member_count(db: AsyncSession, project_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(ProjectAgent)
            .where(ProjectAgent.project_id == project_id)
        )
        or 0
    )


class _Problem(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProjectGroupSync:
    """建项目群、同步成员与群信息。每次处理一个项目，自己提交。"""

    def __init__(self, db: AsyncSession, settings: Settings, client: OpenIMClient | None) -> None:
        self.db = db
        self.settings = settings
        self.client = client
        self.audit = AuditTrail(db, settings)

    async def sync(self, project_id: uuid.UUID) -> None:
        """项目是 pending 时建群或同步；被别处锁住（正在同步）时跳过。

        不用 rollback 结束事务：它会让调用方会话里已加载的对象（如当前工作空间）过期；
        失败时只回滚保存点里的写入。
        """
        project = await self.db.scalar(
            select(Project).where(Project.id == project_id).with_for_update(skip_locked=True)
        )
        if project is None or project.im_group_status != "pending":
            await self.db.commit()
            return
        if self.client is None:
            project.im_group_error = "not_configured"
            await self.db.commit()
            return
        try:
            async with self.db.begin_nested():
                group = (await live_groups(self.db, [project.id])).get(project.id)
                if group is None:
                    await self._create(project, self.client)
                else:
                    await self._sync(project, group, self.client)
        except ConnectorError as error:
            project.im_group_error = error.kind.lower()
            await self.db.commit()
            logger.warning(
                "project group sync failed",
                extra={"project_id": str(project_id), "error_kind": error.kind},
            )
            return
        except _Problem as problem:
            project.im_group_error = problem.code
            await self.db.commit()
            return
        project.im_group_status = "ready"
        project.im_group_error = None
        await self.db.commit()

    async def _owner(self, project: Project) -> User:
        """群主：记下的那个人仍是本工作空间正常的 owner / admin 就用他，否则用 owner。"""
        query = (
            select(User)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .where(
                WorkspaceMember.workspace_id == project.workspace_id,
                WorkspaceMember.role.in_(IM_ROLES),
                User.status == "active",
            )
        )
        if project.im_group_owner_id is not None:
            user: User | None = await self.db.scalar(
                query.where(User.id == project.im_group_owner_id)
            )
            if user is not None:
                return user
        user = await self.db.scalar(
            query.where(WorkspaceMember.role == "owner").order_by(User.created_at, User.id)
        )
        if user is None:
            raise _Problem("owner_missing")
        return user

    async def _wanted(self, project: Project) -> tuple[list[AgentInstance], set[uuid.UUID]]:
        """(要在群里的实例, 全部项目成员的 ID)：IM 就绪、没有停用的才邀请。"""
        found = (await members(self.db, [project.id]))[project.id]
        ready = [
            m.instance for m in found if m.instance.im_ready and m.instance.status != "disabled"
        ]
        return ready, {m.instance.id for m in found}

    async def _create(self, project: Project, client: OpenIMClient) -> None:
        owner = await self._owner(project)
        generation = int(
            await self.db.scalar(
                select(func.count()).select_from(ImGroup).where(ImGroup.project_id == project.id)
            )
            or 0
        )
        group_id = project_group_id(project.id, generation)
        if await self.db.scalar(select(exists().where(ImGroup.group_id == group_id))):
            # 极少见：同一个群 ID 已经登记（解散后外键置空的旧群）；换下一个
            group_id = project_group_id(project.id, generation + 1)
        accounts = ImAccounts(self.db, client)
        owner_im = await accounts.ensure_user(owner)
        instances, _ = await self._wanted(project)
        member_ims = [i.im_user_id for i in instances]
        member_ims.append(await accounts.ensure_notice_user())
        try:
            await client.create_group(
                owner=owner_im,
                name=group_name(project),
                members=member_ims,
                group_id=group_id,
                notification=group_announcement(project),
            )
        except ConnectorError as error:
            if error.kind != "CONFLICT":  # 1202：上次已经建好
                raise
        group = ImGroup(
            workspace_id=project.workspace_id,
            group_id=group_id,
            name=group_name(project),
            announcement=group_announcement(project),
            owner_user_id=owner.id,
            project_id=project.id,
        )
        self.db.add(group)
        await self.db.flush()
        self.db.add(
            ImGroupMember(group_pk=group.id, im_user_id=owner_im, user_id=owner.id, role="owner")
        )
        for instance in instances:
            self.db.add(
                ImGroupMember(
                    group_pk=group.id, im_user_id=instance.im_user_id, instance_id=instance.id
                )
            )
        await self.db.flush()
        await self.db.execute(
            pg_insert(ImConversation)
            .values(
                workspace_id=project.workspace_id,
                owner_user_id=owner.id,
                conversation_id=group_conversation_id(group_id),
                kind="group",
                group_pk=group.id,
            )
            .on_conflict_do_nothing(index_elements=["owner_user_id", "conversation_id"])
        )
        await self.audit.record(
            "im.group.created",
            workspace_ids=[project.workspace_id],
            actor_id=owner.id,
            resource_type="im_group",
            resource_id=group_id,
            metadata={"project_id": str(project.id), "instances": len(instances), "users": 0},
        )

    async def _sync(self, project: Project, group: ImGroup, client: OpenIMClient) -> None:
        instances, all_ids = await self._wanted(project)
        current = (await group_instance_ids(self.db, [group.id]))[group.id]
        invite = [i for i in instances if i.id not in current]
        kick_ids = [i for i in current if i not in all_ids]
        if invite:
            await client.invite_to_group(group.group_id, [i.im_user_id for i in invite])
            for instance in invite:
                self.db.add(
                    ImGroupMember(
                        group_pk=group.id, im_user_id=instance.im_user_id, instance_id=instance.id
                    )
                )
        if kick_ids:
            rows = (
                await self.db.scalars(
                    select(ImGroupMember).where(
                        ImGroupMember.group_pk == group.id, ImGroupMember.instance_id.in_(kick_ids)
                    )
                )
            ).all()
            try:
                await client.kick_from_group(group.group_id, [r.im_user_id for r in rows])
            except ConnectorError as error:
                if error.kind != "NOT_FOUND":  # 已经不在群里
                    raise
            for row in rows:
                await self.db.delete(row)
        name, announcement = group_name(project), group_announcement(project)
        if (name, announcement) != (group.name, group.announcement):
            await client.set_group_info(
                group.group_id,
                name=name if name != group.name else None,
                notification=announcement if announcement != group.announcement else None,
            )
            group.name, group.announcement = name, announcement
            group.version += 1
        if invite or kick_ids:
            group.version += 1

    async def due(self) -> list[uuid.UUID]:
        """要处理的项目：pending 的，以及项目群里缺了已就绪成员的（实例后来才就绪）。"""
        pending = (
            await self.db.scalars(
                select(Project.id)
                .where(Project.im_group_status == "pending")
                .order_by(Project.updated_at)
                .limit(SYNC_BATCH)
            )
        ).all()
        missing = (
            await self.db.scalars(
                select(Project.id)
                .join(ImGroup, (ImGroup.project_id == Project.id) & ImGroup.dismissed_at.is_(None))
                .join(ProjectAgent, ProjectAgent.project_id == Project.id)
                .join(AgentInstance, AgentInstance.id == ProjectAgent.instance_id)
                .where(
                    Project.im_group_status == "ready",
                    AgentInstance.im_ready.is_(True),
                    AgentInstance.status != "disabled",
                    ~exists().where(
                        ImGroupMember.group_pk == ImGroup.id,
                        ImGroupMember.instance_id == ProjectAgent.instance_id,
                    ),
                )
                .distinct()
                .limit(SYNC_BATCH)
            )
        ).all()
        if missing:
            for project in (
                await self.db.scalars(select(Project).where(Project.id.in_(missing)))
            ).all():
                project.im_group_status = "pending"
        await self.db.commit()
        return list(dict.fromkeys([*pending, *missing]))

    async def run_due(self) -> int:
        """Worker 每分钟：处理到期的项目，返回成功建好或同步完的个数。"""
        done = 0
        for project_id in await self.due():
            await self.sync(project_id)
            status = await self.db.scalar(
                select(Project.im_group_status).where(Project.id == project_id)
            )
            await self.db.commit()
            if status == "ready":
                done += 1
        return done


async def dismiss_for_project(
    db: AsyncSession, client: OpenIMClient | None, project: Project
) -> None:
    """删除项目前解散它的项目群；失败只记日志（群留在 IM 里，由群主处理）。调用方提交。"""
    group = (await live_groups(db, [project.id])).get(project.id)
    if group is None:
        return
    if client is None:
        logger.warning("project group left behind", extra={"group_id": group.group_id})
        return
    try:
        await client.dismiss_group(group.group_id)
    except ConnectorError as error:
        if error.kind != "NOT_FOUND":  # 已经解散：照常记下
            logger.warning(
                "project group dismiss failed",
                extra={"group_id": group.group_id, "error_kind": error.kind},
            )
            return
    group.dismissed_at = datetime.now(UTC)
    group.version += 1


def group_outs(project: Project, group: ImGroup | None) -> dict[str, str | None]:
    """ProjectOut.im_group 的内容。"""
    return {
        "status": project.im_group_status,
        "group_id": group.group_id if group is not None else None,
        "conversation_id": group_conversation_id(group.group_id) if group is not None else None,
        "error": project.im_group_error if project.im_group_status == "pending" else None,
    }
