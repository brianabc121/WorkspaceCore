"""运行服务器的定时检查与回收（T-0136、T-0139，docs/v2/RUNNER_PLAN.md 第 5、6 节）。

每分钟一次（worker `runner_tick`）：
- 指令超过时限：能重发的重发（最多 3 次）；`exec` 记为结果未知（不自动重跑）；
  建环境、构建模板记为失败；
- 运行服务器超过 90 秒没有长轮询 → 不在线，通知管理员；
- 删除条件：`at`、`idle`，任务完成、Issue 关闭、项目归档，创建者实例停用或删除 → 待删除；
  待删除过了宽限期 → 删除（常驻环境先做快照，保留 30 天）；正在执行运行的等运行结束；
- 空闲分档（第 6.1 节）：运行中空闲到点 → 停止；停止到点 → 归档（常驻先快照）；
  会话的归档 30 天后 → 删除记录；
- 资源紧张（第 6.4 节）：内存预留或磁盘超过高水位时按保留分从低到高停止、归档，
  清理没人用的模板镜像；
- 清理：删除 30 天前的已删除环境与过期快照，运行输出保留 7 天，完成的指令保留 7 天。
被保护（pinned）的环境、正在执行运行的环境不会被自动回收。
"""

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import notifications
from app.domain import runner_specs as specs
from app.domain.runner import (
    DEADLINES,
    MAX_ATTEMPTS,
    ONLINE_SECONDS,
    RETRYABLE,
    Runner,
    Viewer,
)
from app.domain.runner_snapshots import SnapshotStore
from app.models import (
    AgentInstance,
    AgentWakeup,
    Issue,
    Project,
    RunnerCommand,
    RunnerDeleteCondition,
    RunnerEnvironment,
    RunnerHost,
    RunnerRun,
    RunnerSnapshot,
    RunnerTemplate,
    Task,
)

logger = logging.getLogger("app.runner")

GB = 1024**3
RECLAIM_PER_TICK = 5
OUTPUT_KEEP_DAYS = 7
COMMAND_KEEP_DAYS = 7


@dataclass
class TickResult:
    expired: int = 0
    retried: int = 0
    offline: list[uuid.UUID] = field(default_factory=list)
    stopped: int = 0
    archived: int = 0
    pending_delete: int = 0
    deleted: int = 0
    purged: int = 0

    @property
    def changed(self) -> int:
        return (
            self.expired
            + self.retried
            + self.stopped
            + self.archived
            + self.pending_delete
            + self.deleted
            + self.purged
        )


def retention_score(
    env: RunnerEnvironment, uses_7d: int, now: datetime, *, snapshot_max_bytes: float
) -> float:
    """保留分（第 6.2 节）：重建耗时 × 使用频率 ÷ 占用 × 老化系数；分低的先回收。

    常驻环境的数据大到做不了快照时重建不出来，记为无穷大（只停止、不归档）。
    """
    start = env.start_seconds or 5.0
    if env.state == "running":
        cost = start
        occupancy = max(float(env.spec.get("memory_mb", 1024)) / 1024, 0.05)
    else:
        cost = env.rebuild_seconds or start + 30
        disk = env.disk_bytes or int(env.spec.get("disk_gb", 1)) * GB
        occupancy = max(disk / GB, 0.05)
    if env.lifetime == "persistent" and (env.disk_bytes or 0) > snapshot_max_bytes:
        return math.inf
    idle_days = max((now - env.last_used_at).total_seconds() / 86400, 0.0)
    aging: float = 0.5 ** (idle_days / 7)
    score: float = cost * (uses_7d + 1) / occupancy * aging
    return round(score, 3)


async def tick(db: AsyncSession, settings: Settings, redis: Redis | None = None) -> TickResult:
    runner = Runner(db, settings, redis)
    out = TickResult()
    await _expire_commands(runner, out)
    await _hosts_offline(runner, out)
    await _event_conditions(runner, out)
    await _lifecycle(runner, out)
    await _watermarks(runner, out)
    await _purge(runner, out)
    await db.commit()
    return out


# ---------------------------------------------------------------- 指令与在线


