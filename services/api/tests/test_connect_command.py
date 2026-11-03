"""部署脚本的 connect 命令（T-0076；T-0082 移除了 Vikunja）。

真实 PostgreSQL / Redis + 内存版 GitHub、n8n。内存版服务只补上初始化用的接口
（n8n 的 /rest/*），其余请求交给 tests/fake_*.py；GitHub 在网页上接入（T-0091），见 test_github.py；
接口格式按 app/integrations/provisioning.py 里实测的写法。
覆盖：以首次安装的管理员身份创建集成并测试、同步；已接入时跳过；失败时不留下集成；
密码与 Token 不出现在输出里；角色与工作空间的选择规则；status 的输出。
"""

import argparse
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.commands import connect
from app.integrations.base import ConnectorError
from app.integrations.provisioning import N8N_READ_SCOPES
from tests.fake_github import TOKEN as GITHUB_TOKEN
from tests.fake_github import FakeGitHub
from tests.fake_n8n import API_KEY as N8N_KEY
from tests.fake_n8n import BASE_URL as N8N_URL
from tests.fake_n8n import FakeN8n
from tests.fake_openim import BASE_URL as OPENIM_URL
from tests.fake_openim import SECRET as OPENIM_SECRET
from tests.fake_openim import FakeOpenIM
from tests.helpers import OWNER, ClientFactory, add_member, add_user, run_sql
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration

N8N_PASSWORD = "N8n password 1"  # noqa: S105 - 测试密码（n8n 要求含大写字母与数字）
N8N_OWNER = "ops@example.com"


@dataclass
class Provisioning:
    """初始化接口的内存版，记录收到的请求体。"""

    n8n_owner: tuple[str, str] | None = None
    n8n_keys: list[dict[str, Any]] = field(default_factory=list)
    n8n_credentials: list[dict[str, Any]] = field(default_factory=list)
    n8n_scopes_override: list[str] | None = None
    github_repos: list[str] = field(default_factory=lambda: ["octo/app", "octo/docs"])

    def n8n(self, request: httpx.Request) -> httpx.Response | None:
        path = request.url.path
        if path == "/rest/settings":
            setup = self.n8n_owner is None
            return httpx.Response(
                200, json={"data": {"userManagement": {"showSetupOnFirstLoad": setup}}}
            )
        body = json.loads(request.content) if request.content else {}
        if path == "/rest/owner/setup":
            if self.n8n_owner is not None:
                return httpx.Response(400, json={"message": "Instance owner already setup"})
            if len(body["password"]) < 8 or not any(c.isupper() for c in body["password"]):
                return httpx.Response(400, json={"message": "Password must ..."})
            self.n8n_owner = (body["email"], body["password"])
            return httpx.Response(200, json={"data": {"email": body["email"]}})
        if path == "/rest/login":
            if self.n8n_owner != (body["emailOrLdapLoginId"], body["password"]):
                return httpx.Response(401, json={"message": "Wrong username or password."})
            return httpx.Response(
                200, json={"data": {}}, headers={"set-cookie": "n8n-auth=cookie-1; Path=/"}
            )
        if path.startswith("/rest/") and request.headers.get("cookie") != "n8n-auth=cookie-1":
            return httpx.Response(401, json={"message": "Unauthorized"})
        if path == "/rest/api-keys":
            self.n8n_keys.append(body)
            scopes = self.n8n_scopes_override or body["scopes"]
            return httpx.Response(200, json={"data": {"rawApiKey": N8N_KEY, "scopes": scopes}})
        if path == "/rest/credentials":
            self.n8n_credentials.append(body)
            return httpx.Response(200, json={"data": {"id": "cred1"}})
        return None

    def github(self, request: httpx.Request) -> httpx.Response | None:
        if request.url.path != "/user/repos":
            return None
        if request.headers.get("authorization") != f"Bearer {GITHUB_TOKEN}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(200, json=[{"full_name": name} for name in self.github_repos])


@dataclass
class World:
    env: Env
    provisioning: Provisioning
    github: FakeGitHub
    n8n: FakeN8n


