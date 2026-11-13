"""Read-only restart recovery anchored to durable original Kubernetes UIDs."""

import logging
from typing import Any
from uuid import UUID

from app.runner_agent.client import Workspace, WorkspaceError
from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.runner import KubernetesCommandError, KubernetesRunner, OutcomeUnknown

logger = logging.getLogger(__name__)


def refresh_bindings(
    runner: KubernetesRunner,
    workspace: Workspace,
    receipts: list[dict[str, Any]],
    *,
    workspace_id: UUID,
    host_id: UUID,
    node_name: str,
) -> None:
    for entry in receipts:
        try:
            context, provenance = entry["context"], entry["provenance"]
            data = context["identity"]
            identity = RuntimeIdentity(
                UUID(data["workspace_id"]),
                UUID(data["instance_id"]),
                UUID(data["env_id"]),
                UUID(data["allocation_id"]),
                data["generation"],
                data["node_name"],
            )
            if (
                identity.workspace_id != workspace_id
                or context["host_id"] != str(host_id)
                or identity.node_name != node_name
            ):
                continue
            observed = runner.reconcile_lifecycle(identity, "start_env", provenance)
            if observed.get("ready") is not True:
                continue
            body = {
                "workspace_id": str(identity.workspace_id),
                "instance_id": str(identity.instance_id),
                "environment_id": str(identity.environment_id),
                "allocation_id": str(identity.allocation_id),
                "generation": identity.generation,
                "node_name": node_name,
                **{k: observed[k] for k in ("deployment_uid", "pvc_uid", "pod_uid", "pod_ip")},
            }
            workspace.post("/network/binding", body, timeout=10)
        except WorkspaceError as error:
            if error.status in (401, 403):
                raise
            logger.warning("runtime binding awaiting control-plane confirmation")
        except (ValueError, KeyError, TypeError, OutcomeUnknown, KubernetesCommandError):
            logger.warning("runtime binding awaiting verified Kubernetes identity")
