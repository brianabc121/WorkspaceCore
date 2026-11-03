"""SSH 自动部署 B1（T-0089）：目标服务器、主机公钥的探测与确认。

用进程内的 asyncssh 服务器（127.0.0.1 随机端口）当目标；测试打开 agent_ssh_allow_loopback。
"""

import ipaddress
import secrets
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncssh
import httpx
import pytest
from pydantic import SecretStr

from app.integrations import ssh
from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf, run_sql

pytestmark = pytest.mark.integration

SETTINGS: dict[str, Any] = {
    "credential_encryption_key": SecretStr(secrets.token_hex(32)),
    "agent_public_url": "https://agent.example.com:9443",
    "agent_ssh_allow_loopback": True,
}
ADMIN = {"email": "admin@example.com", "password": "admin password 123456"}


class _NoLogin(asyncssh.SSHServer):
    """只用来读取主机公钥：探测不应该登录，任何认证都失败。"""

    def begin_auth(self, username: str) -> bool:
        raise AssertionError("探测不应该尝试登录")


@asynccontextmanager
async def ssh_server(
    key: asyncssh.SSHKey | None = None,
) -> AsyncIterator[tuple[int, asyncssh.SSHKey]]:
    host_key = key or asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        _NoLogin, "127.0.0.1", 0, server_host_keys=[host_key], config=[]
    )
    try:
        yield server.sockets[0].getsockname()[1], host_key
    finally:
        server.close()
        await server.wait_closed()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


class Owner:
    def __init__(self, client: httpx.AsyncClient, workspace: str) -> None:
        self.client = client
        self.workspace = workspace

    def headers(self) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace, **csrf(self.client)}

    async def instance(self, network_mode: str = "public") -> dict[str, Any]:
        response = await self.client.post(
            "/api/v1/agents/instances",
            json={"name": "自动部署", "network_mode": network_mode},
            headers=self.headers(),
        )
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    async def put(self, instance_id: str, **body: Any) -> httpx.Response:
        return await self.client.put(
            f"/api/v1/agents/instances/{instance_id}/ssh",
            json={"username": "deploy", **body},
            headers=self.headers(),
        )

    async def state(self, instance_id: str) -> dict[str, Any]:
        response = await self.client.get(
            f"/api/v1/agents/instances/{instance_id}/ssh", headers=self.headers()
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def confirm(self, instance_id: str, version: int, fingerprint: str) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/agents/instances/{instance_id}/ssh/confirm",
            json={"version": version, "fingerprint": fingerprint},
            headers=self.headers(),
        )


@pytest.fixture
async def owner(make_client: ClientFactory) -> AsyncIterator[Owner]:
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Owner(client, setup.json()["workspaces"][0]["id"])


# ---------------------------------------------------------------- 地址检查（不需要服务器）


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "169.254.169.254",
        "fe80::1",
        "0.0.0.0",  # noqa: S104 - 测试“不允许的地址”
        "224.0.0.1",
        "240.0.0.1",
        "::ffff:127.0.0.1",
    ],
)
def test_addresses_that_are_never_targets(address: str) -> None:
    with pytest.raises(ssh.SshError) as caught:
        ssh.check_address(ipaddress.ip_address(address))
    assert caught.value.reason == "not_allowed"


@pytest.mark.parametrize("address", ["10.0.0.5", "192.168.1.20", "100.64.0.9", "203.0.113.7"])
def test_private_and_public_addresses_are_allowed(address: str) -> None:
    ssh.check_address(ipaddress.ip_address(address))


async def test_hostnames_resolve_once_and_are_checked() -> None:
    assert await ssh.resolve("localhost", 22, allow_loopback=True) in {"127.0.0.1", "::1"}
    with pytest.raises(ssh.SshError) as caught:
        await ssh.resolve("localhost", 22)
    assert caught.value.reason == "not_allowed"
    with pytest.raises(ssh.SshError) as dns:
        await ssh.resolve("no-such-host.invalid", 22)
    assert dns.value.reason == "dns"


# ---------------------------------------------------------------- 探测与确认


