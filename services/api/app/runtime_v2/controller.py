"""Durable command receipts for a per-host Kubernetes controller.

A begun operation without a completed receipt is unknown after a restart. It
must be reconciled, not replayed or reported as a definitive failure.
"""

import json
import sqlite3
from typing import Any

from app.runner_agent.client import Workspace
from app.runtime_v2.runner import KubernetesRunner


class CommandJournal:
    def __init__(self, path: str) -> None:
        self.path = path
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, result TEXT)")

    def begin(self, command_id: str) -> str | dict[str, Any]:
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT result FROM receipts WHERE id = ?", (command_id,)).fetchone()
            if row is None:
                db.execute("INSERT INTO receipts(id) VALUES (?)", (command_id,))
                return "new"
            return json.loads(row[0]) if row[0] is not None else "unknown"

    def finish(self, command_id: str, result: dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as db:
            updated = db.execute(
                "UPDATE receipts SET result = ? WHERE id = ? AND result IS NULL",
                (json.dumps(result, separators=(",", ":")), command_id),
            )
            if updated.rowcount != 1:
                raise ValueError("missing or already completed receipt")


class Controller:
    """Process a polled command once and report only confirmed outcomes."""

    def __init__(
        self,
        runner: KubernetesRunner,
        workspace: Workspace,
        journal: CommandJournal,
        *,
        attempts: int = 30,
        interval: float = 2.0,
    ) -> None:
        if attempts < 1 or attempts > 300 or interval < 0 or interval > 10:
            raise ValueError("invalid reconciliation bounds")
        self.runner = runner
        self.workspace = workspace
        self.journal = journal
        self.attempts = attempts
        self.interval = interval

    def process(self, command: dict[str, Any]) -> None:
        import time
        from uuid import UUID

        from app.runtime_v2.kubernetes import RuntimeIdentity
        from app.runtime_v2.runner import KubernetesCommandError, OutcomeUnknown, PendingDeletion

        command_id = str(UUID(str(command["id"])))
        previous = self.journal.begin(command_id)
        if previous == "unknown":
            return
        if isinstance(previous, dict):
            self.workspace.post(f"/commands/{command_id}/result", previous, timeout=60)
            return
        mutated = False
        try:
            payload = command.get("payload") or {}
            data = payload["runtime_identity"]
            ident = RuntimeIdentity(
                UUID(str(data["workspace_id"])),
                UUID(str(data["instance_id"])),
                UUID(str(data["env_id"])),
                UUID(str(data["allocation_id"])),
                int(data["generation"]),
                str(data["node_name"]),
            )
            if str(ident.environment_id) != str(UUID(str(payload["env_id"]))):
                raise ValueError("environment identity mismatch")
            kind = command.get("kind")
            if kind == "create_env":
                self.runner.validate_create(ident, payload)
                # A failed request can have reached the server; mark before calling.
                mutated = True
                self.runner.create(ident, payload)
                expected = "ready"
            elif kind in ("start_env", "stop_env"):
                mutated = True
                self.runner.scale(ident, 1 if kind == "start_env" else 0)
                expected = "ready" if kind == "start_env" else "stopped"
            elif kind == "delete_env":
                mutated = True
                expected = "deleted"
            else:
                raise ValueError(f"unsupported Kubernetes command: {kind}")
            for index in range(self.attempts):
                if expected == "deleted":
                    try:
                        state = self.runner.delete(ident)
                    except PendingDeletion:
                        state = {}
                else:
                    state = self.runner.observe(ident)
                if (
                    (expected == "deleted" and state.get("deleted"))
                    or (expected == "ready" and state.get("ready"))
                    or (expected == "stopped" and state.get("pod_uid") is None)
                ):
                    result = {"status": "done", "result": state}
                    break
                if index + 1 < self.attempts:
                    time.sleep(self.interval)
            else:
                return
        except (ValueError, KeyError, TypeError, KubernetesCommandError, OutcomeUnknown) as error:
            if mutated:
                return
            result = {"status": "failed", "error": str(error)[:4000]}
        self.journal.finish(command_id, result)
        self.workspace.post(f"/commands/{command_id}/result", result, timeout=60)

    def poll_once(self, metrics: dict[str, Any]) -> None:
        """Poll using separately collected, trusted host metrics."""
        reply = self.workspace.post("/poll", metrics, timeout=45)
        for command in reply.get("commands") or []:
            self.process(command)
