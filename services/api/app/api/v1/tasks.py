"""工作台自己的项目与任务（T-0082；之前是 Vikunja 的投影）。

工作空间范围的资源，用 X-Workspace-Id 指定工作空间：viewer 可读，member 及以上可写。
任务的写接口必须带 Idempotency-Key（UUID），语义沿用
docs/decisions/2026-10-30-sprint-3-tasks-contract.md 第 7 节；
项目的写接口用整数 version 做乐观并发。
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, Security, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, Field

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_run_service,
    get_task_service,
    workspace_member,
    workspace_viewer,
)
from app.core.errors import ApiError
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess
from app.domain.tasks import TaskService, WriteResult
from app.domain.timeline import RunService
from app.schemas.common import error_responses
from app.schemas.tasks import (
    ProgressFilter,
    ProjectActivityPage,
    ProjectAgentList,
    ProjectCreate,
    ProjectOut,
    ProjectPage,
    ProjectProgressList,
    ProjectSummary,
    ProjectUpdate,
    ProjectVersion,
    TaskAssigneeFilter,
    TaskBlock,
    TaskCreate,
    TaskEventList,
    TaskOut,
    TaskPage,
    TaskProgressOut,
    TaskReopen,
    TaskResultView,
    TaskSort,
    TaskStatusFilter,
    TaskSummary,
    TaskUpdate,
    TaskVersion,
    WorkStateFilter,
)
from app.schemas.timeline import (
    AssigneeOptions,
    TaskRunList,
    TaskRunOut,
    TaskRunStart,
    TaskRunStop,
    TimelineGroup,
    TimelineOut,
)

router = APIRouter(tags=["tasks"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Member = Annotated[WorkspaceAccess, Depends(workspace_member)]
Context = Annotated[AuthContext, Security(authenticated)]
Service = Annotated[TaskService, Depends(get_task_service)]
Runs = Annotated[RunService, Depends(get_run_service)]
IdempotencyKey = Annotated[
    UUID,
    Header(
        alias="Idempotency-Key",
        description="每次用户提交生成一个新的 UUID；网络重试与查询结果时复用同一个",
    ),
]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
_read_errors = error_responses(401, 404, 422)
_write = [Depends(csrf_protected)]
_write_errors = error_responses(401, 403, 404, 409, 422)
_REPLAY = (
    "同一个 Idempotency-Key 的重复请求原样返回第一次的结果，响应头带 Idempotent-Replayed: true。"
)


def _respond(result: WriteResult) -> Response:
    """写接口的结果；重放时带 Idempotent-Replayed（契约第 7 节）。"""
    headers = {"Idempotent-Replayed": "true"} if result.replayed else None
    if result.body is None:
        return Response(status_code=result.status_code, headers=headers)
    return JSONResponse(result.body, status_code=result.status_code, headers=headers)


@router.get(
    "/projects",
    summary="项目列表（viewer）",
    description="默认不含已归档的项目；每个项目带未完成 / 已完成任务数，以及按当前用户查看状态"
    "的五种任务状态计数与最近更新时间（T-0166）。attention_only 只看需要你关注的（有你待查看"
    "或明确阻塞的任务），archived_only 只看已归档的；sort=updated 按最近更新排。",
    responses=_read_errors,
)
async def list_projects(
    access: Viewer,
    service: Service,
    include_archived: bool = False,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    cursor: Cursor = None,
    limit: Limit = 30,
    attention_only: bool = False,
    archived_only: bool = False,
    sort: Annotated[Literal["title", "updated"], Query()] = "title",
) -> ProjectPage:
    return await service.list_projects(
        access,
        include_archived=include_archived,
        q=q,
        cursor=cursor,
        limit=limit,
        attention_only=attention_only,
        archived_only=archived_only,
        sort=sort,
    )


@router.post(
    "/projects",
    status_code=status.HTTP_201_CREATED,
    summary="新建项目（member）",
    description="owner / admin 可以同时选择加入的智能体（instance_ids）并建 IM 项目群（im_group），"
    "member 带这两项时返回 403。OpenIM 暂时不可用时项目照常建好，im_group.status 为 pending，"
    "Worker 每分钟重试（T-0110）。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 422),
)
async def create_project(
    access: Member, context: Context, service: Service, body: ProjectCreate
) -> ProjectOut:
    return await service.create_project(context, access, body)


@router.get(
    "/projects/progress",
    summary="项目进度（viewer）",
    description="未归档项目的未完成 / 已完成任务数，供首页“项目进度”组件使用；"
    "有未完成任务的项目在前。",
    responses=_read_errors,
)
async def project_progress(
    access: Viewer, service: Service, limit: Annotated[int, Query(ge=1, le=20)] = 5
) -> ProjectProgressList:
    return await service.project_progress(access, limit=limit)


@router.get(
    "/projects/summary",
    summary="项目页顶部的统计（viewer）",
    description="未归档项目数；其中需要你关注的项目数（有你待查看或明确阻塞的任务）；"
    "[completed_after, completed_before) 内完成、现在仍是已完成的任务数（按 done_at，"
    "不是完整的历史完成事件数）。时间窗由前端按显示时区算好（例如本周一 00:00 到下周一 00:00）。",
    responses=_read_errors,
)
async def project_summary(
    access: Viewer,
    service: Service,
    completed_after: Annotated[AwareDatetime, Query()],
    completed_before: Annotated[AwareDatetime, Query()],
) -> ProjectSummary:
    return await service.project_summary(
        access, completed_after=completed_after, completed_before=completed_before
    )


@router.get(
    "/projects/activity",
    summary="项目的最近动态（viewer）",
    description="任务动态（设为可领取、领取、放弃、完成、打回、标记 / 解除阻塞），新的在前；"
    "project_id 不填时是全部项目。note 来自人或智能体，按纯文本显示（T-0166）。",
    responses=_read_errors,
)
async def project_activity(
    access: Viewer,
    service: Service,
    project_id: UUID | None = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> ProjectActivityPage:
    return await service.project_activity(access, project_id=project_id, cursor=cursor, limit=limit)


@router.get(
    "/projects/{project_id}",
    summary="项目详情（viewer）",
    responses=_read_errors,
)
async def get_project(access: Viewer, service: Service, project_id: UUID) -> ProjectOut:
    return await service.get_project(access, project_id)


@router.patch(
    "/projects/{project_id}",
    summary="修改项目（member，乐观并发）",
    description="改名、改描述、归档 / 取消归档；"
    "version 与当前版本不一致时返回 409 VERSION_CONFLICT，details.current 为最新的项目。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_project(
    access: Member, context: Context, service: Service, project_id: UUID, body: ProjectUpdate
) -> ProjectOut:
    return await service.update_project(context, access, project_id, body)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除项目及它的全部任务（member，乐观并发；前端先二次确认）",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_project(
    access: Member,
    context: Context,
    service: Service,
    project_id: UUID,
    version: Annotated[ProjectVersion, Query()],
) -> None:
    await service.delete_project(context, access, project_id, version)


@router.get(
    "/projects/{project_id}/agents",
    summary="项目的智能体成员（viewer）",
    description="加入项目的智能体能读写项目知识库、收到项目简报，有项目群时也在群里（T-0110）。",
    responses=_read_errors,
)
async def list_project_agents(
    access: Viewer, service: Service, project_id: UUID
) -> ProjectAgentList:
    return await service.list_project_agents(access, project_id)


@router.put(
    "/projects/{project_id}/agents/{instance_id}",
    summary="让智能体加入项目（admin）",
    description="同时得到项目知识库的读写权限，并被拉进项目群（有项目群时）。已经是成员时不变。"
    "停用的实例返回 422；一个项目最多 50 个智能体。",
    dependencies=_write,
    responses=_write_errors,
)
async def add_project_agent(
    access: Member, context: Context, service: Service, project_id: UUID, instance_id: UUID
) -> ProjectAgentList:
    return await service.add_project_agent(context, access, project_id, instance_id)


@router.delete(
    "/projects/{project_id}/agents/{instance_id}",
    summary="把智能体移出项目（admin）",
    description="不再能读写项目知识库，也移出项目群；它已经领取的任务不受影响。不是成员时不变。",
    dependencies=_write,
    responses=_write_errors,
)
async def remove_project_agent(
    access: Member, context: Context, service: Service, project_id: UUID, instance_id: UUID
) -> ProjectAgentList:
    return await service.remove_project_agent(context, access, project_id, instance_id)


@router.post(
    "/projects/{project_id}/im-group",
    summary="建项目群 / 立即重试（admin）",
    description="没有项目群的项目（以前建的项目）建一个，群主是自己，成员是自己和已加入的智能体；"
    "正在建群的立即重试；已建好的重新同步一次成员与群信息。没有部署 OpenIM 时返回 409。",
    dependencies=_write,
    responses=_write_errors,
)
async def request_project_group(
    access: Member, context: Context, service: Service, project_id: UUID
) -> ProjectOut:
    return await service.request_project_group(context, access, project_id)


@router.get(
    "/tasks",
    summary="任务列表（viewer）",
    description="看板视图由前端按 done 分列。work_state 按当前用户的状态筛选（T-0166，"
    "与 status 取交集；按状态看时传 status=all）；assignee_user_id 与 assignee_instance_id "
    "只能给一个。",
    responses=_read_errors,
)
async def list_tasks(
    access: Viewer,
    service: Service,
    status_filter: Annotated[TaskStatusFilter, Query(alias="status")] = "open",
    project_id: UUID | None = None,
    priority: Annotated[list[Annotated[int, Field(ge=0, le=5)]] | None, Query()] = None,
    due_before: Annotated[AwareDatetime | None, Query()] = None,
    due_after: Annotated[AwareDatetime | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    sort: TaskSort = "due_at",
    cursor: Cursor = None,
    limit: Limit = 30,
    assignee: Annotated[
        TaskAssigneeFilter | None,
        Query(description="me：只看执行者是当前用户的任务（首页“我的待办”，T-0163）"),
    ] = None,
    work_state: WorkStateFilter | None = None,
    assignee_user_id: UUID | None = None,
    assignee_instance_id: UUID | None = None,
) -> TaskPage:
    user_filter = _assignee_filter(access, assignee, assignee_user_id, assignee_instance_id)
    return await service.list_tasks(
        access,
        assignee_user_id=user_filter,
        assignee_instance_id=assignee_instance_id,
        work_state=work_state,
        status=status_filter,
        project_id=project_id,
        priorities=priority,
        due_before=due_before,
        due_after=due_after,
        q=q,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


def _assignee_filter(
    access: WorkspaceAccess,
    assignee: str | None,
    assignee_user_id: UUID | None,
    assignee_instance_id: UUID | None,
) -> UUID | None:
    """执行者筛选：me、成员、实例三者只能用一个。"""
    if sum(x is not None for x in (assignee, assignee_user_id, assignee_instance_id)) > 1:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "执行者筛选只能用一个：assignee、assignee_user_id、assignee_instance_id",
        )
    return access.user_id if assignee == "me" else assignee_user_id


@router.get(
    "/tasks/summary",
    summary="任务状态计数（viewer）",
    description="与任务列表同样的筛选（不含 status / work_state），返回五种状态各几项与合计，"
    "供任务页的状态筛选标签使用（T-0166）。“已完成待查看”按当前用户的查看状态统计。",
    responses=_read_errors,
)
async def task_summary(
    access: Viewer,
    service: Service,
    project_id: UUID | None = None,
    priority: Annotated[list[Annotated[int, Field(ge=0, le=5)]] | None, Query()] = None,
    due_before: Annotated[AwareDatetime | None, Query()] = None,
    due_after: Annotated[AwareDatetime | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    assignee: TaskAssigneeFilter | None = None,
    assignee_user_id: UUID | None = None,
    assignee_instance_id: UUID | None = None,
) -> TaskSummary:
    return await service.summary(
        access,
        project_id=project_id,
        priorities=priority,
        due_before=due_before,
        due_after=due_after,
        q=q,
        assignee_user_id=_assignee_filter(access, assignee, assignee_user_id, assignee_instance_id),
        assignee_instance_id=assignee_instance_id,
    )


@router.post(
    "/tasks",
    status_code=status.HTTP_201_CREATED,
    summary="创建任务（member，必须带 Idempotency-Key）",
    description=_REPLAY,
    response_model=TaskOut,
    dependencies=_write,
    responses=_write_errors,
)
async def create_task(
    access: Member,
    context: Context,
    service: Service,
    idempotency_key: IdempotencyKey,
    body: TaskCreate,
) -> Response:
    return _respond(await service.create_task(context, access, idempotency_key, body))


@router.get(
    "/tasks/progress",
    summary="首页任务进展（viewer）",
    description="未归档项目里任务的四种状态（与任务页同一判定，T-0166）：已完成待查看（按当前用户）、"
    "阻塞（有人写了原因）、进行中（有正在执行的一段）、待办。返回各状态的数量和按状态筛选的一页；"
    "全部时按 待查看 → 阻塞 → 进行中 → 待办 排，同状态最近更新的在前。已完成且已查看的任务不出现。",
    responses=_read_errors,
)
async def task_progress(
    access: Viewer,
    service: Service,
    status_filter: Annotated[ProgressFilter, Query(alias="status")] = "all",
    limit: Annotated[int, Query(ge=1, le=20)] = 7,
) -> TaskProgressOut:
    return await service.progress(access, status=status_filter, limit=limit)


@router.get(
    "/tasks/assignees",
    summary="可以指派的执行者（viewer）",
    description="本工作空间的成员与没有停用的 OpenClaw 实例（名字与状态），"
    "供任务编辑器选择执行者。",
    responses=_read_errors,
)
async def task_assignees(access: Viewer, runs: Runs) -> AssigneeOptions:
    return await runs.assignees(access)


@router.get(
    "/tasks/timeline",
    summary="任务时间轴（viewer）",
    description="时间范围内的轨道（按执行者或按项目）、片段（计划 / 执行段，"
    "执行段带 token 与等待审批）、token 用量分桶与概况。"
    "from / to 默认是现在前后 12 小时，范围最长 31 天；"
    "project_id 只看这个项目（概况与 token 也只算这个项目）。"
    "token 按时间归属到执行段：同一实例同时有几段在执行时平均分（tokens_shared）。",
    responses=_read_errors,
)
async def task_timeline(
    access: Viewer,
    runs: Runs,
    range_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    range_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    project_id: UUID | None = None,
    group: TimelineGroup = "executor",
) -> TimelineOut:
    now = datetime.now(UTC)
    start = range_from or (
        range_to - timedelta(hours=24) if range_to else now - timedelta(hours=12)
    )
    end = range_to or start + timedelta(hours=24)
    return await runs.timeline(access, start=start, end=end, project_id=project_id, group=group)


@router.get(
    "/tasks/{task_id}",
    summary="任务详情（viewer）",
    responses=_read_errors,
)
async def get_task(access: Viewer, service: Service, task_id: UUID) -> TaskOut:
    return await service.get_task(access, task_id)


@router.patch(
    "/tasks/{task_id}",
    summary="修改任务（member，必须带 Idempotency-Key 与 version）",
    description=_REPLAY + "version 与当前版本不一致时返回 409 VERSION_CONFLICT，"
    "details.current 为最新的任务。",
    response_model=TaskOut,
    dependencies=_write,
    responses=_write_errors,
)
async def update_task(
    access: Member,
    context: Context,
    service: Service,
    task_id: UUID,
    idempotency_key: IdempotencyKey,
    body: TaskUpdate,
) -> Response:
    result = await service.update_task(context, access, task_id, idempotency_key, body)
    # 设为自由接取：提交之后通知符合条件的智能体（T-0111；重放的请求不会再通知）
    await service.announce_opened()
    return _respond(result)


@router.patch(
    "/tasks/{task_id}/block",
    summary="标记 / 解除阻塞（member，必须带 Idempotency-Key 与 version）",
    description=_REPLAY + "blocked=true 时必须写原因（1–2000 字）；false 清除原因。"
    "标记阻塞不会停止正在执行的一段，解除也不会自动重新开始；阻塞的任务不能被新领取或开始执行。"
    "已完成的任务返回 409 TASK_DONE；version 不一致返回 409 VERSION_CONFLICT（T-0166）。",
    response_model=TaskOut,
    dependencies=_write,
    responses=_write_errors,
)
async def block_task(
    access: Member,
    context: Context,
    service: Service,
    task_id: UUID,
    idempotency_key: IdempotencyKey,
    body: TaskBlock,
) -> Response:
    return _respond(await service.block_task(context, access, task_id, idempotency_key, body))


@router.put(
    "/tasks/{task_id}/result-view",
    summary="标为已查看（viewer）",
    description="把当前这一轮完成结果记为你已查看，只影响你自己（T-0166）。带回读取任务时的 "
    "completion_seq；任务已重新打开或又完成了一次时返回 409 COMPLETION_CHANGED，details.current "
    "是最新的任务。同一轮重复调用是幂等的。打开任务详情不会自动标记。",
    dependencies=_write,
    responses=_write_errors,
)
async def view_task_result(
    access: Viewer, service: Service, task_id: UUID, body: TaskResultView
) -> TaskOut:
    return await service.view_result(access, task_id, body)


@router.get(
    "/tasks/{task_id}/events",
    summary="任务的动态（viewer）",
    description="设为自由接取、领取、放弃、完成、打回（T-0111），最新的在前，最多 100 条。"
    "note 是原因或完成说明，来自人或智能体的文字，按纯文本显示。",
    responses=_read_errors,
)
async def list_task_events(access: Viewer, service: Service, task_id: UUID) -> TaskEventList:
    return await service.list_events(access, task_id)


@router.post(
    "/tasks/{task_id}/reopen",
    summary="打回已完成的任务（admin，乐观并发）",
    description="任务回到进行中（未完成、执行者不变）。执行者是智能体时开新的一段，"
    "并用你的 IM 账号把原因发给它；原执行者已停用或删除时任务放回未分配。"
    "原因写进任务的动态。没完成的任务返回 409 TASK_NOT_DONE（T-0111）。",
    dependencies=_write,
    responses=_write_errors,
)
async def reopen_task(
    access: Member, context: Context, runs: Runs, task_id: UUID, body: TaskReopen
) -> TaskOut:
    return await runs.reopen(context, access, task_id, body)


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除任务（member，必须带 Idempotency-Key 与 version；前端先二次确认）",
    description=_REPLAY + "任务已经不存在时同样返回 204。",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_task(
    access: Member,
    context: Context,
    service: Service,
    task_id: UUID,
    idempotency_key: IdempotencyKey,
    version: Annotated[TaskVersion, Query()],
) -> Response:
    return _respond(await service.delete_task(context, access, task_id, idempotency_key, version))


@router.get(
    "/tasks/{task_id}/runs",
    summary="执行记录（viewer）",
    description="最近 100 段，新的在前；实例执行的段带 token（按时间归属）、"
    "每 10 分钟的用量与等待审批。",
    responses=_read_errors,
)
async def list_task_runs(access: Viewer, runs: Runs, task_id: UUID) -> TaskRunList:
    return await runs.list_runs(access, task_id)


@router.post(
    "/tasks/{task_id}/runs",
    status_code=status.HTTP_201_CREATED,
    summary="开始一段（人的任务：member；交给实例执行：owner / admin；必须带 Idempotency-Key）",
    description=_REPLAY
    + "dispatch 为 true 时，以当前管理员的 IM 账号给执行者实例发一条固定格式的消息，"
    "发出后才开这一段；IM 结果不确定时返回 504 RESULT_UNKNOWN、不开段，"
    "用同一个 Idempotency-Key 重试即可（不会发两条）。"
    "409：TASK_DONE 任务已完成、RUN_ALREADY_OPEN 已有一段在执行、AGENT_TASK 执行者是实例、"
    "NO_AGENT_ASSIGNEE 执行者不是实例、AGENT_DISABLED / AGENT_OFFLINE 实例停用 / 不在线、"
    "IM_NOT_READY / IM_SEND_REJECTED；503 IM_UNAVAILABLE。",
    response_model=TaskRunOut,
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 429, 503, 504),
)
async def start_task_run(
    access: Member,
    context: Context,
    runs: Runs,
    task_id: UUID,
    idempotency_key: IdempotencyKey,
    body: TaskRunStart,
) -> Response:
    return _respond(await runs.start(context, access, task_id, idempotency_key, body))


@router.post(
    "/tasks/{task_id}/runs/{run_id}/stop",
    summary="结束一段（人的任务：member；实例执行的段：owner / admin）",
    description="已经结束的段原样返回（重试安全）。",
    dependencies=_write,
    responses=_write_errors,
)
async def stop_task_run(
    access: Member,
    context: Context,
    runs: Runs,
    task_id: UUID,
    run_id: UUID,
    body: TaskRunStop,
) -> TaskRunOut:
    return await runs.stop(context, access, task_id, run_id, body)