async def _expire_commands(runner: Runner, out: TickResult) -> None:
    db = runner.db
    rows = await db.scalars(
        select(RunnerCommand)
        .where(
            RunnerCommand.status.in_(("pending", "sent")),
            RunnerCommand.deadline_at < specs.now(),
        )
        .with_for_update(skip_locked=True)
        .limit(200)
    )
    for command in rows:
        if command.kind in RETRYABLE and command.attempts < MAX_ATTEMPTS:
            command.status = "pending"
            command.deadline_at = specs.later(DEADLINES.get(command.kind, 600))
            out.retried += 1
            continue
        out.expired += 1
        command.status = "unknown"
        command.finished_at = specs.now()
        command.error = "容器服务器在时限内没有回报结果"
        env = (
            await db.get(RunnerEnvironment, command.environment_id)
            if command.environment_id
            else None
        )
        if command.kind == "exec" and command.run_id is not None:
            await _run_unknown(db, command.run_id)
        elif command.kind == "create_env" and env is not None and env.state == "creating":
            env.state = "failed"
            env.state_reason = "创建超时：容器服务器在时限内没有回报"
        elif command.kind == "build_template" and command.template_id is not None:
            template = await db.get(RunnerTemplate, command.template_id)
            if template is not None and template.build_status == "building":
                template.build_status = "failed"
                template.build_error = "构建超时：容器服务器在时限内没有回报"
        elif command.kind == "snapshot" and env is not None:
            snapshot_id = command.payload.get("snapshot_id")
            if snapshot_id:
                snapshot = await db.get(RunnerSnapshot, uuid.UUID(str(snapshot_id)))
                if snapshot is not None and snapshot.status == "uploading":
                    snapshot.status = "failed"
                    snapshot.error = "快照超时"
            if env.state == "pending_delete":
                env.state = "stopped"
                env.delete_after = None
            env.state_reason = "快照超时，没有归档或删除"


async def _run_unknown(db: AsyncSession, run_id: uuid.UUID) -> None:
    run = await db.get(RunnerRun, run_id)
    if run is None or run.finished_at is not None:
        return
    run.status = "unknown"
    run.finished_at = specs.now()
    run.output = (
        run.output + "\n[工作台] 结果未知：容器服务器在时限内没有回报（不会自动重跑）"
    ).strip()
    if run.wake and run.instance_id is not None:
        db.add(
            AgentWakeup(
                id=uuid.uuid4(),
                workspace_id=run.workspace_id,
                instance_id=run.instance_id,
                source="runner",
                target=None,
                session_key=f"runner:{run.id}",
                prompt=f"[工作台] 运行 {run.id} 结果未知（容器服务器没有回报）。",
                run_at=specs.now(),
            )
        )


async def _hosts_offline(runner: Runner, out: TickResult) -> None:
    db = runner.db
    cutoff = specs.now() - timedelta(seconds=ONLINE_SECONDS)
    hosts = await db.scalars(
        select(RunnerHost).where(RunnerHost.status == "online", RunnerHost.last_seen_at < cutoff)
    )
    for host in hosts:
        host.status = "offline"
        out.offline.append(host.id)
        await notifications.notify_admins(
            db,
            host.workspace_id,
            type_="runner.host_offline",
            severity="warning",
            title=f"容器服务器「{host.name}」不在线",
            body="超过 90 秒没有连上工作台；智能体暂时不能建环境或执行命令",
            link="/runner",
            key=f"{host.id}:offline:{specs.now():%Y%m%d%H}",
        )


# ---------------------------------------------------------------- 删除条件


async def _event_conditions(runner: Runner, out: TickResult) -> None:
    """任务完成（或删除）、Issue 关闭、项目归档（或删除）；创建者实例停用或删除。"""
    db = runner.db
    now = specs.now()
    rows = list(
        await db.scalars(
            select(RunnerDeleteCondition).where(
                RunnerDeleteCondition.fired_at.is_(None),
                RunnerDeleteCondition.kind.in_(("task_done", "issue_closed", "project_archived")),
            )
        )
    )
    for condition in rows:
        env = await db.get(RunnerEnvironment, condition.environment_id)
        if env is None or env.state in ("pending_delete", "deleted"):
            continue
        due = False
        if condition.kind == "task_done" and condition.ref_id is not None:
            task = await db.get(Task, condition.ref_id)
            due = task is None or task.workspace_id != env.workspace_id or task.done
        elif condition.kind == "issue_closed" and condition.ref_id is not None:
            issue = await db.get(Issue, condition.ref_id)
            due = issue is None or issue.state == "closed"
        elif condition.kind == "project_archived" and condition.ref_id is not None:
            project = await db.get(Project, condition.ref_id)
            due = project is None or project.workspace_id != env.workspace_id or project.archived
        if due:
            condition.fired_at = now
            await runner.mark_pending_delete(env, f"删除条件触发：{condition.kind}")
            out.pending_delete += 1
    # 总是生效：创建者实例停用或删除
    orphans = list(
        await db.scalars(
            select(RunnerEnvironment)
            .outerjoin(AgentInstance, AgentInstance.id == RunnerEnvironment.owner_instance_id)
            .where(
                RunnerEnvironment.state.in_(("creating", "running", "stopped", "archived")),
                (RunnerEnvironment.owner_instance_id.is_(None))
                | (AgentInstance.status == "disabled"),
            )
        )
    )
    for env in orphans:
        if env.state == "archived":
            await runner.delete(env, "创建者实例已停用或删除")
            out.deleted += 1
        else:
            await runner.mark_pending_delete(env, "创建者实例已停用或删除")
            out.pending_delete += 1


