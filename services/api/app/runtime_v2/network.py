"""Continuous, scoped private-network reconciliation with durable API UID fences.

Kubernetes receipts prove object observation, never Cilium dataplane enforcement.
The host ACK endpoint independently requires operator network validation.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any
from uuid import UUID

from app.runner_agent.client import Workspace, WorkspaceError
from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.runner import KubernetesCommandError, KubernetesRunner, OutcomeUnknown

logger = logging.getLogger(__name__)


class PrivateNetworkReconciler:
    def __init__(
        self,
        runner: KubernetesRunner,
        workspace: Workspace,
        *,
        workspace_id: UUID,
        host_id: UUID | str,
        node_name: str,
        journal_path: str | None = None,
    ) -> None:
        self.runner, self.workspace = runner, workspace
        self.workspace_id, self.host_id = UUID(str(workspace_id)), UUID(str(host_id))
        self.node_name = node_name
        self._saved: dict[str, dict[str, Any]] = {}
        self.journal_path = journal_path
        if journal_path is not None:
            with sqlite3.connect(journal_path, timeout=5) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS network_receipts "
                    "(record_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )

    def _load(self, record_id: str) -> dict[str, Any]:
        import json

        if self.journal_path is None:
            return self._saved.get(record_id, {}).copy()
        with sqlite3.connect(self.journal_path, timeout=5) as db:
            row = db.execute(
                "SELECT data FROM network_receipts WHERE record_id = ?", (record_id,)
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def _save(self, record_id: str, data: dict[str, Any]) -> None:
        import json

        if self.journal_path is None:
            self._saved[record_id] = data.copy()
            return
        with sqlite3.connect(self.journal_path, timeout=5) as db:
            db.execute(
                "INSERT INTO network_receipts(record_id, data) VALUES (?, ?) "
                "ON CONFLICT(record_id) DO UPDATE SET data = excluded.data",
                (record_id, json.dumps(data)),
            )

    def _identity(self, row: dict[str, Any]) -> RuntimeIdentity:
        if type(row["generation"]) is not int:
            raise ValueError("invalid network generation")
        identity = RuntimeIdentity(
            UUID(str(row["workspace_id"])),
            UUID(str(row["instance_id"])),
            UUID(str(row["environment_id"])),
            UUID(str(row["allocation_id"])),
            row["generation"],
            str(row["node_name"]),
        )
        if identity.workspace_id != self.workspace_id or identity.node_name != self.node_name:
            raise ValueError("network controller scope mismatch")
        return identity

    def ensure_domain(self, identity: RuntimeIdentity) -> None:
        if identity.workspace_id != self.workspace_id or identity.node_name != self.node_name:
            raise ValueError("network controller scope mismatch")
        self.runner.ensure_domain(identity)

    @staticmethod
    def _failure(error: Exception) -> None:
        # Never include workload data, control-plane URLs, credentials or raw errors.
        logger.warning("private network object pending reconciliation (%s)", type(error).__name__)

    def _ack(self, row: dict[str, Any], receipt: dict[str, Any], identity: RuntimeIdentity) -> None:
        if not receipt.get("uid") or not receipt.get("resource_version"):
            raise OutcomeUnknown("network receipt UID or resource version missing")
        self.workspace.post(
            "/network/ack",
            {
                "record_id": str(UUID(row["id"])),
                "revision": row["desired_revision"],
                "generation": identity.generation,
                "success": True,
                "receipt_uid": receipt["uid"],
                "resource_version": receipt["resource_version"],
            },
            timeout=30,
        )

    def _service(self, row: dict[str, Any], gateway_revision: int | None) -> None:
        if row.get("backend") != "kubernetes":
            return
        identity = self._identity(row)
        record_id = str(UUID(row["id"]))
        revision = row["desired_revision"]
        if type(revision) is not int or revision < 1:
            raise ValueError("invalid desired service revision")
        revoked = row["state"] in ("revoked", "expired")
        if row["state"] not in (
            "revoked",
            "expired",
            "approved_pending_apply",
            "active",
            "apply_failed",
        ):
            raise ValueError("invalid desired service state")
        saved = self._load(record_id)
        allocation = str(identity.allocation_id)
        if saved:
            if saved["generation"] > identity.generation or saved["revision"] > revision:
                raise ValueError("stale network desired snapshot")
            if saved["generation"] == identity.generation and saved["allocation"] != allocation:
                raise ValueError("network allocation identity changed")
            if saved.get("revoked") and not revoked:
                raise ValueError("revoked service cannot reopen")
            if gateway_revision is not None and saved.get("gateway_revision", 0) > gateway_revision:
                raise ValueError("stale gateway ingress snapshot")
            if saved["generation"] < identity.generation:
                saved = {}
        saved.update(
            generation=identity.generation,
            revision=revision,
            allocation=allocation,
            revoked=revoked,
        )
        if gateway_revision is not None:
            saved["gateway_revision"] = gateway_revision
        self._save(record_id, saved)  # persist tombstone before mutation or ACK
        sid = UUID(record_id)
        policy_revision = gateway_revision or saved.get("gateway_revision") or revision
        if revoked:
            self.runner.apply_gateway_ingress(identity, sid, policy_revision, row["port"], False)
            # Save the UID before requesting deletion, including an unknown response.
            observed = self.runner.api.get_optional("service", "svc-" + sid.hex, identity.namespace)
            if observed is not None and not saved.get("uid"):
                from app.runtime_v2.runner import verify_identity

                verify_identity(observed, identity)
                meta = observed.get("metadata") or {}
                if not meta.get("uid") or not meta.get("resourceVersion"):
                    raise OutcomeUnknown("service revocation receipt unavailable")
                saved.update(uid=meta["uid"], resource_version=meta["resourceVersion"])
                self._save(record_id, saved)
            receipt = self.runner.delete_private_service(
                identity, sid, revision, expected_uid=saved.get("uid")
            )
            if receipt is not None:
                saved.update(receipt)
                self._save(record_id, saved)
            self._ack(row, saved, identity)
            return
        self.ensure_domain(identity)
        enabled = row.get("gateway_ingress", False)
        if type(enabled) is not bool or (enabled and gateway_revision is None):
            raise ValueError("gateway ingress requires a fenced snapshot")
        self.runner.apply_gateway_ingress(identity, sid, policy_revision, row["port"], enabled)
        receipt = self.runner.apply_private_service(
            identity, sid, revision, row["port"], row["protocol"], expected_uid=saved.get("uid")
        )
        saved.update(receipt)
        self._save(record_id, saved)
        self._ack(row, receipt, identity)

    def reconcile(self) -> None:
        desired = self.workspace.post("/network/desired", {}, timeout=30)
        if not isinstance(desired, dict) or UUID(str(desired.get("host_id"))) != self.host_id:
            raise ValueError("network desired host mismatch")
        gateway_revisions: dict[str, int] = {}
        domains = desired.get("domains") or []
        for domain in domains:
            try:
                identity = self._identity(domain)
                revision = domain.get("gateway_revision")
                ports = domain.get("gateway_ports") or []
                if revision is not None:
                    if type(revision) is not int or revision < 1:
                        raise ValueError("invalid gateway snapshot revision")
                    key = "domain:" + str(identity.instance_id)
                    saved = self._load(key)
                    if saved.get("revision", 0) > revision or (
                        saved.get("revision") == revision
                        and saved.get("ports") != sorted(set(ports))
                    ):
                        raise ValueError("stale gateway domain snapshot")
                    self._save(key, {"revision": revision, "ports": sorted(set(ports))})
                elif ports:
                    raise ValueError("gateway egress requires a fenced snapshot")
                self.ensure_domain(identity)
                self.runner.apply_gateway_egress(identity, ports, revision)
                if revision is not None:
                    gateway_revisions[str(identity.instance_id)] = revision
            except (
                OutcomeUnknown,
                KubernetesCommandError,
                ValueError,
                KeyError,
                TypeError,
            ) as error:
                self._failure(error)
        for row in desired.get("services") or []:
            try:
                self._service(row, gateway_revisions.get(str(row.get("instance_id"))))
            except WorkspaceError as error:
                if error.status in (401, 403):
                    raise
                self._failure(error)
            except (
                OutcomeUnknown,
                KubernetesCommandError,
                ValueError,
                KeyError,
                TypeError,
            ) as error:
                self._failure(error)
