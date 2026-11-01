"""V2 的运维指标（方案第 6.1 节）：OpenClaw 实例、取密钥、MCP、IM 回调与对账、群聊防刷屏、审批。

- 计数类：发生在 api 的不同进程和 worker 里，所以记在 Redis 的哈希里（HINCRBY），`/metrics`
  抓取时读出来，作为 counter 输出。Redis 清空后从 0 重新计数，Prometheus 按计数器重置处理。
  记数失败只写日志，绝不影响业务请求。
- 状态类：抓取时直接查 PostgreSQL（实例数、待审批数、回调积压），都是带索引或很小的表上的 count。
- 标签只用固定的枚举值（结果、工具名、入口、状态），不放实例 ID、工作空间 ID、用户输入或 Token；
  需要看单个实例时去工作台的实例页。
"""

import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentInstance, AiToolExecution, ImWebhookInboxEvent
from app.models.agent import INSTANCE_STATUSES

logger = logging.getLogger("app.ops_metrics")

_KEY = "ops:counter:{name}"
_NO_LABEL = "_"
# 回调收件箱里还没处理完的状态（completed 的不计入）
INBOX_OPEN_STATUSES = ("pending", "processing", "retry", "dead_letter")


@dataclass(frozen=True)
class CounterSpec:
    metric: str
    help: str
    labels: tuple[str, ...] = ()


COUNTERS: dict[str, CounterSpec] = {
    "agent_secret_resolves": CounterSpec(
        "workspace_agent_secret_resolves",
        "实例取密钥的次数（每个密钥 ID 一次），result 为 ok / not_found / rate_limited",
        ("result",),
    ),
    "agent_auth_failures": CounterSpec(
        "workspace_agent_auth_failures",
        "实例认证失败（凭证无效、吊销、停用、入口不符、缺客户端证书）的次数，按入口",
        ("ingress",),
    ),
    "mcp_tool_calls": CounterSpec(
        "workspace_mcp_tool_calls",
        "MCP 工具调用次数；outcome 为 ok 或工具错误码（小写）",
        ("tool", "outcome"),
    ),
    "im_relay_pauses": CounterSpec(
        "workspace_im_relay_pauses", "群聊因实例连续发言达到上限而暂停自动接力的次数"
    ),
    "im_reconcile_filled": CounterSpec(
        "workspace_im_reconcile_filled_messages", "周期对账补进投影的消息条数（回调丢失或延迟时）"
    ),
}


async def bump(redis: Redis | None, name: str, *labels: str, amount: int = 1) -> None:
    """计数加 amount；Redis 不可用时只记日志。"""
    spec = COUNTERS[name]
    if redis is None or amount <= 0:
        return
    if len(labels) != len(spec.labels):
        raise ValueError(f"{name} 需要标签 {spec.labels}")
    try:
        field = "|".join(labels) or _NO_LABEL
        await redis.hincrby(_KEY.format(name=name), field, amount)  # type: ignore[misc]
    except Exception as error:  # 指标不能影响业务
        logger.warning(
            "ops counter failed", extra={"counter": name, "error_type": type(error).__name__}
        )


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


async def _counters(redis: Redis) -> list[Metric]:
    families: list[Metric] = []
    for name, spec in COUNTERS.items():
        raw = await redis.hgetall(_KEY.format(name=name))  # type: ignore[misc]
        family = CounterMetricFamily(spec.metric, spec.help, labels=list(spec.labels))
        if not spec.labels:
            total = sum(int(v) for v in raw.values()) if raw else 0
            family.add_metric([], total)
        else:
            for field, value in sorted((_text(k), int(v)) for k, v in raw.items()):
                parts = field.split("|")
                if len(parts) == len(spec.labels):
                    family.add_metric(parts, value)
        families.append(family)
    return families


async def _gauges(db: AsyncSession) -> list[Metric]:
    now = datetime.now(UTC)
    instances = dict(
        (
            await db.execute(
                select(AgentInstance.status, func.count()).group_by(AgentInstance.status)
            )
        ).all()
    )
    family = GaugeMetricFamily(
        "workspace_agent_instances",
        "登记的 OpenClaw 实例数，按状态（online 为 IM 在线）",
        labels=["status"],
    )
    for status in INSTANCE_STATUSES:
        family.add_metric([status], int(instances.get(status, 0)))

    pending = await db.scalar(
        select(func.count())
        .select_from(AiToolExecution)
        .where(
            AiToolExecution.status == "pending_approval",
            or_(AiToolExecution.expires_at.is_(None), AiToolExecution.expires_at > now),
        )
    )
    approvals = GaugeMetricFamily(
        "workspace_ai_pending_approvals", "等待人工审批、还没过期的 AI 写操作数"
    )
    approvals.add_metric([], int(pending or 0))

    inbox = dict(
        (
            await db.execute(
                select(ImWebhookInboxEvent.status, func.count())
                .where(ImWebhookInboxEvent.status.in_(INBOX_OPEN_STATUSES))
                .group_by(ImWebhookInboxEvent.status)
            )
        ).all()
    )
    backlog = GaugeMetricFamily(
        "workspace_im_inbox_events",
        "OpenIM 回调收件箱里还没处理完的事件数，按状态（dead_letter 需要人工查看）",
        labels=["status"],
    )
    for status in INBOX_OPEN_STATUSES:
        backlog.add_metric([status], int(inbox.get(status, 0)))
    return [family, approvals, backlog]


class _Snapshot:
    """一次抓取的结果：只给 generate_latest 用的一次性 collector。"""

    def __init__(self, families: Sequence[Metric]) -> None:
        self.families = families

    def collect(self) -> Iterator[Metric]:
        return iter(self.families)


def render(families: Iterable[Metric]) -> bytes:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(_Snapshot(list(families)))
    return generate_latest(registry)


async def collect(db: AsyncSession, redis: Redis | None) -> list[Metric]:
    families = await _gauges(db)
    if redis is not None:
        families.extend(await _counters(redis))
    return families
