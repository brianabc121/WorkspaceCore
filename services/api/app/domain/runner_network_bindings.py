"""Host receipts may refresh a Pod only within the original deployment and allocation."""

import uuid
from ipaddress import IPv4Address, ip_address, ip_network

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import RunnerCommand, RunnerEnvironment, RunnerHost


class BindingReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: uuid.UUID
    instance_id: uuid.UUID
    environment_id: uuid.UUID
    allocation_id: uuid.UUID
    generation: int = Field(ge=1)
    deployment_uid: str = Field(min_length=1, max_length=128)
    pvc_uid: str = Field(min_length=1, max_length=128)
    pod_uid: str = Field(min_length=1, max_length=128)
    pod_ip: IPv4Address
    node_name: str = Field(min_length=1, max_length=253)


async def update_binding(
    db: AsyncSession, settings: Settings, host: RunnerHost, body: BindingReceipt
) -> bool:
    if (
        not settings.runner_network_verified
        or host.backend != "kubernetes"
        or host.status != "online"
        or body.workspace_id != host.workspace_id
        or body.node_name != host.node_name
    ):
        return False
    obj = await db.scalar(
        select(RunnerEnvironment)
        .where(
            RunnerEnvironment.id == body.environment_id,
            RunnerEnvironment.workspace_id == host.workspace_id,
            RunnerEnvironment.host_id == host.id,
        )
        .with_for_update()
    )
    if (
        obj is None
        or obj.state != "running"
        or obj.runtime_backend != "kubernetes"
        or obj.owner_instance_id != body.instance_id
    ):
        return False
    meta = obj.runtime_metadata or {}
    if (
        body.generation != meta.get("generation")
        or str(body.allocation_id) != str(meta.get("allocation_id") or obj.id)
        or body.deployment_uid != meta.get("deployment_uid")
        or body.pvc_uid != meta.get("pvc_uid")
    ):
        return False
    if not any(
        ip_address(str(body.pod_ip)) in ip_network(c.strip())
        for c in settings.runner_network_pod_cidrs.split(",")
        if c.strip()
    ):
        return False
    pending = await db.scalar(
        select(RunnerCommand.id)
        .where(
            RunnerCommand.environment_id == obj.id,
            RunnerCommand.status.in_(("pending", "sent", "unknown")),
            RunnerCommand.kind.in_(
                ("create_env", "start_env", "stop_env", "delete_env", "archive_env")
            ),
        )
        .limit(1)
    )
    if pending is not None:
        return False
    obj.runtime_metadata = {
        **meta,
        "pod_uid": body.pod_uid,
        "pod_ip": str(body.pod_ip),
        "node": body.node_name,
    }
    await db.commit()
    return True
