"""Durable, scoped lifecycle execution; ambiguous writes are reconciled read-only."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import suppress
from typing import Any
from uuid import UUID

from app.domain.runner_runtime_profile import KUBERNETES_COMMANDS
from app.runner_agent.client import Workspace
from app.runtime_v2.kubernetes import RuntimeIdentity
from app.runtime_v2.network import PrivateNetworkReconciler
from app.runtime_v2.runner import (
    KubernetesCommandError,
    KubernetesRunner,
    OutcomeUnknown,
    PendingDeletion,
)

_REJECTED = "Kubernetes lifecycle command rejected; unsupported or invalid input"
_KINDS = KUBERNETES_COMMANDS
_RESULT_FIELDS = {
    "ready",
    "stopped",
    "deleted",
    "pod_uid",
    "node",
    "pod_ip",
    "allocation_id",
    "generation",
    "deployment_uid",
    "pvc_uid",
}


class CommandJournal:
    """Persist identity, operation phase and API UIDs, never command payloads."""

    def __init__(self, path: str) -> None:
        self.path = path
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, result TEXT)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(receipts)")}
            if "recovery" not in columns:
                db.execute("ALTER TABLE receipts ADD COLUMN recovery TEXT")
            if "delivered" not in columns:
                db.execute("ALTER TABLE receipts ADD COLUMN delivered INTEGER NOT NULL DEFAULT 0")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def begin(self, command_id: str, context: dict[str, Any] | None = None) -> str | dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT result, recovery FROM receipts WHERE id = ?", (command_id,)
            ).fetchone()
            if row is None:
                recovery = {"context": context} if context is not None else None
                db.execute(
                    "INSERT INTO receipts(id, recovery) VALUES (?, ?)",
                    (command_id, json.dumps(recovery) if recovery else None),
                )
                return "new"
            saved = json.loads(row[1]) if row[1] else {}
            if context is not None and saved.get("context") != context:
                raise ValueError("command identity changed")
            return json.loads(row[0]) if row[0] is not None else "unknown"

    def recovery(self, command_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT recovery FROM receipts WHERE id = ?", (command_id,)).fetchone()
            return json.loads(row[0]) if row and row[0] else {}

    def remember(self, command_id: str, provenance: dict[str, Any]) -> None:
        # Values originate only from prepare_lifecycle / API UID receipts.
        safe = {
            key: provenance[key] for key in ("deployment", "pvc", "started") if key in provenance
        }
        for key in ("deployment", "pvc"):
            if (
                key in safe
                and safe[key] is not None
                and (not isinstance(safe[key], str) or not safe[key])
            ):
                raise ValueError("invalid UID receipt")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT recovery FROM receipts WHERE id = ? AND result IS NULL", (command_id,)
            ).fetchone()
            if not row or not row[0]:
                raise ValueError("missing lifecycle context")
            recovery = json.loads(row[0])
            previous = recovery.get("provenance") or {}
            if any(
                previous.get(key) is not None and safe.get(key) != previous[key]
                for key in ("deployment", "pvc")
            ):
                raise OutcomeUnknown("operation UID receipt cannot be replaced")
            recovery["provenance"] = safe
            db.execute(
                "UPDATE receipts SET recovery = ? WHERE id = ?", (json.dumps(recovery), command_id)
            )

    def fence(
        self, command_id: str, context: dict[str, Any], provenance: dict[str, Any]
    ) -> dict[str, Any]:
        """Retain original allocation/generation UIDs across every command.

        Completed and delivered receipts are the canonical history; they must
        not be pruned while this allocation/generation can receive commands.
        """
        identity = context["identity"]
        originals: dict[str, Any] = {}
        with self._connect() as db:
            rows = db.execute("SELECT recovery FROM receipts WHERE id != ?", (command_id,))
            for row in rows:
                saved = json.loads(row[0]) if row[0] else {}
                prior = saved.get("context") or {}
                prior_identity = prior.get("identity") or {}
                if (prior_identity.get("allocation_id"), prior_identity.get("generation")) != (
                    identity["allocation_id"],
                    identity["generation"],
                ):
                    continue
                if prior_identity != identity or prior.get("host_id") != context["host_id"]:
                    raise OutcomeUnknown("allocation identity conflicts with durable history")
                for resource in ("deployment", "pvc"):
                    previous = saved.get("provenance") or {}
                    if resource not in previous:
                        continue
                    uid = previous[resource]
                    if resource in originals and originals[resource] != uid:
                        raise OutcomeUnknown("allocation UID history is ambiguous")
                    originals[resource] = uid
        if context["kind"] == "create_env" and originals:
            raise OutcomeUnknown("allocation generation already has lifecycle resource receipts")
        fenced = dict(provenance)
        for resource, uid in originals.items():
            current = fenced.get(resource)
            if current is not None and current != uid:
                raise OutcomeUnknown("resource replaced since original allocation receipt")
            # Absence during deletion does not erase the original UID fence.
            fenced[resource] = uid
        return fenced

    def blocked(self, command_id: str, environment_id: str) -> bool:
        with self._connect() as db:
            rows = db.execute(
                "SELECT recovery FROM receipts WHERE result IS NULL "
                "AND rowid < (SELECT rowid FROM receipts WHERE id = ?)",
                (command_id,),
            )
            return any(
                ((json.loads(row[0]) if row[0] else {}).get("context") or {})
                .get("identity", {})
                .get("env_id")
                == environment_id
                for row in rows
            )

    def pending(self) -> list[tuple[str, dict[str, Any], dict[str, Any] | None]]:
        with self._connect() as db:
            return [
                (
                    row[0],
                    json.loads(row[1]) if row[1] else {},
                    json.loads(row[2]) if row[2] else None,
                )
                for row in db.execute(
                    "SELECT id, recovery, result FROM receipts WHERE delivered = 0 ORDER BY rowid"
                )
            ]

    def finish(self, command_id: str, result: dict[str, Any]) -> None:
        safe = (
            {
                "status": "done",
                "result": {
                    k: v for k, v in (result.get("result") or {}).items() if k in _RESULT_FIELDS
                },
            }
            if result.get("status") == "done"
            else {"status": "failed", "error": _REJECTED}
        )
        with self._connect() as db:
            updated = db.execute(
                "UPDATE receipts SET result = ? WHERE id = ? AND result IS NULL",
                (json.dumps(safe, separators=(",", ":")), command_id),
            )
            if updated.rowcount != 1:
                raise ValueError("missing or already completed receipt")

    def delivered(self, command_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE receipts SET delivered = 1 WHERE id = ? AND result IS NOT NULL",
                (command_id,),
            )

    def binding_receipts(self) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        with self._connect() as db:
            for recovery, value, delivered in db.execute(
                "SELECT recovery, result, delivered FROM receipts ORDER BY rowid DESC"
            ):
                entry = json.loads(recovery) if recovery else {}
                identity = (entry.get("context") or {}).get("identity") or {}
                env_id = identity.get("env_id")
                if not isinstance(env_id, str) or env_id in seen:
                    continue
                seen.add(env_id)
                receipt = json.loads(value) if value else {}
                if (
                    delivered
                    and receipt.get("status") == "done"
                    and entry.get("provenance")
                    and (entry.get("context") or {}).get("kind") in ("create_env", "start_env")
                ):
                    result.append(entry)
        return result


class Controller:
    def __init__(
        self,
        runner: KubernetesRunner,
        workspace: Workspace,
        journal: CommandJournal,
        *,
        workspace_id: UUID,
        host_id: UUID,
        node_name: str,
        attempts: int = 30,
        interval: float = 2.0,
        stopping: threading.Event | None = None,
        network: PrivateNetworkReconciler | None = None,
    ) -> None:
        if type(attempts) is not int or not 1 <= attempts <= 300 or not 0 <= interval <= 10:
            raise ValueError("invalid reconciliation bounds")
        self.workspace_id, self.host_id = UUID(str(workspace_id)), UUID(str(host_id))
        RuntimeIdentity(self.workspace_id, self.host_id, self.host_id, self.host_id, 1, node_name)
        self.node_name = node_name
        self.runner, self.workspace, self.journal = runner, workspace, journal
        self.attempts, self.interval = attempts, interval
        self.stopping = stopping or threading.Event()
        self.network = network

    def _context(self, command: dict[str, Any]) -> tuple[RuntimeIdentity, dict[str, Any]]:
        payload = command["payload"]
        data = payload["runtime_identity"]
        if type(data["generation"]) is not int or command["kind"] not in _KINDS:
            raise ValueError("invalid lifecycle command")
        ident = RuntimeIdentity(
            UUID(str(data["workspace_id"])),
            UUID(str(data["instance_id"])),
            UUID(str(data["env_id"])),
            UUID(str(data["allocation_id"])),
            data["generation"],
            str(data["node_name"]),
        )
        if (
            ident.workspace_id != self.workspace_id
            or ident.node_name != self.node_name
            or UUID(str(payload["host_id"])) != self.host_id
            or ident.environment_id != UUID(str(payload["env_id"]))
        ):
            raise ValueError("controller scope mismatch")
        identity = {
            "workspace_id": str(ident.workspace_id),
            "instance_id": str(ident.instance_id),
            "env_id": str(ident.environment_id),
            "allocation_id": str(ident.allocation_id),
            "generation": ident.generation,
            "node_name": ident.node_name,
        }
        return ident, {"kind": command["kind"], "host_id": str(self.host_id), "identity": identity}

    @staticmethod
    def _expected(kind: str) -> str:
        return "deleted" if kind == "delete_env" else "stopped" if kind == "stop_env" else "ready"

    def _send(self, command_id: str, result: dict[str, Any]) -> None:
        self.workspace.post(f"/commands/{command_id}/result", result, timeout=60)
        self.journal.delivered(command_id)

    def process(self, command: dict[str, Any]) -> None:
        if self.stopping.is_set():
            return
        try:
            command_id = str(UUID(str(command["id"])))
        except (ValueError, KeyError, TypeError):
            return
        try:
            ident, context = self._context(command)
        except (ValueError, KeyError, TypeError):
            if self.journal.begin(command_id) == "new":
                self.journal.finish(command_id, {"status": "failed"})
                self._send(command_id, self.journal.begin(command_id))  # type: ignore[arg-type]
            return
        if command["kind"] == "create_env" and self.network is not None:
            try:
                self.network.ensure_domain(ident)
            except (OutcomeUnknown, KubernetesCommandError, ValueError, KeyError, TypeError):
                return
        try:
            previous = self.journal.begin(command_id, context)
        except ValueError:
            return
        if isinstance(previous, dict):
            if previous.get("status") == "done":
                provenance = self.journal.recovery(command_id).get("provenance")
                if not provenance:
                    return
                try:
                    state = self.runner.reconcile_lifecycle(ident, command["kind"], provenance)
                except (OutcomeUnknown, KubernetesCommandError, ValueError, KeyError, TypeError):
                    return
                expected = self._expected(command["kind"])
                if not state.get(expected):
                    return
                cached = previous.get("result") or {}
                if expected == "ready" and any(
                    cached.get(key) != state.get(key) for key in ("pod_uid", "pod_ip", "node")
                ):
                    # Rebinding is a separate supported operation, not a
                    # license to ACK the old Pod execution target.
                    return
            self._send(command_id, previous)
            return
        if self.journal.blocked(command_id, str(ident.environment_id)):
            return
        kind, payload = command["kind"], command["payload"]
        provenance = self.journal.recovery(command_id).get("provenance")
        started = previous == "unknown"
        try:
            if previous == "new":
                if kind == "create_env":
                    self.runner.validate_create(ident, payload)
                provenance = self.runner.prepare_lifecycle(ident, kind)
                provenance = self.journal.fence(command_id, context, provenance)
                provenance["started"] = True
                self.journal.remember(command_id, provenance)
                started = True
                if kind == "create_env":

                    def receipt(resource: str, uid: str) -> None:
                        provenance[resource] = uid
                        self.journal.remember(command_id, provenance)

                    self.runner.create(ident, payload, operation_id=command_id, receipt=receipt)
                elif kind in ("start_env", "stop_env"):
                    self.runner.scale(ident, 1 if kind == "start_env" else 0)
            if not provenance or not provenance.get("started"):
                return
            attempts = 1 if previous == "unknown" else self.attempts
            for index in range(attempts):
                if self.stopping.is_set():
                    return
                if kind == "delete_env" and previous == "new":
                    with suppress(PendingDeletion):
                        self.runner.delete(ident)
                state = self.runner.reconcile_lifecycle(ident, kind, provenance)
                expected = self._expected(kind)
                if state.get(expected):
                    result = {"status": "done", "result": state}
                    self.journal.finish(command_id, result)
                    self._send(command_id, self.journal.begin(command_id))  # type: ignore[arg-type]
                    return
                if index + 1 < attempts and self.stopping.wait(self.interval):
                    return
        except OutcomeUnknown:
            return
        except (ValueError, KeyError, TypeError, KubernetesCommandError):
            if not started:
                self.journal.finish(command_id, {"status": "failed"})
                self._send(command_id, self.journal.begin(command_id))  # type: ignore[arg-type]

    def recover_pending(self) -> None:
        for command_id, recovery, _result in self.journal.pending():
            if self.stopping.is_set():
                return
            context = recovery.get("context") or {}
            if context.get("host_id") != str(self.host_id):
                continue
            identity = context.get("identity") or {}
            reconstructed = {
                "id": command_id,
                "kind": context.get("kind"),
                "payload": {
                    "host_id": context.get("host_id"),
                    "env_id": identity.get("env_id"),
                    "runtime_identity": identity,
                },
            }
            # process validates the full configured scope before even resending a receipt.
            self.process(reconstructed)

    def poll_once(self, metrics: dict[str, Any]) -> None:
        if self.network is not None:
            from app.runtime_v2.bindings import refresh_bindings

            refresh_bindings(
                self.runner,
                self.workspace,
                self.journal.binding_receipts(),
                workspace_id=self.workspace_id,
                host_id=self.host_id,
                node_name=self.node_name,
            )
            metrics = {**metrics, "wait_seconds": 2}
        reply = self.workspace.post("/poll", metrics, timeout=45)
        for command in reply.get("commands") or []:
            self.process(command)
