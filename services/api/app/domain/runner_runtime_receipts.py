"""Pure current-allocation receipt fences shared by callbacks and part authorization."""

from typing import Any

from app.models import RunnerCommand, RunnerEnvironment, RunnerHost
from app.runtime_v2.journal_results import OPERATION_KINDS, runtime_proof

RECEIPT_FIELDS = ("binding_generation", "deployment_uid", "pvc_uid", "pod_uid")


def runtime_identity(env: RunnerEnvironment, host: RunnerHost) -> dict[str, Any]:
    metadata = env.runtime_metadata or {}
    return {
        "workspace_id": str(env.workspace_id),
        "instance_id": str(env.owner_instance_id) if env.owner_instance_id else None,
        "env_id": str(env.id),
        "allocation_id": str(metadata.get("allocation_id") or env.id),
        "generation": metadata.get("generation", 1),
        "node_name": host.node_name,
    }


def runtime_receipts(env: RunnerEnvironment) -> dict[str, Any]:
    metadata = env.runtime_metadata or {}
    return {key: metadata[key] for key in RECEIPT_FIELDS if key in metadata}


def validated_receipt(
    command: RunnerCommand,
    env: RunnerEnvironment,
    host: RunnerHost | None,
    result: dict[str, Any] | None = None,
    *,
    require_binding: bool = False,
) -> bool:
    """No IO or mutation: callers must lock host, command and current env first.

    Legacy lifecycle commands need identity only. New operation results always
    need immutable binding provenance plus a strict workload/helper proof.
    ``result=None`` authorizes an active part transport, not a callback.
    """
    if host is None or host.status == "disabled" or env.runtime_backend != "kubernetes":
        return False
    expected = runtime_identity(env, host)
    metadata = env.runtime_metadata or {}
    identity = command.payload.get("runtime_identity")
    if not (
        host.backend == "kubernetes"
        and command.host_id == env.host_id == host.id
        and command.workspace_id == env.workspace_id == host.workspace_id
        and command.environment_id == env.id
        and command.payload.get("host_id") == str(host.id)
        and type(identity) is dict
        and identity == expected
        and type(identity.get("generation")) is int
        and type(expected["generation"]) is int
        and expected["generation"] >= 1
        and type(host.node_name) is str
        and bool(host.node_name)
        and ("node" not in metadata or metadata["node"] == host.node_name)
    ):
        return False
    saved = command.payload.get("runtime_receipts")
    if saved is not None:
        if type(saved) is not dict or not saved.keys() <= set(RECEIPT_FIELDS):
            return False
        # A queued volume operation survives its preceding stop revoking the
        # workload Pod. Its Deployment/PVC remain fenced; actual workload proof
        # below still requires both saved and currently bound Pod UIDs.
        volume_operation = command.kind in OPERATION_KINDS and command.kind != "exec"
        if any(
            value != metadata.get(key)
            for key, value in saved.items()
            if key != "pod_uid" or not volume_operation
        ):
            return False
        if "binding_generation" in saved and type(saved["binding_generation"]) is not int:
            return False
    operation = command.kind in OPERATION_KINDS
    if (require_binding or (operation and result is not None)) and not (
        type(saved) is dict
        and saved.get("binding_generation") == expected["generation"]
        and type(metadata.get("binding_generation")) is int
        and all(
            type(saved.get(key)) is str and bool(saved[key])
            for key in ("deployment_uid", "pvc_uid")
        )
    ):
        return False
    if result is None:
        return True
    raw = result.get("runtime_proof")
    if raw is None:
        return not operation
    try:
        proof = runtime_proof(raw)
    except ValueError:
        return False
    if any(proof[key] != value for key, value in expected.items()):
        return False
    if proof["host_id"] != str(host.id) or proof["command_id"] != str(command.id):
        return False
    if any(proof[key] != metadata.get(key) for key in ("deployment_uid", "pvc_uid")):
        return False
    if proof["target_kind"] == "workload":
        if not (
            proof.get("pod_uid") == metadata.get("pod_uid")
            and type(metadata.get("pod_uid")) is str
            and bool(metadata["pod_uid"])
            and (not operation or (saved or {}).get("pod_uid") == proof["pod_uid"])
        ):
            return False
    elif command.kind == "exec":
        return False
    # A timed-out Kubernetes exec may release compute only with a stop receipt.
    return not (
        command.kind == "exec" and result.get("timed_out") and proof.get("stopped") is not True
    )
