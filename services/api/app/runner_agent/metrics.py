"""容器的 CPU 实际用量：两次累计 CPU 时间的差分（T-0184，容器服务器方案的指标约定第 2 节）。

Docker 一次性的 stats（`stream=0&one-shot=1`）里 `precpu_stats` 是空的，没法用它算百分比；
这里自己记住每个容器上一次的累计 CPU 纳秒（`cpu_stats.cpu_usage.total_usage`）和本机单调时钟，
下一次上报时相减：`已用核数 = CPU 时间增量 / 墙钟间隔`，
是这段窗口里的平均核数，不是瞬时值，可以大于 1。

基线按 Docker 容器 ID + 启动代次（inspect 的 `State.StartedAt`）隔离：容器重启后累计时间从 0 开始，
同名重建是另一个 ID，都当作第一次采样。
第一次采样、计数器回退、读数缺失、窗口太长都返回“未知”（None），
只有同一代次、有效窗口里 CPU 时间确实没增加，才返回 0.0。

只在原有的每分钟上报里调用，不起额外的线程去轮询 Docker；本模块只做数字校验与差分，不碰 Docker。
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any

# 窗口不足 1 秒不计算（保留上一个基线）；超过 180 秒当作第一次采样（与页面的过期阈值一致）
MIN_WINDOW_NS = 1_000_000_000
MAX_WINDOW_NS = 180_000_000_000


@dataclass(frozen=True)
class CPUSample:
    used_cores: float | None
    window_seconds: float | None


UNKNOWN = CPUSample(None, None)


@dataclass
class _Baseline:
    generation: str
    cpu_total_ns: int
    monotonic_ns: int


class ContainerCPUSampler:
    """每个容器一个基线；上报线程与命令线程都可能用到，读写都加锁。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._baselines: dict[str, _Baseline] = {}

    def sample(
        self, container_id: str, generation: str, cpu_total_ns: int | None, monotonic_ns: int
    ) -> CPUSample:
        with self._lock:
            old = self._baselines.get(container_id)
            if cpu_total_ns is None or cpu_total_ns < 0 or not generation:
                # 读数缺失或无效：这一轮未知；基线不动
                # （同一代次的累计时间不会因为这一次没读到而失效）
                return UNKNOWN
            new = _Baseline(generation, cpu_total_ns, monotonic_ns)
            if old is None or old.generation != generation:
                self._baselines[container_id] = new  # 第一次采样，或容器重启过
                return UNKNOWN
            elapsed = monotonic_ns - old.monotonic_ns
            if elapsed < MIN_WINDOW_NS:
                return UNKNOWN  # 窗口太短（含时钟没走）：不算，留着上一个基线
            if elapsed > MAX_WINDOW_NS:
                self._baselines[container_id] = new
                return UNKNOWN
            delta = cpu_total_ns - old.cpu_total_ns
            self._baselines[container_id] = new
            if delta < 0:
                return UNKNOWN  # 计数器回退：重新开始
            return CPUSample(delta / elapsed, elapsed / 1e9)

    def prune(self, active_container_ids: set[str]) -> None:
        """删掉已经不在运行的容器的基线，避免越积越多。"""
        with self._lock:
            for key in [k for k in self._baselines if k not in active_container_ids]:
                del self._baselines[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._baselines)


def nonnegative_int(value: Any) -> int | None:
    """Docker 返回的计数：整数且 >= 0 才算数。

    缺失、负数哨兵（如数据卷 Size=-1）、其他类型都是未知。
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    number = int(value)
    return number if number >= 0 else None


def cpu_total_ns(stats: dict[str, Any]) -> int | None:
    """stats 里的累计 CPU 时间（纳秒，cgroup v1 / v2 都换算成纳秒）。"""
    usage = ((stats.get("cpu_stats") or {}).get("cpu_usage")) or {}
    return nonnegative_int(usage.get("total_usage"))


def memory_bytes(stats: dict[str, Any]) -> int | None:
    """stats 里的内存用量（`memory_stats.usage`，字节，含页缓存；与原来的口径相同）。

    已停止或刚退出的容器 `memory_stats` 是空的：这时是未知，不是 0。
    """
    return nonnegative_int((stats.get("memory_stats") or {}).get("usage"))


def started_generation(inspect: dict[str, Any] | None) -> str | None:
    """容器的启动代次：inspect 的 `State.StartedAt`；读不到或是零值时间就没有可靠的代次。"""
    state = (inspect or {}).get("State") or {}
    value = state.get("StartedAt")
    if not isinstance(value, str) or not value or value.startswith("0001-01-01"):
        return None
    return value


__all__ = [
    "MAX_WINDOW_NS",
    "MIN_WINDOW_NS",
    "UNKNOWN",
    "CPUSample",
    "ContainerCPUSampler",
    "cpu_total_ns",
    "memory_bytes",
    "nonnegative_int",
    "started_generation",
]