async def test_probe_shows_the_fingerprint_and_confirm_pins_it(owner: Owner) -> None:
    instance = await owner.instance()
    assert (await owner.state(instance["id"])) == {"target": None, "job": None}
    async with ssh_server() as (port, host_key):
        response = await owner.put(instance["id"], host="LOCALHOST", port=port)
    assert response.status_code == 200, response.text
    target = response.json()["target"]
    fingerprint = host_key.get_fingerprint("sha256")
    assert target["host_key_fingerprint"] == fingerprint and fingerprint.startswith("SHA256:")
    assert target["host"] == "localhost" and target["address"] in {"127.0.0.1", "::1"}
    assert target["port"] == port and target["username"] == "deploy"
    assert target["host_key_algorithm"] == "ssh-ed25519"
    assert target["host_key_confirmed"] is False and target["client_key_installed"] is False
    stored = await run_sql(owner_db(owner), "SELECT host_key FROM workspace.agent_ssh_targets")
    assert stored == " ".join(host_key.export_public_key().decode().split()[:2])

    # 指纹不一致：拒绝（例如页面显示的不是 owner 在服务器上核对的那一个）
    other = asyncssh.generate_private_key("ssh-ed25519").get_fingerprint("sha256")
    mismatch = await owner.confirm(instance["id"], target["version"], other)
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "AGENT_SSH_FINGERPRINT_MISMATCH"
    stale = await owner.confirm(instance["id"], target["version"] + 5, fingerprint)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"

    confirmed = await owner.confirm(instance["id"], target["version"], fingerprint)
    assert confirmed.status_code == 200, confirmed.text
    after = confirmed.json()["target"]
    assert after["host_key_confirmed"] is True and after["host_key_confirmed_at"]
    assert after["version"] == target["version"] + 1

    # 更换服务器（端口变了）：要带版本号；重新探测后确认作废
    async with ssh_server() as (port2, key2):
        missing = await owner.put(instance["id"], host="127.0.0.1", port=port2)
        assert missing.status_code == 409
        replaced = await owner.put(
            instance["id"], host="127.0.0.1", port=port2, version=after["version"]
        )
    assert replaced.status_code == 200, replaced.text
    again = replaced.json()["target"]
    assert again["host_key_fingerprint"] == key2.get_fingerprint("sha256")
    assert again["host_key_confirmed"] is False

    actions = await run_sql(
        owner_db(owner),
        "SELECT string_agg(action, ',' ORDER BY created_at) FROM workspace.audit_logs"
        " WHERE action LIKE 'agents.ssh.%'",
    )
    assert actions == ("agents.ssh.target_set,agents.ssh.host_key_confirmed,agents.ssh.target_set")

    removed = await owner.client.delete(
        f"/api/v1/agents/instances/{instance['id']}/ssh",
        params={"version": again["version"]},
        headers=owner.headers(),
    )
    assert removed.status_code == 204
    assert (await owner.state(instance["id"])) == {"target": None, "job": None}


async def test_unreachable_and_refused_targets(owner: Owner) -> None:
    instance = await owner.instance()
    refused = await owner.put(instance["id"], host="127.0.0.1", port=_free_port())
    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "AGENT_SSH_UNREACHABLE"
    assert refused.json()["error"]["details"]["reason"] == "refused"
    dns = await owner.put(instance["id"], host="no-such-host.invalid")
    assert dns.status_code == 503 and dns.json()["error"]["details"]["reason"] == "dns"
    assert (await owner.state(instance["id"])) == {"target": None, "job": None}


async def test_loopback_is_refused_outside_tests(make_client: ClientFactory) -> None:
    async with make_client(**{**SETTINGS, "agent_ssh_allow_loopback": False}) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        owner = Owner(client, setup.json()["workspaces"][0]["id"])
        instance = await owner.instance()
        for host in ("127.0.0.1", "localhost", "169.254.169.254"):
            response = await owner.put(instance["id"], host=host)
            assert response.status_code == 422, host
            assert response.json()["error"]["code"] == "AGENT_SSH_ADDRESS_NOT_ALLOWED"


async def test_only_public_mode_enabled_instances(owner: Owner) -> None:
    intranet = await owner.instance("intranet")
    response = await owner.put(intranet["id"], host="127.0.0.1")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_SSH_PUBLIC_ONLY"
    public = await owner.instance()
    await owner.client.post(
        f"/api/v1/agents/instances/{public['id']}/disable",
        json={"version": public["version"]},
        headers=owner.headers(),
    )
    disabled = await owner.put(public["id"], host="127.0.0.1")
    assert disabled.status_code == 409 and disabled.json()["error"]["code"] == "AGENT_DISABLED"


