"""Trusted node identity; capacity remains unknown until collector validation.

metrics.k8s.io lacks storage availability and original Pod UID provenance.
"""

import hashlib
import re
from typing import Any

from app.runner_agent import VERSION
from app.runner_agent.client import Workspace
from app.runtime_v2.runner import Kubectl


class TelemetryUnavailable(RuntimeError):
    """A complete, trusted host sample cannot currently be produced."""


class NodeTelemetry:
    def __init__(self, api: Kubectl, node_name: str) -> None:
        self.api, self.node_name = api, node_name

    def physical_host_id(self) -> str:
        node = self.api.get_optional("node", self.node_name)
        meta = (node or {}).get("metadata") or {}
        machine_id = (((node or {}).get("status") or {}).get("nodeInfo") or {}).get("machineID")
        if (
            meta.get("name") != self.node_name
            or not meta.get("uid")
            or not isinstance(machine_id, str)
        ):
            raise TelemetryUnavailable("configured Node identity unavailable")
        normalized = machine_id.strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", normalized) is None:
            raise TelemetryUnavailable("Node machine identity unavailable")
        return hashlib.sha256(("machine-id:" + normalized).encode("ascii")).hexdigest()

    def capacity(self) -> dict[str, Any]:
        """Heartbeat only; unknowns never refresh capacity eligibility."""
        return {
            "agent_version": VERSION,
            "docker_version": None,
            "runtimes": [],
            "kvm": False,
            "cpus": None,
            "mem_bytes": None,
            "disk_bytes": None,
            "disk_free_bytes": None,
            "wait_seconds": 25,
        }

    def report(self, workspace: Workspace) -> None:
        # NEEDS_CONTEXT: a versioned node/storage collector and Kubernetes report
        # contract with UID provenance must precede /report/start + collection +
        # /report(sample_id, physical_host_id). Today's Docker report would turn
        # missing measurements into zeros.
        raise TelemetryUnavailable("trusted Kubernetes host/storage collector is not configured")
