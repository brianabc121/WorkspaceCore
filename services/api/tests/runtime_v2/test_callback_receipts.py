"""Current generation callback fences and authenticated snapshot part operations."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import runner_specs as specs
from app.domain.runner import Runner, RunnerError
from app.domain.runner_snapshots import SnapshotStore
from app.models import RunnerCommand, RunnerEnvironment, RunnerHost, RunnerRun, RunnerSnapshot
from app.runtime_v2.journal_results import operation_result
from tests.runtime_v2.test_operation_journal import exec_result
from tests.runtime_v2.test_runtime_profile import environment, host
from tests.test_mcp_api import new_instance
from tests.test_runner_api import SETTINGS, add_host, runner_headers
from tests.workspace_support import Env, app_of, open_env


def identity(target: RunnerEnvironment, node: RunnerHost) -> dict[str, Any]:
    return {
        "workspace_id": str(target.workspace_id),
        "instance_id": str(target.owner_instance_id),
        "env_id": str(target.id),
        "allocation_id": target.runtime_metadata["allocation_id"],
        "generation": target.runtime_metadata["generation"],
        "node_name": node.node_name,
    }


def receipts(target: RunnerEnvironment) -> dict[str, Any]:
    return {
        key: target.runtime_metadata[key]
        for key in ("binding_generation", "deployment_uid", "pvc_uid", "pod_uid")
    }


def command_for(target: RunnerEnvironment, node: RunnerHost, kind: str = "exec") -> RunnerCommand:
    return RunnerCommand(
        id=uuid4(),
        workspace_id=target.workspace_id,
        host_id=node.id,
        environment_id=target.id,
        kind=kind,
        status="sent",
        sent_at=specs.now(),
        deadline_at=specs.later(600),
        payload={
            "host_id": str(node.id),
            "backend": "kubernetes",
            "runtime_identity": identity(target, node),
            "runtime_receipts": receipts(target),
        },
    )


def proof(command: RunnerCommand, target: RunnerEnvironment) -> dict[str, Any]:
    return {
        **command.payload["runtime_identity"],
        "host_id": str(command.host_id),
        "command_id": str(command.id),
        "deployment_uid": target.runtime_metadata["deployment_uid"],
        "pvc_uid": target.runtime_metadata["pvc_uid"],
        "pod_uid": target.runtime_metadata["pod_uid"],
        "target_kind": "workload",
    }


@pytest.fixture
async def lab(
    make_client: Any, migrated_database_url: str, tmp_path: Path
) -> AsyncIterator[tuple[Env, str, UUID]]:
    async with open_env(
        make_client, migrated_database_url, **SETTINGS, file_dir=str(tmp_path)
    ) as env:
        token = await add_host(env)
        owner = UUID(await new_instance(env))
        async with app_of(env.client).state.sessionmaker() as db:
            node = await db.scalar(select(RunnerHost))
            assert node is not None
            node.backend, node.node_name = "kubernetes", "node-a"
            target = environment()
            target.workspace_id, target.host_id, target.owner_instance_id = (
                node.workspace_id,
                node.id,
                owner,
            )
            target.purpose, target.state, target.lifetime = "receipt test", "running", "persistent"
            target.runtime_metadata = {
                "generation": 2,
                "allocation_id": str(uuid4()),
                "binding_generation": 2,
                "deployment_uid": "deployment-2",
                "pvc_uid": "pvc-2",
                "pod_uid": "pod-2",
                "node": "node-a",
                "compute_stopped": False,
            }
            db.add(target)
            await db.commit()
            target_id = target.id
        yield env, token, target_id


@pytest.mark.integration
async def test_stale_success_and_failure_refuse_before_committed_writes(
    lab: tuple[Env, str, UUID],
) -> None:
    env, _token, target_id = lab
    app = app_of(env.client)
    for status in ("done", "failed"):
        for mismatch in (
            "generation",
            "allocation_id",
            "instance_id",
            "node_name",
            "host_id",
            "pod_uid",
            "deployment_uid",
            "pvc_uid",
            "missing",
            "bool_generation",
        ):
            async with app.state.sessionmaker() as db:
                target = await db.get(RunnerEnvironment, target_id)
                assert target is not None
                node = await db.get(RunnerHost, target.host_id)
                assert node is not None
                command = command_for(target, node)
                run = RunnerRun(
                    workspace_id=target.workspace_id,
                    environment_id=target.id,
                    instance_id=target.owner_instance_id,
                    command="true",
                    timeout_seconds=60,
                    status="running",
                    output="unchanged",
                )
                db.add(run)
                await db.flush()
                command.run_id = run.id
                db.add(command)
                await db.commit()
                result = exec_result()["result"]
                result["runtime_proof"] = proof(command, target)
                if mismatch == "missing":
                    result.pop("runtime_proof")
                elif mismatch == "bool_generation":
                    result["runtime_proof"]["generation"] = True
                else:
                    result["runtime_proof"][mismatch] = 1 if mismatch == "generation" else "old"
                before = dict(target.runtime_metadata)
                with pytest.raises(RunnerError) as error:
                    await Runner(db, app.state.settings).apply_result(
                        command, status, result, "bad"
                    )
                assert error.value.code == "NETWORK_RUNTIME_UNCONFIRMED"
                await db.commit()  # Rejection must not rely on rollback.
                await db.refresh(command)
                await db.refresh(run)
                await db.refresh(target)
                assert command.status == "sent" and command.finished_at is None
                assert command.result == {} and command.error is None
                assert (
                    run.status == "running"
                    and run.output == "unchanged"
                    and run.finished_at is None
                )
                assert target.state == "running" and target.runtime_metadata == before


@pytest.mark.integration
async def test_persisted_identity_fences_every_kubernetes_callback(
    lab: tuple[Env, str, UUID],
) -> None:
    env, _token, target_id = lab
    app = app_of(env.client)
    for kind in ("create_env", "start_env", "stop_env", "delete_env", "exec", "snapshot"):
        for status in ("done", "failed"):
            async with app.state.sessionmaker() as db:
                target = await db.get(RunnerEnvironment, target_id)
                assert target is not None
                node = await db.get(RunnerHost, target.host_id)
                assert node is not None
                command = command_for(target, node, kind)
                command.payload = {
                    **command.payload,
                    "runtime_identity": {**command.payload["runtime_identity"], "generation": 1},
                }
                db.add(command)
                await db.commit()
                with pytest.raises(RunnerError):
                    await Runner(db, app.state.settings).apply_result(command, status, {}, "old")
                await db.commit()
                await db.refresh(command)
                assert command.status == "sent" and command.result == {}
                assert target.state == "running" and not target.runtime_metadata["compute_stopped"]


@pytest.mark.integration
async def test_current_exec_and_formal_api_conflict(lab: tuple[Env, str, UUID]) -> None:
    env, token, target_id = lab
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        command = command_for(target, node)
        run = RunnerRun(
            workspace_id=target.workspace_id,
            environment_id=target.id,
            instance_id=target.owner_instance_id,
            command="true",
            timeout_seconds=60,
        )
        db.add(run)
        await db.flush()
        command.run_id = run.id
        db.add(command)
        await db.commit()
        result = exec_result(b"accepted")["result"]
        result["runtime_proof"] = proof(command, target)
        command_id, run_id, before = command.id, run.id, dict(target.runtime_metadata)
    url = f"/runner-api/v1/commands/{command_id}/result"
    response = await env.client.post(
        url, headers=runner_headers(token), json={"status": "failed", "result": {}}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "NETWORK_RUNTIME_UNCONFIRMED"
    response = await env.client.post(
        url, headers=runner_headers(token), json={"status": "done", "result": result}
    )
    assert response.status_code == 200, response.text
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        run = await db.get(RunnerRun, run_id)
        assert target is not None and run is not None
        assert run.status == "succeeded" and run.output == "accepted"
        assert target.state == "running" and target.runtime_metadata == before
        target.runtime_backend = "docker"
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        node.backend = "docker"
        command = command_for(target, node, "stop_env")
        command.payload = {}
        db.add(command)
        await db.commit()
        await Runner(db, app.state.settings).apply_result(command, "done", {}, None)
        await db.commit()
        assert command.status == "done" and target.state == "stopped"
        assert target.runtime_metadata["pod_uid"] == before["pod_uid"]


@pytest.mark.integration
async def test_snapshot_parts_require_current_active_operation(
    lab: tuple[Env, str, UUID], tmp_path: Path
) -> None:
    env, token, target_id = lab
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        snapshot = await SnapshotStore(db, app.state.settings).begin(target)
        await db.commit()
        snapshot_id = snapshot.id
        command = command_for(target, node, "snapshot")
        command.payload = {**command.payload, "snapshot_id": str(snapshot_id)}
        valid = dict(command.payload)
        db.add(command)
        await db.commit()
        command_id = command.id
    url = f"/runner-api/v1/snapshots/{snapshot_id}/parts/0"
    for problem in (
        "no_ref",
        "unknown",
        "cancelled",
        "pending",
        "expired",
        "generation",
        "pvc",
        "duplicate",
    ):
        async with app.state.sessionmaker() as db:
            command = await db.get(RunnerCommand, command_id)
            assert command is not None
            command.status, command.deadline_at, command.payload = "sent", specs.later(600), valid
            duplicate_id = None
            if problem == "no_ref":
                command.payload = {**valid, "snapshot_id": str(uuid4())}
            elif problem in {"unknown", "cancelled", "pending"}:
                command.status = problem
            elif problem == "expired":
                command.deadline_at = specs.now() - timedelta(seconds=1)
            elif problem == "generation":
                command.payload = {
                    **valid,
                    "runtime_identity": {**valid["runtime_identity"], "generation": 1},
                }
            elif problem == "pvc":
                command.payload = {
                    **valid,
                    "runtime_receipts": {**valid["runtime_receipts"], "pvc_uid": "old"},
                }
            else:
                target = await db.get(RunnerEnvironment, target_id)
                node = await db.get(RunnerHost, command.host_id)
                assert target is not None and node is not None
                duplicate = command_for(target, node, "snapshot")
                duplicate.payload = valid
                db.add(duplicate)
                duplicate_id = duplicate.id
            await db.commit()
        response = await env.client.put(url, headers=runner_headers(token), content=b"material")
        assert response.status_code in {404, 409}, (problem, response.text)
        assert not await asyncio.to_thread(lambda: list(tmp_path.rglob("000000"))), problem
        async with app.state.sessionmaker() as db:
            stored = await db.get(RunnerSnapshot, snapshot_id)
            assert stored is not None and stored.parts == 0
            if duplicate_id:
                duplicate = await db.get(RunnerCommand, duplicate_id)
                assert duplicate is not None
                await db.delete(duplicate)
                await db.commit()
    async with app.state.sessionmaker() as db:
        command = await db.get(RunnerCommand, command_id)
        assert command is not None
        command.status, command.deadline_at, command.payload = "sent", specs.later(600), valid
        await db.commit()
    response = await env.client.put(url, headers=runner_headers(token), content=b"material")
    assert response.status_code == 200, response.text
    assert await asyncio.to_thread(lambda: list(tmp_path.rglob("000000")))
    async with app.state.sessionmaker() as db:
        snapshot = await db.get(RunnerSnapshot, snapshot_id)
        command = await db.get(RunnerCommand, command_id)
        assert snapshot is not None and command is not None
        snapshot.status, command.status = "ready", "done"
        await db.commit()
    response = await env.client.get(url, headers=runner_headers(token))
    assert response.status_code in {404, 409}, response.text
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        node = await db.get(RunnerHost, command.host_id)
        assert target is not None and node is not None
        restore = command_for(target, node, "create_env")
        restore.payload = {**restore.payload, "snapshot": {"id": str(snapshot_id)}}
        db.add(restore)
        await db.commit()
        restore_id = restore.id
    response = await env.client.get(url, headers=runner_headers(token))
    assert response.status_code == 200 and response.content == b"material", response.text
    async with app.state.sessionmaker() as db:
        restore = await db.get(RunnerCommand, restore_id)
        assert restore is not None
        restore.payload = {
            **restore.payload,
            "runtime_identity": {**restore.payload["runtime_identity"], "generation": 1},
        }
        await db.commit()
    response = await env.client.get(url, headers=runner_headers(token))
    assert response.status_code in {404, 409}, response.text


def sample_proof() -> dict[str, Any]:
    return {
        **{
            key: "value"
            for key in (
                "workspace_id",
                "instance_id",
                "env_id",
                "allocation_id",
                "node_name",
                "host_id",
                "command_id",
                "deployment_uid",
                "pvc_uid",
            )
        },
        "generation": 1,
        "pod_uid": "pod",
        "target_kind": "workload",
    }


@pytest.mark.parametrize("status", ["done", "failed"])
def test_common_runtime_proof_survives_operation_schema(status: str) -> None:
    value = exec_result()
    value["status"] = status
    value["result"]["runtime_proof"] = sample_proof()
    assert operation_result("exec", value)["result"]["runtime_proof"] == sample_proof()


@pytest.mark.parametrize(
    "update",
    [
        {"generation": True},
        {"generation": 0},
        {"pvc_uid": ""},
        {"extra": "bad"},
        {"target_kind": "other"},
        {"stopped": 1},
    ],
)
def test_common_runtime_proof_schema_is_strict(update: dict[str, Any]) -> None:
    value = exec_result()
    value["result"]["runtime_proof"] = {**sample_proof(), **update}
    with pytest.raises(ValueError, match="invalid operation result"):
        operation_result("exec", value)


def test_enqueue_records_actual_allocation_and_binding() -> None:
    node, target = host("kubernetes"), environment()
    target.runtime_metadata = {
        "generation": 2,
        "allocation_id": "allocated",
        "binding_generation": 2,
        "deployment_uid": "deployment",
        "pvc_uid": "pvc",
        "pod_uid": "pod",
    }
    command = Runner(AsyncSession(), Settings(app_env="test")).enqueue(
        node, "stop_env", {}, env=target
    )
    assert command.payload["runtime_identity"]["allocation_id"] == "allocated"
    assert command.payload["runtime_receipts"] == receipts(target)


def receipt_objects() -> tuple[RunnerEnvironment, RunnerHost, RunnerCommand]:
    node, target = host("kubernetes"), environment()
    target.workspace_id, target.host_id, target.owner_instance_id = (
        node.workspace_id,
        node.id,
        uuid4(),
    )
    target.runtime_metadata = {
        "generation": 2,
        "allocation_id": "allocation-2",
        "binding_generation": 2,
        "deployment_uid": "deployment-2",
        "pvc_uid": "pvc-2",
        "pod_uid": "pod-2",
    }
    return target, node, command_for(target, node)


@pytest.mark.parametrize(
    "field",
    [
        "host_id",
        "owner_instance_id",
        "workspace_id",
        "generation",
        "allocation_id",
        "node",
        "pod_uid",
        "deployment_uid",
        "pvc_uid",
    ],
)
def test_pure_receipt_checks_current_binding_changes(field: str) -> None:
    from app.domain.runner_runtime_receipts import validated_receipt

    target, node, command = receipt_objects()
    result = {"runtime_proof": proof(command, target)}
    if field in {"host_id", "owner_instance_id", "workspace_id"}:
        setattr(target, field, uuid4())
    else:
        target.runtime_metadata[field] = 3 if field == "generation" else "replacement"
    assert not validated_receipt(command, target, node, result)


def test_helper_receipt_uses_operation_pod_uid_and_original_pvc() -> None:
    from app.domain.runner_runtime_receipts import validated_receipt

    target, node, command = receipt_objects()
    command.kind = "put_files"
    helper_proof = {
        **proof(command, target),
        "target_kind": "volume-helper",
        "operation_pod_uid": "unique-helper",
        "pod_uid": "unique-helper",
    }
    assert validated_receipt(command, target, node, {"runtime_proof": helper_proof})
    helper_proof["pvc_uid"] = "replaced-pvc"
    assert not validated_receipt(command, target, node, {"runtime_proof": helper_proof})


def test_exec_timeout_requires_stopped_proof() -> None:
    from app.domain.runner_runtime_receipts import validated_receipt

    target, node, command = receipt_objects()
    current = proof(command, target)
    result = {"runtime_proof": current, "timed_out": True}
    assert not validated_receipt(command, target, node, result)
    current["stopped"] = True
    assert validated_receipt(command, target, node, result)


def test_common_proof_remains_encrypted_in_journal(tmp_path: Path) -> None:
    from app.runtime_v2.controller import CommandJournal

    path = tmp_path / "proof.db"
    journal = CommandJournal(str(path))
    target, _node, command = receipt_objects()
    from tests.runtime_v2.test_operation_journal import context

    journal.begin(str(command.id), context())
    value = exec_result()
    value["result"]["runtime_proof"] = proof(command, target)
    journal.finish(str(command.id), value)
    assert CommandJournal(str(path)).begin(str(command.id)) == value
    assert b"runtime_proof" not in path.read_bytes()
    assert b"deployment-2" not in path.read_bytes()


@pytest.mark.integration
async def test_current_failures_keep_encrypted_proof_through_api(
    lab: tuple[Env, str, UUID],
    tmp_path: Path,
) -> None:
    from app.runtime_v2.controller import CommandJournal

    env, token, target_id = lab
    app = app_of(env.client)
    journal = CommandJournal(str(tmp_path / "failures.db"))
    for kind in ("exec", "get_files"):
        async with app.state.sessionmaker() as db:
            target = await db.get(RunnerEnvironment, target_id)
            assert target is not None
            node = await db.get(RunnerHost, target.host_id)
            assert node is not None
            command = command_for(target, node, kind)
            run_id = None
            if kind == "exec":
                run = RunnerRun(
                    workspace_id=target.workspace_id,
                    environment_id=target.id,
                    instance_id=target.owner_instance_id,
                    command="false",
                    timeout_seconds=60,
                    status="running",
                )
                db.add(run)
                await db.flush()
                command.run_id = run_id = run.id
            db.add(command)
            await db.commit()
            command_id = command.id
            current_proof = proof(command, target)
            if kind == "get_files":
                current_proof.pop("pod_uid")
                current_proof.update(
                    target_kind="volume-helper", operation_pod_uid="helper-failure"
                )
            context_value = {
                "kind": kind,
                "host_id": str(node.id),
                "identity": command.payload["runtime_identity"],
            }
            before = dict(target.runtime_metadata)
        journal.begin(str(command_id), context_value)
        journal.finish(
            str(command_id),
            {
                "status": "failed",
                "error": "synthetic-secret",
                "result": {"runtime_proof": current_proof, "private_failure": "synthetic-secret"},
            },
        )
        recovered = CommandJournal(str(tmp_path / "failures.db")).begin(str(command_id))
        assert recovered == {
            "status": "failed",
            "error": "Kubernetes operation failed",
            "result": {"runtime_proof": current_proof},
        }
        response = await env.client.post(
            f"/runner-api/v1/commands/{command_id}/result",
            headers=runner_headers(token),
            json=recovered,
        )
        assert response.status_code == 200, response.text
        async with app.state.sessionmaker() as db:
            stored = await db.get(RunnerCommand, command_id)
            target = await db.get(RunnerEnvironment, target_id)
            assert stored is not None and target is not None
            assert stored.status == "failed" and stored.error == "Kubernetes operation failed"
            assert stored.result == {"runtime_proof": current_proof}
            assert target.runtime_metadata == before and target.state == "running"
            if run_id:
                run = await db.get(RunnerRun, run_id)
                assert run is not None and run.status == "failed" and run.finished_at is not None
                assert "synthetic-secret" not in run.output


@pytest.mark.integration
async def test_snapshot_clone_new_host_and_revoked_host_are_denied(
    lab: tuple[Env, str, UUID],
) -> None:
    from app.domain.runner_hosts import hash_token, new_token

    env, token, target_id = lab
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        snapshot = await SnapshotStore(db, app.state.settings).begin(target)
        await db.flush()
        await SnapshotStore(db, app.state.settings).put_part(snapshot, 0, b"private snapshot")
        snapshot.status = "ready"
        clone = environment()
        clone.workspace_id, clone.host_id, clone.owner_instance_id = (
            target.workspace_id,
            node.id,
            target.owner_instance_id,
        )
        clone.purpose, clone.runtime_metadata = "clone", dict(target.runtime_metadata)
        clone.runtime_metadata["allocation_id"] = str(clone.id)
        db.add(clone)
        await db.flush()
        restore = command_for(clone, node, "create_env")
        restore.payload = {**restore.payload, "snapshot": {"id": str(snapshot.id)}}
        db.add(restore)
        other_token = new_token()
        other = host("kubernetes")
        other.workspace_id, other.name, other.node_name = target.workspace_id, "other", "node-b"
        other.token_hash, other.token_prefix, other.ingress = (
            hash_token(other_token),
            other_token[:12],
            "intranet",
        )
        db.add(other)
        await db.commit()
        snapshot_id, host_id = snapshot.id, node.id
    url = f"/runner-api/v1/snapshots/{snapshot_id}/parts/0"
    # Same-host cloning has no explicit source scope, so its create command is insufficient.
    response = await env.client.get(url, headers=runner_headers(token))
    assert response.status_code == 404, response.text
    response = await env.client.get(url, headers=runner_headers(other_token))
    assert response.status_code == 404, response.text
    async with app.state.sessionmaker() as db:
        node = await db.get(RunnerHost, host_id)
        assert node is not None
        node.status = "disabled"
        await db.commit()
    response = await env.client.get(url, headers=runner_headers(token))
    assert response.status_code == 401, response.text
    response = await env.client.put(url, headers=runner_headers(token), content=b"unauthorized")
    assert response.status_code == 401, response.text


@pytest.mark.integration
async def test_failed_kubernetes_callback_sanitizes_at_domain_boundary(
    lab: tuple[Env, str, UUID],
) -> None:
    env, token, target_id = lab
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        command = command_for(target, node)
        db.add(command)
        await db.commit()
        command_id = command.id
        current = proof(command, target)
    response = await env.client.post(
        f"/runner-api/v1/commands/{command_id}/result",
        headers=runner_headers(token),
        json={
            "status": "failed",
            "error": "synthetic-private-error",
            "result": {"runtime_proof": current, "private_failure": "synthetic-private-result"},
        },
    )
    assert response.status_code == 200, response.text
    async with app.state.sessionmaker() as db:
        stored = await db.get(RunnerCommand, command_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.error == "Kubernetes operation failed"
        assert stored.result == {"runtime_proof": current}


def lifecycle_result(target: RunnerEnvironment, pod_uid: str) -> dict[str, Any]:
    return {
        "ready": True,
        "generation": target.runtime_metadata["generation"],
        "allocation_id": target.runtime_metadata["allocation_id"],
        "node": "node-a",
        "pod_uid": pod_uid,
        "pod_ip": "10.42.0.8",
        "deployment_uid": target.runtime_metadata["deployment_uid"],
        "pvc_uid": target.runtime_metadata["pvc_uid"],
    }


@pytest.mark.integration
async def test_lifecycle_cannot_replace_current_pod_with_old_pod(
    lab: tuple[Env, str, UUID],
) -> None:
    env, _token, target_id = lab
    app = app_of(env.client)
    for kind in ("create_env", "start_env"):
        async with app.state.sessionmaker() as db:
            target = await db.get(RunnerEnvironment, target_id)
            assert target is not None
            node = await db.get(RunnerHost, target.host_id)
            assert node is not None
            target.runtime_metadata = {**target.runtime_metadata, "pod_ip": "10.42.0.9"}
            runner = Runner(db, app.state.settings)
            command = runner.enqueue(node, kind, {}, env=target)
            await db.commit()
            before = dict(target.runtime_metadata)
            with pytest.raises(RunnerError) as error:
                await runner.apply_result(
                    command, "done", lifecycle_result(target, "old-pod-a"), None
                )
            assert error.value.code == "NETWORK_RUNTIME_UNCONFIRMED"
            await db.commit()  # Refusal must not depend on rollback.
            await db.refresh(target)
            await db.refresh(command)
            assert target.runtime_metadata == before and target.state == "running"
            assert command.status == "pending" and command.result == {}
            assert command.error is None and command.finished_at is None


@pytest.mark.integration
async def test_confirmed_stop_allows_new_pod_on_legitimate_restart(
    lab: tuple[Env, str, UUID],
) -> None:
    env, token, target_id = lab
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        target.runtime_metadata = {**target.runtime_metadata, "pod_ip": "10.42.0.9"}
        stopped = Runner(db, app.state.settings).enqueue(node, "stop_env", {}, env=target)
        await db.commit()
        stopped_id = stopped.id
    response = await env.client.post(
        f"/runner-api/v1/commands/{stopped_id}/result",
        headers=runner_headers(token),
        json={"status": "done", "result": {"stopped": True}},
    )
    assert response.status_code == 200, response.text
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        assert target.state == "stopped" and target.runtime_metadata["compute_stopped"] is True
        assert "pod_uid" not in target.runtime_metadata and "pod_ip" not in target.runtime_metadata
        assert target.runtime_metadata["deployment_uid"] == "deployment-2"
        assert target.runtime_metadata["pvc_uid"] == "pvc-2"
        assert target.runtime_metadata["binding_generation"] == 2
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        target.state = "creating"
        target.runtime_metadata = {**target.runtime_metadata, "compute_stopped": False}
        started = Runner(db, app.state.settings).enqueue(node, "start_env", {}, env=target)
        await db.commit()
        started_id = started.id
        result = lifecycle_result(target, "legitimate-new-pod-c")
    response = await env.client.post(
        f"/runner-api/v1/commands/{started_id}/result",
        headers=runner_headers(token),
        json={"status": "done", "result": result},
    )
    assert response.status_code == 200, response.text
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        started = await db.get(RunnerCommand, started_id)
        assert target is not None and started is not None
        assert started.status == "done" and target.state == "running"
        assert target.runtime_metadata["pod_uid"] == "legitimate-new-pod-c"
        assert target.runtime_metadata["pod_ip"] == "10.42.0.8"
        assert target.runtime_metadata["compute_stopped"] is False


@pytest.mark.integration
async def test_new_generation_creation_replaces_old_generation_binding(
    lab: tuple[Env, str, UUID],
) -> None:
    env, _token, target_id = lab
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        target.runtime_metadata = {**target.runtime_metadata, "generation": 3}
        target.state = "creating"
        runner = Runner(db, app.state.settings)
        command = runner.enqueue(node, "create_env", {}, env=target)
        await db.commit()
        result = {
            **lifecycle_result(target, "new-generation-pod"),
            "deployment_uid": "deployment-3",
            "pvc_uid": "pvc-3",
        }
        await runner.apply_result(command, "done", result, None)
        await db.commit()
        await db.refresh(target)
        assert target.runtime_metadata["binding_generation"] == 3
        assert target.runtime_metadata["pod_uid"] == "new-generation-pod"
        assert target.runtime_metadata["deployment_uid"] == "deployment-3"
        assert target.runtime_metadata["pvc_uid"] == "pvc-3"


@pytest.mark.integration
async def test_ordered_stop_then_snapshot_uses_current_volume_receipts(
    lab: tuple[Env, str, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.runner_hosts import RunnerHosts

    # Exercise the established queueing flow while normal operation admission
    # remains closed. Only this test's snapshot gate is opened.
    original_require = Runner._require_operation

    def allow_test_snapshot(backend: str | None, kind: str) -> None:
        if backend == "kubernetes" and kind == "snapshot":
            return
        original_require(backend, kind)

    monkeypatch.setattr(Runner, "_require_operation", staticmethod(allow_test_snapshot))
    env, token, target_id = lab
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None
        node = await db.get(RunnerHost, target.host_id)
        assert node is not None
        await Runner(db, app.state.settings).start_snapshot(node, target, then="delete")
        await db.commit()
        delivered = await RunnerHosts(db, app.state.settings, app.state.redis).take(
            node, client_ip="127.0.0.1"
        )
        await db.commit()
        assert [command.kind for command in delivered] == ["stop_env", "snapshot"]
        stop, snapshot_command = delivered
        saved = snapshot_command.payload["runtime_receipts"]
        assert saved["pod_uid"] == "pod-2"
        snapshot_id = UUID(snapshot_command.payload["snapshot_id"])
        helper_proof = {
            **snapshot_command.payload["runtime_identity"],
            "host_id": str(node.id),
            "command_id": str(snapshot_command.id),
            "deployment_uid": saved["deployment_uid"],
            "pvc_uid": saved["pvc_uid"],
            "target_kind": "volume-helper",
            "operation_pod_uid": "snapshot-helper-pod",
            "stopped": True,
        }
    response = await env.client.post(
        f"/runner-api/v1/commands/{stop.id}/result",
        headers=runner_headers(token),
        json={"status": "done", "result": {"stopped": True}},
    )
    assert response.status_code == 200, response.text
    async with app.state.sessionmaker() as db:
        target = await db.get(RunnerEnvironment, target_id)
        assert target is not None and target.state == "stopped"
        assert "pod_uid" not in target.runtime_metadata
        assert target.runtime_metadata["pvc_uid"] == saved["pvc_uid"]
    response = await env.client.put(
        f"/runner-api/v1/snapshots/{snapshot_id}/parts/0",
        headers=runner_headers(token),
        content=b"portable snapshot material",
    )
    assert response.status_code == 200, response.text
    manifest = {
        "entries": [{"path": ".", "first": 0, "parts": 1, "bytes": 26}],
        "parts": 1,
        "bytes": 26,
    }
    url = f"/runner-api/v1/commands/{snapshot_command.id}/result"
    # Relaxing the saved workload UID for a helper must not authorize old
    # workload proof or replacement volume receipts.
    invalid_workload = {
        key: value for key, value in helper_proof.items() if key != "operation_pod_uid"
    }
    invalid_workload.update(target_kind="workload", pod_uid="pod-2")
    for invalid in (invalid_workload, {**helper_proof, "pvc_uid": "replacement-pvc"}):
        response = await env.client.post(
            url,
            headers=runner_headers(token),
            json={"status": "done", "result": {**manifest, "runtime_proof": invalid}},
        )
        assert response.status_code == 409, response.text
    async with app.state.sessionmaker() as db:
        command = await db.get(RunnerCommand, snapshot_command.id)
        snapshot = await db.get(RunnerSnapshot, snapshot_id)
        assert command is not None and snapshot is not None
        assert command.status == "sent" and command.finished_at is None
        assert snapshot.status == "uploading" and snapshot.parts == 1
    response = await env.client.post(
        url,
        headers=runner_headers(token),
        json={"status": "done", "result": {**manifest, "runtime_proof": helper_proof}},
    )
    assert response.status_code == 200, response.text
    async with app.state.sessionmaker() as db:
        command = await db.get(RunnerCommand, snapshot_command.id)
        snapshot = await db.get(RunnerSnapshot, snapshot_id)
        target = await db.get(RunnerEnvironment, target_id)
        assert command is not None and snapshot is not None and target is not None
        assert command.status == "done" and snapshot.status == "ready"
        assert target.snapshot_key == str(snapshot_id) and target.state == "stopped"
        assert "pod_uid" not in target.runtime_metadata
        assert target.runtime_metadata["compute_stopped"] is True
