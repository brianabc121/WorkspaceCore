# ruff: noqa: E501
"""Bounded, independent Linux host sampling over the pinned password SSH channel.

Only the platform can write these snapshots. OpenClaw reports remain useful for
charts but never supply placement authority. No caller-provided shell fragments.
"""

import asyncio
import json
import math
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncssh
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.agent_server_ssh import connect_agent_server
from app.domain.credentials import CredentialBox
from app.integrations import ssh
from app.models import AgentSshTarget

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings

PROBE_TIMEOUT = 25.0
MAX_OUTPUT = 8192
MAX_AGE_SECONDS = 60
# Fixed, read-only Linux commands; both filesystem paths come from the host's
# own Docker configuration / managed install root, and are always quoted.
PROBE_SCRIPT = r"""set -eu
export LC_ALL=C
case "$(uname -m)" in x86_64|aarch64) ;; *) exit 1 ;; esac
root=$(docker info --format '{{.DockerRootDir}}')
case "$root" in /*) ;; *) exit 1 ;; esac
[ "$(docker network inspect bridge --format '{{.Driver}}')" = bridge ]
[ -d "$HOME/myworkspace-openclaw" ]
boot=$(cat /proc/sys/kernel/random/boot_id)
machine=$(tr -d '\n' < /etc/machine-id)
[ "${#machine}" -eq 32 ]
case "$machine" in *[!0-9a-f]*) exit 1 ;; esac
physical=$(printf 'machine-id:%s' "$machine" | sha256sum | awk '{print $1}')
cores=$(getconf _NPROCESSORS_ONLN)
load=$(awk '{print $1}' /proc/loadavg)
set -- $(awk '/^cpu / {print $2+$3+$4+$5+$6+$7+$8+$9, $5+$6}' /proc/stat)
total=$1; idle=$2
sleep 1
set -- $(awk '/^cpu / {print $2+$3+$4+$5+$6+$7+$8+$9, $5+$6}' /proc/stat)
cpu=$(awk -v t="$total" -v i="$idle" -v nt="$1" -v ni="$2" 'BEGIN {if(nt<=t) exit 1; printf "%.4f", 100*(1-(ni-i)/(nt-t))}')
set -- $(awk '/^MemTotal:/ {t=$2*1024} /^MemAvailable:/ {a=$2*1024} END {printf "%.0f %.0f", t,a}' /proc/meminfo)
mem_total=$1; mem_available=$2
set -- $(df -Pk -- "$root" | awk 'END {printf "%.0f %.0f", $2*1024,$4*1024}')
root_total=$1; root_free=$2
set -- $(df -Pk -- "$HOME/myworkspace-openclaw" | awk 'END {printf "%.0f %.0f", $2*1024,$4*1024}')
data_total=$1; data_free=$2
seq=$(awk '{printf "%.0f", $1*1000}' /proc/uptime)
ids=$(docker ps --no-trunc --filter label=com.docker.compose.service=openclaw --format '{{.ID}}')
identities='[]'; usage='[]'
if [ -n "$ids" ]; then
  identities=$(docker inspect --format '{"id":{{json .Id}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"directory":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}}}' $ids | awk 'BEGIN {printf "["} {if(NR>1) printf ","; printf "%s",$0} END {print "]"}')
  usage=$(docker stats --no-stream --no-trunc --format '{"id":{{json .ID}},"memory":{{json .MemUsage}}}' $ids | awk 'BEGIN {printf "["} {if(NR>1) printf ","; printf "%s",$0} END {print "]"}')
fi
latest_available=$(awk '/^MemAvailable:/ {printf "%.0f", $2*1024}' /proc/meminfo)
mem_available=$(awk -v before="$mem_available" -v after="$latest_available" 'BEGIN {printf "%.0f", before<after?before:after}')
printf '{"physical_host_id":"%s","generation":"%s","sequence":%s,"cores":%s,"cpu_percent":%s,"load1":%s,"mem_total":%s,"mem_available":%s,"root_total":%s,"root_free":%s,"data_total":%s,"data_free":%s,"runtime_ready":true,"network_ready":true,"managed_identities":%s,"managed_usage":%s}\n' "$physical" "$boot" "$seq" "$cores" "$cpu" "$load" "$mem_total" "$mem_available" "$root_total" "$root_free" "$data_total" "$data_free" "$identities" "$usage"
"""