@pytest.fixture
async def world(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[World]:
    provisioning = Provisioning()
    github = FakeGitHub()
    for name in provisioning.github_repos:
        github.add_repo(name)
    n8n = FakeN8n()
    async with open_env(
        make_client,
        migrated_database_url,
        integration_allowed_hosts="n8n,api.github.com",
    ) as env:

        def route(request: httpx.Request) -> httpx.Response:
            host = request.headers.get("host") or request.url.host
            if host.startswith("n8n"):
                return provisioning.n8n(request) or n8n.handler(request)
            return provisioning.github(request) or github.handle(request)

        app_of(env.client).state.connectors.transport = httpx.MockTransport(route)
        yield World(env, provisioning, github, n8n)


@asynccontextmanager
async def cli(world: World) -> AsyncIterator[connect.Env]:
    app = app_of(world.env.client)
    async with app.state.sessionmaker() as db:
        yield connect.Env(
            settings=app.state.settings,
            db=db,
            runtime=app.state.connectors,
            redis=app.state.redis,
            jobs=world.env.queue,
        )


def args(*argv: str) -> argparse.Namespace:
    return connect.parser().parse_args(list(argv))


async def call(
    world: World,
    command: Callable[[connect.Env, argparse.Namespace], Any],
    namespace: argparse.Namespace,
    monkeypatch: pytest.MonkeyPatch,
    stdin: str = "",
) -> int:
    monkeypatch.setattr("sys.stdin", _Stdin(stdin))
    async with cli(world) as env:
        result: int = await command(env, namespace)
        return result


class _Stdin:
    def __init__(self, text: str) -> None:
        self._lines = text.splitlines(keepends=True)

    def readline(self) -> str:
        return self._lines.pop(0) if self._lines else ""


async def audit_actions(world: World) -> list[str]:
    raw = await run_sql(
        world.env.database_url,
        "SELECT coalesce(json_agg(action ORDER BY created_at), '[]') FROM workspace.audit_logs",
    )
    return list(json.loads(raw))


async def reveal(world: World, app: str) -> dict[str, Any] | None:
    """工作台保存的登录账号（T-0080）；没有保存时为 None。"""
    env = world.env
    response = await env.client.post(
        f"/api/v1/app-logins/{app}/reveal", headers=env.headers(write=True)
    )
    if response.status_code == 404:
        return None
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def test_n8n_sets_up_owner_key_and_credential(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    namespace = args(
        "n8n", "--owner-email", N8N_OWNER, "--base-url", N8N_URL,
        "--public-url", "http://localhost:5678",
    )  # fmt: skip
    code = await call(world, connect.cmd_n8n, namespace, monkeypatch, N8N_PASSWORD + "\n")
    assert code == 0
    assert world.provisioning.n8n_owner == (N8N_OWNER, N8N_PASSWORD)
    [key] = world.provisioning.n8n_keys
    assert key["scopes"] == list(N8N_READ_SCOPES)
    [credential] = world.provisioning.n8n_credentials
    assert credential["type"] == "httpHeaderAuth"
    assert credential["data"]["name"] == "X-Workspace-Trigger"
    assert len(credential["data"]["value"]) >= 32
    connection = await world.env.get("/api/v1/automations/connection")
    assert connection["status"] == "active"
    assert connection["public_url"] == "http://localhost:5678"
    saved = await reveal(world, "n8n")
    assert saved == {"app": "n8n", "username": N8N_OWNER, "password": N8N_PASSWORD}
    out = capsys.readouterr()
    assert N8N_PASSWORD not in out.out + out.err
    assert credential["data"]["value"] not in out.out + out.err
    assert N8N_KEY not in out.out + out.err

    code = await call(world, connect.cmd_n8n, namespace, monkeypatch, N8N_PASSWORD + "\n")
    assert code == 0
    assert len(world.provisioning.n8n_keys) == 1
    assert "已有 n8n 连接（已接入）" in capsys.readouterr().out


async def test_n8n_owner_probe(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    namespace = args("n8n-owner", "--base-url", N8N_URL)
    await call(world, connect.cmd_n8n_owner, namespace, monkeypatch)
    assert capsys.readouterr().out.strip() == "owner=none"
    world.provisioning.n8n_owner = (N8N_OWNER, N8N_PASSWORD)
    await call(world, connect.cmd_n8n_owner, namespace, monkeypatch)
    assert capsys.readouterr().out.strip() == "owner=exists"


async def test_n8n_existing_owner_logs_in_and_weak_password_is_explained(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = args("n8n", "--owner-email", N8N_OWNER, "--base-url", N8N_URL)
    with pytest.raises(connect.Failed) as raised:
        await call(world, connect.cmd_n8n, namespace, monkeypatch, "weak\n")
    assert "大写字母" in str(raised.value)

    world.provisioning.n8n_owner = (N8N_OWNER, N8N_PASSWORD)
    code = await call(world, connect.cmd_n8n, namespace, monkeypatch, N8N_PASSWORD + "\n")
    assert code == 0
    assert world.provisioning.n8n_owner == (N8N_OWNER, N8N_PASSWORD)  # 没有重新建 owner


async def test_n8n_key_with_unexpected_scopes_is_not_saved(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.provisioning.n8n_scopes_override = [*N8N_READ_SCOPES, "workflow:delete"]
    namespace = args("n8n", "--owner-email", N8N_OWNER, "--base-url", N8N_URL)
    with pytest.raises(ConnectorError) as raised:
        await call(world, connect.cmd_n8n, namespace, monkeypatch, N8N_PASSWORD + "\n")
    assert raised.value.reason == "scope_mismatch"
    connection = await world.env.get("/api/v1/automations/connection")
    assert connection["configured"] is False


async def test_public_urls_update_the_n8n_editor_link(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    namespace = args("public-urls", "--n8n", "https://n8n.example.com")
    await call(world, connect.cmd_public_urls, namespace, monkeypatch)
    assert "还没有 n8n 连接" in capsys.readouterr().out

    n8n = args("n8n", "--owner-email", N8N_OWNER, "--base-url", N8N_URL)
    assert await call(world, connect.cmd_n8n, n8n, monkeypatch, N8N_PASSWORD + "\n") == 0
    capsys.readouterr()
    assert await call(world, connect.cmd_public_urls, namespace, monkeypatch) == 0
    assert "改为 https://n8n.example.com" in capsys.readouterr().out
    connection = await world.env.get("/api/v1/automations/connection")
    assert connection["public_url"] == "https://n8n.example.com"
    assert connection["status"] == "active"  # 只改打开地址，不需要重新测试连接

    # 再运行一次：地址没变，不写入
    await call(world, connect.cmd_public_urls, namespace, monkeypatch)
    assert "已经是" in capsys.readouterr().out

    with pytest.raises(connect.Usage):
        await call(
            world, connect.cmd_public_urls, args("public-urls", "--n8n", "ftp://x"), monkeypatch
        )
    with pytest.raises(SystemExit):  # T-0082 移除了 Vikunja
        args("public-urls", "--vikunja", "https://vikunja.example.com")


# ---------------------------------------------------------------- 身份与状态


async def test_status_reports_each_component(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    await call(world, connect.cmd_status, args("status"), monkeypatch)
    lines = capsys.readouterr().out.split()
    assert lines == [
        "setup=done",
        f"user={OWNER['email'].lower()}",
        "github=none",
        "n8n=none",
        "n8n_login=none",
        "openim=none",
    ]
    await call(
        world,
        connect.cmd_save_login,
        args("save-login", "--app", "n8n", "--username", N8N_OWNER, "--no-verify"),
        monkeypatch,
        N8N_PASSWORD + "\n",
    )
    await call(world, connect.cmd_status, args("status"), monkeypatch)
    lines = capsys.readouterr().out.split()
    assert "n8n_login=saved" in lines and not any(x.startswith("vikunja") for x in lines)


async def test_save_login_checks_the_password_first(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world.provisioning.n8n_owner = (N8N_OWNER, N8N_PASSWORD)
    n8n = args("save-login", "--app", "n8n", "--username", N8N_OWNER, "--base-url", N8N_URL)
    with pytest.raises(ConnectorError):
        await call(world, connect.cmd_save_login, n8n, monkeypatch, "Wrong password 1\n")
    assert await reveal(world, "n8n") is None  # 密码不对就不保存
    with pytest.raises(connect.Usage):
        await call(world, connect.cmd_save_login, n8n, monkeypatch, "")
    code = await call(world, connect.cmd_save_login, n8n, monkeypatch, N8N_PASSWORD + "\n")
    assert code == 0
    assert (await reveal(world, "n8n") or {})["username"] == N8N_OWNER
    # 再保存一次覆盖旧的（新密码）
    world.provisioning.n8n_owner = (N8N_OWNER, "Changed password 2")
    code = await call(world, connect.cmd_save_login, n8n, monkeypatch, "Changed password 2\n")
    assert code == 0
    assert (await reveal(world, "n8n") or {})["password"] == "Changed password 2"  # noqa: S105
    with pytest.raises(SystemExit):  # T-0082 移除了 Vikunja
        args("save-login", "--app", "vikunja", "--username", "admin")
    out = capsys.readouterr()
    assert N8N_PASSWORD not in out.out + out.err
    actions = await audit_actions(world)
    assert "app_login.created" in actions and "app_login.updated" in actions


async def test_members_below_admin_cannot_connect(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await add_user(world.env.database_url, "member@example.com", "member password 1")
    await add_member(world.env.database_url, uuid.UUID(world.env.workspace_id), user_id, "member")
    namespace = args(
        "--email", "member@example.com", "n8n", "--owner-email", N8N_OWNER,
        "--base-url", N8N_URL,
    )  # fmt: skip
    with pytest.raises(connect.Usage):
        await call(world, connect.cmd_n8n, namespace, monkeypatch, N8N_PASSWORD + "\n")
    assert world.provisioning.n8n_keys == []

    # admin 可以；指定了不属于他的工作空间则不行
    await run_sql(
        world.env.database_url,
        "UPDATE workspace.workspace_members SET role = 'admin' WHERE user_id = $1",
        user_id,
    )
    other = args("--email", "member@example.com", "--workspace", str(uuid.uuid4()), "status")
    with pytest.raises(connect.Usage):
        await call(world, connect.cmd_status, other, monkeypatch)
    await call(
        world, connect.cmd_status, args("--email", "member@example.com", "status"), monkeypatch
    )


async def test_unknown_user_is_a_usage_error(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    namespace = args("--email", "nobody@example.com", "status")
    with pytest.raises(connect.Usage):
        await call(world, connect.cmd_status, namespace, monkeypatch)


async def test_status_before_setup(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async with make_client() as client:
        app = app_of(client)
        async with app.state.sessionmaker() as db:
            env = connect.Env(app.state.settings, db, app.state.connectors, app.state.redis, None)  # type: ignore[arg-type]
            assert await connect.cmd_status(env, args("status")) == 0
    assert capsys.readouterr().out.split() == ["setup=required", "openim=none"]


async def test_status_reports_openim(
    make_client: ClientFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """T-0081：部署脚本据此判断 OpenIM 是否已部署、工作台能否连上（与运维页的检查相同）。"""
    fake = FakeOpenIM()
    settings = {"openim_api_url": OPENIM_URL, "openim_secret": SecretStr(OPENIM_SECRET)}
    async with make_client(**settings) as client:
        app = app_of(client)
        app.state.connectors.transport = httpx.MockTransport(fake.handler)
        async with app.state.sessionmaker() as db:
            env = connect.Env(app.state.settings, db, app.state.connectors, app.state.redis, None)  # type: ignore[arg-type]
            assert await connect.cmd_status(env, args("status")) == 0
            assert capsys.readouterr().out.split() == ["setup=required", "openim=ok"]
            fake.down = True
            assert await connect.cmd_status(env, args("status")) == 0
            assert capsys.readouterr().out.split() == ["setup=required", "openim=unavailable"]
