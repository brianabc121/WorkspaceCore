"""旧系统通过页面初始化的回归：不只放开检查，还实际经过 SSH 安装与重新登录。"""

from pathlib import Path

import pytest

from app.domain import agent_ssh_install
from tests import test_agent_ssh_install as base
from tests.test_agent_server_credentials import _confirmed, _test
from tests.test_agent_server_password_jobs import _initialize
from tests.test_agent_ssh_install import FakeHost, Owner, ssh_host

owner = base.owner
fast_online = base.fast_online


@pytest.mark.integration
@pytest.mark.parametrize(("os_id", "os_version"), [("debian", "11"), ("ubuntu", "20.04")])
@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
async def test_legacy_root_without_sudo_can_initialize(
    owner: Owner, tmp_path: Path, os_id: str, os_version: str, arch: str
) -> None:
    host = FakeHost(
        tmp_path, os_id=os_id, os_version=os_version, arch=arch, uid="0", user="root", sudo="no"
    )
    async with ssh_host(host) as (port, _):
        job, _ = await _initialize(owner, host, port)
    assert job["status"] == "succeeded", job
    assert "install-docker user=" in host.calls()
    assert not any(call.startswith("sudo") for call in host.calls())


@pytest.mark.integration
@pytest.mark.parametrize(("os_id", "os_version"), [("debian", "11"), ("ubuntu", "20.04")])
async def test_legacy_missing_compose_can_initialize(
    owner: Owner, tmp_path: Path, os_id: str, os_version: str
) -> None:
    host = FakeHost(tmp_path, os_id=os_id, os_version=os_version, docker="yes", compose="no")
    async with ssh_host(host) as (port, _):
        job, _ = await _initialize(owner, host, port)
    assert job["status"] == "succeeded", job
    assert host.calls().count("install-docker user=deploy") == 1


@pytest.mark.integration
@pytest.mark.parametrize(("os_id", "os_version"), [("debian", "11"), ("ubuntu", "20.04")])
async def test_connection_check_accepts_legacy_docker_install(
    owner: Owner, tmp_path: Path, os_id: str, os_version: str
) -> None:
    host = FakeHost(tmp_path, os_id=os_id, os_version=os_version, uid="0", user="root", sudo="no")
    async with ssh_host(host) as (port, _):
        server_id = await _confirmed(owner, port)
        response = await _test(owner, server_id)
    assert response.status_code == 200, response.text
    assert response.json()["warning"] is None
    assert host.calls() == []


@pytest.mark.integration
async def test_unknown_os_with_docker_is_reused(owner: Owner, tmp_path: Path) -> None:
    host = FakeHost(
        tmp_path, os_id="centos", os_version="9", docker="yes", compose="yes", docker_access="yes"
    )
    (host.state / "docker_ok").touch()
    async with ssh_host(host) as (port, _):
        job, _ = await _initialize(owner, host, port)
    assert job["status"] == "succeeded", job
    assert not any(call.startswith("install-docker") for call in host.calls())


@pytest.mark.parametrize(
    ("os_id", "os_version", "arch"),
    [("debian", "10", "x86_64"), ("ubuntu", "18.04", "x86_64"), ("debian", "11", "armv7l")],
)
def test_unsupported_hosts_still_require_manual_install(
    os_id: str, os_version: str, arch: str
) -> None:
    info = agent_ssh_install.parse_inspect(
        f"os_id={os_id}\nos_version={os_version}\narch={arch}\nuid=0\ngid=0\nhome=/root\n"
    )
    assert not info.supported