class _ManagedIdentity(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    project: str
    directory: str


class _ManagedUsage(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory: str = Field(max_length=100)


def docker_memory_credit(raw: str) -> float | None:
    """Subtract a display unit from Docker CLI working set to avoid rounding up."""
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(B|KiB|MiB|GiB|TiB|PiB) / .+", raw)
    if match is None:
        return None
    number, unit = match.groups()
    places = len(number.partition(".")[2])
    lower = max(Decimal(0), Decimal(number) - Decimal(10) ** -places)
    return float(lower * (1024 ** ("B", "KiB", "MiB", "GiB", "TiB", "PiB").index(unit)))


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    generation: UUID
    physical_host_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    sequence: int = Field(ge=0)
    cores: int = Field(ge=1, le=4096)
    cpu_percent: float = Field(ge=0, le=100)
    load1: float = Field(ge=0, le=100_000)
    mem_total: int = Field(gt=0, le=1 << 56)
    mem_available: int = Field(ge=0, le=1 << 56)
    root_total: int = Field(gt=0, le=1 << 56)
    root_free: int = Field(ge=0, le=1 << 56)
    data_total: int = Field(gt=0, le=1 << 56)
    data_free: int = Field(ge=0, le=1 << 56)
    runtime_ready: bool
    network_ready: bool
    managed_identities: list[_ManagedIdentity] = Field(max_length=200)
    managed_usage: list[_ManagedUsage] = Field(max_length=200)


def parse_resources(raw: str, *, received_at: datetime) -> dict[str, Any]:
    """Validate everything before accepting; never include raw output in errors."""
    try:
        if len(raw) > MAX_OUTPUT:
            raise ValueError
        value = _Snapshot.model_validate(json.loads(raw))
        ids = [item.id for item in value.managed_identities]
        samples = [item.id for item in value.managed_usage]
        if (
            len(set(ids)) != len(ids)
            or len(set(samples)) != len(samples)
            or set(ids) != set(samples)
        ):
            raise ValueError
        if any(docker_memory_credit(item.memory) is None for item in value.managed_usage):
            raise ValueError
        if (
            value.mem_available > value.mem_total
            or value.root_free > value.root_total
            or value.data_free > value.data_total
        ):
            raise ValueError
    except (ValueError, TypeError, ValidationError) as error:
        raise ValueError("Invalid independent resource snapshot") from error
    return {
        **value.model_dump(mode="json"),
        "source": "trusted_ssh",
        "received_at": received_at.isoformat(),
        "sampled_at": received_at.isoformat(),
    }


def is_fresh(snapshot: dict[str, Any], now: datetime, max_age: int = MAX_AGE_SECONDS) -> bool:
    try:
        if snapshot.get("probe_error"):
            return False
        observed = datetime.fromisoformat(snapshot["sampled_at"])
        age = (now - observed).total_seconds()
        return snapshot["source"] == "trusted_ssh" and math.isfinite(age) and 0 <= age <= max_age
    except (KeyError, ValueError, TypeError):
        return False


def newer_snapshot(previous: dict[str, Any], incoming: dict[str, Any]) -> bool:
    if not previous:
        return True
    if str(incoming["sampled_at"]) < str(previous.get("probe_attempted_at", "")):
        return False
    if previous.get("generation") == incoming["generation"]:
        return int(incoming["sequence"]) > int(previous.get("sequence", -1))
    # Start time, not completion order, prevents a delayed old boot response
    # from overwriting a probe that started after that host rebooted.
    return str(incoming["sampled_at"]) > str(previous.get("sampled_at", ""))


async def probe_resources(target: AgentSshTarget, box: CredentialBox) -> dict[str, Any]:
    """At most 25 seconds, 8 KiB stdout; pinned host identity, no credential logs."""
    conn = None
    started_at = datetime.now(UTC)
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            conn = await connect_agent_server(
                target, box, expected_revision=target.credential_revision
            )
            async with conn.create_process("sh -s", stderr=asyncssh.DEVNULL) as process:
                process.stdin.write(PROBE_SCRIPT)
                process.stdin.write_eof()
                raw = await process.stdout.read(MAX_OUTPUT + 1)
                if len(raw) > MAX_OUTPUT:
                    process.terminate()
                    raise ValueError("Invalid independent resource snapshot")
                await process.wait_closed()
                if process.exit_status != 0:
                    raise ValueError("Independent resource probe failed")
            snapshot = parse_resources(raw, received_at=datetime.now(UTC))
            snapshot["sampled_at"] = started_at.isoformat()
            return snapshot
    except (TimeoutError, asyncssh.Error, ssh.SshError, ValueError) as error:
        raise ValueError("Independent resource probe unavailable") from error
    finally:
        if conn is not None:
            conn.close()


async def refresh_managed_resources(
    db: "AsyncSession", settings: "Settings", *, batch_size: int = 4
) -> int:
    """Worker hook: max four concurrent probes, bounded to 25 seconds each.

    System-owned SSH read: no user identity or membership is impersonated. The
    workspace ID is retained in every write predicate. Oldest attempt first
    means broken hosts cannot starve healthy ones; failures remain ineligible.
    """
    from sqlalchemy import func, select

    from app.domain.agent_server_credentials import ServerCredentialError, password_readable
    from app.domain.agent_servers import _connection_snapshot
    from app.domain.physical_hosts import claim_physical_host
    from app.models import AgentServer

    batch_size = max(1, min(batch_size, 4))
    box = CredentialBox.from_settings(settings)
    rows = (
        await db.execute(
            select(AgentServer, AgentSshTarget)
            .join(AgentSshTarget, AgentSshTarget.server_id == AgentServer.id)
            .where(
                AgentServer.kind == "ssh",
                AgentSshTarget.prepared_at.is_not(None),
                AgentSshTarget.host_key_confirmed_at.is_not(None),
                AgentSshTarget.login_password_ref.is_not(None),
            )
            .order_by(
                func.coalesce(
                    AgentServer.resource_snapshot["probe_attempted_at"].astext,
                    AgentServer.resource_snapshot["received_at"].astext,
                )
                .asc()
                .nulls_first(),
                AgentServer.id,
            )
            .limit(batch_size)
        )
    ).all()
    pending = []
    for server, target in sorted(rows, key=lambda row: row[0].id.int):
        await db.refresh(server, with_for_update=True)
        await db.refresh(target)
        if (
            is_fresh(
                server.resource_snapshot or {},
                datetime.now(UTC),
                settings.agent_placement_metrics_max_age_seconds,
            )
            and (server.resource_snapshot or {}).get("physical_identity_verified") is True
        ):
            continue
        attempted_at = datetime.now(UTC).isoformat()
        server.resource_snapshot = {
            **(server.resource_snapshot or {}),
            "probe_attempted_at": attempted_at,
        }
        if not password_readable(box, target.login_password_ref):
            server.resource_snapshot = {**server.resource_snapshot, "probe_error": "unavailable"}
            continue
        identity = _connection_snapshot(target)
        db.expunge(target)
        pending.append((server.id, server.workspace_id, target, identity, attempted_at))
    await db.commit()

    async def sample(target: AgentSshTarget) -> dict[str, Any] | None:
        try:
            return await probe_resources(target, box)
        except (ValueError, ServerCredentialError):
            return None

    results = await asyncio.gather(*(sample(target) for _, _, target, _, _ in pending))
    accepted = 0
    for (server_id, workspace_id, _, identity, attempted_at), incoming in sorted(
        zip(pending, results, strict=True), key=lambda item: item[0][0].int
    ):
        current_server = await db.scalar(
            select(AgentServer)
            .where(AgentServer.id == server_id, AgentServer.workspace_id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        current_target = await db.scalar(
            select(AgentSshTarget)
            .where(
                AgentSshTarget.server_id == server_id, AgentSshTarget.workspace_id == workspace_id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current_server is None or current_target is None:
            continue
        if incoming is None or _connection_snapshot(current_target) != identity:
            if (current_server.resource_snapshot or {}).get("probe_attempted_at") != attempted_at:
                continue
            current_server.resource_snapshot = {
                **(current_server.resource_snapshot or {}),
                "probe_error": "unavailable",
            }
        elif newer_snapshot(current_server.resource_snapshot or {}, incoming):
            if not await claim_physical_host(
                db,
                incoming["physical_host_id"],
                workspace_id=workspace_id,
                role="agent",
                server_id=server_id,
            ):
                current_server.resource_snapshot = {
                    **(current_server.resource_snapshot or {}),
                    "probe_error": "PHYSICAL_HOST_CONFLICT",
                }
                continue
            current_server.resource_snapshot = {
                **incoming,
                "physical_identity_verified": True,
                "probe_attempted_at": incoming["sampled_at"],
            }
            accepted += 1
    await db.commit()
    return accepted