async def test_invalid_input_is_rejected(owner: Owner) -> None:
    instance = await owner.instance()
    for body in (
        {"host": "bad host", "port": 22},
        {"host": "a" * 254, "port": 22},
        {"host": "example.com", "port": 0},
        {"host": "example.com", "port": 22, "username": "root; rm -rf /"},
        {"host": "example.com", "port": 22, "password": "x"},
    ):
        response = await owner.put(instance["id"], **body)
        assert response.status_code == 422, body


async def test_probes_are_rate_limited(owner: Owner) -> None:
    instance = await owner.instance()
    port = _free_port()
    codes = [
        (await owner.put(instance["id"], host="127.0.0.1", port=port)).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [503] * 10 and codes[10] == 429


async def test_only_the_owner_and_only_in_its_workspace(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with make_client(**SETTINGS) as client, make_client(**SETTINGS) as admin_client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        workspace = setup.json()["workspaces"][0]["id"]
        owner = Owner(client, workspace)
        instance = await owner.instance()
        user = await add_user(migrated_database_url, ADMIN["email"], ADMIN["password"])
        await add_member(migrated_database_url, uuid.UUID(workspace), user, "admin")
        assert (await admin_client.post("/api/v1/auth/login", json=ADMIN)).status_code == 200
        admin = Owner(admin_client, workspace)
        path = f"/api/v1/agents/instances/{instance['id']}/ssh"
        assert (await admin_client.get(path, headers=admin.headers())).status_code == 403
        assert (await admin.put(instance["id"], host="127.0.0.1")).status_code == 403
        # 别的工作空间的实例：404
        unknown = await owner.put(str(uuid.uuid4()), host="127.0.0.1")
        assert unknown.status_code == 404


def owner_db(owner: Owner) -> str:
    """测试应用用的数据库地址（与 migrated_database_url 相同）。"""
    transport = owner.client._transport
    assert isinstance(transport, httpx.ASGITransport)
    url: str = transport.app.state.settings.database_url.get_secret_value()  # type: ignore[attr-defined]
    return url


async def test_confirming_during_a_replacement_never_pins_the_new_key(
    owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """更换服务器的探测进行中，另一个请求确认了旧指纹：更换必须失败，旧确认不能落到新公钥上。"""
    instance = await owner.instance()
    async with ssh_server() as (port, old_key):
        first = (await owner.put(instance["id"], host="127.0.0.1", port=port)).json()["target"]
    new_key = asyncssh.generate_private_key("ssh-ed25519")
    confirmed: list[int] = []

    async def probe_while_confirming(address: str, port: int) -> ssh.HostKey:
        response = await owner.confirm(
            instance["id"], first["version"], old_key.get_fingerprint("sha256")
        )
        confirmed.append(response.status_code)
        return ssh.HostKey(
            algorithm="ssh-ed25519",
            public_key=" ".join(new_key.export_public_key().decode().split()[:2]),
            fingerprint=new_key.get_fingerprint("sha256"),
        )

    monkeypatch.setattr(ssh, "probe_host_key", probe_while_confirming)
    replaced = await owner.put(
        instance["id"], host="127.0.0.1", port=port + 1, version=first["version"]
    )
    assert confirmed == [200]
    assert replaced.status_code == 409, replaced.text
    assert replaced.json()["error"]["code"] == "VERSION_CONFLICT"
    target = (await owner.state(instance["id"]))["target"]
    assert target["host_key_fingerprint"] == old_key.get_fingerprint("sha256")
    assert target["port"] == port and target["host_key_confirmed"] is True


async def test_hostnames_with_overlong_labels_are_rejected(owner: Owner) -> None:
    instance = await owner.instance()
    response = await owner.put(instance["id"], host="a" * 64 + ".com")
    assert response.status_code == 422, response.text
    with pytest.raises(ssh.SshError) as caught:
        await ssh.resolve("a" * 64 + ".com", 22)
    assert caught.value.reason == "dns"
