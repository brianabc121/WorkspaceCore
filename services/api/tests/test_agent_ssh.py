"""SSH 部署 B1（T-0089；一台服务器多个实例后按服务器，T-0145）：添加服务器、主机公钥的探测与确认。

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

    async def create(self, **body: Any) -> httpx.Response:
        return await self.client.post(
            "/api/v1/agents/servers",
            json={"name": "服务器 A", "username": "deploy", **body},
            headers=self.headers(),
        )

    async def address(self, server_id: str, **body: Any) -> httpx.Response:
        return await self.client.put(
            f"/api/v1/agents/servers/{server_id}/address",
            json={"username": "deploy", **body},
            headers=self.headers(),
        )

    async def detail(self, server_id: str) -> dict[str, Any]:
        response = await self.client.get(
            f"/api/v1/agents/servers/{server_id}", headers=self.headers()
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def confirm(self, server_id: str, version: int, fingerprint: str) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/agents/servers/{server_id}/confirm",
            json={"version": version, "fingerprint": fingerprint},
            headers=self.headers(),
        )

    async def servers(self) -> dict[str, Any]:
        response = await self.client.get("/api/v1/agents/servers", headers=self.headers())
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data


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
    assert (await owner.servers()) == {"items": [], "unassigned": []}
    async with ssh_server() as (port, host_key):
        response = await owner.create(host="LOCALHOST", port=port)
    assert response.status_code == 201, response.text
    server = response.json()["server"]
    ssh_info = server["ssh"]
    fingerprint = host_key.get_fingerprint("sha256")
    assert server["kind"] == "ssh" and server["name"] == "服务器 A"
    assert ssh_info["host_key_fingerprint"] == fingerprint and fingerprint.startswith("SHA256:")
    assert ssh_info["host"] == "localhost" and ssh_info["address"] in {"127.0.0.1", "::1"}
    assert ssh_info["port"] == port and ssh_info["username"] == "deploy"
    assert ssh_info["host_key_algorithm"] == "ssh-ed25519"
    assert ssh_info["host_key_confirmed"] is False and ssh_info["client_key_installed"] is False
    stored = await run_sql(owner_db(owner), "SELECT host_key FROM workspace.agent_ssh_targets")
    assert stored == " ".join(host_key.export_public_key().decode().split()[:2])
    listed = await owner.servers()
    assert [s["id"] for s in listed["items"]] == [server["id"]]

    # 指纹不一致：拒绝（例如页面显示的不是 owner 在服务器上核对的那一个）
    other = asyncssh.generate_private_key("ssh-ed25519").get_fingerprint("sha256")
    mismatch = await owner.confirm(server["id"], server["version"], other)
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "AGENT_SSH_FINGERPRINT_MISMATCH"
    stale = await owner.confirm(server["id"], server["version"] + 5, fingerprint)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"

    confirmed = await owner.confirm(server["id"], server["version"], fingerprint)
    assert confirmed.status_code == 200, confirmed.text
    after = confirmed.json()["server"]
    assert after["ssh"]["host_key_confirmed"] is True and after["ssh"]["host_key_confirmed_at"]
    assert after["version"] == server["version"] + 1

    # 更换地址（端口变了）：要带版本号；重新探测后确认作废
    async with ssh_server() as (port2, key2):
        replaced = await owner.address(
            server["id"], host="127.0.0.1", port=port2, version=after["version"]
        )
    assert replaced.status_code == 200, replaced.text
    again = replaced.json()["server"]
    assert again["ssh"]["host_key_fingerprint"] == key2.get_fingerprint("sha256")
    assert again["ssh"]["host_key_confirmed"] is False

    actions = await run_sql(
        owner_db(owner),
        "SELECT string_agg(action, ',' ORDER BY created_at) FROM workspace.audit_logs"
        " WHERE action LIKE 'agents.server.%'",
    )
    assert actions == (
        "agents.server.created,agents.server.host_key_confirmed,agents.server.address_set"
    )

    removed = await owner.client.delete(
        f"/api/v1/agents/servers/{server['id']}",
        params={"version": again["version"]},
        headers=owner.headers(),
    )
    assert removed.status_code == 204
    assert (await owner.servers()) == {"items": [], "unassigned": []}


async def test_the_same_server_cannot_be_added_twice(owner: Owner) -> None:
    async with ssh_server() as (port, _):
        first = await owner.create(host="127.0.0.1", port=port)
        assert first.status_code == 201
        again = await owner.create(host="127.0.0.1", port=port, name="又一台")
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "AGENT_SERVER_EXISTS"
        other_user = await owner.create(host="127.0.0.1", port=port, username="ops")
        assert other_user.status_code == 201


async def test_unreachable_and_refused_targets(owner: Owner) -> None:
    refused = await owner.create(host="127.0.0.1", port=_free_port())
    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "AGENT_SSH_UNREACHABLE"
    assert refused.json()["error"]["details"]["reason"] == "refused"
    dns = await owner.create(host="no-such-host.invalid")
    assert dns.status_code == 503 and dns.json()["error"]["details"]["reason"] == "dns"
    assert (await owner.servers())["items"] == []


async def test_loopback_is_refused_outside_tests(make_client: ClientFactory) -> None:
    async with make_client(**{**SETTINGS, "agent_ssh_allow_loopback": False}) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        owner = Owner(client, setup.json()["workspaces"][0]["id"])
        for host in ("127.0.0.1", "localhost", "169.254.169.254"):
            response = await owner.create(host=host)
            assert response.status_code == 422, host
            assert response.json()["error"]["code"] == "AGENT_SSH_ADDRESS_NOT_ALLOWED"


async def test_invalid_input_is_rejected(owner: Owner) -> None:
    for body in (
        {"host": "bad host", "port": 22},
        {"host": "a" * 254, "port": 22},
        {"host": "example.com", "port": 0},
        {"host": "example.com", "port": 22, "username": "root; rm -rf /"},
        {"host": "example.com", "port": 22, "password": "x"},
        {"host": "example.com", "port": 22, "name": ""},
    ):
        response = await owner.create(**body)
        assert response.status_code == 422, body


async def test_probes_are_rate_limited(owner: Owner) -> None:
    port = _free_port()
    codes = [(await owner.create(host="127.0.0.1", port=port)).status_code for _ in range(11)]
    assert codes[:10] == [503] * 10 and codes[10] == 429


async def test_only_the_owner_and_only_in_its_workspace(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with make_client(**SETTINGS) as client, make_client(**SETTINGS) as admin_client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        workspace = setup.json()["workspaces"][0]["id"]
        owner = Owner(client, workspace)
        user = await add_user(migrated_database_url, ADMIN["email"], ADMIN["password"])
        await add_member(migrated_database_url, uuid.UUID(workspace), user, "admin")
        assert (await admin_client.post("/api/v1/auth/login", json=ADMIN)).status_code == 200
        admin = Owner(admin_client, workspace)
        listed = await admin_client.get("/api/v1/agents/servers", headers=admin.headers())
        assert listed.status_code == 403
        assert (await admin.create(host="127.0.0.1")).status_code == 403
        # 别的工作空间的服务器：404
        unknown = await owner.client.get(
            f"/api/v1/agents/servers/{uuid.uuid4()}", headers=owner.headers()
        )
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
    """更换地址的探测进行中，另一个请求确认了旧指纹：更换必须失败，旧确认不能落到新公钥上。"""
    async with ssh_server() as (port, old_key):
        first = (await owner.create(host="127.0.0.1", port=port)).json()["server"]
    new_key = asyncssh.generate_private_key("ssh-ed25519")
    confirmed: list[int] = []

    async def probe_while_confirming(address: str, port: int) -> ssh.HostKey:
        response = await owner.confirm(
            first["id"], first["version"], old_key.get_fingerprint("sha256")
        )
        confirmed.append(response.status_code)
        return ssh.HostKey(
            algorithm="ssh-ed25519",
            public_key=" ".join(new_key.export_public_key().decode().split()[:2]),
            fingerprint=new_key.get_fingerprint("sha256"),
        )

    monkeypatch.setattr(ssh, "probe_host_key", probe_while_confirming)
    replaced = await owner.address(
        first["id"], host="127.0.0.1", port=port + 1, version=first["version"]
    )
    assert confirmed == [200]
    assert replaced.status_code == 409, replaced.text
    assert replaced.json()["error"]["code"] == "VERSION_CONFLICT"
    info = (await owner.detail(first["id"]))["server"]["ssh"]
    assert info["host_key_fingerprint"] == old_key.get_fingerprint("sha256")
    assert info["port"] == port and info["host_key_confirmed"] is True


async def test_hostnames_with_overlong_labels_are_rejected(owner: Owner) -> None:
    response = await owner.create(host="a" * 64 + ".com")
    assert response.status_code == 422, response.text
    with pytest.raises(ssh.SshError) as caught:
        await ssh.resolve("a" * 64 + ".com", 22)
    assert caught.value.reason == "dns"
