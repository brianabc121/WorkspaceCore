"""运行服务器的定时检查与回收（docs/v2/RUNNER_PLAN.md 第 5、6 节）。

T-0136 ~ T-0138 先做每分钟一次的基础检查：
- 指令超过时限：能重发的重发（最多 3 次）；`exec` 记为结果未知（不自动重跑）；
  建环境、构建模板记为失败；
- 运行服务器超过 90 秒没有长轮询 → 不在线，通知管理员；
- 运行中的环境空闲到点 → 停止（数据卷保留）；会话环境超过最长存活 → 停止；
- 删除条件 `at`、`idle` 到点 → 待删除；待删除过了宽限期 → 删除。
归档、快照与重建、保留分、水位与 task_done / issue_closed / project_archived 条件在 T-0139。
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
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
from app.models import (
    AgentWakeup,
    RunnerCommand,
    RunnerDeleteCondition,
    RunnerEnvironment,
    RunnerHost,
    RunnerRun,
    RunnerTemplate,
)

logger = logging.getLogger("app.runner")


@dataclass
class TickResult:
    expired: int = 0
    retried: int = 0
    offline: list[uuid.UUID] = field(default_factory=list)
    stopped: int = 0
    pending_delete: int = 0
    deleted: int = 0


async def after_snapshot(
    runner: Runner,
    env: RunnerEnvironment,
    command: RunnerCommand,
    ok: bool,
    result: dict[str, Any],
    error: str | None,
) -> None:
    """快照指令的结果（常驻环境归档前做）：成功就记下位置与大小，归档在 T-0139 接着做。"""
    if not ok:
        env.state_reason = f"快照失败：{(error or '')[:300]}"
        return
    env.snapshot_key = str(result.get("key") or "") or None
    size = result.get("bytes")
    env.snapshot_bytes = int(size) if isinstance(size, int | float) else None
    env.snapshot_at = specs.now()


async def tick(db: AsyncSession, settings: Settings, redis: Redis | None = None) -> TickResult:
    runner = Runner(db, settings, redis)
    out = TickResult()
    await _expire_commands(runner, out)
    await _hosts_offline(runner, out)
    await _idle_and_conditions(runner, out)
    await db.commit()
    return out


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
        command.error = "运行服务器在时限内没有回报结果"
        env = (
            await db.get(RunnerEnvironment, command.environment_id)
            if command.environment_id
            else None
        )
        if command.kind == "exec" and command.run_id is not None:
            run = await db.get(RunnerRun, command.run_id)
            if run is not None and run.finished_at is None:
                run.status = "unknown"
                run.finished_at = specs.now()
                run.output = (
                    run.output + "\n[工作台] 结果未知：运行服务器在时限内没有回报（不会自动重跑）"
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
                            prompt=f"[工作台] 运行 {run.id} 结果未知（运行服务器没有回报）。",
                            run_at=specs.now(),
                        )
                    )
        elif command.kind == "create_env" and env is not None and env.state == "creating":
            env.state = "failed"
            env.state_reason = "创建超时：运行服务器在时限内没有回报"
        elif command.kind == "build_template" and command.template_id is not None:
            template = await db.get(RunnerTemplate, command.template_id)
            if template is not None and template.build_status == "building":
                template.build_status = "failed"
                template.build_error = "构建超时：运行服务器在时限内没有回报"


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
            title=f"运行服务器「{host.name}」不在线",
            body="超过 90 秒没有连上工作台；智能体暂时不能建环境或执行命令",
            link="/runner",
            key=f"{host.id}:offline:{specs.now():%Y%m%d%H}",
        )


async def _busy(
    db: AsyncSession, env_ids: list[uuid.UUID]
) -> tuple[set[uuid.UUID], set[uuid.UUID], set[uuid.UUID]]:
    """(有没结束的运行, 有在途指令, 已经在删) 的环境。"""
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
    deleting = {e for e, kind in pending if e and kind == "delete_env"}
    return runs, commands | runs, deleting


async def _idle_and_conditions(runner: Runner, out: TickResult) -> None:
    db = runner.db
    now = specs.now()
    envs = list(
        await db.scalars(
            select(RunnerEnvironment).where(
                RunnerEnvironment.state.in_(("running", "stopped", "pending_delete"))
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
        if fired or env.state != "running" or env.id in busy or env.pinned:
            continue
        viewer = Viewer(env.workspace_id)
        if idle >= specs.idle_stop_after(env.lifetime, env.start_seconds, policy):
            await runner.stop(viewer, env, f"空闲 {round(idle / 60)} 分钟，自动停止")
            out.stopped += 1
        elif (
            env.lifetime == "session"
            and env.created_at is not None
            and (now - env.created_at).total_seconds() > policy["session_max_hours"] * 3600
        ):
            await runner.stop(viewer, env, "会话环境超过最长存活时间，自动停止")
            out.stopped += 1
