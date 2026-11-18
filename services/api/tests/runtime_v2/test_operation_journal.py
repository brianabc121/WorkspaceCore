"""Real SQLite receipts exercise encryption, bounds and binding history."""

import base64
import os
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.runtime_v2.controller import CommandJournal


def context(kind: str = "exec") -> dict[str, Any]:
    return {
        "kind": kind,
        "host_id": str(uuid4()),
        "identity": {
            "workspace_id": str(uuid4()),
            "instance_id": str(uuid4()),
            "env_id": str(uuid4()),
            "allocation_id": str(uuid4()),
            "generation": 1,
            "node_name": "node-a",
        },
    }


def exec_result(output: bytes = b"hello") -> dict[str, Any]:
    return {
        "status": "done",
        "result": {
            "exit_code": 0,
            "output_b64": base64.b64encode(output).decode(),
            "truncated": False,
            "timed_out": False,
            "seconds": 1.5,
            "started_at": "2026-11-14T00:00:00+00:00",
        },
    }


def test_encrypted_redacted_exec_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    journal = CommandJournal(str(path))
    command = str(uuid4())
    journal.begin(command, context())
    expected = exec_result(b"hello [REDACTED]")
    journal.finish(
        command, exec_result(b"hello synthetic-secret"), secret_values=["synthetic-secret"]
    )
    assert journal.begin(command) == expected
    assert CommandJournal(str(path)).pending()[0][2] == expected
    raw = path.read_bytes()
    assert b"synthetic-secret" not in raw
    assert b"output_b64" not in raw
    assert base64.b64encode(b"hello [REDACTED]") not in raw
    key = Path(str(path) + ".key")
    assert len(key.read_bytes()) == 32
    assert key.stat().st_mode & 0o777 == 0o600


