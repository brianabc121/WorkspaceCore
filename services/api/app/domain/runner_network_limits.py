"""Admission constraints for the finite shared TCP gateway."""

from ipaddress import IPv4Network

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.models import RunnerEnvironment
from app.models.runner_network import RunnerIngressRule, RunnerPublishedEndpoint

MAX_GATEWAY_BINDINGS = 100


def public_sources_available(settings: Settings, values: list[str]) -> bool:
    blocked = [
        IPv4Network(c.strip(), strict=True)
        for c in (
            settings.runner_network_denied_cidrs + "," + settings.runner_network_pod_cidrs
        ).split(",")
        if c.strip()
    ]
    for value in values:
        source = IPv4Network(value, strict=True)
        intervals = [(int(source.network_address), int(source.broadcast_address))]
        for deny in blocked:
            lower, upper = int(deny.network_address), int(deny.broadcast_address)
            rest = []
            for start, end in intervals:
                if upper < start or lower > end:
                    rest.append((start, end))
                else:
                    if start < lower:
                        rest.append((start, lower - 1))
                    if upper < end:
                        rest.append((upper + 1, end))
            intervals = rest
        if intervals:
            return True
    return False


def require_public_sources(settings: Settings, values: list[str]) -> None:
    if not public_sources_available(settings, values):
        raise ApiError(
            409,
            "NETWORK_SOURCE_FORBIDDEN",
            "来源均属于受管节点或平台禁止网段，请选择允许的公网来源",
        )


async def reserve_gateway_slot(db: AsyncSession, *, kind: str) -> None:
    # Namespace/workspace locks do not serialize this shared finite resource.
    await db.execute(text("SELECT pg_advisory_xact_lock(178246311)"))
    total = 0
    for model in (RunnerIngressRule, RunnerPublishedEndpoint):
        total += int(
            await db.scalar(
                select(func.count())
                .select_from(model)
                .join(RunnerEnvironment, RunnerEnvironment.id == model.environment_id)
                .where(
                    RunnerEnvironment.runtime_backend == "kubernetes",
                    model.desired_generation > 0,
                    # Withdrawal still consumes a snapshot slot until acknowledged;
                    # otherwise repeated revoke/expire can exhaust the whole gateway.
                    or_(
                        model.state.not_in(("revoked", "expired")),
                        model.applied_revision != model.desired_revision,
                        model.applied_generation != model.desired_generation,
                        model.apply_error.is_not(None),
                    ),
                )
            )
            or 0
        )
    if total >= MAX_GATEWAY_BINDINGS:
        raise ApiError(409, "NETWORK_GATEWAY_CAPACITY", "网关可用入口已满，请先撤销未使用的入口")
    query, maximum = (
        ("SELECT last_value, is_called FROM workspace.runner_ingress_port_seq", 39999)
        if kind == "ingress"
        else ("SELECT last_value, is_called FROM workspace.runner_public_port_seq", 29999)
    )
    value = await db.execute(text(query))
    last, called = value.one()
    if called and last >= maximum:
        raise ApiError(409, "NETWORK_GATEWAY_PORTS_EXHAUSTED", "网关端口池已用尽，需要管理员扩容")
