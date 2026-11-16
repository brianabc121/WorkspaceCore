"""旧系统通过页面初始化的回归：不只放开检查，还实际经过 SSH 安装与重新登录。"""

import os
import subprocess
from pathlib import Path

import pytest

from app import agent_provisioning as prov
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


def _apt_preflight(root: Path, os_id: str = "debian", version: str = "11") -> None:
    """执行真实安装脚本；仅把系统文件映射到临时目录，在第一次 apt 调用前退出。"""
    release = root / "os-release"
    release.write_text(f"ID={os_id}\nVERSION_ID={version}\nVERSION_CODENAME=bullseye\n")
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in {
        "dpkg": "echo amd64",
        "docker": "exit 1",  # 固定模拟缺 Compose，不调用 CI 宿主上真实的 Docker。
        "apt-get": "exit 73",
    }.items():
        command = bin_dir / name
        command.write_text(f"#!/bin/sh\n{body}\n")
        command.chmod(0o755)
    script = prov.script("install-docker.sh").replace("/etc/os-release", str(release))
    script = script.replace("/etc/apt", str(root / "apt"))
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", "-c", script],
        env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 73, result.stderr


def test_debian11_migrates_only_official_security_list_and_keeps_backup(tmp_path: Path) -> None:
    apt = tmp_path / "apt"
    apt.mkdir()
    source = apt / "sources.list"
    original = (
        "deb http://deb.debian.org/debian bullseye main\n"
        "deb http://deb.debian.org/debian-security bullseye-security main\n"
        "deb-src https://security.debian.org/debian-security bullseye-security main\n"
        "deb https://deb.debian.org/debian-security bookworm-security main\n"
        "deb https://mirror.example.org/debian-security bullseye-security main\n"
        "deb https://deb.debian.org/debian-security bookworm-security main"
        " # was bullseye-security\n"
        "deb [arch=amd64 signed-by=/key.gpg] https://deb.debian.org/debian-security/"
        " bullseye-security main\n"
        "deb https://mirror.example.org/debian-security bullseye-security main # http://deb.debian.org/debian-security\n"
        "# deb http://deb.debian.org/debian-security bullseye-security main\n"
    )
    source.write_text(original)
    _apt_preflight(tmp_path)
    expected = (
        "deb http://deb.debian.org/debian bullseye main\n"
        "deb http://archive.debian.org/debian-security bullseye-security main\n"
        "deb-src https://archive.debian.org/debian-security bullseye-security main\n"
        "deb https://deb.debian.org/debian-security bookworm-security main\n"
        "deb https://mirror.example.org/debian-security bullseye-security main\n"
        "deb https://deb.debian.org/debian-security bookworm-security main"
        " # was bullseye-security\n"
        "deb [arch=amd64 signed-by=/key.gpg] https://archive.debian.org/debian-security/"
        " bullseye-security main\n"
        "deb https://mirror.example.org/debian-security bullseye-security main # http://deb.debian.org/debian-security\n"
        "# deb http://deb.debian.org/debian-security bullseye-security main\n"
    )
    assert source.read_text() == expected
    backup = apt / "sources.list.myworkspace-before-archive"
    assert backup.read_text() == original
    _apt_preflight(tmp_path)
    assert source.read_text() == expected
    assert backup.read_text() == original


def test_debian11_migrates_only_bullseye_security_deb822_stanza(tmp_path: Path) -> None:
    apt = tmp_path / "apt" / "sources.list.d"
    apt.mkdir(parents=True)
    source = apt / "debian.sources"
    source.write_text(
        "Types: deb\nURIs: https://deb.debian.org/debian-security\n"
        "Suites: bullseye-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian.gpg\n\n"
        "Types: deb\nURIs: https://deb.debian.org/debian-security\n"
        "Suites: bookworm-security\nComponents: main\n\n"
    )
    _apt_preflight(tmp_path)
    assert source.read_text() == (
        "Types: deb\nURIs: https://archive.debian.org/debian-security\n"
        "Suites: bullseye-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian.gpg\n\n"
        "Types: deb\nURIs: https://deb.debian.org/debian-security\n"
        "Suites: bookworm-security\nComponents: main\n\n"
    )


@pytest.mark.parametrize(("os_id", "version"), [("debian", "12"), ("ubuntu", "22.04")])
def test_other_systems_do_not_migrate_apt_sources(tmp_path: Path, os_id: str, version: str) -> None:
    apt = tmp_path / "apt"
    apt.mkdir()
    source = apt / "sources.list"
    original = "deb http://deb.debian.org/debian-security bullseye-security main\n"
    source.write_text(original)
    _apt_preflight(tmp_path, os_id, version)
    assert source.read_text() == original
    assert not (apt / "sources.list.myworkspace-before-archive").exists()
