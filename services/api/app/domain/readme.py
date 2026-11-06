"""项目与任务的 README（T-0122，docs/v2/AGENT_MESSAGING_PLAN.md 第 9 节）。

README 是导览：新来的人或智能体先读它，就知道这个项目或任务是什么、谁在做、去哪里找东西。

- 存成项目知识库里的笔记（类型 `readme`）：项目 README 置顶在根目录，
  任务 README 在“任务导览”文件夹，
  笔记上记着 task_id。这样直接用上知识库的编辑器、版本、搜索、链接和智能体工具（D16）。
- 创建时按模板生成，不调用模型；
  自动部分用 `<!-- myws:auto 名字 -->` 包起来，
  数据变了由 Worker 合并后更新：只替换标记之间的内容，真的变了才写一个版本，作者是“工作台”（9.4）。
- 哪些数据变了由 ORM 的 after_flush 收集（`readme_marks.marks()`，在 app/domain/events.py 里调用），
  提交后写进 Redis 的有序集合；Worker 每 20 秒取到期的，同一篇 1 分钟内最多更新一次。
  丢了也不要紧：每 30 分钟补建缺的 README 并刷新所有项目 README（`reconcile`）。
- 工作台自己写 README 不经过 KnowledgeBase.update：那里会把“人改了智能体写的内容”记成纠正，
  自动更新不是人改的。人和智能体改 README 照常走 KnowledgeBase（版本号、密钥拦截、限额）。
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import knowledge_links as links
from app.domain import readme_text as rt
from app.domain.knowledge_base import KnowledgeBase, SaveOptions, _line_stats
from app.domain.readme_marks import DIRTY_KEY, push
from app.models import (
    AgentInstance,
    ImGroup,
    KnowledgeNote,
    KnowledgeSpace,
    Project,
    ProjectAgent,
    Task,
    TaskEvent,
    User,
    WorkspaceMember,
)

logger = logging.getLogger("app.readme")

PROJECT_TITLE = "README"
TASK_FOLDER = "任务导览"
REASON = "工作台自动更新"
RAN_KEY = "readme:ran:{key}"
THROTTLE_SECONDS = 60
KNOWLEDGE_TYPES = {"decision": "决定", "rule": "规范", "process": "流程", "runbook": "流程"}
EVENT_LABELS = {
    "opened": "开放为可以自由接取",
    "claimed": "领取了",
    "released": "放弃了",
    "completed": "完成了",
    "reopened": "打回了",
}
PRIORITY = {1: "低", 2: "中", 3: "高", 4: "紧急", 5: "立刻"}


def _now() -> datetime:
    return datetime.now(UTC)


def _time(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if value else "—"


def _one_line(text: str, limit: int = 120) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _display(text: str) -> str:
    """链接里显示的文字：去掉会打断 [[…]] 语法的字符。"""
    return _one_line(text.replace("[", "（").replace("]", "）").replace("|", "／"), 80) or "—"


def task_link(task_id: uuid.UUID, title: str) -> str:
    return f"[[task:{task_id}|{_display(title)}]]"


def note_link(title: str, label: str | None = None) -> str:
    """链到同一个知识库里的笔记；标题里有打断语法的字符时只写文字。"""
    if any(ch in title for ch in "[]|#/\n"):
        return title
    return f"[[{title}|{_display(label)}]]" if label else f"[[{title}]]"


def task_title(task: Task) -> str:
    return f"{_one_line(task.title, 100)}（任务 {str(task.id)[:8]}）"


# ================================================================ 生成与更新


@dataclass
class Written:
    note: KnowledgeNote
    changed: bool


class Readmes:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.kb = KnowledgeBase(db, settings)

    # ------------------------------------------------------------ 查找

    async def project_space(self, project: Project) -> KnowledgeSpace:
        query = select(KnowledgeSpace).where(
            KnowledgeSpace.kind == "project", KnowledgeSpace.project_id == project.id
        )
        space = await self.db.scalar(query)
        if space is None:
            await self.kb.ensure_spaces(project.workspace_id)
            space = await self.db.scalar(query)
        if space is None:  # 理论上不会：ensure_spaces 给每个项目建一个
            raise RuntimeError("项目没有知识库")
        return space

    async def project_note(self, space_id: uuid.UUID) -> KnowledgeNote | None:
        note: KnowledgeNote | None = await self.db.scalar(
            select(KnowledgeNote).where(
                KnowledgeNote.space_id == space_id,
                KnowledgeNote.type == "readme",
                KnowledgeNote.task_id.is_(None),
                KnowledgeNote.folder == "",
                KnowledgeNote.deleted_at.is_(None),
            )
        )
        return note

    async def task_note(self, task_id: uuid.UUID) -> KnowledgeNote | None:
        note: KnowledgeNote | None = await self.db.scalar(
            select(KnowledgeNote).where(
                KnowledgeNote.task_id == task_id, KnowledgeNote.deleted_at.is_(None)
            )
        )
        return note

    # ------------------------------------------------------------ 项目

    async def ensure_project(self, project: Project) -> KnowledgeNote:
        space = await self.project_space(project)
        note = await self.project_note(space.id)
        if note is not None:
            return note
        sections = await self.project_sections(project, space)
        body = project_body(project, sections)
        return await self._create(space, PROJECT_TITLE, body, task_id=None, pinned=True)

    async def refresh_project(self, project: Project) -> Written:
        space = await self.project_space(project)
        note = await self.project_note(space.id)
        if note is None:
            return Written(await self.ensure_project(project), True)
        note = await self._lock(note)
        body = rt.merge(note.body, await self.project_sections(project, space))
        return Written(note, await self._write(note, space, body=body))

    async def project_sections(self, project: Project, space: KnowledgeSpace) -> dict[str, str]:
        return {
            "成员": await self._members(project),
            "沟通": await self._communication(project, space),
            "任务概况": await self._task_overview(project),
            "知识库": await self._knowledge(space),
        }

    async def _members(self, project: Project) -> str:
        lines: list[str] = []
        if project.created_by is not None:
            name = await self.db.scalar(
                select(User.display_name).where(User.id == project.created_by)
            )
            lines.append(f"- 创建人：{_one_line(name or '已删除的用户', 40)}")
        elif project.created_by_instance is not None:
            name = await self.db.scalar(
                select(AgentInstance.name).where(AgentInstance.id == project.created_by_instance)
            )
            lines.append(f"- 创建人：智能体「{_one_line(name or '已删除的实例', 40)}」")
        admins = (
            await self.db.execute(
                select(User.display_name, WorkspaceMember.role)
                .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                .where(
                    WorkspaceMember.workspace_id == project.workspace_id,
                    WorkspaceMember.role.in_(("owner", "admin")),
                )
                .order_by(WorkspaceMember.role.desc(), User.display_name)
                .limit(20)
            )
        ).all()
        if admins:
            lines.append("- 管理员：" + "、".join(_one_line(n, 40) for n, _ in admins))
        agents = (
            await self.db.execute(
                select(AgentInstance.name, AgentInstance.description, ProjectAgent.access)
                .join(ProjectAgent, ProjectAgent.instance_id == AgentInstance.id)
                .where(ProjectAgent.project_id == project.id)
                .order_by(AgentInstance.name)
                .limit(50)
            )
        ).all()
        if agents:
            lines.append("- 智能体：")
            for name, description, access in agents:
                rights = "读写" if access == "read_write" else "只读"
                about = _one_line(description or "", 120)
                lines.append(
                    f"  - 「{_one_line(name, 40)}」（{rights}）" + (f"：{about}" if about else "")
                )
        else:
            lines.append("- 智能体：还没有（管理员在项目卡片的“成员”里添加）")
        return "\n".join(lines)

    async def _communication(self, project: Project, space: KnowledgeSpace) -> str:
        lines: list[str] = []
        group = await self.db.scalar(
            select(ImGroup.name).where(
                ImGroup.project_id == project.id, ImGroup.dismissed_at.is_(None)
            )
        )
        if group:
            lines.append(f"- 项目群：「{_one_line(group, 60)}」（在 IM 里，项目成员都在群里）")
        elif project.im_group_status == "pending":
            lines.append("- 项目群：正在建")
        else:
            lines.append("- 项目群：还没有")
        brief = (
            await self.db.execute(
                select(KnowledgeNote.title, KnowledgeNote.version).where(
                    KnowledgeNote.space_id == space.id,
                    KnowledgeNote.type == "brief",
                    KnowledgeNote.deleted_at.is_(None),
                )
            )
        ).first()
        if brief:
            lines.append(
                f"- 项目简报：{note_link(brief[0])}（第 {brief[1]} 版；目标、规矩和决定以它为准）"
            )
        else:
            lines.append("- 项目简报：还没有（由人在项目知识库里写）")
        return "\n".join(lines)

    async def _executor(self, task: Task) -> str:
        if task.assignee_instance_id is not None:
            name = await self.db.scalar(
                select(AgentInstance.name).where(AgentInstance.id == task.assignee_instance_id)
            )
            return f"智能体「{_one_line(name or '已删除的实例', 40)}」"
        if task.assignee_user_id is not None:
            name = await self.db.scalar(
                select(User.display_name).where(User.id == task.assignee_user_id)
            )
            return _one_line(name or "已删除的用户", 40)
        if task.claim_mode == "open":
            return "可以自由接取" + ("（项目成员）" if task.claim_scope == "project" else "")
        return "还没有分配"

    async def _task_overview(self, project: Project) -> str:
        base = and_(Task.project_id == project.id, Task.workspace_id == project.workspace_id)
        assigned = or_(Task.assignee_user_id.is_not(None), Task.assignee_instance_id.is_not(None))
        now = _now()

        async def count(*where: Any) -> int:
            return int(await self.db.scalar(select(func.count()).where(base, *where)) or 0)

        async def rows(*where: Any, order: Any, limit: int) -> list[Task]:
            return list(
                (
                    await self.db.scalars(
                        select(Task).where(base, *where).order_by(*order).limit(limit)
                    )
                ).all()
            )

        open_count, done_count = await count(Task.done.is_(False)), await count(Task.done.is_(True))
        lines = [f"- 未完成 {open_count} 个，已完成 {done_count} 个"]
        doing = await rows(
            Task.done.is_(False),
            assigned,
            order=(Task.due_at.asc().nulls_last(), Task.created_at),
            limit=10,
        )
        if doing:
            lines.append(
                f"- 进行中（有执行者、未完成，{await count(Task.done.is_(False), assigned)} 个）："
            )
            for task in doing:
                lines.append(f"  - {task_link(task.id, task.title)}：{await self._executor(task)}")
        claimable = await rows(
            Task.done.is_(False),
            Task.claim_mode == "open",
            ~assigned,
            order=(Task.priority.desc(), Task.created_at),
            limit=10,
        )
        if claimable:
            lines.append("- 可以自由接取：")
            lines.extend(f"  - {task_link(t.id, t.title)}" for t in claimable)
        soon = await rows(
            Task.done.is_(False),
            Task.due_at.is_not(None),
            Task.due_at <= now + timedelta(days=7),
            order=(Task.due_at.asc(),),
            limit=10,
        )
        if soon:
            lines.append("- 7 天内到期（含已逾期）：")
            lines.extend(f"  - {task_link(t.id, t.title)}：截止 {_time(t.due_at)}" for t in soon)
        recent = await rows(Task.done.is_(True), order=(Task.done_at.desc().nulls_last(),), limit=5)
        if recent:
            lines.append("- 最近完成：")
            lines.extend(f"  - {task_link(t.id, t.title)}：{_time(t.done_at)}" for t in recent)
        return "\n".join(lines)

    async def _knowledge(self, space: KnowledgeSpace) -> str:
        notes = (
            await self.db.execute(
                select(KnowledgeNote.title, KnowledgeNote.type)
                .where(
                    KnowledgeNote.space_id == space.id,
                    KnowledgeNote.type.in_(tuple(KNOWLEDGE_TYPES)),
                    KnowledgeNote.deleted_at.is_(None),
                )
                .order_by(KnowledgeNote.updated_at.desc())
                .limit(15)
            )
        ).all()
        if not notes:
            return "- 还没有决定、规范或流程类的笔记"
        grouped: dict[str, list[str]] = {}
        for title, kind in notes:
            grouped.setdefault(KNOWLEDGE_TYPES[kind], []).append(note_link(title))
        return "\n".join(f"- {label}：" + "、".join(items) for label, items in grouped.items())

    # ------------------------------------------------------------ 任务

    async def ensure_task(self, task: Task) -> KnowledgeNote:
        note = await self.task_note(task.id)
        if note is not None:
            return note
        project = await self.db.get(Project, task.project_id)
        if project is None:
            raise RuntimeError("任务没有项目")
        space = await self.project_space(project)
        project_note = await self.ensure_project(project)
        body = task_body(task, await self.task_sections(task, project, project_note))
        return await self._create(space, task_title(task), body, task_id=task.id, pinned=False)

    async def refresh_task(self, task: Task) -> Written:
        note = await self.task_note(task.id)
        if note is None:
            return Written(await self.ensure_task(task), True)
        project = await self.db.get(Project, task.project_id)
        if project is None:
            return Written(note, False)
        note = await self._lock(note)
        space = await self.project_space(project)
        project_note = await self.ensure_project(project)
        body = rt.merge(note.body, await self.task_sections(task, project, project_note))
        # 任务移到了别的项目：README 跟着移到新项目的知识库（9.2）
        changed = await self._write(note, space, body=body, title=task_title(task))
        return Written(note, changed)

    async def task_sections(
        self, task: Task, project: Project, project_note: KnowledgeNote
    ) -> dict[str, str]:
        return {
            "状态": await self._status(task, project, project_note),
            "讨论": "- 还没有关联的 Issue",
            "执行记录": await self._history(task),
        }

    async def _status(self, task: Task, project: Project, project_note: KnowledgeNote) -> str:
        lines = [
            f"- 状态：已完成（{_time(task.done_at)}）" if task.done else "- 状态：未完成",
            f"- 执行者：{await self._executor(task)}",
        ]
        if task.planned_start_at:
            lines.append(f"- 计划开始：{_time(task.planned_start_at)}")
        if task.due_at:
            lines.append(f"- 截止：{_time(task.due_at)}")
        if task.priority:
            lines.append(f"- 优先级：{PRIORITY.get(task.priority, str(task.priority))}")
        if task.labels:
            lines.append("- 标签：" + "、".join(_one_line(label, 30) for label in task.labels[:20]))
        lines.append(
            f"- 所属项目：{_one_line(project.title, 60)}"
            f"（导览：{note_link(project_note.title, '项目 README')}）"
        )
        return "\n".join(lines)

    async def _history(self, task: Task) -> str:
        events = (
            await self.db.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == task.id)
                .order_by(TaskEvent.created_at.desc())
                .limit(10)
            )
        ).all()
        if not events:
            return "- 还没有领取、放弃、完成或打回的记录"
        lines = []
        for event in events:
            actor = (
                f"智能体「{_one_line(event.actor_name, 40)}」"
                if event.actor_kind == "instance"
                else ("工作台" if event.actor_kind == "system" else _one_line(event.actor_name, 40))
            )
            note = f"：{_one_line(event.note, 160)}" if event.note else ""
            lines.append(
                f"- {_time(event.created_at)} {actor}"
                f"{EVENT_LABELS.get(event.kind, event.kind)}{note}"
            )
        return "\n".join(lines)

    async def trash_task(self, task_id: uuid.UUID) -> None:
        """任务删除：它的 README 进回收站（30 天后清除，方案 9.2）。调用方提交。"""
        note = await self.task_note(task_id)
        if note is None:
            return
        now = _now()
        note.deleted_at = now
        note.purge_after = now + timedelta(days=30)
        note.version += 1
        self.kb._revision(
            note, None, "delete", SaveOptions(reason="任务已删除", source_kind="system"), 0, 0
        )

    # ------------------------------------------------------------ 写入

    async def _lock(self, note: KnowledgeNote) -> KnowledgeNote:
        locked: KnowledgeNote | None = await self.db.scalar(
            select(KnowledgeNote).where(KnowledgeNote.id == note.id).with_for_update()
        )
        return locked or note

    async def _create(
        self,
        space: KnowledgeSpace,
        title: str,
        body: str,
        *,
        task_id: uuid.UUID | None,
        pinned: bool,
    ) -> KnowledgeNote:
        candidates = [title, f"{title}（导览）", f"{title}（{uuid.uuid4().hex[:6]}）"]
        for candidate in candidates:
            note = KnowledgeNote(
                workspace_id=space.workspace_id,
                space_id=space.id,
                folder=TASK_FOLDER if task_id is not None else "",
                title=candidate,
                title_key=links.normalize(candidate),
                body=body,
                type="readme",
                task_id=task_id,
                pinned=pinned,
                source_kind="system",
                version=1,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(note)
                    await self.db.flush()
            except IntegrityError:
                # 同时建了一篇（页面打开与 Worker 撞上），或者标题被别的笔记占了
                existing = (
                    await self.task_note(task_id) if task_id else await self.project_note(space.id)
                )
                if existing is not None:
                    return existing
                continue
            added, _ = _line_stats("", body)
            self.kb._revision(
                note, None, "create", SaveOptions(reason=REASON, source_kind="system"), added, 0
            )
            await self.kb.relink(note, space)
            return note
        raise RuntimeError("README 的标题都被占用了")

    async def _write(
        self, note: KnowledgeNote, space: KnowledgeSpace, *, body: str, title: str | None = None
    ) -> bool:
        """内容（或所在知识库、标题）真的变了才写一个版本，作者是工作台。"""
        new_title = title if title is not None else note.title
        if body == note.body and new_title == note.title and space.id == note.space_id:
            return False
        old_body = note.body
        note.body = body
        if space.id != note.space_id:
            note.space_id = space.id
        if new_title != note.title:
            note.title, note.title_key = new_title, links.normalize(new_title)
        note.updated_by_user = None
        note.updated_by_instance = None
        note.source_kind = "system"
        note.version += 1
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            # 新标题在目标知识库里被占了：加上编号再试一次
            note.title = f"{new_title}（{uuid.uuid4().hex[:6]}）"
            note.title_key = links.normalize(note.title)
            await self.db.flush()
        added, removed = _line_stats(old_body, body)
        self.kb._revision(
            note, None, "update", SaveOptions(reason=REASON, source_kind="system"), added, removed
        )
        await self.kb.relink(note, space)
        return True

    # ------------------------------------------------------------ 按登记的键刷新

    async def refresh_key(self, key: str) -> list[str]:
        """刷新一个键；s: / i: / w: 展开成项目键返回（由调用方再排队，照样受 1 分钟限制）。"""
        kind, _, raw = key.partition(":")
        try:
            target = uuid.UUID(raw)
        except ValueError:
            return []
        if kind == "p":
            project = await self.db.get(Project, target)
            if project is not None:
                await self.refresh_project(project)
            return []
        if kind == "t":
            task = await self.db.get(Task, target)
            if task is not None:
                await self.refresh_task(task)
            return []
        if kind == "s":
            project_id = await self.db.scalar(
                select(KnowledgeSpace.project_id).where(KnowledgeSpace.id == target)
            )
            return [f"p:{project_id}"] if project_id else []
        if kind == "i":
            ids = await self.db.scalars(
                select(ProjectAgent.project_id).where(ProjectAgent.instance_id == target)
            )
            return [f"p:{i}" for i in ids]
        if kind == "w":
            ids = await self.db.scalars(select(Project.id).where(Project.workspace_id == target))
            return [f"p:{i}" for i in ids]
        return []


async def readme_refs(
    db: AsyncSession, project_ids: list[uuid.UUID]
) -> dict[uuid.UUID, KnowledgeNote]:
    """这些项目的项目 README（list_projects 用；还没生成的不在结果里）。"""
    if not project_ids:
        return {}
    rows = (
        await db.execute(
            select(KnowledgeSpace.project_id, KnowledgeNote)
            .join(KnowledgeNote, KnowledgeNote.space_id == KnowledgeSpace.id)
            .where(
                KnowledgeSpace.project_id.in_(project_ids),
                KnowledgeNote.type == "readme",
                KnowledgeNote.task_id.is_(None),
                KnowledgeNote.folder == "",
                KnowledgeNote.deleted_at.is_(None),
            )
        )
    ).all()
    return {project_id: note for project_id, note in rows if project_id is not None}


# ================================================================ Worker


async def process_due(
    sessions: Any,
    settings: Settings,
    redis: Redis,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """取到期的键逐个刷新；1 分钟内刷新过的推后。返回刷新了几个。"""
    moment = (now or _now()).timestamp()
    due = await redis.zrangebyscore(DIRTY_KEY, "-inf", moment, start=0, num=limit)
    done = 0
    for raw in due:
        key = raw.decode() if isinstance(raw, bytes) else str(raw)
        if not await redis.zrem(DIRTY_KEY, key):
            continue  # 别的进程拿走了
        if not await redis.set(RAN_KEY.format(key=key), "1", nx=True, ex=THROTTLE_SECONDS):
            wait = await redis.ttl(RAN_KEY.format(key=key))
            await redis.zadd(DIRTY_KEY, {key: moment + max(int(wait), 1)}, nx=True)
            continue
        async with sessions() as db:
            try:
                more = await Readmes(db, settings).refresh_key(key)
                await db.commit()
            except Exception as error:  # 一个失败不影响别的，下次对账再补
                await db.rollback()
                logger.warning(
                    "readme refresh failed", extra={"key": key, "error_type": type(error).__name__}
                )
                continue
        if more:
            await redis.delete(RAN_KEY.format(key=key))  # 展开类的键不占 1 分钟的限制
            await push(redis, more, delay=0)
        done += 1
    return done


async def reconcile(
    sessions: Any, settings: Settings, redis: Redis | None, *, batch: int = 300
) -> int:
    """补建缺的 README（迁移后已有的项目和任务也由这里补），并把所有项目 README 排进刷新队列。"""
    created = 0
    async with sessions() as db:
        has_project_readme = exists().where(
            KnowledgeNote.space_id == KnowledgeSpace.id,
            KnowledgeNote.type == "readme",
            KnowledgeNote.task_id.is_(None),
            KnowledgeNote.folder == "",
            KnowledgeNote.deleted_at.is_(None),
        )
        projects = list(
            (
                await db.scalars(
                    select(Project)
                    .outerjoin(
                        KnowledgeSpace,
                        and_(
                            KnowledgeSpace.project_id == Project.id,
                            KnowledgeSpace.kind == "project",
                        ),
                    )
                    .where(or_(KnowledgeSpace.id.is_(None), ~has_project_readme))
                    .order_by(Project.created_at.desc())
                    .limit(batch)
                )
            ).all()
        )
        tasks = list(
            (
                await db.scalars(
                    select(Task)
                    .where(
                        ~exists().where(
                            KnowledgeNote.task_id == Task.id, KnowledgeNote.deleted_at.is_(None)
                        )
                    )
                    .order_by(Task.created_at.desc())
                    .limit(batch)
                )
            ).all()
        )
        all_projects = list((await db.scalars(select(Project.id))).all())
    for project in projects:
        created += await _one(sessions, settings, lambda r, p=project.id: _ensure_project(r, p))
    for task in tasks:
        created += await _one(sessions, settings, lambda r, t=task.id: _ensure_task(r, t))
    if redis is not None and all_projects:
        await push(redis, (f"p:{p}" for p in all_projects), delay=0)
    return created


async def _ensure_project(readmes: Readmes, project_id: uuid.UUID) -> None:
    project = await readmes.db.get(Project, project_id)
    if project is not None:
        await readmes.ensure_project(project)


async def _ensure_task(readmes: Readmes, task_id: uuid.UUID) -> None:
    task = await readmes.db.get(Task, task_id)
    if task is not None:
        await readmes.ensure_task(task)


async def _one(sessions: Any, settings: Settings, work: Any) -> int:
    async with sessions() as db:
        try:
            await work(Readmes(db, settings))
            await db.commit()
            return 1
        except Exception as error:  # 一个失败不影响别的
            await db.rollback()
            logger.warning("readme create failed", extra={"error_type": type(error).__name__})
            return 0


# ================================================================ 模板


def project_body(project: Project, sections: dict[str, str]) -> str:
    intro = rt.first_paragraph(project.description or "") or "（还没有项目描述）"
    return "\n".join(
        [
            f"# {_one_line(project.title, 100)}",
            "",
            "> 这是项目的导览，由工作台生成。标着“自动更新”的部分随项目数据变化，"
            "其余部分人和智能体都可以补充。"
            "目标、规矩和决定以项目简报为准（链接在“沟通”里）。",
            "",
            "## 简介",
            "",
            intro,
            "",
            "## 成员",
            rt.block("成员", sections["成员"]),
            "",
            "## 沟通",
            rt.block("沟通", sections["沟通"]),
            "",
            "## 任务概况",
            rt.block("任务概况", sections["任务概况"]),
            "",
            "## 知识库",
            rt.block("知识库", sections["知识库"]),
            "",
            "## 代码与资源",
            "",
            "（由人或智能体填写：仓库、分支约定、部署方式、文档链接）",
            "",
            "## 约定",
            "",
            "（由人或智能体填写：分支、提交、评审、完成标准）",
            "",
            "## 上手步骤",
            "",
            "1. 读项目简报。",
            "2. 读这篇 README。",
            "3. 看分给你的任务和未关闭的 Issue。",
            "4. 领取任务前先读任务 README；交出或完成前更新它的“进展与交接”。",
            "",
        ]
    )


def task_body(task: Task, sections: dict[str, str]) -> str:
    description = (task.description or "").strip() or "（还没有描述）"
    criteria = rt.checklist(task.description or "") or "（由人或智能体填写）"
    return "\n".join(
        [
            f"# {_one_line(task.title, 100)}",
            "",
            "> 任务导览，由工作台生成。领取前先读；交出或完成前更新“进展与交接”。"
            "标着“自动更新”的部分随任务数据变化。",
            "",
            "## 要做什么",
            "",
            description,
            "",
            "## 状态",
            rt.block("状态", sections["状态"]),
            "",
            "## 讨论",
            rt.block("讨论", sections["讨论"]),
            "",
            "## 执行记录",
            rt.block("执行记录", sections["执行记录"]),
            "",
            "## 验收标准",
            "",
            criteria,
            "",
            "## 背景与约束",
            "",
            "（由人或智能体填写）",
            "",
            "## 进展与交接",
            "",
            "（执行者填写：做到哪了、怎么验证、下一步）",
            "",
        ]
    )


__all__ = ["PROJECT_TITLE", "Readmes", "process_due", "readme_refs", "reconcile"]
