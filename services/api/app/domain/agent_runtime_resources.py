"""Read-only verification of historical Docker limits; never reads container secrets."""

import asyncio
import math
import re
import shlex
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import asyncssh
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.agent_server_ssh import connect_agent_server
from app.domain.credentials import CredentialBox
from app.integrations import ssh
from app.models import AgentSshTarget

if TYPE_CHECKING:
    from uuid import UUID

    from app.domain.agent_servers import AgentServerService
    from app.domain.policy import WorkspaceAccess
    from app.schemas.agents import AgentDeploymentState


class RuntimeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project: str
    directory: str
    service: Literal["openclaw"]
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory: int = Field(gt=0, le=1 << 50)
    nano_cpus: int = Field(ge=0, le=256_000_000_000)
    cpu_quota: int = Field(ge=-1)
    cpu_period: int = Field(ge=0)


def parse_runtime_limits(raw: str, project: str, directory: str) -> dict[str, Any]:
    try:
        value = RuntimeLimits.model_validate_json(raw)
        if value.project != project or value.directory != directory:
            raise ValueError("runtime identity mismatch")
        cpu = value.nano_cpus / 1_000_000_000
        if not cpu and value.cpu_quota > 0 and value.cpu_period > 0:
            cpu = value.cpu_quota / value.cpu_period
        if not math.isfinite(cpu) or not 0 < cpu <= 256:
            raise ValueError("runtime has no supported finite CPU limit")
        return {
            "cpus": cpu,
            "memory_mb": (value.memory + (1 << 20) - 1) // (1 << 20),
            "container_id": value.id,
            "source": "trusted_docker_inspect",
        }
    except (ValidationError, ValueError) as error:
        raise ValueError("运行容器身份或有限资源上限尚未确认") from error


_INSPECT = (
    '{"id":{{json .Id}},"project":{{json (index .Config.Labels '
    '"com.docker.compose.project")}},"directory":{{json (index .Config.Labels '
    '"com.docker.compose.project.working_dir")}},"service":{{json (index .Config.Labels '
    '"com.docker.compose.service")}},"memory":{{json .HostConfig.Memory}},'
    '"nano_cpus":{{json .HostConfig.NanoCpus}},"cpu_quota":{{json .HostConfig.CpuQuota}},'
    '"cpu_period":{{json .HostConfig.CpuPeriod}}}'
)


async def inspect_runtime_limits(
    target: AgentSshTarget, box: CredentialBox, *, project: str, directory: str
) -> dict[str, Any]:
    if not re.fullmatch(r"myworkspace-openclaw-[0-9a-f]{12}", project):
        raise ValueError("invalid managed project identity")
    command = (
        "set -eu\nset -- $(docker ps -aq --filter "
        + shlex.quote("label=com.docker.compose.project=" + project)
        + " --filter label=com.docker.compose.service=openclaw)\n"
        + '[ "$#" -eq 1 ]\ndocker inspect --format '
        + shlex.quote(_INSPECT)
        + ' "$1"'
    )
    conn = None
    try:
        async with asyncio.timeout(25):
            conn = await connect_agent_server(
                target, box, expected_revision=target.credential_revision
            )
            async with conn.create_process(command, stderr=asyncssh.DEVNULL) as process:
                raw = await process.stdout.read(8193)
                if len(raw) > 8192:
                    process.terminate()
                    raise ValueError("resource response too large")
                await process.wait_closed()
                if process.exit_status != 0:
                    raise ValueError("runtime not uniquely identified")
            result = parse_runtime_limits(raw, project, directory)
            result["observed_at"] = datetime.now(UTC).isoformat()
            return result
    except (TimeoutError, asyncssh.Error, ssh.SshError, ValueError) as error:
        raise ValueError("受信资源核对失败；未改变实例或容量账本") from error
    finally:
        if conn is not None:
            conn.close()


