"""托管进程的输出与结束（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 21.3、21.4）。

runner-agent 边跑边把输出按偏移交上来；这里按运行的 owner_kind 找到模块的 runner_proc_sink，
由模块转给它的下游（例如 MCP 管理器），确认了多少就把 acked_offset 推进多少。
输出本身不落工作台数据库：下游没收下时 runner-agent 从已确认处重发（文件还在容器里）。
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import runner_specs as specs
from app.domain.runner import _redact
from app.domain.runner_managed import proc_dir
from app.models import RunnerEnvironment, RunnerHost, RunnerRun
from app.modules import registry
from app.schemas.runner import RunnerProcExit, RunnerProcOutput, RunnerProcWatch

logger = logging.getLogger(__name__)

# 进程结束时写进运行记录的末尾（stderr）上限
STDERR_KEEP = 16 * 1024


async def _run_for(
    db: AsyncSession, host: RunnerHost, run_id: uuid.UUID, *, lock: bool = False
) -> tuple[RunnerRun, RunnerEnvironment]:
    query = (
        select(RunnerRun, RunnerEnvironment)
        .join(RunnerEnvironment, RunnerEnvironment.id == RunnerRun.environment_id)
        .where(
            RunnerRun.id == run_id,
            RunnerRun.kind == "proc",
            RunnerEnvironment.host_id == host.id,
        )
    )
    if lock:
        query = query.with_for_update(of=RunnerRun).execution_options(populate_existing=True)
    row = (await db.execute(query)).first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "没有这个托管进程")
    return row[0], row[1]


def _sink(run: RunnerRun) -> Any | None:
    return registry.runner_proc_sink(run.owner_kind) if run.owner_kind else None


async def accept_output(
    db: AsyncSession,
    settings: Settings,
    redis: Redis | None,
    host: RunnerHost,
    run_id: uuid.UUID,
    body: RunnerProcOutput,
    runtime: Any = None,
) -> int:
    """收下一块输出，返回新的已确认字节数。

    偏移对不上就只返回已确认数（runner-agent 从那里重发）。"""
    run, _env = await _run_for(db, host, run_id, lock=True)
    if body.offset != run.acked_offset:
        return run.acked_offset
    try:
        data = base64.b64decode(body.data_b64, validate=True)
    except ValueError:
        raise ApiError(422, "VALIDATION_FAILED", "data_b64 不对") from None
    if not data:
        return run.acked_offset
    if run.status == "queued":
        run.status = "running"
        run.started_at = run.started_at or specs.now()
    sink = _sink(run)
    if sink is None:
        # 没有去处（模块被删了）：丢掉，免得 runner-agent 一直重发
        run.acked_offset += len(data)
        return run.acked_offset
    acked = int(await sink.output(db, settings, run, run.acked_offset, data, runtime=runtime))
    if acked > run.acked_offset:
        run.acked_offset = min(acked, run.acked_offset + len(data))
    return run.acked_offset


async def accept_exit(
    db: AsyncSession,
    settings: Settings,
    redis: Redis | None,
    host: RunnerHost,
    run_id: uuid.UUID,
    body: RunnerProcExit,
    runtime: Any = None,
) -> bool:
    """进程结束：结束这条运行，交给模块。重复回报只认第一次。"""
    run, env = await _run_for(db, host, run_id, lock=True)
    if run.finished_at is not None:
        return False
    run.finished_at = specs.now()
    run.started_at = run.started_at or run.finished_at
    # 落库前脱敏：这个进程注入的凭据值、环境的密钥值，再按密钥样式（和普通执行一样）
    secrets = await redaction_values(db, settings, redis, run, env)
    tail = _redact(body.stderr_tail[-STDERR_KEEP:], secrets)
    error = _redact(body.error, secrets) if body.error else None
    note = f"\n[工作台] {error}" if error else ""
    run.output = (tail + note)[-specs.OUTPUT_LIMIT :]
    if body.state == "exited":
        run.exit_code = body.exit_code
        run.status = "succeeded" if body.exit_code == 0 else "failed"
    elif body.state == "timeout":
        run.status = "timeout"
    elif body.state == "killed":
        run.exit_code = body.exit_code
        run.status = "cancelled"
    else:
        run.status = "unknown"
    env.last_used_at = specs.now()
    sink = _sink(run)
    if sink is not None:
        try:
            info = {**body.model_dump(), "stderr_tail": tail, "error": error}
            await sink.exit(db, settings, run, info, runtime=runtime)
        except Exception:  # 模块转不出去时由它自己的对账补发
            logger.exception("托管进程 %s 的结束交给模块失败", run.id)
    return True


async def redaction_values(
    db: AsyncSession,
    settings: Settings,
    redis: Redis | None,
    run: RunnerRun,
    env: RunnerEnvironment | None,
) -> list[str]:
    from app.domain.runner import Runner
    from app.domain.runner_managed import take_proc_secrets

    values = await take_proc_secrets(redis, settings, run.id)
    if env is not None:
        values += await Runner(db, settings, redis).secrets_for(env)
    return values


async def notify_exit(
    db: AsyncSession, settings: Settings, run: RunnerRun, state: str, error: str | None
) -> None:
    """运行已经由工作台记成结束（没起来、过期、容器被删）：告诉模块。失败由模块自己的对账补发。"""
    sink = _sink(run)
    if sink is None:
        return
    try:
        await sink.exit(
            db,
            settings,
            run,
            {"exit_code": None, "state": state, "stderr_tail": "", "error": error},
        )
    except Exception:
        logger.exception("托管进程 %s 的结束交给模块失败", run.id)


async def open_procs(db: AsyncSession, host: RunnerHost) -> list[RunnerProcWatch]:
    """这台服务器上已经起来、还没结束的托管进程（对账时交给 runner-agent 接着读）。"""
    rows = await db.execute(
        select(RunnerRun, RunnerEnvironment)
        .join(RunnerEnvironment, RunnerEnvironment.id == RunnerRun.environment_id)
        .where(
            RunnerRun.kind == "proc",
            RunnerRun.finished_at.is_(None),
            RunnerRun.status == "running",
            RunnerEnvironment.host_id == host.id,
        )
        .limit(500)
    )
    out = []
    for run, env in rows:
        started = run.started_at or run.created_at
        out.append(
            RunnerProcWatch(
                run_id=run.id,
                env_id=env.id,
                container=env.container_name,
                proc_dir=proc_dir(run.id),
                acked=run.acked_offset,
                timeout_at=started + timedelta(seconds=run.timeout_seconds),
            )
        )
    return out
