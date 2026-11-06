"""智能体的 Issue 工具（T-0123，docs/v2/AGENT_MESSAGING_PLAN.md 8.4）。

- issue_list / issue_get / issue_inbox：只读；issue_get 读完记为已读。
- issue_create / issue_comment / issue_update：直接执行（docs/AGENTS.md 第 8 节例外五），
  经 IssueService 写入，所以仍要过项目成员与读写权限、限额、连续评论暂停、密钥拦截；
  智能体不能锁定、隐藏、删除（这些不做成工具）。每次调用都写执行记录（正文只记长度）。
- 只能用在自己参与的项目上；别的工作空间、没参与的项目一律当作不存在。
- 带 client_key 的创建与评论：同一个实例 24 小时内用同一个键重试，返回第一次的结果、不重复写。
"""

import logging
import uuid
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.core.errors import ApiError
from app.domain import task_claims
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.issues import AGENT_STREAK_LIMIT, IssueService, Who
from app.integrations.openim import OpenIMClient
from app.models import AgentInstance, Issue, IssueAssignee, IssueComment, IssueTask, Project
from app.schemas.ai_tools import ToolSpec
from app.schemas.issue_tools import (
    IssueActorRef,
    IssueBodyText,
    IssueCommentInput,
    IssueCommentItem,
    IssueCommentOutput,
    IssueCommentText,
    IssueCreateInput,
    IssueCreateOutput,
    IssueEventItem,
    IssueEventText,
    IssueGetInput,
    IssueGetOutput,
    IssueInboxInput,
    IssueInboxOutput,
    IssueItemText,
    IssueListInput,
    IssueListOutput,
    IssueNameText,
    IssuePullItem,
    IssuePullText,
    IssueTaskItem,
    IssueTaskText,
    IssueToolItem,
    IssueUpdateInput,
    IssueUpdateOutput,
)
from app.schemas.issues import Actor, ProjectIssueOut

logger = logging.getLogger("app.issues")

COMMENTS_PER_PAGE = 50
EVENTS_SHOWN = 50
CLIENT_KEY_TTL = 24 * 3600
_MISSING = "没有这个 Issue 或项目，或者你没有参与这个项目"


def who_for(requester: Requester) -> Who:
    instance = requester.instance
    return Who(workspace_id=requester.workspace.id, name=instance.name, instance=instance)


def _actor(actor: Actor, me: uuid.UUID) -> IssueActorRef:
    return IssueActorRef(
        kind=actor.kind,
        id=actor.id,
        is_you=actor.kind == "instance" and actor.id == me,
        untrusted=IssueNameText(name=actor.name),
    )


def item(out: ProjectIssueOut, me: uuid.UUID) -> IssueToolItem:
    return IssueToolItem(
        issue_id=out.id,
        project_id=out.project_id,
        number=out.number,
        kind=out.kind,
        state=out.state,
        close_reason=out.close_reason,
        locked=out.locked,
        author=_actor(out.author, me),
        assignees=[_actor(a, me) for a in out.assignees],
        task_ids=[t.id for t in out.tasks],
        comment_count=out.comment_count,
        unread=out.unread,
        agent_paused=out.agent_paused,
        last_activity_at=out.last_activity_at,
        version=out.version,
        untrusted=IssueItemText(title=out.title, labels=out.labels),
    )


def _me(who: Who) -> uuid.UUID:
    if who.instance is None:  # 只有智能体调用这些工具
        raise ToolCallError("FORBIDDEN_ROLE", "只有智能体能调用 Issue 工具")
    return who.instance.id


async def _items(service: IssueService, who: Who, issues: list[Issue]) -> list[IssueToolItem]:
    me = _me(who)
    return [item(o, me) for o in await service.outs(who, issues)]


def _translate(error: ApiError) -> ToolCallError:
    if error.status_code == 404:
        return ToolCallError("NOT_FOUND", _MISSING)
    return ToolCallError(error.code, error.message)


