"""服务器状态（T-0113，docs/v2/AGENT_OPS_PLAN.md 第 5 节）。

一台服务器多个实例：T-0146，docs/v2/MULTI_OPENCLAW_PLAN.md 第 5 节。

上报（接入包 bin/host-report.mjs → POST /agent-api/v1/host-metrics）只收数字，多余字段 422，
数值有上下限。分两级：

- 整台服务器（/proc 与两个分区，同一台服务器上的每个实例都会报，每分钟只存一份）；
- 这个智能体（自己容器的 cgroup、数据目录大小、自己网卡的收发字节）。

每个实例有自己的网络命名空间，整台服务器的网速是这台服务器上各实例网速之和。
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

HostRange = Literal["24h", "7d", "30d"]
# 整台服务器的告警（每台服务器一份）
AlertKind = Literal["disk_low", "memory_low", "load_high", "not_reporting"]
# 单个智能体的告警：内存接近上限、频繁重启、长时间占满整台服务器的 CPU
AgentAlertKind = Literal["mem_near_limit", "restarting", "cpu_hog"]
HostKey = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

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


class ContainerIn(_Strict):
    """这个实例自己容器的 cgroup（v2 或 v1）与数据目录大小。"""

    cpu_usec: Ticks | None = Field(description="CPU 用量累计（微秒）；容器重启后从 0 开始")
    mem_bytes: Bytes | None = Field(description="内存已用（不含可回收的文件缓存）")
    pids: Annotated[int, Field(ge=0, le=1 << 22)] | None
    data_bytes: Bytes | None = Field(
        default=None, description="数据目录（state/）大小；每 10 分钟报一次，其他时候为 null"
    )
    data_truncated: bool = Field(default=False, description="文件太多，只累加了前 20 万个")


class HostReport(_Strict):
    cpu: CpuIn
    memory: MemoryIn
    disks: DisksIn
    network: NetworkIn | None = Field(description="这个实例自己网卡的收发累计字节")
    uptime_seconds: Annotated[int, Field(ge=0, le=1 << 40)] | None
    container: ContainerIn | None = Field(
        default=None, description="读不到 cgroup 时缺省：这个智能体的负载显示“无法获取”"
    )
    host_key: HostKey | None = Field(
        default=None,
        description="服务器指纹：boot_id 的 HMAC-SHA256（盐来自接入包），同一台服务器上的实例相同",
    )


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
    mem_limit: int | None = Field(
        description="整台服务器恒为 null（T-0146 起）；每个智能体自己的上限见 agent.mem_limit"
    )
    swap_total: int | None
    swap_used: int | None
    data_disk: DiskOut | None
    root_disk: DiskOut | None
    rx_rate: int | None = Field(
        description="下载速率（字节 / 秒）：这台服务器上各实例之和（每个实例有自己的网卡）"
    )
    tx_rate: int | None = Field(description="上传速率（字节 / 秒）：各实例之和")
    uptime_seconds: int | None


class AgentLoad(BaseModel):
    """这个智能体（自己的 OpenClaw 容器）的当前用量。"""

    reported_at: datetime
    stale: bool = Field(description="超过 3 分钟没有上报")
    available: bool = Field(description="读得到 cgroup；false 时界面显示“无法获取”")
    cpu_cores: float | None = Field(description="占用几个核（两次上报之间的平均）")
    cpu_percent: float | None = Field(description="占整台服务器 CPU 的百分比")
    mem_bytes: int | None = Field(description="内存已用（不含可回收的缓存）")
    mem_limit: int | None = Field(description="内存上限；没设上限时为 null（界面显示 / 整机总量）")
    mem_total: int | None = Field(description="整台服务器的内存总量")
    pids: int | None
    data_bytes: int | None = Field(description="数据目录（会话、记忆、插件）大小，每 10 分钟更新")
    data_truncated: bool
    rx_rate: int | None
    tx_rate: int | None
    restarts_1h: int = Field(description="最近一小时容器重启了几次")


class AgentPoint(BaseModel):
    """这个智能体每小时一个点：平均与峰值。"""

    at: datetime
    cpu_avg: float | None = Field(description="占整台服务器 CPU 的百分比")
    cpu_max: float | None
    cores_avg: float | None
    mem_avg: float | None = Field(description="内存已用（字节）")
    mem_max: float | None
    rx_avg: float | None
    rx_max: float | None
    tx_avg: float | None
    tx_max: float | None


class ServerRef(BaseModel):
    id: UUID
    name: str


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
    server: ServerRef | None = Field(description="所在的服务器（自动识别或管理员指定）")
    current: HostCurrent | None = Field(description="整台服务器的当前值")
    range: HostRange
    points: list[HostPoint] = Field(description="整台服务器的趋势")
    alerts: list[AlertKind] = Field(description="整台服务器正在提醒的告警")
    agent: AgentLoad | None = Field(description="这个智能体自己的用量")
    agent_points: list[AgentPoint] = Field(description="这个智能体的趋势")
    agent_alerts: list[AgentAlertKind] = Field(description="这个智能体正在提醒的告警")


class HostSummaryItem(BaseModel):
    instance_id: UUID
    server_id: UUID | None
    reported_at: datetime | None
    stale: bool
    cpu_percent: float | None = Field(description="整台服务器的 CPU 使用率")
    mem_used_percent: float | None = Field(description="整台服务器的内存已用百分比")
    disk_used_percent: float | None = Field(description="两个分区里较高的已用百分比")
    alerts: list[AlertKind]
    agent_cpu_percent: float | None = Field(description="这个智能体占整台服务器 CPU 的百分比")
    agent_mem_bytes: int | None
    agent_mem_limit: int | None
    agent_alerts: list[AgentAlertKind]


class HostSummaryList(BaseModel):
    items: list[HostSummaryItem]