async def reconcile_resources(
    service: "AgentServerService",
    access: "WorkspaceAccess",
    instance_id: "UUID",
    *,
    version: int,
    client_ip: str,
) -> "AgentDeploymentState":
    """Recover historical finite commitments without changing runtime or desired limits."""
    from sqlalchemy import select

    from app.agent_bundle import project_name
    from app.core.errors import ApiError
    from app.domain.agent_ssh import _version_conflict
    from app.models import AgentDeployment, AgentSshJob

    await service._check_probe_rate(access)
    instance = await service._instance(access, instance_id)
    if instance.version != version:
        raise _version_conflict(instance.version)
    deployment = await service.db.scalar(
        select(AgentDeployment).where(
            AgentDeployment.instance_id == instance_id,
            AgentDeployment.workspace_id == access.workspace.id,
        )
    )
    if deployment is None or deployment.runtime_installed is not True or not deployment.install_dir:
        raise ApiError(409, "RESOURCE_RECONCILIATION_PENDING", "尚无已确认的运行部署可核对")
    server = await service._server(access, deployment.server_id)
    target = await service._ssh_target(server, lock=False)
    latest = await service.db.scalar(
        select(AgentSshJob.status)
        .where(
            AgentSshJob.instance_id == instance_id,
            AgentSshJob.workspace_id == access.workspace.id,
            AgentSshJob.kind.in_(("install", "uninstall")),
        )
        .order_by(AgentSshJob.started_at.desc())
        .limit(1)
    )
    if latest != "succeeded" or await service._busy(target):
        raise ApiError(409, "RESOURCE_RECONCILIATION_PENDING", "请先核对未完成的服务器操作")
    stamp = (instance.version, deployment.version, server.version, target.credential_revision)
    server_id, directory = server.id, deployment.install_dir
    box = service._require_box()
    await service.db.commit()  # release read transaction before SSH; no network under row locks
    try:
        observed = await inspect_runtime_limits(
            target, box, project=project_name(str(instance_id)), directory=directory
        )
    except ValueError as error:
        raise ApiError(
            409,
            "RESOURCE_RECONCILIATION_REQUIRED",
            "未确认有限的实际资源上限；未改变实例或运行容器",
        ) from error
    instance = await service._instance(access, instance_id, lock=True)
    server = await service._server(access, server_id, lock=True)
    target = await service._ssh_target(server, lock=True)
    deployment = await service.db.scalar(
        select(AgentDeployment)
        .where(
            AgentDeployment.instance_id == instance_id,
            AgentDeployment.workspace_id == access.workspace.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        deployment is None
        or (instance.version, deployment.version, server.version, target.credential_revision)
        != stamp
        or await service._busy(target)
    ):
        raise ApiError(
            409, "RESOURCE_RECONCILIATION_STALE", "核对期间部署或凭据发生变化，请刷新重试"
        )
    if deployment.server_id != server_id or deployment.install_dir != directory:
        raise ApiError(409, "RESOURCE_RECONCILIATION_STALE", "核对期间实际部署位置发生变化")
    receipt = dict(deployment.selection or {})
    # Preserve any larger pending promise: inspection must not erase a prior attempt.
    for key in ("cpus", "memory_mb"):
        prior = receipt.get(key)
        if isinstance(prior, int | float) and math.isfinite(prior) and prior > 0:
            observed[key] = max(prior, observed[key])
    prior_disk = receipt.get("disk_gb")
    disk = service.settings.agent_placement_disk_gb
    if isinstance(prior_disk, int | float) and math.isfinite(prior_disk) and prior_disk > 0:
        disk = max(disk, prior_disk)
    deployment.selection = {
        **receipt,
        **observed,
        "disk_gb": disk,
        "actual_server_id": str(server_id),
    }
    deployment.version += 1
    await service.audit.record(
        "agents.runtime.resources_reconciled",
        workspace_ids=[access.workspace.id],
        actor_id=access.user_id,
        resource_type="agent_instance",
        resource_id=instance_id,
        metadata={"cpus": observed["cpus"], "memory_mb": observed["memory_mb"]},
        client_ip=client_ip,
    )
    await service.db.commit()
    return await service.deployment_state(access, instance_id)
