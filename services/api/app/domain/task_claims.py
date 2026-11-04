"""自由接取的公共部分（T-0111，docs/v2/AGENT_OPS_PLAN.md 第 3 节）：判断、动态、消息。

- **能领取**：claim_mode 为 open、没有执行者、没有完成；范围是 project 时只限项目成员。
- **空闲**：实例没有正在执行的段（一个实例同一时间只做一个任务，D4）。
- **通知空闲的智能体**：用设为自由接取的人（claim_opened_by）的 IM 账号，
  给符合条件的智能体各发一条单聊：
  在线、IM 就绪、scope 为 read_write、空闲、在范围内。接入包的 IM 渠道只放行管理员，
  所以和“交给它执行”一样用管理员的账号。消息的幂等键由（任务、实例、任务版本）推出：
  同一次设置重复通知不会多发，放弃后（版本变了）再通知是新的一条。
- **项目群通知**由 ws_notice 发，智能体不会把它当成指令（接入包只放行管理员）。
- 消息都是尽力而为：发不出去只记日志，不影响领取、完成、打回本身。

这里不依赖 app/domain/ai_tools.py（任务服务也要用，避免循环引用）。
"""

import logging
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import project_groups
from app.domain.im import IM_ROLES, NOTICE_USER_ID
from app.domain.im_service import ImService
from app.domain.policy import WorkspaceAccess
from app.integrations.base import ConnectorError
from app.integrations.openim import OpenIMClient
from app.models import (
    AgentInstance,
    Project,
    ProjectAgent,
    Task,
    TaskEvent,
    TaskRun,
    User,
    Workspace,
    WorkspaceMember,
)
from app.schemas.im import ImMessageSend
from app.schemas.workspaces import Role

logger = logging.getLogger("app.task_claims")

ANNOUNCE_LIMIT = 50
_MESSAGE_NAMESPACE = uuid.UUID("2c4b7e1a-93f0-4d6e-8b15-6a0f9d3c2e71")


def message_key(*parts: object) -> uuid.UUID:
    """消息的幂等键（IM 接口按“发送人 + 键”去重）。"""
    return uuid.uuid5(_MESSAGE_NAMESPACE, ":".join(str(p) for p in parts))


def claimable(task: Task) -> bool:
    return (
        task.claim_mode == "open"
        and not task.done
        and task.assignee_user_id is None
        and task.assignee_instance_id is None
    )


async def busy(db: AsyncSession, instance_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(exists().where(TaskRun.instance_id == instance_id, TaskRun.ended_at.is_(None)))
    )
    return bool(found)


async def busy_instances(db: AsyncSession, instance_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not instance_ids:
        return set()
    rows = await db.scalars(
        select(TaskRun.instance_id).where(
            TaskRun.instance_id.in_(instance_ids), TaskRun.ended_at.is_(None)
        )
    )
    return {i for i in rows.all() if i is not None}


async def in_scope(db: AsyncSession, task: Task, instance_id: uuid.UUID) -> bool:
    if task.claim_scope == "all":
        return True
    return await db.get(ProjectAgent, (task.project_id, instance_id)) is not None


def event(
    task: Task,
    kind: str,
    *,
    actor_kind: str,
    name: str,
    user_id: uuid.UUID | None = None,
    instance_id: uuid.UUID | None = None,
    note: str = "",
) -> TaskEvent:
    return TaskEvent(
        workspace_id=task.workspace_id,
        task_id=task.id,
        kind=kind,
        actor_kind=actor_kind,
        user_id=user_id,
        instance_id=instance_id,
        actor_name=name,
        note=note,
        created_at=datetime.now(UTC),
    )


def _due(task: Task) -> str:
    if task.due_at is None:
        return "没有截止时间"
    return f"截止时间：{task.due_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M')}（UTC）"


def announce_text(task: Task, project: Project | None) -> str:
    return (
        f"工作台有一个可以自由接取的任务「{task.title}」\n"
        f"task_id: {task.id}\n"
        f"所属项目：{project.title if project is not None else '（已删除）'}\n"
        f"{_due(task)}\n"
        "先用 get_task 看任务描述，符合你的能力就用 claim_task 领取；不符合不用回复。"
    )


def reopen_text(task: Task, reason: str) -> str:
    return (
        f"任务「{task.title}」被打回\n"
        f"task_id: {task.id}\n"
        f"原因：{reason}\n"
        "请继续处理，做完再用 complete_task 把它标为完成。"
    )


async def admin_access(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID | None
) -> WorkspaceAccess | None:
    """这个人现在仍是本工作空间里状态正常的 owner / admin 时，返回他的访问上下文。"""
    if user_id is None:
        return None
    row = (
        await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(
                Workspace.id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.role.in_(IM_ROLES),
                User.status == "active",
            )
        )
    ).first()
    if row is None:
        return None
    workspace, role = row
    return WorkspaceAccess(workspace=workspace, role=Role(role), user_id=user_id)


