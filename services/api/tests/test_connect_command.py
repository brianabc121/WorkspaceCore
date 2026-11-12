"""部署脚本的状态、身份校验与知识库存储自检，使用真实 PostgreSQL / Redis。"""

import argparse
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.commands import connect
from tests.fake_github import TOKEN as GITHUB_TOKEN
from tests.fake_github import FakeGitHub
from tests.fake_openim import BASE_URL as OPENIM_URL
from tests.fake_openim import SECRET as OPENIM_SECRET
from tests.fake_openim import FakeOpenIM
from tests.helpers import OWNER, ClientFactory, add_member, add_user, run_sql
from tests.workspace_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


@dataclass
class Provisioning:
    """初始化接口的内存版，记录收到的请求体。"""

    github_repos: list[str] = field(default_factory=lambda: ["octo/app", "octo/docs"])

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


@pytest.fixture
async def world(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[World]:
    provisioning = Provisioning()
    github = FakeGitHub()
    for name in provisioning.github_repos:
        github.add_repo(name)
    async with open_env(
        make_client,
        migrated_database_url,
        integration_allowed_hosts="api.github.com",
    ) as env:

        def route(request: httpx.Request) -> httpx.Response:
            return provisioning.github(request) or github.handle(request)

        app_of(env.client).state.connectors.transport = httpx.MockTransport(route)
        yield World(env, provisioning, github)


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
        "openim=none",
    ]


async def test_members_below_admin_cannot_connect(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await add_user(world.env.database_url, "member@example.com", "member password 1")
    await add_member(world.env.database_url, uuid.UUID(world.env.workspace_id), user_id, "member")
    namespace = args("--email", "member@example.com", "status")
    with pytest.raises(connect.Usage):
        await call(world, connect.cmd_status, namespace, monkeypatch)

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
