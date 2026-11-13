"""Execute the exact acceptance diagnostic against real PostgreSQL and secret settings."""

import ast
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.resources import create_engine
from app.domain.agent_server_resources import parse_resources
from app.models import AgentDeployment, AgentServer, AgentSshJob
from tests.helpers import add_user, add_workspace
from tests.test_agent_placement import snapshot

pytestmark = pytest.mark.integration
API_DIR = Path(__file__).resolve().parents[1]


def diagnostic_script() -> str:
    root = Path(__file__).resolve().parents[3]
    tree = ast.parse((root / "tests/e2e/agent_ssh_acceptance.py").read_text())
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "wait_capacity"
    )
    assignment = next(
        node
        for node in method.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "script" for target in node.targets)
    )
    value = ast.literal_eval(assignment.value)
    assert isinstance(value, str)
    return value


async def test_capacity_diagnostic_uses_secret_settings_and_keeps_admission_read_only(
    migrated_database_url: str,
) -> None:
    owner_id = await add_user(
        migrated_database_url, "capacity@example.com", "capacity-test-password"
    )
    workspace_id = await add_workspace(migrated_database_url, owner_id, "capacity test")
    engine = create_engine(Settings(database_url=SecretStr(migrated_database_url)))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    server_id = uuid.uuid4()
    # Controlled trusted-host fixture; this is not a real remote SSH measurement.
    resources = parse_resources(json.dumps(snapshot()), received_at=datetime.now(UTC))
    resources["physical_identity_verified"] = True
    try:
        async with sessions() as db:
            db.add(
                AgentServer(
                    id=server_id,
                    workspace_id=workspace_id,
                    name="diagnostic",
                    kind="ssh",
                    resource_snapshot=resources,
                )
            )
            await db.commit()
        script = await asyncio.to_thread(diagnostic_script)
        for cpus, expected in ((1, True), (32, False)):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script,
                str(server_id),
                str(cpus),
                "2048",
                "",
                env={**os.environ, "DATABASE_URL": migrated_database_url},
                cwd=API_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            assert process.returncode == 0, stderr.decode()
            assert migrated_database_url.encode() not in stdout + stderr
            result: dict[str, Any] = json.loads(stdout)
            assert result["eligible"] is expected
            assert result["candidate"]["server_id"] == str(server_id)
            if not expected:
                assert result["details"] == {str(server_id): "INSUFFICIENT_CAPACITY"}
        async with sessions() as db:
            server = await db.get(AgentServer, server_id)
            assert server is not None
            assert server.resource_snapshot == resources and server.version == 1
            assert await db.scalar(select(func.count()).select_from(AgentDeployment)) == 0
            assert await db.scalar(select(func.count()).select_from(AgentSshJob)) == 0
    finally:
        await engine.dispose()
