"""智能体建项目、拉智能体进项目（T-0112，docs/v2/AGENT_OPS_PLAN.md 第 2.3 节）。

`create_project` / `add_to_project` / `list_peers` **只给管理员打开了项目管理权限的实例**：
- 没打开的实例：工具列表里没有这三个工具，直接调用返回 FORBIDDEN_CAPABILITY 并留一条“拒绝”记录；
- 打开了的实例：直接执行、不审批（人类 2026-11-04 决定，`docs/AGENTS.md` 第 8 节例外三），
  仍要经过 scope（read_write）与角色检查；每次都留执行记录（direct）、审计、通知管理员。
- 限额：每个实例每小时建 5 个项目、拉人 20 次（超出 RATE_LIMITED）。
- 建出来的项目与网页上建的一样：项目知识库、项目群（群主是打开权限的那位管理员，
  他已不在时由同步逻辑改为 owner），它自己自动加入。
- 只能把智能体拉进它自己建的项目；只能拉本工作空间里没有停用的实例。
- 智能体不能改、归档、删除项目，也不能移出成员（这些都在网页上做）。
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.core.errors import ApiError
from app.domain import notifications, project_groups, task_claims
from app.domain.agent_tasks import After, _Ctx, _nothing
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.audit import AuditTrail
from app.domain.ratelimit import WindowLimiter
from app.integrations.openim import OpenIMClient
from app.models import AgentInstance, Project
from app.schemas.ai_tools import (
    AddProjectAgentsInput,
    AddProjectAgentsOutput,
    AgentItem,
    AgentText,
    CreateProjectInput,
    CreateProjectOutput,
    EmptyInput,
    ListAgentsOutput,
    ToolSpec,
)

logger = logging.getLogger("app.agent_projects")

CREATES_PER_HOUR = 5
ADDS_PER_HOUR = 20


async def _limit(ctx: _Ctx, key: str, limit: int, message: str) -> None:
    if ctx.redis is None:
        return
    if await WindowLimiter(ctx.redis, 3600).hit(f"{key}:{ctx.instance.id}", limit) is not None:
        raise ToolCallError("RATE_LIMITED", message)


async def _instances(ctx: _Ctx, ids: list[uuid.UUID]) -> list[AgentInstance]:
    """本工作空间、没有停用的实例；有不是的返回 VALIDATION_FAILED。"""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    found = {
        i.id: i
        for i in (
            await ctx.db.scalars(
                select(AgentInstance).where(
                    AgentInstance.workspace_id == ctx.requester.workspace.id,
                    AgentInstance.id.in_(wanted),
                )
            )
        ).all()
    }
    bad = [str(i) for i in wanted if i not in found or found[i].status == "disabled"]
    if bad:
        raise ToolCallError("VALIDATION_FAILED", f"这些智能体不存在或已停用：{', '.join(bad)}")
    return [found[i] for i in wanted]


async def _record(ctx: _Ctx, action: str, project_id: uuid.UUID, extra: dict[str, Any]) -> None:
    await AuditTrail(ctx.db, ctx.settings).record(
        action,
        workspace_ids=[ctx.requester.workspace.id],
        actor_id=None,
        resource_type="project",
        resource_id=project_id,
        metadata={"instance_id": str(ctx.instance.id), **extra},
    )


async def _sync_after(ctx: _Ctx, project_id: uuid.UUID) -> After:
    async def after() -> None:
        if ctx.im is not None:
            await project_groups.ProjectGroupSync(ctx.db, ctx.settings, ctx.im).sync(project_id)

    return after


async def _create(ctx: _Ctx, args: CreateProjectInput) -> tuple[CreateProjectOutput, After]:
    db, me = ctx.db, ctx.instance
    me_id, me_name, owner = me.id, me.name, me.project_admin_by
    await _limit(ctx, "agent_project_creates", CREATES_PER_HOUR, "每小时最多新建 5 个项目")
    others = [i for i in await _instances(ctx, args.instance_ids) if i.id != me_id]
    project = Project(
        workspace_id=ctx.requester.workspace.id,
        title=args.title,
        description=args.description,
        created_by=None,
        created_by_instance=me_id,
    )
    db.add(project)
    await db.flush()
    await project_groups.add_agents(db, project, [me_id, *[i.id for i in others]], added_by=None)
    if ctx.settings.openim_enabled:
        # 群主是打开权限的管理员；他已不在时同步逻辑改用 owner
        project_groups.request_group(project, owner)
    await _record(ctx, "project.created", project.id, {"agents": 1 + len(others)})
    await notifications.notify_admins(
        db,
        project.workspace_id,
        type_="project.created_by_agent",
        severity="info",
        title=f"「{me_name}」新建了项目",
        body=project.title,
        link="/projects",
        key=str(project.id),
    )
    await db.flush()
    project_id, status = project.id, project.im_group_status
    return (
        CreateProjectOutput(
            project_id=project_id,
            agents=[me_id, *[i.id for i in others]],
            im_group=status,
        ),
        await _sync_after(ctx, project_id),
    )


async def _add(ctx: _Ctx, args: AddProjectAgentsInput) -> tuple[AddProjectAgentsOutput, After]:
    db, me = ctx.db, ctx.instance
    me_id, me_name = me.id, me.name
    await _limit(ctx, "agent_project_adds", ADDS_PER_HOUR, "每小时最多拉 20 次智能体进项目")
    project: Project | None = await db.scalar(
        select(Project)
        .where(Project.id == args.project_id, Project.workspace_id == ctx.requester.workspace.id)
        .with_for_update()
    )
    if project is None:
        raise ToolCallError("NOT_FOUND", "项目不存在")
    if project.created_by_instance != me_id:
        raise ToolCallError("FORBIDDEN_PROJECT", "只能把智能体拉进你自己建的项目")
    instances = await _instances(ctx, args.instance_ids)
    current = await project_groups.member_count(db, project.id)
    if current + len(instances) > project_groups.MAX_PROJECT_AGENTS:
        raise ToolCallError(
            "VALIDATION_FAILED", f"一个项目最多 {project_groups.MAX_PROJECT_AGENTS} 个智能体"
        )
    added = await project_groups.add_agents(db, project, [i.id for i in instances], added_by=None)
    already = [i.id for i in instances if i.id not in added]
    if added:
        await _record(ctx, "project.agent_added", project.id, {"added": [str(i) for i in added]})
        names = "、".join(i.name for i in instances if i.id in added)
        await notifications.notify_admins(
            db,
            project.workspace_id,
            type_="project.agents_added_by_agent",
            severity="info",
            title=f"「{me_name}」把智能体拉进了项目",
            body=f"{project.title}：{names}",
            link="/projects",
            key=f"{project.id}:{project.version}:{len(added)}",
        )
    await db.flush()
    project_id = project.id
    after = await _sync_after(ctx, project_id) if added else _nothing
    return AddProjectAgentsOutput(project_id=project_id, added=added, already=already), after


async def _list(ctx: _Ctx, args: EmptyInput) -> tuple[ListAgentsOutput, After]:
    instances = (
        await ctx.db.scalars(
            select(AgentInstance)
            .where(AgentInstance.workspace_id == ctx.requester.workspace.id)
            .order_by(AgentInstance.name, AgentInstance.id)
            .limit(200)
        )
    ).all()
    taken = await task_claims.busy_instances(ctx.db, [i.id for i in instances])
    return (
        ListAgentsOutput(
            items=[
                AgentItem(
                    id=i.id,
                    status=i.status,
                    idle=i.id not in taken,
                    can_write=i.scope == "read_write",
                    untrusted=AgentText(name=i.name, description=i.description),
                )
                for i in instances
            ]
        ),
        _nothing,
    )


_HANDLERS: dict[str, Callable[[_Ctx, Any], Awaitable[tuple[BaseModel, After]]]] = {
    "create_project": _create,
    "add_to_project": _add,
    "list_peers": _list,
}


def _summary(name: str, arguments: dict[str, Any]) -> str:
    if name == "create_project":
        return f"新建项目「{arguments.get('title', '')}」"
    if name == "add_to_project":
        return f"把 {len(arguments.get('instance_ids') or [])} 个智能体拉进项目"
    return "列出智能体"


async def run(
    service: AiToolService,
    requester: Requester,
    spec: ToolSpec,
    raw: dict[str, Any],
    *,
    request_id: str | None,
    im: OpenIMClient | None,
) -> BaseModel:
    try:
        parsed = spec.input_model.model_validate(raw)
    except ValidationError as error:
        raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
    arguments = parsed.model_dump(mode="json")
    denied = service.check(requester, spec)
    if denied is None and not requester.instance.project_admin_enabled:
        denied = "FORBIDDEN_CAPABILITY"
    if denied is not None:
        await service.record_denied(requester, spec, arguments, denied, request_id=request_id)
        message = (
            "管理员还没有给你开项目管理权限"
            if denied == "FORBIDDEN_CAPABILITY"
            else "这个实例没有权限调用这个工具"
        )
        raise ToolCallError(denied, message)
    instance_id = requester.instance.id
    ctx = _Ctx(service.db, service.settings, service.redis, im, requester)
    try:
        output, after = await _HANDLERS[spec.name](ctx, parsed)
    except (ToolCallError, ApiError) as error:
        await service.db.rollback()
        failure = (
            error
            if isinstance(error, ToolCallError)
            else ToolCallError("VALIDATION_FAILED", error.message)
        )
        instance = await service.db.get(AgentInstance, instance_id)
        if instance is not None:
            await service.record_knowledge(
                await service.requester(instance),
                spec,
                arguments,
                ok=False,
                summary=_summary(spec.name, arguments),
                error_code=failure.code,
                request_id=request_id,
            )
        raise failure from None
    await service.record_knowledge(
        requester,
        spec,
        arguments,
        ok=True,
        summary=_summary(spec.name, arguments),
        request_id=request_id,
    )
    try:
        await after()
    except Exception:  # pragma: no cover - 建群失败由 Worker 重试
        logger.exception("project tool follow-up failed", extra={"tool": spec.name})
    return output