async def _busy(
    db: AsyncSession, env_ids: list[uuid.UUID]
) -> tuple[set[uuid.UUID], set[uuid.UUID], set[uuid.UUID]]:
    """(有没结束的运行, 有在途指令或运行, 已经在删或在做快照) 的环境。"""
    if not env_ids:
        return set(), set(), set()
    running = await db.scalars(
        select(RunnerRun.environment_id).where(
            RunnerRun.environment_id.in_(env_ids), RunnerRun.finished_at.is_(None)
        )
    )
    pending = list(
        await db.execute(
            select(RunnerCommand.environment_id, RunnerCommand.kind).where(
                RunnerCommand.environment_id.in_(env_ids),
                RunnerCommand.status.in_(("pending", "sent")),
            )
        )
    )
    runs = {e for e in running if e}
    commands = {e for e, _ in pending if e}
    deleting = {e for e, kind in pending if e and kind in ("delete_env", "snapshot")}
    return runs, commands | runs, deleting


# ---------------------------------------------------------------- 空闲分档


async def _lifecycle(runner: Runner, out: TickResult) -> None:
    db = runner.db
    now = specs.now()
    envs = list(
        await db.scalars(
            select(RunnerEnvironment).where(
                RunnerEnvironment.state.in_(("running", "stopped", "pending_delete", "archived"))
            )
        )
    )
    if not envs:
        return
    hosts = {
        h.id: h
        for h in await db.scalars(
            select(RunnerHost).where(RunnerHost.id.in_({e.host_id for e in envs}))
        )
    }
    running, busy, deleting = await _busy(db, [e.id for e in envs])
    conditions: dict[uuid.UUID, list[RunnerDeleteCondition]] = {}
    for row in await db.scalars(
        select(RunnerDeleteCondition).where(
            RunnerDeleteCondition.environment_id.in_([e.id for e in envs]),
            RunnerDeleteCondition.fired_at.is_(None),
            RunnerDeleteCondition.kind.in_(("at", "idle")),
        )
    ):
        conditions.setdefault(row.environment_id, []).append(row)
    for env in envs:
        host = hosts.get(env.host_id)
        policy = specs.policy_for(host.policy if host else None)
        idle = (now - env.last_used_at).total_seconds() if env.last_used_at else 0.0
        if env.state == "pending_delete":
            if env.id in running or env.id in deleting:
                continue
            if env.delete_after is None or env.delete_after <= now:
                await runner.delete(env, env.state_reason or "待删除到期")
                out.deleted += 1
            continue
        if env.state == "archived":
            # 会话、一次性的归档：保留配置记录 archive_retention_days 天后删除记录
            stale = env.archived_at is not None and (now - env.archived_at).total_seconds() > (
                policy["archive_retention_days"] * 86400
            )
            if env.lifetime != "persistent" and stale:
                env.state = "deleted"
                env.deleted_at = now
                env.state_reason = "归档到期"
                out.deleted += 1
            continue
        fired = False
        for condition in conditions.get(env.id, []):
            due = (condition.kind == "at" and condition.at is not None and condition.at <= now) or (
                condition.kind == "idle"
                and condition.seconds is not None
                and env.id not in busy
                and idle >= condition.seconds
            )
            if due:
                condition.fired_at = now
                await runner.mark_pending_delete(env, f"删除条件到点：{condition.kind}")
                out.pending_delete += 1
                fired = True
                break
        if fired or env.id in busy or env.pinned:
            continue
        viewer = Viewer(env.workspace_id)
        if env.state == "running":
            if idle >= specs.idle_stop_after(env.lifetime, env.start_seconds, policy):
                await runner.stop(viewer, env, f"空闲 {round(idle / 60)} 分钟，自动停止")
                out.stopped += 1
            elif (
                env.lifetime == "session"
                and (now - env.created_at).total_seconds() > policy["session_max_hours"] * 3600
            ):
                await runner.stop(viewer, env, "会话环境超过最长存活时间，自动停止")
                out.stopped += 1
            continue
        # 已停止：到点归档（常驻按没用的天数，会话按停止的时长）
        since = (now - (env.stopped_at or env.last_used_at)).total_seconds()
        if env.lifetime == "persistent":
            due = idle >= policy["persistent_archive_days"] * 86400
        else:
            due = since >= policy["session_archive_hours"] * 3600
        if due and await runner.archive(env, "停止到点，自动归档"):
            out.archived += 1