async def _remembered(service: IssueService, key: str | None) -> uuid.UUID | None:
    if key is None or service.redis is None:
        return None
    raw = await service.redis.get(key)
    if raw is None:
        return None
    try:
        return uuid.UUID(raw.decode() if isinstance(raw, bytes) else str(raw))
    except ValueError:
        return None


async def _remember(service: IssueService, key: str | None, value: uuid.UUID) -> None:
    if key is not None and service.redis is not None:
        await service.redis.set(key, str(value), ex=CLIENT_KEY_TTL)


def _key(kind: str, who: Who, client_key: str | None) -> str | None:
    if client_key is None or who.instance is None:
        return None
    return f"issue_ck:{kind}:{who.instance.id}:{client_key}"


# ---------------------------------------------------------------- 读


async def _list(
    service: IssueService, who: Who, args: IssueListInput
) -> tuple[BaseModel, str, str | None]:
    issues, open_count, closed_count = await service.list_issues(
        who,
        args.project_id,
        state=None if args.state == "all" else args.state,
        kind=args.kind,
        label=args.label,
        assigned_to_me=args.assigned_to_me,
        mentioned_me=args.mentioned_me,
        q=args.query,
        limit=args.limit,
    )
    out = IssueListOutput(
        items=await _items(service, who, issues), open_count=open_count, closed_count=closed_count
    )
    return out, f"列出 Issue：{len(issues)} 个", None


async def _find(service: IssueService, who: Who, args: IssueGetInput) -> Issue:
    if args.issue_id is not None:
        found, _ = await service.issue(who, args.issue_id)
        return found
    if args.project_id is None or args.number is None:  # IssueGetInput 已经保证
        raise ToolCallError("VALIDATION_FAILED", "填 issue_id，或者 project_id 加 number")
    await service.project(who, args.project_id)
    by_number = await service.by_number(who, args.project_id, args.number)
    if by_number is None:
        raise ToolCallError("NOT_FOUND", _MISSING)
    return by_number


async def _get(
    service: IssueService, who: Who, args: IssueGetInput
) -> tuple[BaseModel, str, str | None]:
    me = _me(who)
    issue = await _find(service, who, args)
    detail = await service.detail(who, issue)
    start = args.comment_offset
    page = detail.comments[start : start + COMMENTS_PER_PAGE]
    more = start + COMMENTS_PER_PAGE < len(detail.comments)
    _, project = await service.issue(who, issue.id)
    writable = not project.archived and await service.agent_access(me, project.id) == "read_write"
    paused = issue.agent_streak >= AGENT_STREAK_LIMIT
    out = IssueGetOutput(
        issue=item(detail, me),
        untrusted=IssueBodyText(body=detail.body),
        comments=[
            IssueCommentItem(
                comment_id=c.id,
                reply_to=c.reply_to,
                author=_actor(c.author, me),
                hidden=c.hidden,
                edited_at=c.edited_at,
                created_at=c.created_at,
                untrusted=IssueCommentText(body=c.body),
            )
            for c in page
        ],
        next_comment_offset=start + COMMENTS_PER_PAGE if more else None,
        events=[
            IssueEventItem(
                kind=e.kind,
                actor=_actor(e.actor, me),
                created_at=e.created_at,
                untrusted=IssueEventText(data=e.data),
            )
            for e in detail.events[-EVENTS_SHOWN:]
        ],
        tasks=[
            IssueTaskItem(task_id=t.id, done=t.done, untrusted=IssueTaskText(title=t.title))
            for t in detail.tasks
        ],
        pulls=[
            IssuePullItem(
                state=p.state, merged=p.merged, untrusted=IssuePullText(title=p.title, url=p.url)
            )
            for p in detail.pulls
        ],
        can_close=detail.can_close and writable,
        can_comment=writable and not issue.locked and not paused,
        agent_comments_left=max(0, AGENT_STREAK_LIMIT - issue.agent_streak),
    )
    await service.mark_read(who, issue)
    return out, f"读 Issue #{issue.number}", None


