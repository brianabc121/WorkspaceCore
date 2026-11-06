"""项目里的 Issues（T-0123，docs/v2/AGENT_MESSAGING_PLAN.md 第 8 节）。

人和智能体都用这里：页面接口（app/api/v1/issues.py）与 MCP 工具（app/domain/issue_tools.py）。

权限（8.3）：
- 人：能看项目的都能看（viewer）；member 能开、评论、改标签、指派、关联任务，
  关闭 / 重新打开自己开的或指派给自己的；
  owner / admin 能关闭任何 Issue、锁定、隐藏评论、删除（进回收站）。
- 智能体：只在自己参与的项目里；读写参与的能开、评论、指派（项目里的智能体和管理员）、改标签、
  关联本项目的任务，
  关闭 / 重新打开自己开的或指派给自己的；只读参与的只能读；都不能锁定、隐藏、删除；锁定后不能评论。
- 归档的项目只读；删除的项目 Issue 一起删除（外键级联）。

保护（8.6）：连续 10 条评论来自智能体（中间没有人）时智能体暂停评论；
每个实例每小时最多 10 个 Issue、60 条评论；
正文和评论保存前过密钥扫描。通知人进通知中心；智能体被提到、被指派、自己开的或指派给自己的有新评论时进收件箱，
同一个实例 5 分钟内的动态合并成一次唤醒（提示词不含别人写的正文，D13）。
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import agent_wakeups, notifications, secret_scan
from app.domain.ratelimit import WindowLimiter
from app.models import (
    AgentInstance,
    AgentWakeup,
    ExternalResource,
    Issue,
    IssueAssignee,
    IssueComment,
    IssueEvent,
    IssueMention,
    IssueRead,
    IssueTask,
    Project,
    ProjectAgent,
    Task,
    User,
    WorkspaceMember,
)
from app.schemas.issues import (
    Actor,
    CommentOut,
    IssueDetail,
    IssueEventOut,
    IssueTaskRef,
    IssueTemplate,
    ProjectIssueOut,
    PullRef,
)

AGENT_STREAK_LIMIT = 10
ISSUES_PER_HOUR = 10
COMMENTS_PER_HOUR = 60
WAKE_DELAY = timedelta(minutes=5)
MAX_ASSIGNEES = 20
KIND_LABELS = {
    "question": "提问",
    "proposal": "提议",
    "bug": "缺陷",
    "contract": "接口约定",
    "handoff": "交接",
    "review": "评审请求",
    "blocker": "阻塞",
    "other": "其他",
}
TEMPLATES = {
    "question": "## 问题\n\n\n## 背景\n\n\n## 希望谁回答\n\n",
    "proposal": "## 提议\n\n\n## 为什么\n\n\n## 影响\n\n",
    "bug": "## 现象\n\n\n## 复现步骤\n\n1. \n\n## 期望\n\n\n## 环境\n\n",
    "contract": (
        "## 调用方\n\n\n## 提供方\n\n\n## 接口与字段\n\n\n## 错误\n\n\n"
        "## 谁实现、什么时候\n\n\n## 仓库 / 分支 / PR\n\n"
    ),
    "handoff": "## 做到哪了\n\n\n## 怎么验证\n\n\n## 下一步\n\n\n## 注意事项\n\n",
    "review": "## 仓库 / 分支 / PR\n\n\n## 要看的地方\n\n\n## 验收标准\n\n- [ ] \n",
    "blocker": "## 被什么挡住\n\n\n## 需要谁做什么\n\n\n## 影响的任务\n\n",
    "other": "",
}
_MENTION = re.compile(r"(?<![\w@])@([^\s@#,，。:：;；()（）\[\]<>]{1,40})")
_REF = re.compile(r"(?<![\w&])#(\d{1,7})\b")
_PULL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


def _now() -> datetime:
    return datetime.now(UTC)


def _err(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message)


def templates() -> list[IssueTemplate]:
    return [IssueTemplate(kind=k, label=KIND_LABELS[k], body=TEMPLATES[k]) for k in KIND_LABELS]


@dataclass
class Who:
    """谁在操作：人（user_id + 角色）或智能体（instance + 在这个项目里的读写权限）。"""

    workspace_id: uuid.UUID
    name: str
    user_id: uuid.UUID | None = None
    role: str | None = None
    instance: AgentInstance | None = None
    redis: Redis | None = field(default=None, repr=False)

    @property
    def is_agent(self) -> bool:
        return self.instance is not None

    @property
    def is_admin(self) -> bool:
        return not self.is_agent and self.role in ("owner", "admin")

    @property
    def is_member(self) -> bool:
        return self.is_agent or self.role in ("owner", "admin", "member")

    def actor(self) -> Actor:
        if self.instance is not None:
            return Actor(kind="instance", id=self.instance.id, name=self.instance.name)
        return Actor(kind="user", id=self.user_id, name=self.name)


def _labels(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        clean = " ".join(value.split())[:30]
        if clean and clean not in out:
            out.append(clean)
    return out[:10]


class IssueService:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis

    # ------------------------------------------------------------ 查找与权限

    async def project(self, who: Who, project_id: uuid.UUID) -> Project:
        project = await self.db.scalar(
            select(Project).where(
                Project.id == project_id, Project.workspace_id == who.workspace_id
            )
        )
        if project is None or not await self.can_read(who, project.id):
            raise _err(404, "NOT_FOUND", "没有这个项目，或者你没有参与这个项目")
        return project

    async def agent_access(self, instance_id: uuid.UUID, project_id: uuid.UUID) -> str | None:
        access: str | None = await self.db.scalar(
            select(ProjectAgent.access).where(
                ProjectAgent.project_id == project_id, ProjectAgent.instance_id == instance_id
            )
        )
        return access

    async def can_read(self, who: Who, project_id: uuid.UUID) -> bool:
        if who.instance is None:
            return True
        return await self.agent_access(who.instance.id, project_id) is not None

    async def _check_write(self, who: Who, project: Project) -> None:
        if project.archived:
            raise _err(409, "PROJECT_ARCHIVED", "项目已归档，Issue 只读")
        if who.instance is not None:
            if await self.agent_access(who.instance.id, project.id) != "read_write":
                raise _err(403, "READ_ONLY", "你在这个项目里只有读权限")
        elif not who.is_member:
            raise _err(403, "FORBIDDEN_ROLE", "只读角色不能修改 Issue")

    async def issue(
        self, who: Who, issue_id: uuid.UUID, *, lock: bool = False
    ) -> tuple[Issue, Project]:
        query = select(Issue).where(
            Issue.id == issue_id, Issue.workspace_id == who.workspace_id, Issue.deleted_at.is_(None)
        )
        found = await self.db.scalar(query.with_for_update() if lock else query)
        if found is None or not await self.can_read(who, found.project_id):
            raise _err(404, "NOT_FOUND", "没有这个 Issue，或者你没有参与它所在的项目")
        project = await self.db.get(Project, found.project_id)
        if project is None:
            raise _err(404, "NOT_FOUND", "没有这个 Issue")
        return found, project

    async def by_number(self, who: Who, project_id: uuid.UUID, number: int) -> Issue | None:
        found: Issue | None = await self.db.scalar(
            select(Issue).where(
                Issue.project_id == project_id,
                Issue.number == number,
                Issue.workspace_id == who.workspace_id,
                Issue.deleted_at.is_(None),
            )
        )
        return found

    def _scan(self, *texts: str) -> None:
        if secret_scan.scan(*texts):
            raise _err(
                422,
                "SECRET_BLOCKED",
                "内容里像是有令牌、私钥一类的密钥，没有保存；去掉之后再提交",
            )

    async def _limit(self, who: Who, kind: str, limit: int, label: str) -> None:
        if who.instance is None or self.redis is None:
            return
        key = f"issue_{kind}:{who.instance.id}"
        if await WindowLimiter(self.redis, 3600).hit(key, limit) is not None:
            raise _err(429, "RATE_LIMITED", f"每个智能体每小时最多{label}")

    # ------------------------------------------------------------ 指派、提及与任务

    async def _admins(self, workspace_id: uuid.UUID) -> set[uuid.UUID]:
        rows = await self.db.scalars(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role.in_(("owner", "admin")),
            )
        )
        return set(rows)

    async def _check_assignees(
        self,
        who: Who,
        project: Project,
        users: list[uuid.UUID],
        instances: list[uuid.UUID],
        *,
        kept_users: frozenset[uuid.UUID] = frozenset(),
        kept_instances: frozenset[uuid.UUID] = frozenset(),
    ) -> None:
        """检查新加的指派；原来就指派着的（kept_*）不再检查，免得改标签时被以前的指派挡住。"""
        if len(set(users)) + len(set(instances)) > MAX_ASSIGNEES:
            raise _err(422, "VALIDATION_FAILED", f"最多指派 {MAX_ASSIGNEES} 个")
        new_users, new_instances = set(users) - kept_users, set(instances) - kept_instances
        members = set(
            await self.db.scalars(
                select(WorkspaceMember.user_id).where(
                    WorkspaceMember.workspace_id == project.workspace_id,
                    WorkspaceMember.user_id.in_(new_users or [uuid.uuid4()]),
                )
            )
        )
        if new_users - members:
            raise _err(422, "VALIDATION_FAILED", "只能指派给本工作空间的成员")
        if who.is_agent and new_users - await self._admins(project.workspace_id):
            raise _err(422, "VALIDATION_FAILED", "智能体只能指派给管理员或项目里的智能体")
        agents = set(
            await self.db.scalars(
                select(ProjectAgent.instance_id).where(
                    ProjectAgent.project_id == project.id,
                    ProjectAgent.instance_id.in_(new_instances or [uuid.uuid4()]),
                )
            )
        )
        if new_instances - agents:
            raise _err(422, "VALIDATION_FAILED", "只能指派给参与这个项目的智能体")

    async def _check_tasks(self, project: Project, task_ids: list[uuid.UUID]) -> list[Task]:
        if not task_ids:
            return []
        tasks = list(
            await self.db.scalars(
                select(Task).where(Task.id.in_(task_ids), Task.project_id == project.id)
            )
        )
        if len(tasks) != len(set(task_ids)):
            raise _err(422, "VALIDATION_FAILED", "只能关联这个项目里的任务")
        return tasks

    async def _people(self, project: Project) -> tuple[dict[str, uuid.UUID], dict[str, uuid.UUID]]:
        """@ 能提到的：工作空间的人（按名字）与参与项目的智能体（按名字）。名字不分大小写。"""
        users = {
            (name or "").casefold(): uid
            for uid, name in (
                await self.db.execute(
                    select(User.id, User.display_name)
                    .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                    .where(WorkspaceMember.workspace_id == project.workspace_id)
                )
            ).all()
        }
        agents = {
            name.casefold(): iid
            for iid, name in (
                await self.db.execute(
                    select(AgentInstance.id, AgentInstance.name)
                    .join(ProjectAgent, ProjectAgent.instance_id == AgentInstance.id)
                    .where(ProjectAgent.project_id == project.id)
                )
            ).all()
        }
        return users, agents

    async def _mentions(
        self, who: Who, issue: Issue, project: Project, text: str, comment: IssueComment | None
    ) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
        """记下 @ 到的人和智能体，以及 #编号 提到的其他 Issue（那边的时间线记“被提到”）。"""
        users, agents = await self._people(project)
        hit_users: set[uuid.UUID] = set()
        hit_agents: set[uuid.UUID] = set()
        for name in {m.group(1).casefold() for m in _MENTION.finditer(text)}:
            if name in agents:
                hit_agents.add(agents[name])
            elif name in users:
                hit_users.add(users[name])
        if who.user_id is not None:
            hit_users.discard(who.user_id)
        if who.instance is not None:
            hit_agents.discard(who.instance.id)
        for uid in hit_users:
            self.db.add(
                IssueMention(issue_id=issue.id, comment_id=comment and comment.id, user_id=uid)
            )
        for iid in hit_agents:
            self.db.add(
                IssueMention(issue_id=issue.id, comment_id=comment and comment.id, instance_id=iid)
            )
        for number in {int(m.group(1)) for m in _REF.finditer(text)}:
            if number == issue.number:
                continue
            other = await self.by_number(who, project.id, number)
            if other is not None:
                self._event(
                    who, other, "mentioned", {"issue_id": str(issue.id), "number": issue.number}
                )
        return hit_users, hit_agents

    def _event(self, who: Who, issue: Issue, kind: str, data: dict[str, Any] | None = None) -> None:
        self.db.add(
            IssueEvent(
                workspace_id=issue.workspace_id,
                issue_id=issue.id,
                kind=kind,
                actor_kind="instance" if who.is_agent else "user",
                user_id=who.user_id if not who.is_agent else None,
                instance_id=who.instance.id if who.instance else None,
                actor_name=who.name,
                data=data or {},
            )
        )

    async def _set_assignees(
        self, who: Who, issue: Issue, users: list[uuid.UUID], instances: list[uuid.UUID]
    ) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
        current = list(
            await self.db.scalars(select(IssueAssignee).where(IssueAssignee.issue_id == issue.id))
        )
        old_users = {a.user_id for a in current if a.user_id}
        old_agents = {a.instance_id for a in current if a.instance_id}
        new_users, new_agents = set(users), set(instances)
        for a in current:
            if (a.user_id and a.user_id not in new_users) or (
                a.instance_id and a.instance_id not in new_agents
            ):
                await self.db.delete(a)
                self._event(
                    who,
                    issue,
                    "unassigned",
                    {
                        "user_id": str(a.user_id) if a.user_id else None,
                        "instance_id": str(a.instance_id) if a.instance_id else None,
                    },
                )
        added_users, added_agents = new_users - old_users, new_agents - old_agents
        for uid in added_users:
            self.db.add(IssueAssignee(issue_id=issue.id, user_id=uid))
            self._event(who, issue, "assigned", {"user_id": str(uid)})
        for iid in added_agents:
            self.db.add(IssueAssignee(issue_id=issue.id, instance_id=iid))
            self._event(who, issue, "assigned", {"instance_id": str(iid)})
        return added_users, added_agents

    async def _set_tasks(self, who: Who, issue: Issue, tasks: list[Task]) -> None:
        current = list(
            await self.db.scalars(select(IssueTask).where(IssueTask.issue_id == issue.id))
        )
        wanted = {t.id: t for t in tasks}
        for link in current:
            if link.task_id not in wanted:
                await self.db.delete(link)
                self._event(who, issue, "unlinked", {"task_id": str(link.task_id)})
        have = {link.task_id for link in current}
        for task_id, task in wanted.items():
            if task_id not in have:
                self.db.add(IssueTask(issue_id=issue.id, task_id=task_id))
                self._event(
                    who, issue, "linked", {"task_id": str(task_id), "title": task.title[:100]}
                )

    # ------------------------------------------------------------ 新建、评论、修改

    async def create(
        self,
        who: Who,
        project_id: uuid.UUID,
        *,
        title: str,
        body: str,
        kind: str,
        labels: list[str],
        assignee_users: list[uuid.UUID],
        assignee_instances: list[uuid.UUID],
        task_ids: list[uuid.UUID],
        use_template: bool = False,
    ) -> Issue:
        project = await self.project(who, project_id)
        await self._check_write(who, project)
        if use_template and not body.strip():
            body = TEMPLATES.get(kind, "")
        self._scan(title, body)
        await self._check_assignees(who, project, assignee_users, assignee_instances)
        tasks = await self._check_tasks(project, task_ids)
        await self._limit(who, "create", ISSUES_PER_HOUR, " 10 个 Issue")
        # 按项目编号：锁住项目行，取最大编号加一
        await self.db.scalar(select(Project.id).where(Project.id == project.id).with_for_update())
        number = (
            int(
                await self.db.scalar(
                    select(func.coalesce(func.max(Issue.number), 0)).where(
                        Issue.project_id == project.id
                    )
                )
                or 0
            )
            + 1
        )
        issue = Issue(
            workspace_id=project.workspace_id,
            project_id=project.id,
            number=number,
            title=title,
            body=body,
            kind=kind,
            labels=_labels(labels),
            author_user_id=who.user_id if not who.is_agent else None,
            author_instance_id=who.instance.id if who.instance else None,
            author_name=who.name,
        )
        self.db.add(issue)
        await self.db.flush()
        self._event(who, issue, "opened", {"kind": kind})
        added_users, added_agents = await self._set_assignees(
            who, issue, assignee_users, assignee_instances
        )
        await self._set_tasks(who, issue, tasks)
        hit_users, hit_agents = await self._mentions(who, issue, project, f"{title}\n{body}", None)
        await self._read(who, issue)
        await self._tell(
            who,
            issue,
            users=added_users | hit_users,
            agents=added_agents | hit_agents,
            what=f"{who.name} 开了 #{issue.number}",
        )
        await self.db.flush()
        return issue

    async def comment(
        self, who: Who, issue_id: uuid.UUID, *, body: str, reply_to: uuid.UUID | None
    ) -> IssueComment:
        issue, project = await self.issue(who, issue_id, lock=True)
        await self._check_write(who, project)
        if issue.locked and who.is_agent:
            raise _err(403, "ISSUE_LOCKED", "这个 Issue 已锁定，只有人能评论")
        if who.is_agent and issue.agent_streak >= AGENT_STREAK_LIMIT:
            raise _err(
                409,
                "ISSUE_PAUSED",
                f"这个 Issue 里已经连续 {AGENT_STREAK_LIMIT} 条评论来自智能体，暂停智能体评论；"
                "等人评论后再继续，急事在 IM 里找管理员",
            )
        self._scan(body)
        if reply_to is not None:
            parent = await self.db.scalar(
                select(IssueComment.id).where(
                    IssueComment.id == reply_to, IssueComment.issue_id == issue.id
                )
            )
            if parent is None:
                raise _err(422, "VALIDATION_FAILED", "要回复的评论不在这个 Issue 里")
        await self._limit(who, "comment", COMMENTS_PER_HOUR, " 60 条评论")
        comment = IssueComment(
            workspace_id=issue.workspace_id,
            issue_id=issue.id,
            reply_to=reply_to,
            body=body,
            author_user_id=who.user_id if not who.is_agent else None,
            author_instance_id=who.instance.id if who.instance else None,
            author_name=who.name,
        )
        self.db.add(comment)
        await self.db.flush()
        issue.agent_streak = issue.agent_streak + 1 if who.is_agent else 0
        issue.comment_count += 1
        issue.last_activity_at = _now()
        hit_users, hit_agents = await self._mentions(who, issue, project, body, comment)
        # 有新评论：作者与被指派的（人进通知中心，智能体进收件箱并合并唤醒）
        assignees = list(
            (
                await self.db.execute(
                    select(IssueAssignee.user_id, IssueAssignee.instance_id).where(
                        IssueAssignee.issue_id == issue.id
                    )
                )
            ).all()
        )
        users = {u for u, _ in assignees if u} | hit_users
        agents = {i for _, i in assignees if i} | hit_agents
        if issue.author_user_id:
            users.add(issue.author_user_id)
        if issue.author_instance_id:
            agents.add(issue.author_instance_id)
        await self._read(who, issue)
        await self._tell(
            who, issue, users=users, agents=agents, what=f"{who.name} 评论了 #{issue.number}"
        )
        await self.db.flush()
        return comment

    def can_close(self, who: Who, issue: Issue, assigned: bool) -> bool:
        if who.is_admin:
            return True
        mine = (who.user_id and issue.author_user_id == who.user_id) or (
            who.instance is not None and issue.author_instance_id == who.instance.id
        )
        return bool(mine or assigned) and who.is_member

    async def _assigned(self, who: Who, issue: Issue) -> bool:
        cond = (
            IssueAssignee.instance_id == who.instance.id
            if who.instance is not None
            else IssueAssignee.user_id == who.user_id
        )
        found = await self.db.scalar(
            select(IssueAssignee.id).where(IssueAssignee.issue_id == issue.id, cond)
        )
        return found is not None

    async def update(self, who: Who, issue_id: uuid.UUID, changes: dict[str, Any]) -> Issue:
        issue, project = await self.issue(who, issue_id, lock=True)
        await self._check_write(who, project)
        version = changes.pop("version", None)
        if version is not None and version != issue.version:
            raise _err(409, "VERSION_CONFLICT", "Issue 已被别人修改，请刷新后再试")
        touched = False
        tell_users: set[uuid.UUID] = set()
        tell_agents: set[uuid.UUID] = set()
        if "title" in changes and changes["title"] != issue.title:
            self._scan(changes["title"])
            self._event(who, issue, "retitled", {"from": issue.title[:200]})
            issue.title = changes["title"]
            touched = True
        if "body" in changes and changes["body"] != issue.body:
            self._scan(changes["body"])
            issue.body = changes["body"]
            hu, ha = await self._mentions(who, issue, project, issue.body, None)
            tell_users |= hu
            tell_agents |= ha
            touched = True
        if "kind" in changes and changes["kind"] != issue.kind:
            issue.kind = changes["kind"]
            touched = True
        if "labels" in changes:
            new = _labels(changes["labels"])
            for label in set(new) - set(issue.labels):
                self._event(who, issue, "labeled", {"label": label})
            for label in set(issue.labels) - set(new):
                self._event(who, issue, "unlabeled", {"label": label})
            if new != issue.labels:
                issue.labels = new
                touched = True
        if "assignee_user_ids" in changes or "assignee_instance_ids" in changes:
            current = list(
                (
                    await self.db.execute(
                        select(IssueAssignee.user_id, IssueAssignee.instance_id).where(
                            IssueAssignee.issue_id == issue.id
                        )
                    )
                ).all()
            )
            users = changes.get("assignee_user_ids")
            agents = changes.get("assignee_instance_ids")
            users = list(users) if users is not None else [u for u, _ in current if u]
            agents = list(agents) if agents is not None else [i for _, i in current if i]
            await self._check_assignees(
                who,
                project,
                users,
                agents,
                kept_users=frozenset(u for u, _ in current if u),
                kept_instances=frozenset(i for _, i in current if i),
            )
            au, aa = await self._set_assignees(who, issue, users, agents)
            tell_users |= au
            tell_agents |= aa
            touched = touched or bool(au or aa) or len(current) != len(users) + len(agents)
        if "task_ids" in changes:
            await self._set_tasks(who, issue, await self._check_tasks(project, changes["task_ids"]))
            touched = True
        if "locked" in changes and changes["locked"] != issue.locked:
            if not who.is_admin:
                raise _err(403, "FORBIDDEN_ROLE", "只有 owner / admin 能锁定或解锁")
            issue.locked = bool(changes["locked"])
            self._event(who, issue, "locked" if issue.locked else "unlocked")
            touched = True
        state = changes.get("state")
        if state is not None and state != issue.state:
            if not self.can_close(who, issue, await self._assigned(who, issue)):
                raise _err(403, "FORBIDDEN_ROLE", "只能关闭或重新打开自己开的、指派给自己的 Issue")
            if state == "closed":
                reason = changes.get("close_reason") or "resolved"
                duplicate = None
                if reason == "duplicate":
                    target = await self.by_number(who, project.id, int(changes["duplicate_of"]))
                    if target is None or target.id == issue.id:
                        raise _err(422, "VALIDATION_FAILED", "重复的那个 Issue 不存在")
                    duplicate = target.id
                issue.state, issue.close_reason, issue.duplicate_of = "closed", reason, duplicate
                issue.closed_at = _now()
                self._event(
                    who,
                    issue,
                    "closed",
                    {"reason": reason, "duplicate_of": str(duplicate) if duplicate else None},
                )
            else:
                issue.state, issue.close_reason, issue.duplicate_of, issue.closed_at = (
                    "open",
                    None,
                    None,
                    None,
                )
                self._event(who, issue, "reopened")
            touched = True
        if touched:
            issue.version += 1
            issue.last_activity_at = _now()
        await self._read(who, issue)
        if tell_users or tell_agents:
            await self._tell(
                who,
                issue,
                users=tell_users,
                agents=tell_agents,
                what=f"{who.name} 更新了 #{issue.number}",
            )
        await self.db.flush()
        return issue

    async def edit_comment(self, who: Who, comment_id: uuid.UUID, body: str) -> IssueComment:
        comment = await self.db.scalar(
            select(IssueComment).where(
                IssueComment.id == comment_id, IssueComment.workspace_id == who.workspace_id
            )
        )
        if comment is None:
            raise _err(404, "NOT_FOUND", "没有这条评论")
        _, project = await self.issue(who, comment.issue_id)
        await self._check_write(who, project)
        mine = (who.user_id and comment.author_user_id == who.user_id) or (
            who.instance is not None and comment.author_instance_id == who.instance.id
        )
        if not mine or comment.hidden_at is not None:
            raise _err(403, "FORBIDDEN_ROLE", "只能编辑自己的评论")
        self._scan(body)
        comment.body, comment.edited_at = body, _now()
        await self.db.flush()
        return comment

    async def hide_comment(self, who: Who, comment_id: uuid.UUID) -> IssueComment:
        if not who.is_admin:
            raise _err(403, "FORBIDDEN_ROLE", "只有 owner / admin 能隐藏评论")
        comment = await self.db.scalar(
            select(IssueComment).where(
                IssueComment.id == comment_id, IssueComment.workspace_id == who.workspace_id
            )
        )
        if comment is None:
            raise _err(404, "NOT_FOUND", "没有这条评论")
        if comment.hidden_at is None:
            comment.hidden_at, comment.hidden_by = _now(), who.user_id
        await self.db.flush()
        return comment

    async def delete(self, who: Who, issue_id: uuid.UUID) -> None:
        if not who.is_admin:
            raise _err(403, "FORBIDDEN_ROLE", "只有 owner / admin 能删除 Issue")
        issue, _ = await self.issue(who, issue_id, lock=True)
        issue.deleted_at = _now()
        await self.db.flush()

    # ------------------------------------------------------------ 已读、通知、唤醒

    async def _read(self, who: Who, issue: Issue) -> None:
        cond = (
            IssueRead.instance_id == who.instance.id
            if who.instance is not None
            else IssueRead.user_id == who.user_id
        )
        row = await self.db.scalar(select(IssueRead).where(IssueRead.issue_id == issue.id, cond))
        now = _now()
        if row is None:
            self.db.add(
                IssueRead(
                    issue_id=issue.id,
                    user_id=who.user_id if not who.is_agent else None,
                    instance_id=who.instance.id if who.instance else None,
                    read_at=now,
                )
            )
        else:
            row.read_at = now

    async def mark_read(self, who: Who, issue: Issue) -> None:
        await self._read(who, issue)
        await self.db.flush()

    async def _tell(
        self, who: Who, issue: Issue, *, users: set[uuid.UUID], agents: set[uuid.UUID], what: str
    ) -> None:
        if who.user_id:
            users = users - {who.user_id}
        if who.instance is not None:
            agents = agents - {who.instance.id}
        for uid in users:
            await notifications.notify_user(
                self.db,
                issue.workspace_id,
                uid,
                type_="issue.activity",
                severity="info",
                title=what,
                body=issue.title[:200],
                link=f"/issues/{issue.id}",
                key=f"{issue.id}:{uid}:{issue.comment_count}:{issue.version}",
            )
        await self.wake(issue.workspace_id, agents)

    async def wake(self, workspace_id: uuid.UUID, instance_ids: set[uuid.UUID]) -> None:
        """同一个实例 5 分钟内的 Issue 动态合并成一次唤醒；提示词不含别人写的正文（D13）。"""
        if not instance_ids:
            return
        live = set(
            await self.db.scalars(
                select(AgentInstance.id).where(
                    AgentInstance.id.in_(instance_ids),
                    AgentInstance.workspace_id == workspace_id,
                    AgentInstance.status != "disabled",
                )
            )
        )
        now = _now()
        for instance_id in live:
            pending = await self.db.scalar(
                select(AgentWakeup)
                .where(
                    AgentWakeup.instance_id == instance_id,
                    AgentWakeup.source == "issue",
                    AgentWakeup.status == "pending",
                )
                .with_for_update()
            )
            if pending is not None:
                pending.prompt = _wake_prompt(_wake_count(pending.prompt) + 1)
                continue
            wakeup_id = uuid.uuid4()
            self.db.add(
                AgentWakeup(
                    id=wakeup_id,
                    workspace_id=workspace_id,
                    instance_id=instance_id,
                    source="issue",
                    target=None,
                    session_key=agent_wakeups.session_key(None, "", wakeup_id),
                    prompt=_wake_prompt(1),
                    run_at=now + WAKE_DELAY,
                )
            )

    # ------------------------------------------------------------ 输出

    async def _actors(self, issues: list[Issue]) -> dict[uuid.UUID, list[Actor]]:
        if not issues:
            return {}
        rows = (
            await self.db.execute(
                select(
                    IssueAssignee.issue_id,
                    IssueAssignee.user_id,
                    IssueAssignee.instance_id,
                    User.display_name,
                    AgentInstance.name,
                )
                .outerjoin(User, User.id == IssueAssignee.user_id)
                .outerjoin(AgentInstance, AgentInstance.id == IssueAssignee.instance_id)
                .where(IssueAssignee.issue_id.in_([i.id for i in issues]))
                .order_by(IssueAssignee.created_at)
            )
        ).all()
        out: dict[uuid.UUID, list[Actor]] = {}
        for issue_id, uid, iid, uname, iname in rows:
            actor = (
                Actor(kind="instance", id=iid, name=iname or "已删除的实例")
                if iid
                else Actor(kind="user", id=uid, name=uname or "已删除的用户")
            )
            out.setdefault(issue_id, []).append(actor)
        return out

    async def _tasks(self, issues: list[Issue]) -> dict[uuid.UUID, list[IssueTaskRef]]:
        if not issues:
            return {}
        rows = (
            await self.db.execute(
                select(IssueTask.issue_id, Task.id, Task.title, Task.done)
                .join(Task, Task.id == IssueTask.task_id)
                .where(IssueTask.issue_id.in_([i.id for i in issues]))
                .order_by(IssueTask.created_at)
            )
        ).all()
        out: dict[uuid.UUID, list[IssueTaskRef]] = {}
        for issue_id, tid, title, done in rows:
            out.setdefault(issue_id, []).append(IssueTaskRef(id=tid, title=title, done=done))
        return out

    async def _reads(self, who: Who, issues: list[Issue]) -> dict[uuid.UUID, datetime]:
        if not issues:
            return {}
        cond = (
            IssueRead.instance_id == who.instance.id
            if who.instance is not None
            else IssueRead.user_id == who.user_id
        )
        rows = (
            await self.db.execute(
                select(IssueRead.issue_id, IssueRead.read_at).where(
                    IssueRead.issue_id.in_([i.id for i in issues]), cond
                )
            )
        ).all()
        return {issue_id: read_at for issue_id, read_at in rows}

    @staticmethod
    def _author(issue: Issue | IssueComment) -> Actor:
        if issue.author_instance_id:
            return Actor(kind="instance", id=issue.author_instance_id, name=issue.author_name)
        return Actor(kind="user", id=issue.author_user_id, name=issue.author_name)

    async def outs(self, who: Who, issues: list[Issue]) -> list[ProjectIssueOut]:
        actors, tasks, reads = (
            await self._actors(issues),
            await self._tasks(issues),
            await self._reads(who, issues),
        )
        return [
            ProjectIssueOut(
                id=i.id,
                project_id=i.project_id,
                number=i.number,
                title=i.title,
                kind=i.kind,
                labels=list(i.labels),
                state=i.state,
                close_reason=i.close_reason,
                duplicate_of=i.duplicate_of,
                locked=i.locked,
                author=self._author(i),
                assignees=actors.get(i.id, []),
                tasks=tasks.get(i.id, []),
                comment_count=i.comment_count,
                unread=i.id not in reads or reads[i.id] < i.last_activity_at,
                agent_paused=i.agent_streak >= AGENT_STREAK_LIMIT,
                closed_at=i.closed_at,
                last_activity_at=i.last_activity_at,
                created_at=i.created_at,
                version=i.version,
            )
            for i in issues
        ]

    async def detail(self, who: Who, issue: Issue) -> IssueDetail:
        (base,) = await self.outs(who, [issue])
        comments = list(
            await self.db.scalars(
                select(IssueComment)
                .where(IssueComment.issue_id == issue.id)
                .order_by(IssueComment.created_at)
                .limit(500)
            )
        )
        events = list(
            await self.db.scalars(
                select(IssueEvent)
                .where(IssueEvent.issue_id == issue.id)
                .order_by(IssueEvent.created_at)
                .limit(500)
            )
        )
        texts = [issue.body, *(c.body for c in comments if c.hidden_at is None)]
        assigned = any(
            a.id == (who.instance.id if who.instance else who.user_id) for a in base.assignees
        )
        return IssueDetail(
            **base.model_dump(),
            body=issue.body,
            comments=[
                CommentOut(
                    id=c.id,
                    reply_to=c.reply_to,
                    body=None if c.hidden_at else c.body,
                    author=self._author(c),
                    edited_at=c.edited_at,
                    hidden=c.hidden_at is not None,
                    created_at=c.created_at,
                )
                for c in comments
            ],
            events=[
                IssueEventOut(
                    id=e.id,
                    kind=e.kind,
                    actor=Actor(
                        kind=e.actor_kind,
                        id=e.instance_id or e.user_id,
                        name=e.actor_name,
                    ),
                    data={k: v for k, v in e.data.items() if isinstance(v, str | int) or v is None},
                    created_at=e.created_at,
                )
                for e in events
            ],
            pulls=await self.pulls(issue.workspace_id, texts),
            can_close=self.can_close(who, issue, assigned),
            can_moderate=who.is_admin,
        )

    async def pulls(self, workspace_id: uuid.UUID, texts: list[str]) -> list[PullRef]:
        """粘贴的 GitHub PR 链接：仓库已接入时显示同步到的状态（只读，不访问 GitHub）。"""
        urls = list(dict.fromkeys(u.rstrip("/") for t in texts for u in _PULL.findall(t)))[:20]
        if not urls:
            return []
        rows = {
            r.source_url.rstrip("/"): r
            for r in await self.db.scalars(
                select(ExternalResource).where(
                    ExternalResource.workspace_id == workspace_id,
                    ExternalResource.resource_type == "pull",
                    ExternalResource.source_url.in_(urls + [u + "/" for u in urls]),
                )
            )
        }
        out = []
        for url in urls:
            row = rows.get(url)
            if row is None:
                continue
            meta = row.metadata_json or {}
            out.append(
                PullRef(
                    url=url,
                    title=row.title,
                    state=str(meta.get("state")) if meta.get("state") else None,
                    merged=bool(meta.get("merged") or meta.get("merged_at")),
                )
            )
        return out

    # ------------------------------------------------------------ 列表、收件箱、任务

    async def list_issues(
        self,
        who: Who,
        project_id: uuid.UUID,
        *,
        state: str | None = None,
        kind: str | None = None,
        label: str | None = None,
        assigned_to_me: bool = False,
        mentioned_me: bool = False,
        q: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Issue], int, int]:
        project = await self.project(who, project_id)
        base = and_(Issue.project_id == project.id, Issue.deleted_at.is_(None))
        stmt = select(Issue).where(base)
        if state:
            stmt = stmt.where(Issue.state == state)
        if kind:
            stmt = stmt.where(Issue.kind == kind)
        if label:
            stmt = stmt.where(Issue.labels.contains([label]))
        if q:
            pattern = f"%{q.replace('%', '').replace('_', '')}%"
            stmt = stmt.where(or_(Issue.title.ilike(pattern), Issue.body.ilike(pattern)))
        me_assignee = (
            IssueAssignee.instance_id == who.instance.id
            if who.instance is not None
            else IssueAssignee.user_id == who.user_id
        )
        if assigned_to_me:
            stmt = stmt.where(Issue.id.in_(select(IssueAssignee.issue_id).where(me_assignee)))
        if mentioned_me:
            me_mention = (
                IssueMention.instance_id == who.instance.id
                if who.instance is not None
                else IssueMention.user_id == who.user_id
            )
            stmt = stmt.where(Issue.id.in_(select(IssueMention.issue_id).where(me_mention)))
        items = list(
            await self.db.scalars(stmt.order_by(Issue.last_activity_at.desc()).limit(limit))
        )
        counts = dict(
            (
                await self.db.execute(
                    select(Issue.state, func.count()).where(base).group_by(Issue.state)
                )
            ).all()
        )
        return items, int(counts.get("open", 0)), int(counts.get("closed", 0))

    async def inbox(self, who: Who, limit: int = 30) -> list[Issue]:
        """提到我的、指派给我的、我开的有新动态（比我上次读到的更新）的 Issue。"""
        if who.instance is not None:
            me_assignee = IssueAssignee.instance_id == who.instance.id
            me_mention = IssueMention.instance_id == who.instance.id
            me_author = Issue.author_instance_id == who.instance.id
            me_read = IssueRead.instance_id == who.instance.id
            projects = select(ProjectAgent.project_id).where(
                ProjectAgent.instance_id == who.instance.id
            )
        else:
            me_assignee = IssueAssignee.user_id == who.user_id
            me_mention = IssueMention.user_id == who.user_id
            me_author = Issue.author_user_id == who.user_id
            me_read = IssueRead.user_id == who.user_id
            projects = select(Project.id).where(Project.workspace_id == who.workspace_id)
        involved = or_(
            Issue.id.in_(select(IssueAssignee.issue_id).where(me_assignee)),
            Issue.id.in_(select(IssueMention.issue_id).where(me_mention)),
            me_author,
        )
        read_at = (
            select(IssueRead.read_at)
            .where(IssueRead.issue_id == Issue.id, me_read)
            .correlate(Issue)
            .scalar_subquery()
        )
        stmt = (
            select(Issue)
            .where(
                Issue.workspace_id == who.workspace_id,
                Issue.deleted_at.is_(None),
                Issue.project_id.in_(projects),
                involved,
                or_(read_at.is_(None), read_at < Issue.last_activity_at),
            )
            .order_by(Issue.last_activity_at.desc())
            .limit(limit)
        )
        return list(await self.db.scalars(stmt))

    async def for_task(self, task_id: uuid.UUID) -> list[Issue]:
        return list(
            await self.db.scalars(
                select(Issue)
                .join(IssueTask, IssueTask.issue_id == Issue.id)
                .where(IssueTask.task_id == task_id, Issue.deleted_at.is_(None))
                .order_by(Issue.state.desc(), Issue.number)
            )
        )


async def open_blockers(db: AsyncSession, task_id: uuid.UUID) -> list[Issue]:
    """任务关联的、未关闭的阻塞 Issue：智能体有它们时不能完成任务（D15）。"""
    return list(
        await db.scalars(
            select(Issue)
            .join(IssueTask, IssueTask.issue_id == Issue.id)
            .where(
                IssueTask.task_id == task_id,
                Issue.kind == "blocker",
                Issue.state == "open",
                Issue.deleted_at.is_(None),
            )
            .order_by(Issue.number)
        )
    )


def _wake_count(prompt: str) -> int:
    found = re.search(r"有 (\d+) 条", prompt)
    return int(found.group(1)) if found else 1


def _wake_prompt(count: int) -> str:
    return (
        f"[工作台 Issue] 有 {count} 条 Issue 动态"
        "（被提到、被指派，或你开的、指派给你的 Issue 有新评论）。"
        "请用 issue_inbox 查看，用 issue_get 读详情，需要时用 issue_comment 回复。"
    )


__all__ = [
    "AGENT_STREAK_LIMIT",
    "KIND_LABELS",
    "TEMPLATES",
    "IssueService",
    "Who",
    "open_blockers",
    "templates",
]
