"""服务器状态：上报、样本、趋势、告警（T-0113，docs/v2/AGENT_OPS_PLAN.md 第 5 节）。

- 上报：每个实例每分钟最多 2 次；样本记到收到上报的那一分钟（同一分钟再报就覆盖）。
  CPU 使用率 = 1 − 空闲增量 / 总增量；网速 = 字节增量 / 秒数。计数器变小（重启、回绕）或
  两次上报隔得太久（超过 10 分钟）时这一分钟记为空，不编造数值。
- 样本保留 30 天，Worker 每小时清理一次。
- 告警（Worker 每分钟检查，通知 owner / admin）：磁盘可用低于 10%、内存可用低于 5%、
  15 分钟负载连续 15 分钟超过核心数的 2 倍、在线却 30 分钟没有上报（离线的由离线提醒负责，不重复）。
  同一实例同一类告警恢复前只提醒一次（记在 agent_host_state.alerts）。阈值先用默认值（D7）。
- 智能体读自己的服务器状态：get_host_status（只看调用它的实例）。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.domain import notifications
from app.domain.policy import WorkspaceAccess, not_found
from app.domain.ratelimit import WindowLimiter
from app.models import AgentHostSample, AgentHostState, AgentInstance
from app.schemas.host import (
    AlertKind,
    DiskOut,
    HostCurrent,
    HostPoint,
    HostRange,
    HostReport,
    HostReportResult,
    HostStatusOut,
    HostSummaryItem,
    HostSummaryList,
)

logger = logging.getLogger("app.host_metrics")

REPORTS_PER_MINUTE = 2
RETENTION = timedelta(days=30)
STALE_AFTER = timedelta(minutes=3)
MAX_GAP = timedelta(minutes=10)
NOT_REPORTING_AFTER = timedelta(minutes=30)
DISK_FREE_MIN = 10.0  # 百分比
MEMORY_FREE_MIN = 5.0
LOAD_FACTOR = 2.0
LOAD_MINUTES = 15
RANGES: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
ALERT_TITLES: dict[str, str] = {
    "disk_low": "磁盘可用空间不足 10%",
    "memory_low": "内存可用不足 5%",
    "load_high": "负载持续偏高（15 分钟负载超过核心数的 2 倍）",
    "not_reporting": "30 分钟没有上报服务器状态",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _delta(new: int | None, old: int | None) -> int | None:
    if new is None or old is None or new < old:
        return None
    return new - old


def _percent_used(total: int | None, free: int | None) -> float | None:
    if not total or free is None or free > total:
        return None
    return round(100.0 * (total - free) / total, 2)


def _disk(total: int | None, free: int | None) -> DiskOut | None:
    used = _percent_used(total, free)
    if used is None or total is None or free is None:
        return None
    return DiskOut(total=total, free=free, used=total - free, used_percent=used)


class HostService:
    def __init__(self, db: AsyncSession, redis: Redis | None) -> None:
        self.db = db
        self.redis = redis

    # ---------------------------------------------------------------- 上报

    async def report(self, instance: AgentInstance, body: HostReport) -> HostReportResult:
        if self.redis is not None:
            retry = await WindowLimiter(self.redis, 60).hit(
                f"host_reports:{instance.id}", REPORTS_PER_MINUTE
            )
            if retry is not None:
                raise ApiError(
                    429,
                    "RATE_LIMITED",
                    "上报太频繁",
                    headers={"Retry-After": str(retry)},
                )
        now = _now()
        minute = _minute(now)
        state = await self.db.get(AgentHostState, instance.id, with_for_update=True)
        cpu_percent: float | None = None
        rx_rate: int | None = None
        tx_rate: int | None = None
        if state is not None and timedelta(0) < now - state.reported_at <= MAX_GAP:
            total = _delta(body.cpu.total_ticks, state.total_ticks)
            idle = _delta(body.cpu.idle_ticks, state.idle_ticks)
            if total and idle is not None and idle <= total:
                cpu_percent = round(100.0 * (1 - idle / total), 2)
            seconds = (now - state.reported_at).total_seconds()
            if body.network is not None and seconds > 0:
                rx = _delta(body.network.rx_bytes, state.rx_bytes)
                tx = _delta(body.network.tx_bytes, state.tx_bytes)
                rx_rate = round(rx / seconds) if rx is not None else None
                tx_rate = round(tx / seconds) if tx is not None else None
        load: list[float | None] = list(body.cpu.load) if body.cpu.load else [None, None, None]
        mem = body.memory
        data = body.disks.data
        root = body.disks.root
        sample: dict[str, Any] = {
            "cpu_percent": cpu_percent,
            "load1": load[0],
            "load5": load[1],
            "load15": load[2],
            "cores": body.cpu.cores,
            "mem_total": mem.total,
            "mem_available": min(mem.available, mem.total),
            "swap_total": mem.swap_total,
            "swap_used": max(mem.swap_total - mem.swap_free, 0),
            "data_total": data.total if data else None,
            "data_free": data.free if data else None,
            "root_total": root.total if root else None,
            "root_free": root.free if root else None,
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
        }
        await self.db.execute(
            pg_insert(AgentHostSample)
            .values(
                instance_id=instance.id, minute=minute, workspace_id=instance.workspace_id, **sample
            )
            .on_conflict_do_update(index_elements=["instance_id", "minute"], set_=sample)
        )
        counters = {
            "workspace_id": instance.workspace_id,
            "reported_at": now,
            "cpu_model": body.cpu.model,
            "total_ticks": body.cpu.total_ticks,
            "idle_ticks": body.cpu.idle_ticks,
            "rx_bytes": body.network.rx_bytes if body.network else None,
            "tx_bytes": body.network.tx_bytes if body.network else None,
            "uptime_seconds": body.uptime_seconds,
            "mem_limit": mem.limit,
            "latest": sample,
        }
        if state is None:
            self.db.add(AgentHostState(instance_id=instance.id, **counters))
        else:
            for key, value in counters.items():
                setattr(state, key, value)
        await self.db.commit()
        return HostReportResult(minute=minute)

    # ---------------------------------------------------------------- 读取

    @staticmethod
    def current(state: AgentHostState, now: datetime) -> HostCurrent:
        latest = state.latest or {}
        total, available = latest.get("mem_total"), latest.get("mem_available")
        load = [latest.get(k) for k in ("load1", "load5", "load15")]
        return HostCurrent(
            reported_at=state.reported_at,
            stale=now - state.reported_at > STALE_AFTER,
            cpu_model=state.cpu_model,
            cores=latest.get("cores"),
            cpu_percent=latest.get("cpu_percent"),
            load=[float(v) for v in load if v is not None] if None not in load else None,
            mem_total=total,
            mem_available=available,
            mem_used=(total - available) if total is not None and available is not None else None,
            mem_limit=state.mem_limit,
            swap_total=latest.get("swap_total"),
            swap_used=latest.get("swap_used"),
            data_disk=_disk(latest.get("data_total"), latest.get("data_free")),
            root_disk=_disk(latest.get("root_total"), latest.get("root_free")),
            rx_rate=latest.get("rx_rate"),
            tx_rate=latest.get("tx_rate"),
            uptime_seconds=state.uptime_seconds,
        )

    async def _instance(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentInstance:
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id, AgentInstance.workspace_id == access.workspace.id
            )
        )
        if instance is None:
            raise not_found()
        return instance

    async def points(self, instance_id: uuid.UUID, since: datetime) -> list[HostPoint]:
        hour = func.date_trunc("hour", AgentHostSample.minute)
        mem = (
            100.0
            * (AgentHostSample.mem_total - AgentHostSample.mem_available)
            / func.nullif(AgentHostSample.mem_total, 0)
        )
        data = (
            100.0
            * (AgentHostSample.data_total - AgentHostSample.data_free)
            / func.nullif(AgentHostSample.data_total, 0)
        )
        root = (
            100.0
            * (AgentHostSample.root_total - AgentHostSample.root_free)
            / func.nullif(AgentHostSample.root_total, 0)
        )
        rows = await self.db.execute(
            select(
                hour,
                func.avg(AgentHostSample.cpu_percent),
                func.max(AgentHostSample.cpu_percent),
                func.avg(AgentHostSample.load1),
                func.max(AgentHostSample.load1),
                func.avg(mem),
                func.max(mem),
                func.avg(data),
                func.avg(root),
                func.avg(AgentHostSample.rx_rate),
                func.max(AgentHostSample.rx_rate),
                func.avg(AgentHostSample.tx_rate),
                func.max(AgentHostSample.tx_rate),
            )
            .where(AgentHostSample.instance_id == instance_id, AgentHostSample.minute >= since)
            .group_by(hour)
            .order_by(hour)
        )

        def r(value: Any) -> float | None:
            return round(float(value), 2) if value is not None else None

        return [
            HostPoint(
                at=row[0],
                cpu_avg=r(row[1]),
                cpu_max=r(row[2]),
                load_avg=r(row[3]),
                load_max=r(row[4]),
                mem_avg=r(row[5]),
                mem_max=r(row[6]),
                data_avg=r(row[7]),
                root_avg=r(row[8]),
                rx_avg=r(row[9]),
                rx_max=r(row[10]),
                tx_avg=r(row[11]),
                tx_max=r(row[12]),
            )
            for row in rows.all()
        ]

    async def status(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, span: HostRange
    ) -> HostStatusOut:
        instance = await self._instance(access, instance_id)
        return await self.status_of(instance, span)

    async def status_of(self, instance: AgentInstance, span: HostRange) -> HostStatusOut:
        now = _now()
        state = await self.db.get(AgentHostState, instance.id)
        points = await self.points(instance.id, now - RANGES[span]) if state is not None else []
        return HostStatusOut(
            instance_id=instance.id,
            reported=state is not None,
            current=self.current(state, now) if state is not None else None,
            range=span,
            points=points,
            alerts=sorted((state.alerts or {}).keys()) if state is not None else [],
        )

    async def summary(self, access: WorkspaceAccess) -> HostSummaryList:
        now = _now()
        rows = (
            await self.db.execute(
                select(AgentInstance.id, AgentHostState)
                .outerjoin(AgentHostState, AgentHostState.instance_id == AgentInstance.id)
                .where(AgentInstance.workspace_id == access.workspace.id)
                .order_by(AgentInstance.name, AgentInstance.id)
            )
        ).all()
        items = []
        for instance_id, state in rows:
            if state is None:
                items.append(
                    HostSummaryItem(
                        instance_id=instance_id,
                        reported_at=None,
                        stale=True,
                        cpu_percent=None,
                        mem_used_percent=None,
                        disk_used_percent=None,
                        alerts=[],
                    )
                )
                continue
            current = self.current(state, now)
            disks = [d.used_percent for d in (current.data_disk, current.root_disk) if d]
            items.append(
                HostSummaryItem(
                    instance_id=instance_id,
                    reported_at=state.reported_at,
                    stale=current.stale,
                    cpu_percent=current.cpu_percent,
                    mem_used_percent=_percent_used(current.mem_total, current.mem_available),
                    disk_used_percent=max(disks) if disks else None,
                    alerts=sorted((state.alerts or {}).keys()),
                )
            )
        return HostSummaryList(items=items)


# ---------------------------------------------------------------- Worker：清理与告警


async def purge(db: AsyncSession, now: datetime | None = None) -> int:
    moment = now or _now()
    result = await db.execute(
        delete(AgentHostSample).where(AgentHostSample.minute < moment - RETENTION)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def _load_high(db: AsyncSession, state: AgentHostState, now: datetime) -> bool:
    rows = (
        await db.execute(
            select(AgentHostSample.load15, AgentHostSample.cores).where(
                AgentHostSample.instance_id == state.instance_id,
                AgentHostSample.minute >= _minute(now) - timedelta(minutes=LOAD_MINUTES),
            )
        )
    ).all()
    if len(rows) < LOAD_MINUTES:
        return False
    return all(load is not None and cores and load > LOAD_FACTOR * cores for load, cores in rows)


async def active_alerts(
    db: AsyncSession, state: AgentHostState, instance: AgentInstance, now: datetime
) -> set[AlertKind]:
    found: set[AlertKind] = set()
    if instance.status == "online" and now - state.reported_at > NOT_REPORTING_AFTER:
        found.add("not_reporting")
        return found
    latest = state.latest or {}
    for prefix in ("data", "root"):
        used = _percent_used(latest.get(f"{prefix}_total"), latest.get(f"{prefix}_free"))
        if used is not None and 100.0 - used < DISK_FREE_MIN:
            found.add("disk_low")
    mem_used = _percent_used(latest.get("mem_total"), latest.get("mem_available"))
    if mem_used is not None and 100.0 - mem_used < MEMORY_FREE_MIN:
        found.add("memory_low")
    if await _load_high(db, state, now):
        found.add("load_high")
    return found


async def check_alerts(db: AsyncSession, now: datetime | None = None) -> int:
    """每分钟：新出现的告警通知 owner / admin；恢复的清掉（下次再出现时再提醒）。

    返回新提醒的条数。
    """
    moment = now or _now()
    rows = (
        await db.execute(
            select(AgentHostState, AgentInstance)
            .join(AgentInstance, AgentInstance.id == AgentHostState.instance_id)
            .where(AgentInstance.status != "disabled")
            .with_for_update(of=AgentHostState, skip_locked=True)
        )
    ).all()
    sent = 0
    for state, instance in rows:
        active = await active_alerts(db, state, instance, moment)
        previous = dict(state.alerts or {})
        for kind in sorted(active - set(previous)):
            await notifications.notify_admins(
                db,
                instance.workspace_id,
                type_=f"host.{kind}",
                severity="warning",
                title=f"「{instance.name}」{ALERT_TITLES[kind]}",
                body="打开 OpenClaw 实例页的“服务器状态”查看详情。",
                link="/agents",
                key=f"{instance.id}:{moment.isoformat()}",
            )
            previous[kind] = moment.isoformat()
            sent += 1
        for stale in set(previous) - set(active):
            previous.pop(stale, None)
        if previous != (state.alerts or {}):
            state.alerts = previous
    await db.commit()
    if sent:
        logger.info("host alerts sent", extra={"count": sent})
    return sent
