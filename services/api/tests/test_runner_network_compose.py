"""Published Compose settings must actually reach the API and worker."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("configured", [False, True])
def test_compose_delivers_network_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    docker = shutil.which("docker")
    if docker is None:
        if os.environ.get("REQUIRE_INTEGRATION") == "1":
            pytest.fail("Docker Compose CLI is required to verify deployment configuration")
        pytest.skip("Docker Compose CLI unavailable")
    values = {
        "RUNNER_NETWORK_VERIFIED": "false",
        "RUNNER_NETWORK_GATEWAY_TOKEN": "",
        "RUNNER_NETWORK_PRIVATE_HOST": "runner-gateway.runner-system.svc.cluster.local",
        "RUNNER_NETWORK_PUBLIC_HOST": "",
        "RUNNER_NETWORK_POD_CIDRS": "10.42.0.0/16",
        "RUNNER_NETWORK_DENIED_CIDRS": "",
    }
    if configured:
        values.update(
            RUNNER_NETWORK_VERIFIED="true",
            RUNNER_NETWORK_GATEWAY_TOKEN="test-gateway-credential",  # noqa: S106 — test-only
            RUNNER_NETWORK_PRIVATE_HOST="gateway.runner.svc.cluster.local",
            RUNNER_NETWORK_PUBLIC_HOST="runner.example.com",
            RUNNER_NETWORK_POD_CIDRS="10.44.0.0/16",
            RUNNER_NETWORK_DENIED_CIDRS="10.99.0.0/16",
        )
    env_file = tmp_path / "compose.env"
    env_file.write_text(
        "POSTGRES_PASSWORD=compose-test-password\n"
        "SESSION_SECRET=compose-test-session-secret-not-production\n"
        + "".join(f"{key}={value}\n" for key, value in values.items())
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUNNER_NETWORK_")}
    result = subprocess.run(  # noqa: S603 — repository configuration, no daemon or remote use
        [
            docker,
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(ROOT / "compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    for name in ("api", "worker"):
        actual = services[name]["environment"]
        assert {key: actual.get(key) for key in values} == values
        for key, value in actual.items():
            if value is not None:
                monkeypatch.setenv(key, str(value))
        settings = Settings(_env_file=None)
        assert settings.runner_network_verified is configured
        assert (
            settings.runner_network_gateway_token.get_secret_value()
            == values["RUNNER_NETWORK_GATEWAY_TOKEN"]
        )