# ---------------------------------------------------------------- 资源紧张


async def _uses(db: AsyncSession, env_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not env_ids:
        return {}
    since = specs.now() - timedelta(days=7)
    rows = await db.execute(
        select(RunnerRun.environment_id, func.count())
        .where(RunnerRun.environment_id.in_(env_ids), RunnerRun.created_at >= since)
        .group_by(RunnerRun.environment_id)
    )
    return {env_id: int(count) for env_id, count in rows}


async def scores(
    db: AsyncSession, envs: list[RunnerEnvironment], policy: dict[str, float]
) -> dict[uuid.UUID, float]:
    uses = await _uses(db, [e.id for e in envs])
    now = specs.now()
    limit = policy["snapshot_max_gb"] * GB
    return {
        e.id: retention_score(e, uses.get(e.id, 0), now, snapshot_max_bytes=limit) for e in envs
    }


async def _watermarks(runner: Runner, out: TickResult) -> None:
    db = runner.db
    for host in list(await db.scalars(select(RunnerHost).where(RunnerHost.status == "online"))):
        policy = specs.policy_for(host.policy)
        envs = list(
            await db.scalars(
                select(RunnerEnvironment).where(
                    RunnerEnvironment.host_id == host.id,
                    RunnerEnvironment.state.in_(("running", "stopped")),
                    RunnerEnvironment.pinned.is_(False),
                )
            )
        )
        if not envs:
            continue
        _running, busy, _deleting = await _busy(db, [e.id for e in envs])
        ranked = await scores(db, envs, policy)
        candidates = sorted(
            (e for e in envs if e.id not in busy), key=lambda e: ranked.get(e.id, 0.0)
        )
        # 内存：按上限预留合计
        if host.mem_bytes:
            reserved = sum(
                int(e.spec.get("memory_mb", 0)) * 1024 * 1024 for e in envs if e.state == "running"
            )
            if reserved > host.mem_bytes * policy["mem_high"]:
                target = host.mem_bytes * policy["mem_low"]
                for env in [e for e in candidates if e.state == "running"][:RECLAIM_PER_TICK]:
                    if reserved <= target:
                        break
                    await runner.stop(
                        Viewer(env.workspace_id), env, "容器服务器内存紧张，按保留分停止"
                    )
                    reserved -= int(env.spec.get("memory_mb", 0)) * 1024 * 1024
                    out.stopped += 1
        # 磁盘：按最近一次对账
        total = int((host.report or {}).get("disk_bytes") or host.disk_bytes or 0)
        free = int((host.report or {}).get("disk_free_bytes") or host.disk_free_bytes or 0)
        if not total:
            continue
        usage = 1 - free / total
        if usage > policy["disk_high"]:
            archived = 0
            for env in [e for e in candidates if e.state == "stopped"]:
                if archived >= RECLAIM_PER_TICK:
                    break
                if math.isinf(ranked.get(env.id, 0.0)):
                    continue
                if await runner.archive(env, "容器服务器磁盘紧张，按保留分归档"):
                    archived += 1
                    out.archived += 1
            await _prune_images(runner, host)
        if usage > policy["disk_emergency"]:
            await notifications.notify_admins(
                db,
                host.workspace_id,
                type_="runner.disk_emergency",
                severity="error",
                title=f"容器服务器「{host.name}」磁盘告急",
                body=f"已用 {usage:.0%}：暂停新建环境，runner-agent 已停止一次性运行",
                link="/runner",
                key=f"{host.id}:disk:{specs.now():%Y%m%d%H}",
            )


async def make_room(runner: Runner, host: RunnerHost, need_bytes: int) -> int:
    """新建时整台服务器的内存不够（第 6.4 节）：按保留分从低到高停止空闲的运行中环境。

    只在能腾出 need_bytes 时才停（不白停）；返回停了几个，0 表示腾不出来。调用方负责提交。
    """
    db = runner.db
    policy = specs.policy_for(host.policy)
    envs = list(
        await db.scalars(
            select(RunnerEnvironment).where(
                RunnerEnvironment.host_id == host.id,
                RunnerEnvironment.state == "running",
                RunnerEnvironment.pinned.is_(False),
            )
        )
    )
    _runs, busy, _deleting = await _busy(db, [e.id for e in envs])
    ranked = await scores(db, envs, policy)
    chosen: list[RunnerEnvironment] = []
    freed = 0
    for env in sorted((e for e in envs if e.id not in busy), key=lambda e: ranked.get(e.id, 0.0)):
        if freed >= need_bytes or len(chosen) >= RECLAIM_PER_TICK:
            break
        chosen.append(env)
        freed += int(env.spec.get("memory_mb", 0)) * 1024 * 1024
    if freed < need_bytes:
        return 0
    for env in chosen:
        await runner.stop(Viewer(env.workspace_id), env, "新建环境时内存不够，按保留分停止")
    return len(chosen)


async def _prune_images(runner: Runner, host: RunnerHost) -> None:
    """清理没人用的模板镜像：保留每个模板当前版本、以及还在的环境用到的镜像。每小时最多一次。"""
    db = runner.db
    recent = await db.scalar(
        select(RunnerCommand.id).where(
            RunnerCommand.host_id == host.id,
            RunnerCommand.kind == "prune_images",
            RunnerCommand.created_at > specs.now() - timedelta(hours=1),
        )
    )
    if recent is not None:
        return
    keep = {
        str(ref)
        for ref in await db.scalars(
            select(RunnerTemplate.image_ref).where(
                RunnerTemplate.workspace_id == host.workspace_id,
                RunnerTemplate.image_ref.is_not(None),
            )
        )
        if ref
    }
    for spec in await db.scalars(
        select(RunnerEnvironment.spec).where(
            RunnerEnvironment.host_id == host.id,
            RunnerEnvironment.state.in_(("creating", "running", "stopped")),
        )
    ):
        if spec.get("template_image"):
            keep.add(str(spec["template_image"]))
    runner.enqueue(host, "prune_images", {"keep": sorted(keep)})


# ---------------------------------------------------------------- 清理


async def _purge(runner: Runner, out: TickResult) -> None:
    db = runner.db
    now = specs.now()
    store = SnapshotStore(db, runner.settings)
    # 过期的快照（环境删除 30 天后）、失败或卡住一天以上的上传：删掉对象存储里的块
    for snapshot in list(
        await db.scalars(
            select(RunnerSnapshot)
            .where(
                RunnerSnapshot.status != "deleted",
                (RunnerSnapshot.expires_at < now)
                | (
                    RunnerSnapshot.status.in_(("failed", "uploading"))
                    & (RunnerSnapshot.created_at < now - timedelta(days=1))
                ),
            )
            .limit(20)
        )
    ):
        await store.delete(snapshot)
        out.purged += 1
    # 删除记录：已删除超过 archive_retention_days 天、快照也删干净的环境
    hosts = {h.id: h for h in await db.scalars(select(RunnerHost))}
    for env in list(
        await db.scalars(
            select(RunnerEnvironment)
            .where(
                RunnerEnvironment.state == "deleted",
                RunnerEnvironment.deleted_at < now - timedelta(days=1),
            )
            .limit(100)
        )
    ):
        host = hosts.get(env.host_id)
        days = specs.policy_for(host.policy if host else None)["archive_retention_days"]
        if env.deleted_at is None or (now - env.deleted_at).total_seconds() < days * 86400:
            continue
        alive = await db.scalar(
            select(RunnerSnapshot.id).where(
                RunnerSnapshot.environment_id == env.id, RunnerSnapshot.status != "deleted"
            )
        )
        if alive is None:
            await db.delete(env)
            out.purged += 1
    # 运行输出保留 7 天；完成的指令保留 7 天
    await db.execute(
        update(RunnerRun)
        .where(
            RunnerRun.finished_at < now - timedelta(days=OUTPUT_KEEP_DAYS),
            RunnerRun.output != "",
        )
        .values(output="")
    )
    await db.execute(
        delete(RunnerCommand).where(
            RunnerCommand.status.in_(("done", "failed", "unknown", "cancelled")),
            RunnerCommand.finished_at < now - timedelta(days=COMMAND_KEEP_DAYS),
        )
    )
