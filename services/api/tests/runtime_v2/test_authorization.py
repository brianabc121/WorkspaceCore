from datetime import UTC, datetime, timedelta
from typing import TypedDict
from uuid import UUID, uuid4

from app.runtime_v2.authorization import IngressRule, permits_connection


class ConnectionArguments(TypedDict):
    rule: IngressRule
    workspace_id: UUID
    source_instance_id: UUID
    target_instance_id: UUID
    service_id: UUID
    protocol: str
    port: int
    now: datetime
    applied_revision: int


def test_cross_instance_requires_capability_consent_and_exact_scope() -> None:
    workspace, source, target, service = (uuid4() for _ in range(4))
    now = datetime.now(UTC)
    rule = IngressRule(
        workspace, source, target, service, "TCP", 5432, now + timedelta(seconds=30), 4
    )
    args = ConnectionArguments(
        rule=rule,
        workspace_id=workspace,
        source_instance_id=source,
        target_instance_id=target,
        service_id=service,
        protocol="TCP",
        port=5432,
        now=now,
        applied_revision=4,
    )
    assert permits_connection(**args, has_capability=True)
    assert not permits_connection(**args, has_capability=False)
    assert not permits_connection(
        **ConnectionArguments({**args, "source_instance_id": uuid4()}), has_capability=True
    )
    assert not permits_connection(
        **ConnectionArguments({**args, "workspace_id": uuid4()}), has_capability=True
    )
    assert not permits_connection(**ConnectionArguments({**args, "port": 22}), has_capability=True)
    assert not permits_connection(
        **ConnectionArguments({**args, "now": now + timedelta(seconds=30)}), has_capability=True
    )
    assert not permits_connection(
        **ConnectionArguments({**args, "applied_revision": 3}), has_capability=True
    )
    assert not permits_connection(
        **ConnectionArguments({**args, "protocol": "UDP"}), has_capability=True
    )