async def _inbox(
    service: IssueService, who: Who, args: IssueInboxInput
) -> tuple[BaseModel, str, str | None]:
    issues = await service.inbox(who, args.limit)
    out = IssueInboxOutput(items=await _items(service, who, issues))
    return out, f"收件箱：{len(issues)} 个", None


# ---------------------------------------------------------------- 写（例外五）


async def _create(
    service: IssueService, who: Who, args: IssueCreateInput
) -> tuple[BaseModel, str, str | None]:
    key = _key("create", who, args.client_key)
    earlier = await _remembered(service, key)
    if earlier is not None:
        found = await service.db.get(Issue, earlier)
        if found is not None and found.deleted_at is None:
            out = IssueCreateOutput(
                status="already_created",
                issue_id=found.id,
                project_id=found.project_id,
                number=found.number,
                version=found.version,
            )
            return out, f"Issue #{found.number} 已经开过（同一个 client_key）", None
    issue = await service.create(
        who,
        args.project_id,
        title=args.title,
        body=args.body,
        kind=args.kind,
        labels=args.labels,
        assignee_users=args.assignee_user_ids,
        assignee_instances=args.assignee_instance_ids,
        task_ids=args.task_ids,
        use_template=args.use_template,
    )
    await _remember(service, key, issue.id)
    out = IssueCreateOutput(
        status="created",
        issue_id=issue.id,
        project_id=issue.project_id,
        number=issue.number,
        version=issue.version,
    )
    notice = f"#{issue.number} 新开：{issue.title}"
    return out, f"开 Issue #{issue.number}（{args.kind}）", notice


async def _comment(
    service: IssueService, who: Who, args: IssueCommentInput
) -> tuple[BaseModel, str, str | None]:
    key = _key("comment", who, args.client_key)
    earlier = await _remembered(service, key)
    if earlier is not None:
        found = await service.db.get(IssueComment, earlier)
        if found is not None and found.issue_id == args.issue_id:
            issue, _ = await service.issue(who, args.issue_id)
            out = IssueCommentOutput(
                status="already_commented",
                issue_id=issue.id,
                number=issue.number,
                comment_id=found.id,
                agent_comments_left=max(0, AGENT_STREAK_LIMIT - issue.agent_streak),
            )
            return out, f"评论 #{issue.number} 已经发过（同一个 client_key）", None
    comment = await service.comment(who, args.issue_id, body=args.body, reply_to=args.reply_to)
    await _remember(service, key, comment.id)
    issue, _ = await service.issue(who, args.issue_id)
    out = IssueCommentOutput(
        status="commented",
        issue_id=issue.id,
        number=issue.number,
        comment_id=comment.id,
        agent_comments_left=max(0, AGENT_STREAK_LIMIT - issue.agent_streak),
    )
    return out, f"评论 Issue #{issue.number}", None


def _merge(current: list[Any], add: list[Any], remove: list[Any]) -> list[Any]:
    out = [v for v in current if v not in remove]
    out.extend(v for v in add if v not in out)
    return out