def test_file_bytes_are_encrypted_and_authenticated_to_command(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    journal = CommandJournal(str(path))
    first, second = str(uuid4()), str(uuid4())
    for command in (first, second):
        journal.begin(command, context("get_files"))
    content = b"synthetic private file bytes"
    result = {
        "status": "done",
        "result": {"b64": base64.b64encode(content).decode(), "size": len(content)},
    }
    journal.finish(first, result)
    assert base64.b64encode(content) not in path.read_bytes()
    assert journal.begin(first) == result
    with sqlite3.connect(path) as db:
        value = db.execute("SELECT result FROM receipts WHERE id=?", (first,)).fetchone()[0]
        db.execute("UPDATE receipts SET result=? WHERE id=?", (value, second))
    with pytest.raises(ValueError, match="receipt"):
        CommandJournal(str(path))


@pytest.mark.parametrize("damage", ["missing", "wrong", "short", "mode", "symlink", "hardlink"])
def test_existing_ciphertext_never_regenerates_invalid_key(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "journal.db"
    journal = CommandJournal(str(path))
    command = str(uuid4())
    journal.begin(command, context())
    journal.finish(command, exec_result())
    key = Path(str(path) + ".key")
    if damage == "missing":
        key.unlink()
    elif damage == "mode":
        key.chmod(0o644)
    elif damage == "symlink":
        original = key.read_bytes()
        key.unlink()
        other = tmp_path / "other"
        other.write_bytes(original)
        key.symlink_to(other)
    elif damage == "hardlink":
        os.link(key, tmp_path / "other")
    else:
        key.write_bytes(b"x" * (32 if damage == "wrong" else 3))
    with pytest.raises(ValueError, match="receipt"):
        CommandJournal(str(path))
    if damage == "missing":
        assert not key.exists()


@pytest.mark.parametrize(
    "kind,value",
    [
        ("exec", {**exec_result()["result"], "output_b64": "!"}),
        ("exec", {**exec_result()["result"], "seconds": float("nan")}),
        ("exec", {**exec_result()["result"], "exit_code": True}),
        (
            "exec",
            {**exec_result()["result"], "output_b64": base64.b64encode(b"x" * 1048577).decode()},
        ),
        ("put_files", {"size": 20971521}),
        ("get_files", {"size": 2, "b64": "eA=="}),
        ("list_files", {"entries": [{"path": "../secret", "kind": "file", "size": 1}]}),
        ("list_files", {"entries": [{"path": "a", "kind": "file", "size": 0}] * 1001}),
        (
            "snapshot",
            {
                "entries": [{"path": ".", "first": 1, "parts": 1, "bytes": 1}],
                "parts": 1,
                "bytes": 1,
            },
        ),
        ("archive_env", {"deleted": True, "credential": "synthetic-secret"}),
    ],
)
def test_invalid_operation_result_refused_atomically(
    tmp_path: Path, kind: str, value: dict[str, Any]
) -> None:
    journal = CommandJournal(str(tmp_path / "journal.db"))
    command = str(uuid4())
    journal.begin(command, context(kind))
    with pytest.raises(ValueError, match="result"):
        journal.finish(command, {"status": "done", "result": value})
    assert journal.begin(command) == "unknown"


@pytest.mark.parametrize(
    "kind,value",
    [
        ("put_files", {"size": 20971520}),
        ("get_files", {"size": 1, "b64": "eA=="}),
        ("list_files", {"entries": [{"path": "a/b", "kind": "link", "size": 1}]}),
        (
            "snapshot",
            {
                "entries": [{"path": ".", "first": 0, "parts": 1, "bytes": 5}],
                "parts": 1,
                "bytes": 5,
            },
        ),
        ("archive_env", {"deleted": True}),
    ],
)
def test_operation_result_roundtrip(tmp_path: Path, kind: str, value: dict[str, Any]) -> None:
    path = str(tmp_path / "journal.db")
    journal = CommandJournal(path)
    command = str(uuid4())
    journal.begin(command, context(kind))
    result = {"status": "done", "result": value}
    journal.finish(command, result)
    assert CommandJournal(path).begin(command) == result


@pytest.mark.parametrize("new_kind", ["exec", "get_files", "put_files", "list_files", "snapshot"])
@pytest.mark.parametrize("completed", [False, True])
def test_operation_receipt_does_not_hide_actual_binding(
    tmp_path: Path, new_kind: str, completed: bool
) -> None:
    journal = CommandJournal(str(tmp_path / "journal.db"))
    lifecycle = context("create_env")
    first, second = str(uuid4()), str(uuid4())
    journal.begin(first, lifecycle)
    journal.remember(first, {"deployment": "dep", "pvc": "pvc", "started": True})
    journal.finish(first, {"status": "done", "result": {"ready": True}})
    journal.delivered(first)
    journal.begin(second, {**lifecycle, "kind": new_kind})
    if completed:
        journal.finish(second, {"status": "failed", "error": "do not persist synthetic-secret"})
        journal.delivered(second)
    assert len(journal.binding_receipts()) == 1


@pytest.mark.parametrize("barrier", ["stop_env", "delete_env", "archive_env", "start_env"])
def test_unknown_lifecycle_receipt_remains_binding_barrier(tmp_path: Path, barrier: str) -> None:
    journal = CommandJournal(str(tmp_path / "journal.db"))
    lifecycle = context("create_env")
    command = str(uuid4())
    journal.begin(command, lifecycle)
    journal.remember(command, {"deployment": "dep", "pvc": "pvc"})
    journal.finish(command, {"status": "done", "result": {"ready": True}})
    journal.delivered(command)
    journal.begin(str(uuid4()), {**lifecycle, "kind": barrier})
    assert journal.binding_receipts() == []


def test_context_never_persists_request_or_secret_values(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    journal = CommandJournal(str(path))
    with pytest.raises(ValueError, match="context"):
        journal.begin(str(uuid4()), {**context(), "secret_values": ["synthetic-secret"]})
    assert b"synthetic-secret" not in path.read_bytes()


def test_secret_mapping_values_redacted_at_delivery_time(tmp_path: Path) -> None:
    journal = CommandJournal(str(tmp_path / "journal.db"))
    command = str(uuid4())
    journal.begin(command, context())
    journal.finish(
        command,
        exec_result(b"credential revoked-before-callback"),
        secret_values={"API_TOKEN": "revoked-before-callback"},
    )
    assert journal.begin(command) == exec_result(b"credential [REDACTED]")


@pytest.mark.parametrize("mutation", ["version", "payload", "marker", "plaintext"])
def test_ciphertext_tamper_and_plaintext_downgrade_fail_closed(
    tmp_path: Path, mutation: str
) -> None:
    import json

    path = tmp_path / "journal.db"
    journal = CommandJournal(str(path))
    command = str(uuid4())
    journal.begin(command, context())
    journal.finish(command, exec_result())
    with sqlite3.connect(path) as db:
        saved = json.loads(db.execute("SELECT result FROM receipts").fetchone()[0])
        if mutation == "version":
            saved["sealed_operation_receipt"] = 2
        elif mutation == "payload":
            saved["data"] = "A" * len(saved["data"])
        elif mutation == "marker":
            saved["other"] = saved.pop("sealed_operation_receipt")
        else:
            saved = {"status": "done", "result": {"ready": True}}
        db.execute("UPDATE receipts SET result=?", (json.dumps(saved),))
    with pytest.raises(ValueError, match="receipt"):
        CommandJournal(str(path))


@pytest.mark.parametrize(
    "value",
    [
        {"status": []},
        {"status": "done", "result": []},
        {"status": "done", "result": {**exec_result()["result"], "seconds": 10**1000}},
        {"status": "done", "result": {**exec_result()["result"], "output_b64": "eH=="}},
        {"status": "done", "result": {**exec_result()["result"], "unexpected": "secret"}},
    ],
)
def test_malformed_types_refused_as_generic_value_error(
    tmp_path: Path, value: dict[str, Any]
) -> None:
    journal = CommandJournal(str(tmp_path / "journal.db"))
    command = str(uuid4())
    journal.begin(command, context())
    with pytest.raises(ValueError, match="invalid operation result"):
        journal.finish(command, value)
    assert journal.begin(command) == "unknown"


def test_confirmed_timeout_result_retains_docker_null_exit_code(tmp_path: Path) -> None:
    journal = CommandJournal(str(tmp_path / "journal.db"))
    command = str(uuid4())
    journal.begin(command, context())
    result = exec_result()
    result["result"].update(exit_code=None, timed_out=True)
    journal.finish(command, result)
    assert journal.begin(command) == result


def test_snapshot_digests_and_missing_entry_roundtrip(tmp_path: Path) -> None:
    journal = CommandJournal(str(tmp_path / "journal.db"))
    command = str(uuid4())
    journal.begin(command, context("snapshot"))
    result = {
        "status": "done",
        "result": {
            "entries": [
                {"path": "data", "first": 0, "parts": 1, "bytes": 5, "sha256": "a" * 64},
                {"path": "missing", "first": 1, "parts": 0, "missing": True},
            ],
            "parts": 1,
            "bytes": 5,
            "part_sha256": ["b" * 64],
        },
    }
    journal.finish(command, result)
    assert journal.begin(command) == result


@pytest.mark.parametrize("bad_digest", ["", "a" * 63, "A" * 64, ["a" * 64]])
def test_snapshot_rejects_invalid_digest(tmp_path: Path, bad_digest: Any) -> None:
    journal = CommandJournal(str(tmp_path / "journal.db"))
    command = str(uuid4())
    journal.begin(command, context("snapshot"))
    with pytest.raises(ValueError, match="invalid operation result"):
        journal.finish(
            command,
            {
                "status": "done",
                "result": {
                    "entries": [{"path": ".", "first": 0, "parts": 1, "bytes": 5}],
                    "parts": 1,
                    "bytes": 5,
                    "part_sha256": [bad_digest],
                },
            },
        )
    assert journal.begin(command) == "unknown"


def test_lost_key_during_live_journal_does_not_generate_replacement(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    journal = CommandJournal(str(path))
    first, second = str(uuid4()), str(uuid4())
    for command in (first, second):
        journal.begin(command, context())
    journal.finish(first, exec_result())
    key = Path(str(path) + ".key")
    key.unlink()
    with pytest.raises(ValueError, match="receipt"):
        journal.finish(second, exec_result())
    assert not key.exists()
    assert journal.begin(second) == "unknown"


@pytest.mark.parametrize(
    "output,secrets",
    [
        (b"[REDACTED]", ["[REDACTED]"]),
        (b"sensitive REDACTED output", ["REDACTED"]),
        (b"abc abcd bcd", ["ab", "bc", "abcd", "bcd"]),
        (b"cab", ["ab", "c[REDACTED]"]),
        (b"hello synthetic-secret", ["synthetic-secret", "E"]),
    ],
)
def test_redaction_final_output_contains_no_delivered_secret(
    tmp_path: Path, output: bytes, secrets: list[str]
) -> None:
    path = str(tmp_path / "journal.db")
    journal = CommandJournal(path)
    command = str(uuid4())
    journal.begin(command, context())
    journal.finish(command, exec_result(output), secret_values=secrets)
    receipt = CommandJournal(path).begin(command)
    assert isinstance(receipt, dict)
    final_output = base64.b64decode(receipt["result"]["output_b64"], validate=True)
    assert all(secret.encode() not in final_output for secret in secrets if secret)


@pytest.mark.parametrize("damage", ["wrong-key", "ciphertext", "marker"])
def test_live_journal_authenticates_history_before_new_completion(
    tmp_path: Path, damage: str
) -> None:
    import json

    path = tmp_path / "journal.db"
    journal = CommandJournal(str(path))
    first, second = str(uuid4()), str(uuid4())
    for command in (first, second):
        journal.begin(command, context())
    journal.finish(first, exec_result())
    key = Path(str(path) + ".key")
    if damage == "wrong-key":
        key.write_bytes(b"z" * 32)
    else:
        with sqlite3.connect(path) as db:
            saved = json.loads(
                db.execute("SELECT result FROM receipts WHERE id=?", (first,)).fetchone()[0]
            )
            if damage == "ciphertext":
                saved["data"] = "A" * len(saved["data"])
            else:
                saved["other"] = saved.pop("sealed_operation_receipt")
            db.execute("UPDATE receipts SET result=? WHERE id=?", (json.dumps(saved), first))
    current_key = key.read_bytes()
    with sqlite3.connect(path) as db:
        previous_result = db.execute("SELECT result FROM receipts WHERE id=?", (first,)).fetchone()[
            0
        ]
    with pytest.raises(ValueError, match="receipt"):
        journal.finish(second, exec_result())
    assert journal.begin(second) == "unknown"
    assert key.read_bytes() == current_key
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT result FROM receipts WHERE id=?", (first,)).fetchone()[0]
            == previous_result
        )
        assert db.execute("SELECT result FROM receipts WHERE id=?", (second,)).fetchone()[0] is None
