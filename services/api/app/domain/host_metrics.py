"""服务器状态：上报、样本、趋势、告警（T-0113，docs/v2/AGENT_OPS_PLAN.md 第 5 节；
一台服务器多个实例 T-0146，docs/v2/MULTI_OPENCLAW_PLAN.md 第 5、9、10 节）。

- 上报：每个实例每分钟最多 2 次。分两级：
  - 整台服务器：样本记在所在服务器的这一分钟，先到的写入、后到的忽略
    （同一台服务器上的每个实例都会报）；
    CPU 使用率 = 1 − 空闲增量 / 总增量，用同一个实例两次上报的累计值算，所以先到的那份本身就对；
  - 这个实例：自己容器的 CPU（用量增量 / 秒数，几个核）、内存、进程数、数据目录、网速，
    同一分钟再报就覆盖。
  计数器变小（重启、回绕）或两次上报隔得太久（超过 10 分钟）时这一分钟记为空，不编造数值。
  每个实例有自己的网卡，整台服务器的网速是同一分钟各实例网速之和（读的时候算）。
- 所在服务器（D3）：上报带服务器指纹（boot_id 的 HMAC）。指纹相同就是同一台服务器；
  机器重启后指纹会变：这台服务器最后一次上报早于新指纹那台机器的开机时间，就当作同一台重启了，跟着换指纹。
  管理员手动指定过的（server_manual）不自动改归属。
- 样本保留 30 天，Worker 每小时清理一次。
- 告警（Worker 每分钟检查，通知 owner / admin；同一类恢复前只提醒一次）：
  - 整台服务器：磁盘可用低于 10%、内存可用低于 5%、15 分钟负载连续 15 分钟超过核心数的 2 倍、
    有在线实例却 30 分钟没有任何实例上报；
  - 单个实例：内存超过上限的 90% 持续 10 分钟、一小时内重启 3 次以上、
    连续 15 分钟占整台服务器 80% 以上的 CPU。
- 智能体读自己的服务器状态：get_host_status（只看调用它的实例与它所在服务器的整机数值）。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.errors import ApiError
from app.domain import notifications
from app.domain.policy import WorkspaceAccess, not_found
from app.domain.ratelimit import WindowLimiter
from app.models import (
    AgentHostState,
    AgentInstance,
    AgentInstanceSample,
    AgentServer,
    AgentServerSample,
)
from app.schemas.host import (
    AgentAlertKind,
    AgentLoad,
    AgentPoint,
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
    ServerRef,
)

logger = logging.getLogger("app.host_metrics")

REPORTS_PER_MINUTE = 2
RETENTION = timedelta(days=30)
STALE_AFTER = timedelta(minutes=3)
MAX_GAP = timedelta(minutes=10)
NOT_REPORTING_AFTER = timedelta(minutes=30)
# 判断“同一台服务器重启了”：旧指纹最后一次上报不晚于新指纹那台机器开机后这么久
REBOOT_SLACK = timedelta(seconds=30)
DISK_FREE_MIN = 10.0  # 百分比
MEMORY_FREE_MIN = 5.0
LOAD_FACTOR = 2.0
LOAD_MINUTES = 15
MEM_NEAR_LIMIT = 0.9
MEM_NEAR_MINUTES = 10
CPU_HOG_PERCENT = 80.0
CPU_HOG_MINUTES = 15
RESTARTS_PER_HOUR = 3
RESTART_WINDOW = timedelta(hours=1)
RANGES: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
ALERT_TITLES: dict[str, str] = {
    "disk_low": "磁盘可用空间不足 10%",
    "memory_low": "内存可用不足 5%",
    "load_high": "负载持续偏高（15 分钟负载超过核心数的 2 倍）",
    "not_reporting": "30 分钟没有任何实例上报服务器状态",
}
AGENT_ALERT_TITLES: dict[str, str] = {
    "mem_near_limit": "内存用量持续超过上限的 90%",
    "restarting": "一小时内重启了 3 次以上",
    "cpu_hog": "持续 15 分钟占用整台服务器 80% 以上的 CPU",
}
# pg_advisory_xact_lock 的命名空间：同一个工作空间里识别服务器时串行（避免两份同指纹的服务器）
_LOCK_SPACE = 0x6D77_686F_7374  # "mwhost"


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


def _round(value: Any) -> float | None:
    return round(float(value), 2) if value is not None else None


def _lock_key(workspace_id: uuid.UUID) -> int:
    return (int.from_bytes(workspace_id.bytes[:8], "big") ^ _LOCK_SPACE) - (1 << 63)


def _rebooted(server: AgentServer, uptime: int | None, now: datetime) -> bool:
    """这台服务器（旧指纹）最后一次上报早于新指纹那台机器的开机时间：当作同一台重启了。"""
    if server.reported_at is None:
        return True
    if uptime is None:
        return False
    return server.reported_at <= now - timedelta(seconds=uptime) + REBOOT_SLACK


def _restarts(container: dict[str, Any], now: datetime) -> list[str]:
    cutoff = now - RESTART_WINDOW
    return [at for at in container.get("restarts") or [] if datetime.fromisoformat(at) >= cutoff]


class HostService:
    def __init__(self, db: AsyncSession, redis: Redis | None) -> None:
        self.db = db
        self.redis = redis

    # ---------------------------------------------------------------- 所在服务器

    async def _create_server(self, instance: AgentInstance, key: str | None) -> AgentServer:
        server = AgentServer(
            workspace_id=instance.workspace_id,
            name=f"自动识别的服务器（{instance.name}）"[:100],
            kind="auto",
            machine_key=key,
        )
        self.db.add(server)
        await self.db.flush()
        return server

    async def _move(
        self, instance: AgentInstance, old: AgentServer | None, new: AgentServer
    ) -> None:
        """自动归到另一台服务器（手动指定过的不改）；原来那台自动识别的服务器空了就删掉。"""
        await self.db.execute(
            update(AgentInstance)
            .where(AgentInstance.id == instance.id, AgentInstance.server_manual.is_(False))
            .values(server_id=new.id)
            .execution_options(synchronize_session=False)
        )
        # 只改内存里的值、不标脏：提交时不能再无条件写一次 server_id（管理员可能刚手动指定）
        set_committed_value(instance, "server_id", new.id)
        if old is None or old.kind != "auto" or old.id == new.id:
            return
        left = await self.db.scalar(
            select(func.count())
            .select_from(AgentInstance)
            .where(AgentInstance.server_id == old.id, AgentInstance.id != instance.id)
        )
        if not left:
            await self.db.delete(old)

    async def _server_for(
        self, instance: AgentInstance, key: str | None, uptime: int | None, now: datetime
    ) -> tuple[AgentServer, bool]:
        """这次上报属于哪台服务器，以及整机数据要不要记到这台服务器上。"""
        current = await self.db.get(AgentServer, instance.server_id) if instance.server_id else None
        if current is not None and current.workspace_id != instance.workspace_id:
            current = None
        if key is None:
            # 读不到 boot_id：相信现在的归属；还没有归属就单独一台
            if current is None:
                current = await self._create_server(instance, None)
                await self._move(instance, None, current)
            return current, True
        if current is not None and current.machine_key == key:
            return current, True
        await self.db.execute(select(func.pg_advisory_xact_lock(_lock_key(instance.workspace_id))))
        if current is not None:
            await self.db.refresh(current)
            if current.machine_key == key:
                return current, True
        by_key = await self.db.scalar(
            select(AgentServer).where(
                AgentServer.workspace_id == instance.workspace_id, AgentServer.machine_key == key
            )
        )
        adopt = current is not None and (
            current.machine_key is None or _rebooted(current, uptime, now)
        )
        if instance.server_manual and current is not None:
            if by_key is None and adopt:
                current.machine_key = key
                return current, True
            # 指纹对不上管理员指定的那台：整机数据不记，只记这个实例自己的
            return current, False
        if by_key is not None:
            await self._move(instance, current, by_key)
            return by_key, True
        if current is not None and adopt:
            current.machine_key = key
            return current, True
        server = await self._create_server(instance, key)
        await self._move(instance, current, server)
        return server, True

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
        recent = state is not None and timedelta(0) < now - state.reported_at <= MAX_GAP
        seconds = (now - state.reported_at).total_seconds() if state is not None else 0.0
        cpu_percent: float | None = None
        rx_rate: int | None = None
        tx_rate: int | None = None
        cpu_cores: float | None = None
        if state is not None and recent:
            total = _delta(body.cpu.total_ticks, state.total_ticks)
            idle = _delta(body.cpu.idle_ticks, state.idle_ticks)
            if total and idle is not None and idle <= total:
                cpu_percent = round(100.0 * (1 - idle / total), 2)
            if body.network is not None and seconds > 0:
                rx = _delta(body.network.rx_bytes, state.rx_bytes)
                tx = _delta(body.network.tx_bytes, state.tx_bytes)
                rx_rate = round(rx / seconds) if rx is not None else None
                tx_rate = round(tx / seconds) if tx is not None else None
            usec = _delta(body.container.cpu_usec if body.container else None, state.cpu_usec)
            if usec is not None and seconds > 0:
                cpu_cores = round(usec / seconds / 1_000_000, 3)

        load: list[float | None] = list(body.cpu.load) if body.cpu.load else [None, None, None]
        mem = body.memory
        data = body.disks.data
        root = body.disks.root
        host: dict[str, Any] = {
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
        }

        server, write_host = await self._server_for(
            instance, body.host_key, body.uptime_seconds, now
        )
        if write_host:
            await self.db.execute(
                pg_insert(AgentServerSample)
                .values(
                    server_id=server.id, minute=minute, workspace_id=instance.workspace_id, **host
                )
                .on_conflict_do_nothing(index_elements=["server_id", "minute"])
            )
            server.latest = host
            server.reported_at = now
            server.cpu_model = body.cpu.model
            server.uptime_seconds = body.uptime_seconds

        previous = dict(state.container or {}) if state is not None else {}
        restarts = _restarts(previous, now)
        box = body.container
        if (
            box is not None
            and box.cpu_usec is not None
            and state is not None
            and state.cpu_usec is not None
            and box.cpu_usec < state.cpu_usec
        ):
            restarts.append(now.isoformat())  # 容器重启后 cgroup 的累计值从 0 开始
        data_bytes = box.data_bytes if box and box.data_bytes is not None else None
        container: dict[str, Any] = {
            "available": box is not None,
            "cpu_cores": cpu_cores,
            "cpu_percent": (
                round(100.0 * cpu_cores / body.cpu.cores, 2)
                if cpu_cores is not None and body.cpu.cores
                else None
            ),
            "mem_bytes": box.mem_bytes if box else None,
            "mem_limit": mem.limit,
            "pids": box.pids if box else None,
            "data_bytes": data_bytes if data_bytes is not None else previous.get("data_bytes"),
            "data_truncated": (
                box.data_truncated
                if box and box.data_bytes is not None
                else bool(previous.get("data_truncated"))
            ),
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
            "restarts": restarts,
        }
        agent: dict[str, Any] = {
            "server_id": server.id,
            "cpu_cores": container["cpu_cores"],
            "cpu_percent": container["cpu_percent"],
            "mem_bytes": container["mem_bytes"],
            "mem_limit": container["mem_limit"],
            "pids": container["pids"],
            "data_bytes": container["data_bytes"],
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
        }
        await self.db.execute(
            pg_insert(AgentInstanceSample)
            .values(
                instance_id=instance.id, minute=minute, workspace_id=instance.workspace_id, **agent
            )
            .on_conflict_do_update(index_elements=["instance_id", "minute"], set_=agent)
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
            "cpu_usec": box.cpu_usec if box else None,
            "machine_key": body.host_key,
            "container": container,
            "latest": host,
        }
        if state is None:
            self.db.add(AgentHostState(instance_id=instance.id, **counters))
        else:
            for key, value in counters.items():
                setattr(state, key, value)
        await self.db.commit()
        return HostReportResult(minute=minute)

    # ---------------------------------------------------------------- 读取：整台服务器

    async def _network(self, server_id: uuid.UUID, now: datetime) -> tuple[int | None, int | None]:
        """这台服务器上最近 3 分钟报过的实例，网速之和。"""
        rows = (
            await self.db.execute(
                select(AgentHostState.container)
                .join(AgentInstance, AgentInstance.id == AgentHostState.instance_id)
                .where(
                    AgentInstance.server_id == server_id,
                    AgentHostState.reported_at >= now - STALE_AFTER,
                )
            )
        ).scalars()
        rx: int | None = None
        tx: int | None = None
        for box in rows:
            if (box or {}).get("rx_rate") is not None:
                rx = (rx or 0) + int(box["rx_rate"])
            if (box or {}).get("tx_rate") is not None:
                tx = (tx or 0) + int(box["tx_rate"])
        return rx, tx

    async def server_current(self, server: AgentServer, now: datetime) -> HostCurrent | None:
        if server.reported_at is None:
            return None
        latest = server.latest or {}
        total, available = latest.get("mem_total"), latest.get("mem_available")
        load = [latest.get(k) for k in ("load1", "load5", "load15")]
        rx, tx = await self._network(server.id, now)
        return HostCurrent(
            reported_at=server.reported_at,
            stale=now - server.reported_at > STALE_AFTER,
            cpu_model=server.cpu_model,
            cores=latest.get("cores"),
            cpu_percent=latest.get("cpu_percent"),
            load=[float(v) for v in load if v is not None] if None not in load else None,
            mem_total=total,
            mem_available=available,
            mem_used=(total - available) if total is not None and available is not None else None,
            mem_limit=None,  # 整台服务器没有容器上限；每个智能体的上限见 agent.mem_limit
            swap_total=latest.get("swap_total"),
            swap_used=latest.get("swap_used"),
            data_disk=_disk(latest.get("data_total"), latest.get("data_free")),
            root_disk=_disk(latest.get("root_total"), latest.get("root_free")),
            rx_rate=rx,
            tx_rate=tx,
            uptime_seconds=server.uptime_seconds,
        )

    async def server_points(self, server_id: uuid.UUID, since: datetime) -> list[HostPoint]:
        sample = AgentServerSample
        hour = func.date_trunc("hour", sample.minute)
        mem = 100.0 * (sample.mem_total - sample.mem_available) / func.nullif(sample.mem_total, 0)
        data = 100.0 * (sample.data_total - sample.data_free) / func.nullif(sample.data_total, 0)
        root = 100.0 * (sample.root_total - sample.root_free) / func.nullif(sample.root_total, 0)
        rows = (
            await self.db.execute(
                select(
                    hour,
                    func.avg(sample.cpu_percent),
                    func.max(sample.cpu_percent),
                    func.avg(sample.load1),
                    func.max(sample.load1),
                    func.avg(mem),
                    func.max(mem),
                    func.avg(data),
                    func.avg(root),
                )
                .where(sample.server_id == server_id, sample.minute >= since)
                .group_by(hour)
                .order_by(hour)
            )
        ).all()
        # 网速：每分钟先把这台服务器上各实例的加起来，再按小时求平均与峰值
        per_minute = (
            select(
                AgentInstanceSample.minute.label("minute"),
                func.sum(AgentInstanceSample.rx_rate).label("rx"),
                func.sum(AgentInstanceSample.tx_rate).label("tx"),
            )
            .where(AgentInstanceSample.server_id == server_id, AgentInstanceSample.minute >= since)
            .group_by(AgentInstanceSample.minute)
            .subquery()
        )
        net_hour = func.date_trunc("hour", per_minute.c.minute)
        network = {
            row[0]: row[1:]
            for row in (
                await self.db.execute(
                    select(
                        net_hour,
                        func.avg(per_minute.c.rx),
                        func.max(per_minute.c.rx),
                        func.avg(per_minute.c.tx),
                        func.max(per_minute.c.tx),
                    ).group_by(net_hour)
                )
            ).all()
        }
        points = []
        for row in rows:
            net = network.get(row[0], (None, None, None, None))
            points.append(
                HostPoint(
                    at=row[0],
                    cpu_avg=_round(row[1]),
                    cpu_max=_round(row[2]),
                    load_avg=_round(row[3]),
                    load_max=_round(row[4]),
                    mem_avg=_round(row[5]),
                    mem_max=_round(row[6]),
                    data_avg=_round(row[7]),
                    root_avg=_round(row[8]),
                    rx_avg=_round(net[0]),
                    rx_max=_round(net[1]),
                    tx_avg=_round(net[2]),
                    tx_max=_round(net[3]),
                )
            )
        return points

    # ---------------------------------------------------------------- 读取：单个智能体

    @staticmethod
    def agent_load(state: AgentHostState, now: datetime) -> AgentLoad:
        box = state.container or {}
        latest = state.latest or {}
        return AgentLoad(
            reported_at=state.reported_at,
            stale=now - state.reported_at > STALE_AFTER,
            available=bool(box.get("available")),
            cpu_cores=box.get("cpu_cores"),
            cpu_percent=box.get("cpu_percent"),
            mem_bytes=box.get("mem_bytes"),
            mem_limit=box.get("mem_limit"),
            mem_total=latest.get("mem_total"),
            pids=box.get("pids"),
            data_bytes=box.get("data_bytes"),
            data_truncated=bool(box.get("data_truncated")),
            rx_rate=box.get("rx_rate"),
            tx_rate=box.get("tx_rate"),
            restarts_1h=len(_restarts(box, now)),
        )

    async def agent_points(self, instance_id: uuid.UUID, since: datetime) -> list[AgentPoint]:
        sample = AgentInstanceSample
        hour = func.date_trunc("hour", sample.minute)
        rows = await self.db.execute(
            select(
                hour,
                func.avg(sample.cpu_percent),
                func.max(sample.cpu_percent),
                func.avg(sample.cpu_cores),
                func.avg(sample.mem_bytes),
                func.max(sample.mem_bytes),
                func.avg(sample.rx_rate),
                func.max(sample.rx_rate),
                func.avg(sample.tx_rate),
                func.max(sample.tx_rate),
            )
            .where(sample.instance_id == instance_id, sample.minute >= since)
            .group_by(hour)
            .order_by(hour)
        )
        return [
            AgentPoint(
                at=row[0],
                cpu_avg=_round(row[1]),
                cpu_max=_round(row[2]),
                cores_avg=_round(row[3]),
                mem_avg=_round(row[4]),
                mem_max=_round(row[5]),
                rx_avg=_round(row[6]),
                rx_max=_round(row[7]),
                tx_avg=_round(row[8]),
                tx_max=_round(row[9]),
            )
            for row in rows.all()
        ]

    # ---------------------------------------------------------------- 管理员与智能体看到的

    async def _instance(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentInstance:
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id, AgentInstance.workspace_id == access.workspace.id
            )
        )
        if instance is None:
            raise not_found()
        return instance

    async def status(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, span: HostRange
    ) -> HostStatusOut:
        instance = await self._instance(access, instance_id)
        return await self.status_of(instance, span)

    async def _server(self, instance: AgentInstance) -> AgentServer | None:
        if instance.server_id is None:
            return None
        server = await self.db.get(AgentServer, instance.server_id)
        if server is None or server.workspace_id != instance.workspace_id:
            return None
        return server

    async def status_of(self, instance: AgentInstance, span: HostRange) -> HostStatusOut:
        now = _now()
        since = now - RANGES[span]
        state = await self.db.get(AgentHostState, instance.id)
        server = await self._server(instance)
        return HostStatusOut(
            instance_id=instance.id,
            reported=state is not None,
            server=ServerRef(id=server.id, name=server.name) if server is not None else None,
            current=await self.server_current(server, now) if server is not None else None,
            range=span,
            points=await self.server_points(server.id, since) if server is not None else [],
            alerts=sorted((server.alerts or {}).keys()) if server is not None else [],
            agent=self.agent_load(state, now) if state is not None else None,
            agent_points=await self.agent_points(instance.id, since) if state is not None else [],
            agent_alerts=sorted((state.alerts or {}).keys()) if state is not None else [],
        )

    async def summary(self, access: WorkspaceAccess) -> HostSummaryList:
        now = _now()
        rows = (
            await self.db.execute(
                select(AgentInstance.id, AgentInstance.server_id, AgentHostState, AgentServer)
                .outerjoin(AgentHostState, AgentHostState.instance_id == AgentInstance.id)
                .outerjoin(AgentServer, AgentServer.id == AgentInstance.server_id)
                .where(AgentInstance.workspace_id == access.workspace.id)
                .order_by(AgentInstance.name, AgentInstance.id)
            )
        ).all()
        items = []
        for instance_id, server_id, state, server in rows:
            agent = self.agent_load(state, now) if state is not None else None
            latest = (server.latest or {}) if server is not None else {}
            disks = [
                used
                for used in (
                    _percent_used(latest.get("data_total"), latest.get("data_free")),
                    _percent_used(latest.get("root_total"), latest.get("root_free")),
                )
                if used is not None
            ]
            reported_at = server.reported_at if server is not None else None
            items.append(
                HostSummaryItem(
                    instance_id=instance_id,
                    server_id=server_id,
                    reported_at=reported_at,
                    stale=reported_at is None or now - reported_at > STALE_AFTER,
                    cpu_percent=latest.get("cpu_percent"),
                    mem_used_percent=_percent_used(
                        latest.get("mem_total"), latest.get("mem_available")
                    ),
                    disk_used_percent=max(disks) if disks else None,
                    alerts=sorted((server.alerts or {}).keys()) if server is not None else [],
                    agent_cpu_percent=agent.cpu_percent if agent else None,
                    agent_mem_bytes=agent.mem_bytes if agent else None,
                    agent_mem_limit=agent.mem_limit if agent else None,
                    agent_alerts=sorted((state.alerts or {}).keys()) if state is not None else [],
                )
            )
        return HostSummaryList(items=items)


# ---------------------------------------------------------------- Worker：清理与告警


async def purge(db: AsyncSession, now: datetime | None = None) -> int:
    moment = now or _now()
    removed = 0
    for model in (AgentServerSample, AgentInstanceSample):
        result = await db.execute(delete(model).where(model.minute < moment - RETENTION))
        removed += int(getattr(result, "rowcount", 0) or 0)
    return removed


async def _load_high(db: AsyncSession, server: AgentServer, now: datetime) -> bool:
    rows = (
        await db.execute(
            select(AgentServerSample.load15, AgentServerSample.cores).where(
                AgentServerSample.server_id == server.id,
                AgentServerSample.minute >= _minute(now) - timedelta(minutes=LOAD_MINUTES),
            )
        )
    ).all()
    if len(rows) < LOAD_MINUTES:
        return False
    return all(load is not None and cores and load > LOAD_FACTOR * cores for load, cores in rows)


async def server_alerts(db: AsyncSession, server: AgentServer, now: datetime) -> set[AlertKind]:
    found: set[AlertKind] = set()
    online = await db.scalar(
        select(func.count())
        .select_from(AgentInstance)
        .where(AgentInstance.server_id == server.id, AgentInstance.status == "online")
    )
    if server.reported_at is None:
        return found
    if online and now - server.reported_at > NOT_REPORTING_AFTER:
        found.add("not_reporting")
        return found
    latest = server.latest or {}
    for prefix in ("data", "root"):
        used = _percent_used(latest.get(f"{prefix}_total"), latest.get(f"{prefix}_free"))
        if used is not None and 100.0 - used < DISK_FREE_MIN:
            found.add("disk_low")
    mem_used = _percent_used(latest.get("mem_total"), latest.get("mem_available"))
    if mem_used is not None and 100.0 - mem_used < MEMORY_FREE_MIN:
        found.add("memory_low")
    if await _load_high(db, server, now):
        found.add("load_high")
    return found


async def agent_alerts(
    db: AsyncSession, state: AgentHostState, now: datetime
) -> set[AgentAlertKind]:
    found: set[AgentAlertKind] = set()
    if now - state.reported_at > STALE_AFTER:
        return found  # 不上报的由整台服务器的告警与离线提醒负责
    if len(_restarts(state.container or {}, now)) >= RESTARTS_PER_HOUR:
        found.add("restarting")
    since = _minute(now) - timedelta(minutes=max(MEM_NEAR_MINUTES, CPU_HOG_MINUTES))
    rows = (
        await db.execute(
            select(
                AgentInstanceSample.minute,
                AgentInstanceSample.mem_bytes,
                AgentInstanceSample.mem_limit,
                AgentInstanceSample.cpu_percent,
            )
            .where(
                AgentInstanceSample.instance_id == state.instance_id,
                AgentInstanceSample.minute >= since,
            )
            .order_by(AgentInstanceSample.minute.desc())
        )
    ).all()
    mem_rows = rows[:MEM_NEAR_MINUTES]
    if len(mem_rows) >= MEM_NEAR_MINUTES and all(
        used is not None and limit and used > MEM_NEAR_LIMIT * limit
        for _, used, limit, _ in mem_rows
    ):
        found.add("mem_near_limit")
    cpu_rows = rows[:CPU_HOG_MINUTES]
    if len(cpu_rows) >= CPU_HOG_MINUTES and all(
        cpu is not None and cpu > CPU_HOG_PERCENT for *_, cpu in cpu_rows
    ):
        found.add("cpu_hog")
    return found


async def _notify(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    kind: str,
    title: str,
    key: str,
    body: str,
) -> None:
    await notifications.notify_admins(
        db,
        workspace_id,
        type_=f"host.{kind}",
        severity="warning",
        title=title,
        body=body,
        link="/agents",
        key=key,
    )


def _diff(previous: dict[str, Any], active: set[str]) -> tuple[list[str], dict[str, Any]]:
    """新出现的告警，以及更新后的记录（恢复的清掉，下次再出现时再提醒）。"""
    fresh = sorted(active - set(previous))
    kept = {k: v for k, v in previous.items() if k in active}
    return fresh, kept


async def check_alerts(db: AsyncSession, now: datetime | None = None) -> int:
    """每分钟：新出现的告警通知 owner / admin。返回新提醒的条数。"""
    moment = now or _now()
    sent = 0
    servers = (
        (await db.execute(select(AgentServer).with_for_update(skip_locked=True))).scalars().all()
    )
    for server in servers:
        fresh, kept = _diff(dict(server.alerts or {}), set(await server_alerts(db, server, moment)))
        for kind in fresh:
            await _notify(
                db,
                server.workspace_id,
                kind=kind,
                title=f"服务器「{server.name}」{ALERT_TITLES[kind]}",
                body="打开“OpenClaw 实例”页，在这台服务器上的实例里查看服务器状态。",
                key=f"server:{server.id}:{moment.isoformat()}",
            )
            kept[kind] = moment.isoformat()
            sent += 1
        if kept != (server.alerts or {}):
            server.alerts = kept
    rows = (
        await db.execute(
            select(AgentHostState, AgentInstance)
            .join(AgentInstance, AgentInstance.id == AgentHostState.instance_id)
            .where(AgentInstance.status != "disabled")
            .with_for_update(of=AgentHostState, skip_locked=True)
        )
    ).all()
    for state, instance in rows:
        fresh, kept = _diff(dict(state.alerts or {}), set(await agent_alerts(db, state, moment)))
        for kind in fresh:
            await _notify(
                db,
                instance.workspace_id,
                kind=kind,
                title=f"「{instance.name}」{AGENT_ALERT_TITLES[kind]}",
                body="打开 OpenClaw 实例页的“服务器状态”查看这个智能体的负载。",
                key=f"{instance.id}:{kind}:{moment.isoformat()}",
            )
            kept[kind] = moment.isoformat()
            sent += 1
        if kept != (state.alerts or {}):
            state.alerts = kept
    await db.commit()
    if sent:
        logger.info("host alerts sent", extra={"count": sent})
    return sent