async def _update(
    service: IssueService, who: Who, args: IssueUpdateInput
) -> tuple[BaseModel, str, str | None]:
    issue, _ = await service.issue(who, args.issue_id)
    before, was = issue.version, issue.state
    changes: dict[str, Any] = {}
    if args.version is not None:
        changes["version"] = args.version
    for name in ("title", "kind", "state", "close_reason", "duplicate_of"):
        value = getattr(args, name)
        if value is not None:
            changes[name] = value
    if args.add_labels or args.remove_labels:
        changes["labels"] = _merge(list(issue.labels), args.add_labels, args.remove_labels)
    assignees = (
        await service.db.execute(
            select(IssueAssignee.user_id, IssueAssignee.instance_id).where(
                IssueAssignee.issue_id == issue.id
            )
        )
    ).all()
    if args.add_assignee_user_ids or args.remove_assignee_user_ids:
        changes["assignee_user_ids"] = _merge(
            [u for u, _ in assignees if u],
            args.add_assignee_user_ids,
            args.remove_assignee_user_ids,
        )
    if args.add_assignee_instance_ids or args.remove_assignee_instance_ids:
        changes["assignee_instance_ids"] = _merge(
            [i for _, i in assignees if i],
            args.add_assignee_instance_ids,
            args.remove_assignee_instance_ids,
        )
    if args.link_task_ids or args.unlink_task_ids:
        linked = list(
            await service.db.scalars(
                select(IssueTask.task_id)
                .where(IssueTask.issue_id == issue.id)
                .order_by(IssueTask.created_at)
            )
        )
        changes["task_ids"] = _merge(linked, args.link_task_ids, args.unlink_task_ids)
    updated = await service.update(who, issue.id, changes)
    (out,) = await _items(service, who, [updated])
    status = "updated" if updated.version != before else "unchanged"
    notice = None
    if updated.state != was:
        verb = "已关闭" if updated.state == "closed" else "重新打开"
        notice = f"#{updated.number} {verb}：{updated.title}"
    summary = f"更新 Issue #{updated.number}：{status}"
    return IssueUpdateOutput(status=status, issue=out), summary, notice


_HANDLERS: dict[str, Any] = {
    "issue_list": _list,
    "issue_get": _get,
    "issue_inbox": _inbox,
    "issue_create": _create,
    "issue_comment": _comment,
    "issue_update": _update,
}
ISSUE_HANDLER_NAMES = frozenset(_HANDLERS)


def _arguments(parsed: BaseModel) -> dict[str, Any]:
    """执行记录里的参数：正文只记长度。"""
    arguments = parsed.model_dump(mode="json")
    if "body" in arguments:
        arguments["body"] = f"（{len(arguments['body'])} 字）"
    return arguments


async def run(
    service: AiToolService,
    requester: Requester,
    spec: ToolSpec,
    raw: dict[str, Any],
    *,
    request_id: str | None,
    im: OpenIMClient | None = None,
) -> BaseModel:
    try:
        parsed = spec.input_model.model_validate(raw)
    except ValidationError as error:
        raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
    arguments = _arguments(parsed)
    denied = service.check(requester, spec)
    if denied is not None:
        await service.record_denied(requester, spec, arguments, denied, request_id=request_id)
        raise ToolCallError(denied, "这个实例没有权限调用这个工具")
    instance_id = requester.instance.id
    issues = IssueService(service.db, service.settings, service.redis)
    try:
        output, summary, notice = await _HANDLERS[spec.name](issues, who_for(requester), parsed)
        project_id = getattr(output, "project_id", None) or getattr(
            getattr(output, "issue", None), "project_id", None
        )
    except (ToolCallError, ApiError) as raised:
        failure = raised if isinstance(raised, ToolCallError) else _translate(raised)
        await service.db.rollback()
        instance = await service.db.get(AgentInstance, instance_id)
        if instance is not None:
            await service.record_knowledge(
                await service.requester(instance),
                spec,
                arguments,
                ok=False,
                summary=f"{spec.name}：{failure.code}",
                error_code=failure.code,
                request_id=request_id,
            )
        raise failure from None
    await service.record_knowledge(
        requester, spec, arguments, ok=True, summary=summary, request_id=request_id
    )
    if notice and project_id is not None:
        await _notice(service, im, project_id, notice)
    result: BaseModel = output
    return result


async def _notice(
    service: AiToolService, im: OpenIMClient | None, project_id: uuid.UUID, text: str
) -> None:
    """项目打开了 Issue 提示时，在项目群里发一行（默认关闭，方案 8.5）；发不出去不影响结果。"""
    project = await service.db.get(Project, project_id)
    if project is None or not project.issue_notices:
        return
    try:
        await task_claims.group_notice(service.db, im, project_id, text)
    except Exception:  # pragma: no cover - 群提示失败只记日志
        logger.exception("issue group notice failed", extra={"project_id": str(project_id)})


__all__ = ["ISSUE_HANDLER_NAMES", "item", "run", "who_for"]
