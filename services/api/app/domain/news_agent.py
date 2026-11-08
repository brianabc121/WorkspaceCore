"""新闻早报方案 B 与定时工作（T-0153，docs/v2/NEWS_PLAN.md 第 5、8 节）。

方案 B：到推送时间让一个智能体写早报，不新增审批例外：
1. Worker 每分钟检查：启用了早报、选了方案 B、现在落在某个推送时间段 [t, t+30min) 里、今天这个时间
   还没有早报 → 在“每日早报”项目里建一个任务（执行者 = 选定的实例；项目没有就建，实例加入项目），
   再安排一个立即执行的唤醒（source = news）。
2. 智能体醒来按任务说明用 `news_*` 工具取新闻，用 `readme_update` 把早报写进任务 README 的
   「早报」部分，再 `complete_task`（例外五、例外二里已有的、不审批的工具）。
3. Worker 看到任务完成：取出「早报」部分存为早报，经 TrendRadar MCP 的 `send_notification`
   推到所选渠道（工作台自己调用，不开放给智能体），通知 owner。
4. 超过 NEWS_AGENT_TIMEOUT_MINUTES（默认 90 分钟）没完成：记为失败并通知 owner。

另外每分钟顺带：方案 A 收了一半超过 2 小时的早报记为失败；配置文件不在（新部署的卷）就补写；
每天本地时间 03:10 之后重新计算偏好并生成配置文件（第 8 节）。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import agent_wakeups, events, project_groups
from app.domain import readme_text as rt
from app.domain.connectors import ConnectorRuntime
from app.domain.news import (
    MAX_BRIEFING_CHARS,
    RECEIVE_WINDOW,
    NewsService,
    briefing_out,
    slot_for,
    topics_of,
)
from app.domain.notifications import notify_owners
from app.domain.readme import Readmes
from app.models import (
    AgentInstance,
    AgentWakeup,
    NewsBriefing,
    NewsSettings,
    Project,
    Task,
)
from app.schemas.news import NewsBriefingOut

logger = logging.getLogger("app.news")

PROJECT_TITLE = "每日早报"
SECTION = "早报"
RELEARN_AT = (3, 10)  # 本地时间 03:10 之后
RELEARN_EVERY = timedelta(hours=20)


def _now() -> datetime:
    return datetime.now(UTC)


def instructions(row: NewsSettings, task_id: uuid.UUID | None, day: date, slot: str) -> str:
    """任务说明与唤醒提示词共用（唤醒提示词最多 2000 字）。"""
    topics = [t for t in topics_of(row) if t.enabled]
    interests = "；".join(
        f"{t.name}（{t.description or '、'.join(t.keywords[:6])}）" for t in topics[:10]
    )
    excluded = "、".join(row.exclusions[:20]) or "无"
    target = f"任务 {task_id}" if task_id else "这个任务"
    text = (
        f"写 {day.isoformat()} {slot} 的新闻早报。\n"
        "1. 用 news_briefing 看今天有没有已经送达的早报可参考；用 news_search、news_latest、"
        "news_rss 取今天与下面兴趣相关的新闻，只用工具返回的条目，不编造。\n"
        "2. 整理成 10～30 条：开头 2～3 句今天的总体看法；再按兴趣分组，每条一行"
        "“- [标题](原文链接)：一句话摘要”。\n"
        f"3. 用 readme_update 把早报写进{target}的 README 的「{SECTION}」部分（mode=replace，"
        f"不要写 ## 标题），然后用 complete_task 完成{target}。\n"
        f"兴趣：{interests or '（没有设置，按当天热点）'}\n"
        f"不想看：{excluded}\n"
        "只做这件事：不发消息、不改别的任务。"
    )
    return text[:1900]


async def _project(db: AsyncSession, row: NewsSettings) -> Project:
    project = await db.scalar(
        select(Project)
        .where(
            Project.workspace_id == row.workspace_id,
            Project.title == PROJECT_TITLE,
            Project.archived.is_(False),
        )
        .order_by(Project.created_at)
        .limit(1)
    )
    if project is None:
        project = Project(
            workspace_id=row.workspace_id,
            title=PROJECT_TITLE,
            description="新闻早报方案 B：每天到推送时间由智能体写一份早报（工作台自动建的任务）。",
            created_by=row.updated_by,
        )
        db.add(project)
        await db.flush()
    return project


async def start(
    db: AsyncSession, settings: Settings, row: NewsSettings, day: date, slot: str
) -> NewsBriefing:
    """建任务、让实例加入项目、安排唤醒，记一份 pending 的早报。调用方提交。"""
    now = _now()
    instance = await db.get(AgentInstance, row.agent_instance_id) if row.agent_instance_id else None
    title_day = f"{day:%m-%d}"
    if (
        instance is None
        or instance.workspace_id != row.workspace_id
        or instance.disabled_at is not None
    ):
        briefing = NewsBriefing(
            workspace_id=row.workspace_id,
            local_date=day,
            slot=slot,
            source="agent",
            title=f"{title_day} 早报",
            content="",
            status="failed",
            error="写早报的智能体实例不存在或已停用",
        )
        db.add(briefing)
        await db.flush()
        await _failed(db, row.workspace_id, briefing)
        return briefing
    project = await _project(db, row)
    await project_groups.add_agents(db, project, [instance.id], added_by=row.updated_by)
    timeout = timedelta(minutes=settings.news_agent_timeout_minutes)
    task = Task(
        workspace_id=row.workspace_id,
        project_id=project.id,
        title=f"早报 {day.isoformat()} {slot}",
        description="",
        due_at=now + timeout,
        labels=["早报"],
        assignee_instance_id=instance.id,
        created_by=row.updated_by,
    )
    db.add(task)
    await db.flush()
    task.description = instructions(row, task.id, day, slot)
    briefing = NewsBriefing(
        workspace_id=row.workspace_id,
        local_date=day,
        slot=slot,
        source="agent",
        title=f"{title_day} 早报（{instance.name}）",
        content="",
        status="pending",
        task_id=task.id,
        error="",
    )
    db.add(briefing)
    wakeup_id = uuid.uuid4()
    db.add(
        AgentWakeup(
            id=wakeup_id,
            workspace_id=row.workspace_id,
            instance_id=instance.id,
            source="news",
            created_by=row.updated_by,
            target=None,
            session_key=agent_wakeups.session_key(None, "", wakeup_id),
            prompt=instructions(row, task.id, day, slot),
            run_at=now,
        )
    )
    await db.flush()
    events.mark(db, row.workspace_id, "news")
    return briefing


async def generate_now(service: NewsService, workspace_id: uuid.UUID) -> NewsBriefingOut:
    """设置页的“现在生成一份”（owner）：同一时间只能有一份在写。"""
    row = await service.row(workspace_id)
    if row is None or not row.enabled:
        raise ApiError(409, "NEWS_DISABLED", "先启用早报并保存设置")
    if row.briefing_mode != "agent" or row.agent_instance_id is None:
        raise ApiError(409, "NOT_AGENT_MODE", "只有方案 B（智能体写早报）能现在生成")
    pending = await service.db.scalar(
        select(NewsBriefing.id).where(
            NewsBriefing.workspace_id == workspace_id,
            NewsBriefing.source == "agent",
            NewsBriefing.status == "pending",
        )
    )
    if pending is not None:
        raise ApiError(409, "BRIEFING_PENDING", "已经有一份早报正在写")
    local = _now().astimezone(service.zone)
    # 在推送时间段里点的，算这个时间段的那一份（到点不再另写）；时间段外的按点击时间记
    slot = slot_for(local, row.push_times) or f"{local:%H:%M}"
    briefing = await start(service.db, service.settings, row, local.date(), slot)
    await service.db.commit()
    await service.db.refresh(briefing)
    return briefing_out(briefing)


async def _failed(db: AsyncSession, workspace_id: uuid.UUID, briefing: NewsBriefing) -> None:
    await notify_owners(
        db,
        workspace_id,
        type_="news_briefing_failed",
        severity="warning",
        title="今日早报没有生成",
        body=f"{briefing.title}：{briefing.error}",
        link=f"/news?briefing={briefing.id}",
        key=str(briefing.id),
    )
    events.mark(db, workspace_id, "news", "overview")


async def schedule_due(db: AsyncSession, settings: Settings, row: NewsSettings) -> int:
    if row.briefing_mode != "agent" or row.agent_instance_id is None:
        return 0
    local = _now().astimezone(ZoneInfo(settings.news_timezone))
    slot = slot_for(local, row.push_times)
    if not slot:
        return 0
    # 这个时间段已经写过，或者还有一份（例如“现在生成一份”）正在写：同一时间只写一份
    exists = await db.scalar(
        select(NewsBriefing.id)
        .where(
            NewsBriefing.workspace_id == row.workspace_id,
            NewsBriefing.source == "agent",
            or_(
                (NewsBriefing.local_date == local.date()) & (NewsBriefing.slot == slot),
                NewsBriefing.status == "pending",
            ),
        )
        .limit(1)
    )
    if exists is not None:
        return 0
    await start(db, settings, row, local.date(), slot)
    return 1


async def collect(service: NewsService, row: NewsSettings) -> int:
    """方案 B 的任务完成了就取出早报；超时的记为失败。返回处理的份数。"""
    db = service.db
    now = _now()
    timeout = timedelta(minutes=service.settings.news_agent_timeout_minutes)
    pending = list(
        await db.scalars(
            select(NewsBriefing)
            .where(
                NewsBriefing.workspace_id == row.workspace_id,
                NewsBriefing.source == "agent",
                NewsBriefing.status == "pending",
            )
            .with_for_update(skip_locked=True)
        )
    )
    handled = 0
    for briefing in pending:
        task = await db.get(Task, briefing.task_id) if briefing.task_id else None
        if task is None:
            briefing.status = "failed"
            briefing.error = "早报任务被删除了"
            await _failed(db, row.workspace_id, briefing)
        elif task.done:
            note = await Readmes(db, service.settings).task_note(task.id)
            text = rt.section(note.body, SECTION) if note is not None else None
            if not text:
                briefing.status = "failed"
                briefing.error = f"任务完成了，但 README 里没有「{SECTION}」部分"
                await _failed(db, row.workspace_id, briefing)
            else:
                briefing.content = text[:MAX_BRIEFING_CHARS]
                briefing.status = "complete"
                await db.flush()
                await service.announce(row.workspace_id, briefing)
                briefing.error = await _push(service, row, briefing)
                events.mark(db, row.workspace_id, "news", "overview")
        elif now - briefing.created_at > timeout:
            briefing.status = "failed"
            briefing.error = f"{service.settings.news_agent_timeout_minutes} 分钟内没有写完"
            await _failed(db, row.workspace_id, briefing)
        else:
            continue
        handled += 1
    return handled


async def _push(service: NewsService, row: NewsSettings, briefing: NewsBriefing) -> str:
    """推到所选渠道；推送失败不影响早报入库，只记下原因。"""
    if not service.deployed or not service.push_channels(row):
        return ""
    try:
        results = await service.push(row, title=briefing.title, message=briefing.content)
    except ApiError as error:
        return f"推送失败：{error.message}"
    failed = [r.channel for r in results if not r.ok]
    return f"这些渠道推送失败：{'、'.join(failed)}" if failed else ""


async def expire_receiving(db: AsyncSession, row: NewsSettings) -> int:
    """方案 A 收了一半、超过 2 小时没收齐的：记为失败。"""
    stale = list(
        await db.scalars(
            select(NewsBriefing).where(
                NewsBriefing.workspace_id == row.workspace_id,
                NewsBriefing.source == "trendradar",
                NewsBriefing.status == "receiving",
                NewsBriefing.created_at < _now() - RECEIVE_WINDOW,
            )
        )
    )
    for briefing in stale:
        briefing.status = "failed"
        briefing.error = f"批次不完整：只收到 {briefing.parts}/{briefing.total_parts} 批"
    if stale:
        events.mark(db, row.workspace_id, "news")
    return len(stale)


def _relearn_due(row: NewsSettings, zone: ZoneInfo) -> bool:
    local = _now().astimezone(zone)
    if (local.hour, local.minute) < RELEARN_AT:
        return False
    computed = (row.learned or {}).get("computed_at")
    try:
        last = datetime.fromisoformat(computed) if isinstance(computed, str) else None
    except ValueError:
        last = None
    return last is None or _now() - last > RELEARN_EVERY


async def tick(
    db: AsyncSession, settings: Settings, runtime: ConnectorRuntime, redis: Redis | None
) -> dict[str, int]:
    """Worker 每分钟调用一次（同一时刻只有一个工作空间启用早报）。"""
    service = NewsService(db, settings, runtime, redis)
    row = await service.enabled_row()
    if row is None:
        return {}
    stats = {
        "expired": await expire_receiving(db, row),
        "scheduled": await schedule_due(db, settings, row),
    }
    await db.commit()
    stats["collected"] = await collect(service, row)
    await db.commit()
    config_missing = not (Path(settings.news_config_dir) / "config.yaml").exists()
    if service.deployed and (config_missing or _relearn_due(row, service.zone)):
        row = await service.enabled_row()
        if row is not None:
            await service.relearn_and_render()
            stats["rendered"] = 1
    return stats
