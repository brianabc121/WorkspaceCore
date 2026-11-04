"""服务器状态（T-0113，docs/v2/AGENT_OPS_PLAN.md 第 5 节）。

上报（接入包 bin/host-report.mjs → POST /agent-api/v1/host-metrics）只收数字，多余字段 422，
数值有上下限。管理员看到的是“整台服务器”的数值（容器用 host 网络，读的是宿主机的 /proc）。
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

HostRange = Literal["24h", "7d", "30d"]
AlertKind = Literal["disk_low", "memory_low", "load_high", "not_reporting"]

_BYTES = 1 << 56
_TICKS = 1 << 62
Bytes = Annotated[int, Field(ge=0, le=_BYTES)]
Ticks = Annotated[int, Field(ge=0, le=_TICKS)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CpuIn(_Strict):
    cores: Annotated[int, Field(ge=1, le=4096)] | None
    model: Annotated[str, Field(max_length=120)] | None
    load: (
        Annotated[
            list[Annotated[float, Field(ge=0, le=100_000)]], Field(min_length=3, max_length=3)
        ]
        | None
    )
    total_ticks: Ticks | None
    idle_ticks: Ticks | None


class MemoryIn(_Strict):
    total: Bytes
    available: Bytes
    swap_total: Bytes
    swap_free: Bytes
    limit: Bytes | None = Field(description="容器的内存上限（cgroup）；没有上限为 null")


class DiskIn(_Strict):
    total: Bytes | None
    free: Bytes | None


class DisksIn(_Strict):
    data: DiskIn | None = Field(description="接入包目录所在的分区（OpenClaw 的会话与记忆）")
    root: DiskIn | None = Field(description="容器根目录所在的分区（Docker 数据目录，镜像在这里）")


class NetworkIn(_Strict):
    rx_bytes: Bytes | None
    tx_bytes: Bytes | None


class HostReport(_Strict):
    cpu: CpuIn
    memory: MemoryIn
    disks: DisksIn
    network: NetworkIn | None
    uptime_seconds: Annotated[int, Field(ge=0, le=1 << 40)] | None


class HostReportResult(BaseModel):
    minute: datetime = Field(description="样本记在这一分钟（UTC，整分钟）")


# ---------------------------------------------------------------- 管理员看到的


class DiskOut(BaseModel):
    total: int
    free: int
    used: int
    used_percent: float


class HostCurrent(BaseModel):
    reported_at: datetime
    stale: bool = Field(description="超过 3 分钟没有上报")
    cpu_model: str | None
    cores: int | None
    cpu_percent: float | None = Field(
        description="两次上报之间的 CPU 使用率；第一次或计数器重置时为 null"
    )
    load: list[float] | None = Field(description="1 / 5 / 15 分钟负载")
    mem_total: int | None
    mem_available: int | None
    mem_used: int | None
    mem_limit: int | None = Field(description="容器的内存上限；有值时界面标注“受容器限制”")
    swap_total: int | None
    swap_used: int | None
    data_disk: DiskOut | None
    root_disk: DiskOut | None
    rx_rate: int | None = Field(description="下载速率（字节 / 秒）")
    tx_rate: int | None = Field(description="上传速率（字节 / 秒）")
    uptime_seconds: int | None


class HostPoint(BaseModel):
    """每小时一个点：平均与峰值。"""

    at: datetime
    cpu_avg: float | None
    cpu_max: float | None
    load_avg: float | None = Field(description="1 分钟负载的平均")
    load_max: float | None
    mem_avg: float | None = Field(description="内存已用百分比")
    mem_max: float | None
    data_avg: float | None = Field(description="接入包目录所在分区的已用百分比")
    root_avg: float | None = Field(description="Docker 数据目录所在分区的已用百分比")
    rx_avg: float | None
    rx_max: float | None
    tx_avg: float | None
    tx_max: float | None


class HostStatusOut(BaseModel):
    instance_id: UUID
    reported: bool = Field(description="收到过上报；没有时界面提示“重新部署后可用”")
    current: HostCurrent | None
    range: HostRange
    points: list[HostPoint]
    alerts: list[AlertKind] = Field(description="正在提醒的告警")


class HostSummaryItem(BaseModel):
    instance_id: UUID
    reported_at: datetime | None
    stale: bool
    cpu_percent: float | None
    mem_used_percent: float | None
    disk_used_percent: float | None = Field(description="两个分区里较高的已用百分比")
    alerts: list[AlertKind]


class HostSummaryList(BaseModel):
    items: list[HostSummaryItem]
