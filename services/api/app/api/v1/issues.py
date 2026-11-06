"""项目里的 Issues（T-0123，docs/v2/AGENT_MESSAGING_PLAN.md 第 8 节）。

权限在 app/domain/issues.py：viewer 能看；member 能开、评论、改标签、指派、关联任务，
关闭自己开的或指派给自己的；
owner / admin 能关闭任何 Issue、锁定、隐藏评论、删除（进回收站）。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, Security, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_db,
    workspace_admin,
    workspace_member,
    workspace_viewer,
)
from app.core.config import Settings
from app.domain import task_claims
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.im import openim_client
from app.domain.issues import IssueService, Who, templates
from app.domain.policy import WorkspaceAccess, not_found
from app.models import Issue, IssueComment, Project, Task
from app.schemas.common import error_responses
from app.schemas.issues import (
    CommentCreate,
    CommentOut,
    CommentUpdate,
    IssueCreate,
    IssueDetail,
    IssueKind,
    IssueList,
    IssueNoticesPut,
    IssueState,
    IssueTemplates,
    IssueUpdate,
)

router = APIRouter(tags=["issues"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Member = Annotated[WorkspaceAccess, Depends(workspace_member)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Context = Annotated[AuthContext, Security(authenticated)]
Db = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422, 429)


def _who(access: WorkspaceAccess, context: AuthContext) -> Who:
    return Who(
        workspace_id=access.workspace.id,
        name=context.user.display_name,
        user_id=context.user.id,
        role=access.role.value,
    )


def _service(db: AsyncSession, settings: Settings, request: Request) -> IssueService:
    return IssueService(db, settings, request.app.state.redis)


async def _notice(request: Request, db: AsyncSession, issue: Issue, text: str) -> None:
    """项目打开了 Issue 提示时，在项目群里发一行（默认关闭，方案 8.5）。"""
    project = await db.get(Project, issue.project_id)
    if project is None or not project.issue_notices:
        return
    state = request.app.state
    client = openim_client(state.settings, state.connectors, state.redis)
    await task_claims.group_notice(db, client, project.id, text)


@router.get("/issue-templates", summary="Issue 的类型与正文模板（viewer）", responses=_read_errors)
async def issue_templates(access: Viewer) -> IssueTemplates:
    return IssueTemplates(items=templates())


@router.get(
    "/projects/{project_id}/issues",
    summary="项目的 Issues（viewer）",
    description="按最近动态排列；可以按状态、类型、标签、指派给我、提到我、关键词筛选。",
    responses=_read_errors,
)
async def list_project_issues(
    access: Viewer,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    project_id: UUID,
    state: IssueState | None = None,
    kind: IssueKind | None = None,
    label: Annotated[str | None, Query(max_length=30)] = None,
    assigned_to_me: bool = False,
    mentioned_me: bool = False,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> IssueList:
    service, who = _service(db, settings, request), _who(access, context)
    items, open_count, closed_count = await service.list_issues(
        who,
        project_id,
        state=state,
        kind=kind,
        label=label,
        assigned_to_me=assigned_to_me,
        mentioned_me=mentioned_me,
        q=q,
        limit=100,
    )
    return IssueList(
        items=await service.outs(who, items), open_count=open_count, closed_count=closed_count
    )


@router.post(
    "/projects/{project_id}/issues",
    status_code=status.HTTP_201_CREATED,
    summary="开 Issue（member）",
    description="按项目编号；正文为空且 use_template 时套用这种类型的模板。保存前过密钥扫描。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_issue(
    access: Member,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    project_id: UUID,
    body: IssueCreate,
) -> IssueDetail:
    service, who = _service(db, settings, request), _who(access, context)
    issue = await service.create(
        who,
        project_id,
        title=body.title,
        body=body.body,
        kind=body.kind,
        labels=body.labels,
        assignee_users=body.assignee_user_ids,
        assignee_instances=body.assignee_instance_ids,
        task_ids=body.task_ids,
        use_template=body.use_template,
    )
    await db.commit()
    await _notice(request, db, issue, f"#{issue.number} 新开：{issue.title}")
    return await service.detail(who, issue)


@router.get(
    "/projects/{project_id}/issues/{number}",
    summary="按项目内编号读 Issue（viewer）",
    description="正文和评论里的 `#编号` 链到这里。",
    responses=_read_errors,
)
async def get_issue_by_number(
    access: Viewer,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    project_id: UUID,
    number: Annotated[int, Path(ge=1, le=9_999_999)],
) -> IssueDetail:
    service, who = _service(db, settings, request), _who(access, context)
    await service.project(who, project_id)
    issue = await service.by_number(who, project_id, number)
    if issue is None:
        raise not_found()
    return await service.detail(who, issue)


@router.get("/issues/inbox", summary="我的 Issue 收件箱（viewer）", responses=_read_errors)
async def issue_inbox(
    access: Viewer, context: Context, db: Db, settings: AppSettings, request: Request
) -> IssueList:
    service, who = _service(db, settings, request), _who(access, context)
    items = await service.inbox(who)
    return IssueList(items=await service.outs(who, items), open_count=0, closed_count=0)


@router.get("/issues/{issue_id}", summary="Issue 详情（viewer）", responses=_read_errors)
async def get_issue(
    access: Viewer,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    issue_id: UUID,
) -> IssueDetail:
    service, who = _service(db, settings, request), _who(access, context)
    issue, _ = await service.issue(who, issue_id)
    return await service.detail(who, issue)


@router.post(
    "/issues/{issue_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="记为已读（viewer）",
    responses=_read_errors,
)
async def read_issue(
    access: Viewer,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    issue_id: UUID,
) -> Response:
    service, who = _service(db, settings, request), _who(access, context)
    issue, _ = await service.issue(who, issue_id)
    await service.mark_read(who, issue)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/issues/{issue_id}",
    summary="修改 Issue（member，乐观并发）",
    description="改标题、正文、类型、标签、指派、关联任务；关闭要写原因（重复时写明重复哪一个）；"
    "锁定只有 owner / admin。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_issue(
    access: Member,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    issue_id: UUID,
    body: IssueUpdate,
) -> IssueDetail:
    service, who = _service(db, settings, request), _who(access, context)
    before, _ = await service.issue(who, issue_id)
    was = before.state
    issue = await service.update(who, issue_id, body.model_dump(exclude_unset=True))
    await db.commit()
    if issue.state != was:
        verb = "已关闭" if issue.state == "closed" else "重新打开"
        await _notice(request, db, issue, f"#{issue.number} {verb}：{issue.title}")
    return await service.detail(who, issue)


@router.delete(
    "/issues/{issue_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除 Issue（admin，进回收站）",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_issue(
    access: Admin, context: Context, db: Db, settings: AppSettings, request: Request, issue_id: UUID
) -> Response:
    service, who = _service(db, settings, request), _who(access, context)
    await service.delete(who, issue_id)
    await AuditTrail(db, settings).record(
        "issues.issue.deleted",
        workspace_ids=[access.workspace.id],
        actor_id=context.user.id,
        resource_type="issue",
        resource_id=issue_id,
        client_ip=client_ip(request, settings),
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _comment_out(comment: IssueComment) -> CommentOut:
    return CommentOut(
        id=comment.id,
        reply_to=comment.reply_to,
        body=None if comment.hidden_at else comment.body,
        author=IssueService._author(comment),
        edited_at=comment.edited_at,
        hidden=comment.hidden_at is not None,
        created_at=comment.created_at,
    )


@router.post(
    "/issues/{issue_id}/comments",
    status_code=status.HTTP_201_CREATED,
    summary="评论（member）",
    description="可以回复某一条；`@名字` 提到项目里的人或智能体，`#编号` 链到同一项目的 Issue。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_comment(
    access: Member,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    issue_id: UUID,
    body: CommentCreate,
) -> CommentOut:
    service, who = _service(db, settings, request), _who(access, context)
    comment = await service.comment(who, issue_id, body=body.body, reply_to=body.reply_to)
    await db.commit()
    await db.refresh(comment)
    return _comment_out(comment)


@router.patch(
    "/issues/{issue_id}/comments/{comment_id}",
    summary="编辑自己的评论（member）",
    dependencies=_write,
    responses=_write_errors,
)
async def edit_comment(
    access: Member,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    issue_id: UUID,
    comment_id: UUID,
    body: CommentUpdate,
) -> CommentOut:
    service, who = _service(db, settings, request), _who(access, context)
    comment = await service.edit_comment(who, comment_id, body.body)
    if comment.issue_id != issue_id:
        raise not_found()
    await db.commit()
    await db.refresh(comment)
    return _comment_out(comment)


@router.post(
    "/issues/{issue_id}/comments/{comment_id}/hide",
    summary="隐藏评论（admin，不物理删除）",
    dependencies=_write,
    responses=_write_errors,
)
async def hide_comment(
    access: Admin,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    issue_id: UUID,
    comment_id: UUID,
) -> CommentOut:
    service, who = _service(db, settings, request), _who(access, context)
    comment = await service.hide_comment(who, comment_id)
    if comment.issue_id != issue_id:
        raise not_found()
    await db.commit()
    await db.refresh(comment)
    return _comment_out(comment)


@router.get(
    "/tasks/{task_id}/issues",
    summary="任务关联的 Issues（viewer）",
    responses=_read_errors,
)
async def task_issues(
    access: Viewer, context: Context, db: Db, settings: AppSettings, request: Request, task_id: UUID
) -> IssueList:
    task = await db.scalar(
        select(Task).where(Task.id == task_id, Task.workspace_id == access.workspace.id)
    )
    if task is None:
        raise not_found()
    service, who = _service(db, settings, request), _who(access, context)
    items = await service.for_task(task.id)
    outs = await service.outs(who, items)
    return IssueList(
        items=outs,
        open_count=sum(1 for i in outs if i.state == "open"),
        closed_count=sum(1 for i in outs if i.state == "closed"),
    )


@router.put(
    "/projects/{project_id}/issue-notices",
    summary="Issue 新开 / 关闭时在项目群里提示（admin，默认关闭）",
    dependencies=_write,
    responses=_write_errors,
)
async def put_issue_notices(
    access: Admin,
    context: Context,
    db: Db,
    settings: AppSettings,
    request: Request,
    project_id: UUID,
    body: IssueNoticesPut,
) -> IssueNoticesPut:
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.workspace_id == access.workspace.id)
    )
    if project is None:
        raise not_found()
    if project.issue_notices != body.enabled:
        project.issue_notices = body.enabled
        await AuditTrail(db, settings).record(
            "issues.notices_enabled" if body.enabled else "issues.notices_disabled",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="project",
            resource_id=project.id,
            client_ip=client_ip(request, settings),
        )
    await db.commit()
    return IssueNoticesPut(enabled=project.issue_notices)