async def direct_message(
    db: AsyncSession,
    settings: Settings,
    redis: Redis | None,
    client: OpenIMClient | None,
    *,
    workspace_id: uuid.UUID,
    sender_id: uuid.UUID | None,
    instance_id: uuid.UUID,
    text: str,
    key: uuid.UUID,
) -> bool:
    """用 sender 的 IM 账号给实例发一条单聊。发不出去只记日志，返回 False。"""
    if client is None:
        return False
    access = await admin_access(db, workspace_id, sender_id)
    if access is None:
        logger.warning(
            "task message skipped: no admin sender", extra={"instance_id": str(instance_id)}
        )
        return False
    im = ImService(db, settings, redis, client)
    try:
        conversation = await im.open_conversation(access, instance_id)
        await im.send_message(
            access,
            conversation.conversation_id,
            ImMessageSend(kind="text", text=text, at_im_ids=[]),
            key,
            path=f"/internal/task-messages/{key}",
        )
    except ApiError as error:
        logger.warning(
            "task message failed",
            extra={"instance_id": str(instance_id), "error_code": error.code},
        )
        return False
    return True


async def group_notice(
    db: AsyncSession, client: OpenIMClient | None, project_id: uuid.UUID, text: str
) -> bool:
    """项目群里由 ws_notice 发一条通知；没有项目群或发不出去时跳过（不重放）。"""
    if client is None:
        return False
    group = (await project_groups.live_groups(db, [project_id])).get(project_id)
    if group is None:
        return False
    group_id = group.group_id
    try:
        await client.send_text(send_id=NOTICE_USER_ID, group_id=group_id, text=text)
    except ConnectorError as error:
        logger.warning(
            "project group notice failed", extra={"group_id": group_id, "error_kind": error.kind}
        )
        return False
    return True


async def eligible(db: AsyncSession, task: Task) -> list[AgentInstance]:
    """现在可以领取这个任务的智能体：在线、IM 就绪、能写、空闲、在范围内。"""
    instances = list(
        (
            await db.scalars(
                select(AgentInstance)
                .where(
                    AgentInstance.workspace_id == task.workspace_id,
                    AgentInstance.status == "online",
                    AgentInstance.scope == "read_write",
                    AgentInstance.im_ready.is_(True),
                )
                .order_by(AgentInstance.name, AgentInstance.id)
            )
        ).all()
    )
    taken = await busy_instances(db, [i.id for i in instances])
    found = [i for i in instances if i.id not in taken]
    if task.claim_scope == "project":
        members = set(
            (
                await db.scalars(
                    select(ProjectAgent.instance_id).where(
                        ProjectAgent.project_id == task.project_id
                    )
                )
            ).all()
        )
        found = [i for i in found if i.id in members]
    return found[:ANNOUNCE_LIMIT]


async def announce(
    db: AsyncSession,
    settings: Settings,
    redis: Redis | None,
    client: OpenIMClient | None,
    task_id: uuid.UUID,
) -> int:
    """任务可以领取时，通知符合条件的智能体；返回发出的条数。"""
    if client is None:
        return 0
    task = await db.get(Task, task_id)
    if task is None or not claimable(task):
        return 0
    project = await db.get(Project, task.project_id)
    text = announce_text(task, project)
    workspace_id, sender, version = task.workspace_id, task.claim_opened_by, task.version
    sent = 0
    for instance in await eligible(db, task):
        if await direct_message(
            db,
            settings,
            redis,
            client,
            workspace_id=workspace_id,
            sender_id=sender,
            instance_id=instance.id,
            text=text,
            key=message_key("announce", task_id, instance.id, version),
        ):
            sent += 1
    if sent:
        logger.info("claimable task announced", extra={"task_id": str(task_id), "count": sent})
    return sent
