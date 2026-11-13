"""Fail-closed connection decision inputs, independent of transport identity.

The caller must obtain the source identity from an authenticated transport and
must recheck this decision throughout a connection. This is not a gateway.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class IngressRule:
    workspace_id: UUID
    source_instance_id: UUID
    target_instance_id: UUID
    service_id: UUID
    protocol: str
    port: int
    expires_at: datetime
    revision: int


def permits_connection(
    *,
    rule: IngressRule,
    workspace_id: UUID,
    source_instance_id: UUID,
    target_instance_id: UUID,
    service_id: UUID,
    protocol: str,
    port: int,
    now: datetime,
    applied_revision: int,
    has_capability: bool,
) -> bool:
    return bool(
        has_capability
        and source_instance_id != target_instance_id
        and workspace_id == rule.workspace_id
        and source_instance_id == rule.source_instance_id
        and target_instance_id == rule.target_instance_id
        and service_id == rule.service_id
        and protocol == rule.protocol == "TCP"
        and 1 <= port == rule.port <= 65535
        and now.tzinfo is not None
        and rule.expires_at.tzinfo is not None
        and now < rule.expires_at
        and rule.revision > 0
        and applied_revision == rule.revision
    )
