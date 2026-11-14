"""Bounded operation receipts; validation errors never contain supplied material."""

from __future__ import annotations

import base64
import binascii
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

OPERATION_KINDS = frozenset(
    {"exec", "put_files", "get_files", "list_files", "snapshot", "archive_env"}
)
NON_BINDING_KINDS = OPERATION_KINDS - {"archive_env"}
OUTPUT_LIMIT = 1024 * 1024
FILE_LIMIT = 20 * 1024 * 1024
SNAPSHOT_LIMIT = 5 * 1024**3
PART_LIMIT = 100_000
PATH_LIMIT = 4096
_ERROR = "invalid operation result"
LIFECYCLE_FIELDS = frozenset(
    {
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
)


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError(_ERROR)


def _object(value: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    _require(type(value) is dict)
    _require(required <= value.keys() <= required | (optional or set()))
    return dict(value)


def _integer(value: Any, maximum: int, minimum: int = 0) -> None:
    _require(type(value) is int and minimum <= value <= maximum)


def _path(value: Any, *, root: bool = False) -> None:
    _require(type(value) is str)
    _require(0 < len(value.encode("utf-8")) <= PATH_LIMIT and "\x00" not in value)
    if root and value == ".":
        return
    _require(not value.startswith("/") and all(p not in {"", ".", ".."} for p in value.split("/")))
    _require(len(value.split("/")) <= 64)


def _decode(value: Any, limit: int) -> bytes:
    _require(type(value) is str and len(value) <= 4 * ((limit + 2) // 3))
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError(_ERROR) from None
    _require(len(raw) <= limit and base64.b64encode(raw).decode("ascii") == value)
    return raw


def _digest(value: Any) -> None:
    _require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None)


def _snapshot(value: Any) -> dict[str, Any]:
    result = _object(value, {"entries", "parts", "bytes"}, {"part_sha256"})
    _integer(result["parts"], PART_LIMIT)
    _integer(result["bytes"], SNAPSHOT_LIMIT)
    entries = result["entries"]
    _require(type(entries) is list and 1 <= len(entries) <= 1000)
    first, total = 0, 0
    seen: set[str] = set()
    for raw in entries:
        missing = type(raw) is dict and raw.get("missing") is True
        entry = _object(
            raw,
            {"path", "first", "parts", "missing"}
            if missing
            else {"path", "first", "parts", "bytes"},
            {"sha256"},
        )
        _path(entry["path"], root=True)
        _require(entry["path"] not in seen)
        seen.add(entry["path"])
        _integer(entry["first"], PART_LIMIT)
        _integer(entry["parts"], PART_LIMIT)
        _require(entry["first"] == first)
        if missing:
            _require(entry["parts"] == 0 and "sha256" not in entry)
        else:
            _integer(entry["bytes"], SNAPSHOT_LIMIT)
            _require(entry["parts"] > 0 and entry["bytes"] > 0)
            _require(entry["parts"] <= entry["bytes"] <= entry["parts"] * 16 * 1024**2)
            total += entry["bytes"]
        first += entry["parts"]
        if "sha256" in entry:
            _digest(entry["sha256"])
    _require(first == result["parts"] and total == result["bytes"])
    if "part_sha256" in result:
        digests = result["part_sha256"]
        _require(type(digests) is list and len(digests) == first)
        for digest in digests:
            _digest(digest)
    return result


def runtime_proof(value: Any) -> dict[str, Any]:
    """Validate common proof separately from operation-specific result data."""
    required = {
        "workspace_id",
        "instance_id",
        "env_id",
        "allocation_id",
        "generation",
        "node_name",
        "host_id",
        "command_id",
        "deployment_uid",
        "pvc_uid",
        "target_kind",
    }
    proof = _object(value, required, {"pod_uid", "operation_pod_uid", "stopped"})
    _integer(proof["generation"], 2**63 - 1, 1)
    for key, item in proof.items():
        if key == "generation":
            continue
        if key == "stopped":
            _require(type(item) is bool)
        else:
            _require(type(item) is str and 0 < len(item) <= 253 and "\x00" not in item)
    _require(proof["target_kind"] in {"workload", "volume-helper"})
    target_uid = "pod_uid" if proof["target_kind"] == "workload" else "operation_pod_uid"
    _require(target_uid in proof)
    return proof


def operation_result(
    kind: str, value: Any, *, secret_values: Sequence[str] | Mapping[str, str] = ()
) -> dict[str, Any]:
    try:
        return _operation_result(kind, value, secret_values=secret_values)
    except (ValueError, TypeError, OverflowError, UnicodeError):
        raise ValueError(_ERROR) from None


def _operation_result(
    kind: str, value: Any, *, secret_values: Sequence[str] | Mapping[str, str]
) -> dict[str, Any]:
    """Validate Docker-shaped results before persistence and redact transient exec secrets."""
    envelope = _object(value, {"status"}, {"result", "error"})
    _require(envelope["status"] in {"done", "failed"})
    proof = None
    raw = envelope.get("result")
    if type(raw) is dict and "runtime_proof" in raw:
        proof = runtime_proof(raw["runtime_proof"])
        raw = {key: item for key, item in raw.items() if key != "runtime_proof"}
    if envelope["status"] == "failed":
        failed: dict[str, Any] = {"status": "failed", "error": "Kubernetes operation failed"}
        if proof is not None:
            failed["result"] = {"runtime_proof": proof}
        return failed
    _require("result" in envelope and "error" not in envelope)
    if kind == "exec":
        result = _object(
            raw, {"exit_code", "output_b64", "truncated", "timed_out", "seconds", "started_at"}
        )
        if result["exit_code"] is None:
            _require(result["timed_out"] is True)
        else:
            _integer(result["exit_code"], 2**31 - 1, -(2**31))
        _require(type(result["truncated"]) is bool and type(result["timed_out"]) is bool)
        seconds = result["seconds"]
        _require(type(seconds) in {int, float} and math.isfinite(seconds) and 0 <= seconds <= 86400)
        started = result["started_at"]
        _require(type(started) is str and len(started) <= 64)
        try:
            timestamp = datetime.fromisoformat(started)
            _require(timestamp.tzinfo is not None)
        except ValueError:
            raise ValueError(_ERROR) from None
        output = _decode(result["output_b64"], OUTPUT_LIMIT)
        if isinstance(secret_values, Mapping):
            secret_values = tuple(secret_values.values())
        _require(isinstance(secret_values, (list, tuple)))
        _require(len(secret_values) <= 1000 and all(type(s) is str for s in secret_values))
        secrets = sorted({s.encode("utf-8") for s in secret_values if s}, key=len, reverse=True)
        marker = b"[REDACTED]"
        if any(secret in marker for secret in secrets):
            # No replacement may itself disclose a delivered value. Dropping
            # output also avoids amplification when a secret occurs in marker.
            output = b""
            result["truncated"] = True
        else:
            for secret in secrets:
                output = output.replace(secret, marker)
        if len(output) > OUTPUT_LIMIT:
            output = output[:OUTPUT_LIMIT]
            result["truncated"] = True
        # Replacement boundaries can create a different, previously absent
        # secret. Verify once after all replacements; never iterate to a fixed
        # point (which can amplify output or require quadratic redaction passes).
        if any(secret in output for secret in secrets):
            output = b""
            result["truncated"] = True
        result["output_b64"] = base64.b64encode(output).decode("ascii")
    elif kind == "put_files":
        result = _object(raw, {"size"})
        _integer(result["size"], FILE_LIMIT)
    elif kind == "get_files":
        result = _object(raw, {"b64", "size"})
        _integer(result["size"], FILE_LIMIT)
        _require(len(_decode(result["b64"], FILE_LIMIT)) == result["size"])
    elif kind == "list_files":
        result = _object(raw, {"entries"})
        entries = result["entries"]
        _require(type(entries) is list and len(entries) <= 1000)
        seen: set[str] = set()
        for raw_entry in entries:
            entry = _object(raw_entry, {"path", "kind", "size"})
            _path(entry["path"])
            _require(entry["path"] not in seen)
            seen.add(entry["path"])
            _require(entry["kind"] in {"file", "dir", "link", "other"})
            _integer(entry["size"], 2**63 - 1)
    elif kind == "snapshot":
        result = _snapshot(raw)
    elif kind == "archive_env":
        result = _object(raw, set(), set(LIFECYCLE_FIELDS))
        for key, item in result.items():
            if key in {"ready", "stopped", "deleted"}:
                _require(type(item) is bool)
            elif key == "generation":
                _integer(item, 2**63 - 1, 1)
            else:
                _require(type(item) is str and 0 < len(item) <= 253 and "\x00" not in item)
    else:
        raise ValueError(_ERROR)
    if proof is not None:
        result["runtime_proof"] = proof
    return {"status": "done", "result": result}


def journal_context(context: dict[str, Any]) -> dict[str, Any]:
    """Only identity metadata may cross the durable recovery boundary."""
    try:
        result = _object(context, {"kind", "host_id", "identity"})
        _require(type(result["kind"]) is str and 0 < len(result["kind"]) <= 64)
        _require(type(result["host_id"]) is str and len(result["host_id"]) <= 64)
        identity = _object(
            result["identity"],
            {"workspace_id", "instance_id", "env_id", "allocation_id", "generation", "node_name"},
        )
        for key in ("workspace_id", "instance_id", "env_id", "allocation_id", "node_name"):
            _require(type(identity[key]) is str and 0 < len(identity[key]) <= 253)
        _integer(identity["generation"], 2**63 - 1, 1)
        return {**result, "identity": identity}
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("invalid journal context") from None
