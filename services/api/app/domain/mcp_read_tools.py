"""MCP 读工具（V2 OC-5，契约第 9.2、9.3 节）。

每个读工具都调用已有的领域服务（与工作台页面用的同一套查询），身份是实例的登记人、
角色取登记人与实例 max_role 中较低的一个（`Requester.access()`），所以工作空间范围与可见性
（例如别人的私有收藏）和页面上完全一致。

返回内容只放需要的字段；来自第三方服务或用户输入的文字（标题、正文、备注、地址、名称、作者、
标签等）一律放进每个条目的 `untrusted`，结构化字段（ID、枚举、数字、时间）放在外面。
读外部数据的工具带 `freshness`，取值同页面上的同步状态（unauthorized 归为 unavailable）。
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast, get_args

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import notifications as notification_domain
from app.domain import task_claims
from app.domain.ai_tools import AiToolService, Requester, ToolCallError
from app.domain.automations import AutomationService
from app.domain.calendar import CalendarService
from app.domain.connectors import ConnectorRuntime
from app.domain.developer import DeveloperService
from app.domain.host_metrics import HostService
from app.domain.knowledge import KnowledgeService
from app.domain.knowledge_base import KnowledgeBase
from app.domain.notifications import NotificationService
from app.domain.operations import DeploymentHealth, OperationsService
from app.domain.policy import WorkspaceAccess
from app.domain.search import SearchService
from app.domain.tasks import TaskService
from app.models import (
    AgentInstance,
    CalendarEvent,
    Project,
    ProjectAgent,
    Task,
    TaskEvent,
    TaskRun,
    User,
)
from app.repositories import tasks as task_repo
from app.schemas.ai_tools import (
    ActionStatusOutput,
    AutomationItem,
    AutomationParam,
    AutomationText,
    BookmarkItem,
    BookmarkText,
    CalendarAgendaInput,
    CalendarAgendaOutput,
    DashboardOverviewOutput,
    DueTaskItem,
    DueTaskText,
    EmptyInput,
    EventItem,
    EventText,
    Freshness,
    GetActionStatusInput,
    GetHostStatusOutput,
    GetTaskInput,
    GetTaskOutput,
    HostDay,
    HostDiskStatus,
    IssueItem,
    IssueText,
    KnowledgeSourceItem,
    KnowledgeSourceText,
    ListAutomationsOutput,
    ListBookmarksInput,
    ListBookmarksOutput,
    ListIssuesInput,
    ListIssuesOutput,
    ListKnowledgeSourcesOutput,
    ListNotificationsInput,
    ListNotificationsOutput,
    ListProjectsOutput,
    ListPullsOutput,
    ListReposOutput,
    ListTasksInput,
    ListTasksOutput,
    NotificationItem,
    NotificationText,
    PageInput,
    ProjectItem,
    ProjectText,
    PullItem,
    RepoItem,
    RepoText,
    SearchHit,
    SearchHitText,
    SearchInput,
    SearchOutput,
    ServiceItem,
    ServiceStatusOutput,
    TaskBrief,
    TaskClaim,
    TaskDetail,
    TaskDetailText,
    TaskItem,
    TaskText,
)
from app.schemas.host import DiskOut
from app.schemas.integrations import SyncState
from app.schemas.search import AgentSearchType
from app.schemas.tasks import TaskOut

# 没有分页参数的列表工具最多返回的条数（契约第 9.2 节：列表最多 50）
LIST_CAP = 50
DUE_TASKS_CAP = 100


@dataclass(frozen=True)
class ReadCall:
    """一次读工具调用可用的依赖。"""

    db: AsyncSession
    settings: Settings
    runtime: ConnectorRuntime
    redis: Redis | None
    state: Any  # 应用的 app.state：部署级健康检查要用到其中的缓存与检查项
    requester: Requester

    @property
    def access(self) -> WorkspaceAccess:
        return self.requester.access()

    @property
    def user_id(self) -> uuid.UUID:
        user = self.requester.acting_user
        if user is None:  # check() 已经保证有登记人；这里只为类型
            raise ToolCallError("FORBIDDEN_ROLE", "实例没有登记人")
        return user.id

    def tasks(self) -> TaskService:
        return TaskService(self.db, self.settings, self.runtime)

    def health(self) -> DeploymentHealth:
        return DeploymentHealth(
            self.state.redis,
            self.settings,
            self.runtime,
            self.state.readiness_checks,
            self.state.service_cache,
        )


ReadHandler = Callable[[ReadCall, Any], Awaitable[Any]]


def freshness(sync: SyncState) -> Freshness:
    status = "unavailable" if sync.status == "unauthorized" else sync.status
    return Freshness(status=status, as_of=sync.as_of)


def local_freshness() -> Freshness:
    """工作台自己的数据（项目、任务；T-0082）：读到的就是最新的。"""
    return Freshness(status="ok", as_of=datetime.now(UTC))


async def _search(call: ReadCall, args: SearchInput) -> SearchOutput:
    page = await SearchService(call.db).search(
        call.user_id,
        call.access,
        q=args.q,
        # 知识库笔记不在这里（按知识库的读权限，智能体用 knowledge_search）
        types=list(args.types) or list(get_args(AgentSearchType)),
        cursor=None,
        limit=args.limit,
    )
    return SearchOutput(
        items=[
            SearchHit(
                resource_type=cast(AgentSearchType, item.resource_type),
                id=item.id,
                provider=item.provider,
                updated_at=item.updated_at,
                untrusted=SearchHitText(title=item.title, context=item.context),
            )
            for item in page.items
        ]
    )


@dataclass(frozen=True)
class _TaskView:
    assignee: str
    assignee_name: str | None
    claim: TaskClaim
    running: bool


async def _my_projects(call: ReadCall) -> set[uuid.UUID]:
    return set(
        (
            await call.db.scalars(
                select(ProjectAgent.project_id).where(
                    ProjectAgent.instance_id == call.requester.instance.id
                )
            )
        ).all()
    )


async def _task_views(call: ReadCall, items: Sequence[TaskOut]) -> dict[uuid.UUID, _TaskView]:
    """执行者（me / member / instance / none，成员只给显示名）、接取方式、是否在执行（T-0111）。"""
    me = call.requester.instance.id
    user_ids = {t.assignee_user_id for t in items if t.assignee_user_id}
    instance_ids = {t.assignee_instance_id for t in items if t.assignee_instance_id}
    users = (
        {
            uid: name
            for uid, name in (
                await call.db.execute(
                    select(User.id, User.display_name).where(User.id.in_(user_ids))
                )
            ).all()
        }
        if user_ids
        else {}
    )
    instances = (
        {
            iid: name
            for iid, name in (
                await call.db.execute(
                    select(AgentInstance.id, AgentInstance.name).where(
                        AgentInstance.id.in_(instance_ids)
                    )
                )
            ).all()
        }
        if instance_ids
        else {}
    )
    ids = [t.id for t in items]
    running = (
        set(
            (
                await call.db.scalars(
                    select(TaskRun.task_id).where(
                        TaskRun.task_id.in_(ids), TaskRun.ended_at.is_(None)
                    )
                )
            ).all()
        )
        if ids
        else set()
    )
    mine = await _my_projects(call)
    views: dict[uuid.UUID, _TaskView] = {}
    for t in items:
        if t.assignee_instance_id == me:
            kind, name = "me", instances.get(me)
        elif t.assignee_instance_id is not None:
            kind, name = "instance", instances.get(t.assignee_instance_id)
        elif t.assignee_user_id is not None:
            kind, name = "member", users.get(t.assignee_user_id)
        else:
            kind, name = "none", None
        claimable = t.claimable and (t.claim_scope == "all" or t.project_id in mine)
        views[t.id] = _TaskView(
            assignee=kind,
            assignee_name=name,
            claim=TaskClaim(mode=t.claim_mode, scope=t.claim_scope, claimable=claimable),
            running=t.id in running,
        )
    return views


async def _list_tasks(call: ReadCall, args: ListTasksInput) -> ListTasksOutput:
    me = call.requester.instance.id
    page = await call.tasks().list_tasks(
        call.access,
        status=args.status,
        project_id=args.project_id,
        priorities=None,
        due_before=args.due_before,
        due_after=args.due_after,
        q=args.q,
        sort="due_at",
        cursor=args.cursor,
        limit=args.limit,
        assignee_instance_id=me if args.assigned_to_me else None,
        claimable_for=me if args.claimable else None,
    )
    views = await _task_views(call, page.items)
    return ListTasksOutput(
        items=[
            TaskItem(
                id=t.id,
                project_id=t.project_id,
                done=t.done,
                priority=t.priority,
                due_at=t.due_at,
                assignee=views[t.id].assignee,
                claim=views[t.id].claim,
                planned_start_at=t.planned_start_at,
                started_at=t.started_at,
                running=views[t.id].running,
                untrusted=TaskText(title=t.title, assignee_name=views[t.id].assignee_name),
            )
            for t in page.items
        ],
        next_cursor=page.next_cursor,
        freshness=local_freshness(),
    )


async def _get_task(call: ReadCall, args: GetTaskInput) -> GetTaskOutput:
    service = call.tasks()
    task = await service.get_task(call.access, args.task_id)
    brief = None
    if task.project_id is not None:
        found = await KnowledgeBase(call.db, call.settings).brief_status(
            call.requester.instance.id, task.project_id
        )
        if found:
            b = found[0]
            brief = TaskBrief(
                project_id=b.project_id, version=b.version, read_version=b.read, unread=b.unread
            )
    view = (await _task_views(call, [task]))[task.id]
    reason = await call.db.scalar(
        select(TaskEvent.note)
        .where(TaskEvent.task_id == task.id, TaskEvent.kind == "reopened")
        .order_by(TaskEvent.created_at.desc())
        .limit(1)
    )
    return GetTaskOutput(
        brief=brief,
        task=TaskDetail(
            id=task.id,
            project_id=task.project_id,
            done=task.done,
            done_at=task.done_at,
            priority=task.priority,
            due_at=task.due_at,
            assignee=view.assignee,
            claim=view.claim,
            planned_start_at=task.planned_start_at,
            started_at=task.started_at,
            running=view.running,
            untrusted=TaskDetailText(
                title=task.title,
                description=task.description,
                labels=task.labels,
                assignee_name=view.assignee_name,
                last_reopen_reason=reason,
            ),
        ),
        freshness=local_freshness(),
    )


async def _list_projects(call: ReadCall, args: EmptyInput) -> ListProjectsOutput:
    page = await call.tasks().list_projects(
        call.access, include_archived=True, q=None, cursor=None, limit=LIST_CAP
    )
    return ListProjectsOutput(
        items=[
            ProjectItem(id=p.id, archived=p.archived, untrusted=ProjectText(title=p.title))
            for p in page.items
        ],
        freshness=local_freshness(),
    )


async def _calendar_agenda(call: ReadCall, args: CalendarAgendaInput) -> CalendarAgendaOutput:
    page = await CalendarService(call.db, call.settings).list_events(
        call.access, start=args.start, end=args.end, cursor=None, limit=LIST_CAP
    )
    return CalendarAgendaOutput(
        items=[
            EventItem(
                id=e.id,
                all_day=e.all_day,
                start_at=e.start_at,
                end_at=e.end_at,
                start_date=e.start_date,
                end_date=e.end_date,
                timezone=e.timezone,
                untrusted=EventText(title=e.title, location=e.location, description=e.description),
            )
            for e in page.items
        ],
        due_tasks=await _due_tasks(call, args),
    )


async def _due_tasks(call: ReadCall, args: CalendarAgendaInput) -> list[DueTaskItem]:
    """这段时间里有截止时间、还没完成的任务（日历上的待办，T-0111）。"""
    me = call.requester.instance.id
    rows = (
        await call.db.execute(
            select(Task, Project.title)
            .join(Project, Project.id == Task.project_id)
            .where(
                Task.workspace_id == call.requester.workspace.id,
                Task.done.is_(False),
                Task.due_at >= args.start,
                Task.due_at < args.end,
            )
            .order_by(Task.due_at, Task.id)
            .limit(DUE_TASKS_CAP)
        )
    ).all()
    mine = await _my_projects(call)
    items = []
    for task, project in rows:
        if task.due_at is None:  # pragma: no cover - 条件里已排除
            continue
        claimable = task_claims.claimable(task) and (
            task.claim_scope == "all" or task.project_id in mine
        )
        items.append(
            DueTaskItem(
                id=task.id,
                project_id=task.project_id,
                due_at=task.due_at,
                assigned_to_me=task.assignee_instance_id == me,
                claimable=claimable,
                untrusted=DueTaskText(title=task.title, project=project),
            )
        )
    return items


async def _list_bookmarks(call: ReadCall, args: ListBookmarksInput) -> ListBookmarksOutput:
    page = await KnowledgeService(call.db, call.settings).list_resources(
        call.user_id,
        call.access,
        source_id=None,
        linked_resource_id=None,
        visibility="all",
        q=args.q,
        sort="updated",
        cursor=args.cursor,
        limit=args.limit,
    )
    return ListBookmarksOutput(
        items=[
            BookmarkItem(
                id=b.id,
                visibility=b.visibility,
                untrusted=BookmarkText(title=b.title, url=b.url, note=b.note),
            )
            for b in page.items
        ],
        next_cursor=page.next_cursor,
    )


async def _list_knowledge_sources(call: ReadCall, args: EmptyInput) -> ListKnowledgeSourcesOutput:
    page = await KnowledgeService(call.db, call.settings).list_sources(call.access)
    return ListKnowledgeSourcesOutput(
        items=[
            KnowledgeSourceItem(
                id=s.id, provider=s.provider, untrusted=KnowledgeSourceText(name=s.name)
            )
            for s in page.items[:LIST_CAP]
        ]
    )


async def _list_repos(call: ReadCall, args: PageInput) -> ListReposOutput:
    page = await DeveloperService(call.db, call.settings).repos(
        call.access, integration_id=None, cursor=args.cursor, limit=args.limit
    )
    return ListReposOutput(
        items=[
            RepoItem(
                id=r.id,
                full_name=r.full_name,
                private=r.private,
                archived=r.archived,
                default_branch=r.default_branch,
                pushed_at=r.pushed_at,
                untrusted=RepoText(description=r.description),
            )
            for r in page.items
        ],
        next_cursor=page.next_cursor,
        freshness=freshness(page.sync),
    )


async def _list_issues(call: ReadCall, args: ListIssuesInput) -> ListIssuesOutput:
    page = await DeveloperService(call.db, call.settings).issues(
        call.access,
        repo_id=args.repo_id,
        state=args.state,
        q=None,
        cursor=args.cursor,
        limit=args.limit,
    )
    return ListIssuesOutput(
        items=[
            IssueItem(
                id=i.id,
                repo_id=i.repo_id,
                number=i.number,
                state=i.state,
                created_at=i.created_at,
                updated_at=i.source_updated_at,
                untrusted=IssueText(title=i.title, author=i.author, labels=i.labels),
            )
            for i in page.items
        ],
        next_cursor=page.next_cursor,
        freshness=freshness(page.sync),
    )


async def _list_pulls(call: ReadCall, args: ListIssuesInput) -> ListPullsOutput:
    page = await DeveloperService(call.db, call.settings).pulls(
        call.access,
        repo_id=args.repo_id,
        state=args.state,
        q=None,
        cursor=args.cursor,
        limit=args.limit,
    )
    return ListPullsOutput(
        items=[
            PullItem(
                id=p.id,
                repo_id=p.repo_id,
                number=p.number,
                state=p.state,
                created_at=p.created_at,
                updated_at=p.source_updated_at,
                draft=p.draft,
                merged=p.merged,
                untrusted=IssueText(title=p.title, author=p.author, labels=p.labels),
            )
            for p in page.items
        ],
        next_cursor=page.next_cursor,
        freshness=freshness(page.sync),
    )


async def _service_status(call: ReadCall, args: EmptyInput) -> ServiceStatusOutput:
    result = await OperationsService(call.db, call.health()).services(call.access)
    return ServiceStatusOutput(
        items=[
            ServiceItem(id=s.id, kind=s.kind, status=s.status, reason=s.reason)
            for s in result.items
        ]
    )


async def _list_notifications(
    call: ReadCall, args: ListNotificationsInput
) -> ListNotificationsOutput:
    page = await NotificationService(call.db).list(
        call.access, unread_only=args.unread_only, cursor=None, limit=args.limit
    )
    return ListNotificationsOutput(
        items=[
            NotificationItem(
                id=n.id,
                type=n.type,
                severity=n.severity,
                read=n.read_at is not None,
                created_at=n.created_at,
                untrusted=NotificationText(title=n.title, body=n.body),
            )
            for n in page.items
        ],
        unread_count=page.unread_count,
    )


async def _list_automations(call: ReadCall, args: EmptyInput) -> ListAutomationsOutput:
    page = await AutomationService(call.db, call.settings, call.runtime).list_automations(
        call.access, cursor=None, limit=LIST_CAP
    )
    return ListAutomationsOutput(
        items=[
            AutomationItem(
                id=a.id,
                enabled=a.enabled,
                params=[
                    AutomationParam(name=p.name, type=p.type, required=p.required) for p in a.params
                ],
                last_run_status=a.last_run.status if a.last_run else None,
                untrusted=AutomationText(name=a.name, description=a.description),
            )
            for a in page.items
        ]
    )


async def _dashboard_overview(call: ReadCall, args: EmptyInput) -> DashboardOverviewOutput:
    """首页概况的几个数字。未知时为 null，不以 0 冒充；“今天”按 UTC 计算。"""
    access = call.access
    now = datetime.now(UTC)
    pending, overdue = await task_repo.task_counts(call.db, access.workspace.id, now=now)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_events = await call.db.scalar(
        select(func.count())
        .select_from(CalendarEvent)
        .where(
            CalendarEvent.workspace_id == access.workspace.id,
            CalendarEvent.start_at < day + timedelta(days=1),
            CalendarEvent.end_at > day,
        )
    )
    services = await OperationsService(call.db, call.health()).services(access)
    service = AiToolService(call.db, call.settings, call.runtime, call.redis)
    return DashboardOverviewOutput(
        pending_tasks=pending,
        overdue_tasks=overdue,
        today_events=int(today_events or 0),
        unread_notifications=await notification_domain.unread_count(call.db, access),
        pending_approvals=await service.pending_count(access.workspace.id),
        services_down=sum(1 for s in services.items if s.status == "down"),
    )


async def _host_status(call: ReadCall, args: EmptyInput) -> GetHostStatusOutput:
    """只看调用它的实例自己（T-0113，D5）。"""
    status = await HostService(call.db, call.redis).status_of(call.requester.instance, "24h")
    current = status.current

    def disk(value: DiskOut | None) -> HostDiskStatus | None:
        return (
            HostDiskStatus(total=value.total, free=value.free, used_percent=value.used_percent)
            if value is not None
            else None
        )

    def top(values: list[float | None]) -> float | None:
        known = [v for v in values if v is not None]
        return max(known) if known else None

    return GetHostStatusOutput(
        reported=status.reported,
        reported_at=current.reported_at if current else None,
        stale=current.stale if current else True,
        cores=current.cores if current else None,
        cpu_percent=current.cpu_percent if current else None,
        load=current.load if current else None,
        mem_total=current.mem_total if current else None,
        mem_available=current.mem_available if current else None,
        data_disk=disk(current.data_disk) if current else None,
        root_disk=disk(current.root_disk) if current else None,
        rx_rate=current.rx_rate if current else None,
        tx_rate=current.tx_rate if current else None,
        last_24h=HostDay(
            cpu_max=top([p.cpu_max for p in status.points]),
            load_max=top([p.load_max for p in status.points]),
            mem_max=top([p.mem_max for p in status.points]),
            disk_max=top([p.data_avg for p in status.points] + [p.root_avg for p in status.points]),
        ),
        alerts=status.alerts,
    )


async def _action_status(call: ReadCall, args: GetActionStatusInput) -> ActionStatusOutput:
    service = AiToolService(call.db, call.settings, call.runtime, call.redis)
    row = await service.get_for_instance(call.requester.instance, args.action_id)
    if row is None:
        raise ToolCallError("NOT_FOUND", "没有这条操作记录")
    return ActionStatusOutput(
        action_id=row.id,
        tool_name=row.tool_name,
        status=row.status,
        expires_at=row.expires_at,
        decided_at=row.decided_at,
        executed_at=row.executed_at,
        result_summary=row.result_summary,
        error_code=row.error_code,
    )


READ_HANDLERS: dict[str, ReadHandler] = {
    "search": _search,
    "list_tasks": _list_tasks,
    "get_task": _get_task,
    "list_projects": _list_projects,
    "calendar_agenda": _calendar_agenda,
    "list_bookmarks": _list_bookmarks,
    "list_knowledge_sources": _list_knowledge_sources,
    "list_repos": _list_repos,
    "list_issues": _list_issues,
    "list_pull_requests": _list_pulls,
    "service_status": _service_status,
    "list_notifications": _list_notifications,
    "list_automations": _list_automations,
    "dashboard_overview": _dashboard_overview,
    "get_action_status": _action_status,
    "get_host_status": _host_status,
}
