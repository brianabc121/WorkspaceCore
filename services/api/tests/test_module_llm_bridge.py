"""大模型会话桥模块（T-0228，docs/v2/LLM_BRIDGE_PLAN.md）。

真实 PostgreSQL / Redis；会话桥用的是 services/llm-bridge 的真实应用（进程内，经 ASGI），
其中 Claude 这个账号槽位换成不访问外部服务的测试驱动。这样覆盖的是整条链路：
实例 → 工作台 /mcp → 模块（权限、转发）→ 会话桥 /mcp → 驱动。
"""

import contextlib
import importlib
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.modules import registry
from app.modules.base import validate
from app.modules.llm_bridge.manifest import MANIFEST
from app.modules.llm_bridge.tools import SPECS, WRITE_TOOL_NAMES
from tests.helpers import tool_error
from tests.test_mcp_api import SETTINGS, call, issue_token, new_instance, records, rpc
from tests.workspace_support import Env, open_env

BRIDGE_SOURCE = Path(__file__).resolve().parents[2] / "llm-bridge"
BRIDGE_URL = "http://bridge.test:8787"
BRIDGE_TOKEN = "t" * 40
SECOND_URL = "http://bridge2.test:8787"
SECOND_TOKEN = "u" * 40
# 不认识 platforms 这个设置的旧版本会话桥
OLD_URL = "http://old.test:8787"
ALL_TOOLS = {spec.name for spec in SPECS}
READ_TOOLS = ALL_TOOLS - WRITE_TOOL_NAMES


@dataclass
class Rig:
    env: Env
    bridge_app: Any
    bridge_id: str
    down: list[bool]
    # 再起一座会话桥（另一个主机名、另一枚令牌、另一个数据目录），返回它的应用
    spawn: Callable[[str, str], Awaitable[Any]]
    # 通往测试里这些会话桥的传输：以别的成员身份登录的客户端要用它才连得到
    transport: httpx.MockTransport

    async def api(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        write = method != "GET"
        return await self.env.client.request(
            method, f"/api/v1/llm_bridge{path}", headers=self.env.headers(write=write), **kwargs
        )

    async def grant(self, instance_id: str, **flags: Any) -> None:
        response = await self.api("PUT", f"/grants/{instance_id}", json={"enabled": True, **flags})
        assert response.status_code == 200, response.text

    async def agent(self, name: str, **flags: Any) -> str:
        """新建一个实例并给它权限，返回它的令牌。"""
        instance = await new_instance(self.env, name=name)
        if flags:
            await self.grant(instance, **flags)
        return await issue_token(self.env, instance)


def ok(result: dict[str, Any]) -> dict[str, Any]:
    assert result["isError"] is False, result
    data: dict[str, Any] = result["structuredContent"]["data"]
    return data


@pytest.fixture
async def rig(make_client: Any, migrated_database_url: str, tmp_path: Path) -> AsyncIterator[Rig]:
    sys.path.insert(0, str(BRIDGE_SOURCE))
    try:
        bridge_app_module = importlib.import_module("bridge.app")
        bridge_config = importlib.import_module("bridge.config")
        fake_driver = importlib.import_module("bridge.drivers.fake")
        bridge_settings = bridge_config.Settings(
            data_dir=tmp_path / "bridge", admin_token=BRIDGE_TOKEN
        )
        bridge_app = bridge_app_module.create_app(bridge_settings)
        down = [False]
        async with (
            contextlib.AsyncExitStack() as stack,
            bridge_app.router.lifespan_context(bridge_app),
            open_env(make_client, migrated_database_url, **SETTINGS) as env,
        ):
            # Claude 槽位换成测试驱动：不需要真实登录
            bridge_app.state.engine.drivers["claude"] = fake_driver.FakeDriver()
            hosts = {"bridge.test": httpx.ASGITransport(app=bridge_app)}

            async def spawn(host: str, token: str) -> Any:
                extra = bridge_app_module.create_app(
                    bridge_config.Settings(data_dir=tmp_path / host, admin_token=token)
                )
                await stack.enter_async_context(extra.router.lifespan_context(extra))
                extra.state.engine.drivers["claude"] = fake_driver.FakeDriver()
                hosts[host] = httpx.ASGITransport(app=extra)
                return extra

            async def route(request: httpx.Request) -> httpx.Response:
                if request.url.host == "old.test":
                    old = {
                        "error": {"code": "VALIDATION_FAILED", "message": "没有这个设置：platforms"}
                    }
                    return httpx.Response(422, json=old)
                asgi = hosts.get(request.url.host)
                if asgi is None or down[0]:
                    raise httpx.ConnectError("connection refused", request=request)
                response = await asgi.handle_async_request(request)
                body = await response.aread()
                return httpx.Response(response.status_code, headers=response.headers, content=body)

            app = env.client._transport.app  # type: ignore[attr-defined]
            transport = httpx.MockTransport(route)
            app.state.connectors.transport = transport
            enable = await env.client.patch(
                "/api/v1/modules/llm_bridge",
                json={"enabled": True},
                headers=env.headers(write=True),
            )
            assert enable.status_code == 200, enable.text
            created = await env.client.post(
                "/api/v1/llm_bridge/bridges",
                json={"name": "主桥", "base_url": BRIDGE_URL + "/", "token": BRIDGE_TOKEN},
                headers=env.headers(write=True),
            )
            assert created.status_code == 201, created.text
            yield Rig(env, bridge_app, created.json()["id"], down, spawn, transport)
    finally:
        sys.path.remove(str(BRIDGE_SOURCE))
        for name in [m for m in sys.modules if m == "bridge" or m.startswith("bridge.")]:
            del sys.modules[name]


def test_manifest_is_valid_and_registered() -> None:
    assert validate(MANIFEST) == []
    assert registry.get("llm_bridge") is MANIFEST
    # 写工具是 direct（例外九），由模块自己的处理函数执行
    assert set(registry.direct_runners()) >= WRITE_TOOL_NAMES
    assert all(name.startswith("llm_bridge_") for name in ALL_TOOLS)


@pytest.mark.integration
async def test_bridge_registration_and_admin(rig: Rig) -> None:
    listed = (await rig.api("GET", "/bridges")).json()["bridges"]
    assert [(b["name"], b["base_url"], b["has_token"]) for b in listed] == [
        ("主桥", BRIDGE_URL, True)
    ]
    assert BRIDGE_TOKEN not in str(listed)
    dup = await rig.api(
        "POST", "/bridges", json={"name": "主桥", "base_url": BRIDGE_URL, "token": BRIDGE_TOKEN}
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "NAME_TAKEN"
    bad = await rig.api(
        "POST", "/bridges", json={"name": "x", "base_url": "ftp://bridge", "token": BRIDGE_TOKEN}
    )
    assert bad.status_code == 422

    status = (await rig.api("GET", f"/bridges/{rig.bridge_id}/status")).json()
    assert status["reachable"] and [a["slot"] for a in status["accounts"]] == [
        "openai",
        "claude",
        "workbuddy",
    ]
    claude = status["accounts"][1]
    assert claude["state"] == "logged_in" and claude["identity"] == "f***@example.com"
    assert claude["registered"] is True and claude["method"] == "api_key"
    # 没登录的平台：没有登记、也不显示登录方式
    assert all(not a["registered"] and a["method"] is None for a in status["accounts"][::2])
    assert all(c["platform"] != "fake" for c in status["channels"])
    assert status["settings"]["concurrency"] == 3

    updated = await rig.api("PUT", f"/bridges/{rig.bridge_id}/settings", json={"concurrency": 3})
    assert updated.status_code == 200 and updated.json()["concurrency"] == 3
    too_big = await rig.api("PUT", f"/bridges/{rig.bridge_id}/settings", json={"concurrency": 99})
    assert too_big.status_code == 422

    # 环境：增、改、列、删
    made = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/environments",
        json={"platform": "claude", "name": "空目录", "sandbox": "workspace_write"},
    )
    assert made.status_code == 201, made.text
    env_id = made.json()["id"]
    insecure = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/environments",
        json={"platform": "claude", "name": "x", "repo_url": "http://example.com/a.git"},
    )
    assert insecure.status_code == 422
    changed = await rig.api(
        "PUT",
        f"/bridges/{rig.bridge_id}/environments/{env_id}",
        json={"platform": "claude", "name": "空目录", "sandbox": "read_only", "enabled": False},
    )
    assert changed.json()["sandbox"] == "read_only" and changed.json()["enabled"] is False
    envs = (await rig.api("GET", f"/bridges/{rig.bridge_id}/environments")).json()["environments"]
    assert [e["id"] for e in envs] == [env_id]
    assert (
        await rig.api("DELETE", f"/bridges/{rig.bridge_id}/environments/{env_id}")
    ).status_code == 204

    # 登录：Claude 用订阅令牌之前要先确认风险
    blocked = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/accounts/openai/login",
        json={"method": "password"},
    )
    assert blocked.status_code == 422
    state = (await rig.api("GET", f"/bridges/{rig.bridge_id}/accounts/claude/login")).json()
    assert state["state"] == "none"
    reset = await rig.api("POST", f"/bridges/{rig.bridge_id}/accounts/claude/reset")
    assert reset.json() == {"archived_sessions": 0}

    # 令牌不对、连不上：状态页如实显示，不报 500
    wrong = await rig.api("PATCH", f"/bridges/{rig.bridge_id}", json={"token": "x" * 40})
    assert wrong.status_code == 200
    status = (await rig.api("GET", f"/bridges/{rig.bridge_id}/status")).json()
    assert status == {**status, "reachable": False} and "令牌" in status["error"]
    failed = await rig.api("GET", f"/bridges/{rig.bridge_id}/environments")
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "BRIDGE_AUTH_FAILED"
    await rig.api("PATCH", f"/bridges/{rig.bridge_id}", json={"token": BRIDGE_TOKEN})
    rig.down[0] = True
    status = (await rig.api("GET", f"/bridges/{rig.bridge_id}/status")).json()
    assert status["reachable"] is False
    rig.down[0] = False

    assert (await rig.api("DELETE", f"/bridges/{rig.bridge_id}")).status_code == 204
    assert (await rig.api("GET", "/bridges")).json()["bridges"] == []
    assert (await rig.api("GET", f"/bridges/{uuid.uuid4()}/status")).status_code == 404


@pytest.mark.integration
async def test_starting_a_login_waits_longer_than_other_admin_calls(rig: Rig) -> None:
    """开始登录等 90 秒（CodeBuddy 网页登录要先在管理器里启动官方程序的登录服务）；
    查登录进度和其他管理操作仍按默认的 65 秒。页面那边等 95 秒，都在 CDN 的 100 秒之内。"""
    from app.modules.llm_bridge.client import LOGIN_READ_TIMEOUT, READ_TIMEOUT

    seen: list[tuple[str, str, float]] = []
    original = rig.transport.handler

    async def spy(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.extensions["timeout"]["read"]))
        response: httpx.Response = await original(request)  # type: ignore[misc]
        return response

    rig.transport.handler = spy
    login = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/accounts/claude/login",
        json={"method": "api_key", "api_key": "alice@example.com"},
    )
    assert login.status_code == 200, login.text
    assert (
        await rig.api("GET", f"/bridges/{rig.bridge_id}/accounts/claude/login")
    ).status_code == 200
    assert (await rig.api("GET", f"/bridges/{rig.bridge_id}/status")).status_code == 200
    waits = {(method, path): read for method, path, read in seen}
    assert waits[("POST", "/admin/accounts/claude/login")] == LOGIN_READ_TIMEOUT == 90.0
    assert waits[("GET", "/admin/accounts/claude/login")] == READ_TIMEOUT
    assert waits[("GET", "/admin/status")] == READ_TIMEOUT
    assert READ_TIMEOUT < LOGIN_READ_TIMEOUT < 95.0  # 比页面等的时间短，页面总能拿到工作台的答复


def test_login_progress_is_passed_on_with_limits() -> None:
    """登录进度（T-0276）：步骤与运行信息照转，限长；不认识的字段丢掉；没有进度是 none。"""
    from app.modules.llm_bridge.service import LOGIN_LOG_LINES, LOGIN_STEPS_MAX, login_out

    data = {
        "state": "pending",
        "verification_url": "https://www.codebuddy.ai/login?state=x",
        "detail": "",
        "steps": [{"key": f"s{i}", "status": "done", "detail": "x" * 900} for i in range(20)]
        + [{"status": "done"}, "bad"],
        "log": [f"line {i}" for i in range(500)],
        "started_at": "2026-11-17T03:00:00+00:00",
        "extra": "ignored",
    }
    out = login_out(data)
    assert len(out.steps) == LOGIN_STEPS_MAX and len(out.steps[0].detail) == 500
    assert len(out.log) == LOGIN_LOG_LINES and out.log[-1] == "line 499"
    assert out.started_at == "2026-11-17T03:00:00+00:00" and out.finished_at is None
    assert "extra" not in out.model_dump()
    assert login_out(None).state == "none" and login_out(None).steps == []
    # 旧版本的会话桥没有逐步进度：照旧只有状态和网址
    old = login_out({"state": "pending", "verification_url": "https://x.test/", "detail": ""})
    assert old.steps == [] and old.log == [] and old.verification_url == "https://x.test/"


async def test_web_login_progress_reaches_the_page_when_it_fails(rig: Rig) -> None:
    """测试环境里没有 codebuddy 程序：网页登录在“启动登录服务”那一步失败，
    失败原因和运行信息经工作台的“查登录进度”原样给到页面（T-0276）。"""
    started = await rig.api(
        "POST", f"/bridges/{rig.bridge_id}/accounts/workbuddy/login", json={"method": "web_intl"}
    )
    assert started.status_code >= 400, started.text
    assert "codebuddy" in started.json()["error"]["message"]
    state = (await rig.api("GET", f"/bridges/{rig.bridge_id}/accounts/workbuddy/login")).json()
    assert state["state"] == "failed" and state["finished_at"] and state["started_at"]
    assert [(s["key"], s["status"]) for s in state["steps"]] == [
        ("start", "failed"),
        ("request_url", "pending"),
        ("wait_user", "pending"),
        ("finish", "pending"),
    ]
    assert "codebuddy" in state["steps"][0]["detail"]
    assert any("启动 codebuddy 失败" in line for line in state["log"])
    assert state["detail"] == state["steps"][0]["detail"]


async def test_claude_web_login_hands_the_code_through(rig: Rig) -> None:
    """Claude 网页授权登录（T-0287）：开始登录拿到网址和 needs_code，交回授权码完成登录；
    订阅登录要先确认风险；授权码不进审计。"""
    env = rig.env
    fake = importlib.import_module("bridge.drivers.fake").FakeDriver()
    fake.login_methods = ("api_key", "web")
    rig.bridge_app.state.engine.drivers["claude"] = fake
    base = f"/bridges/{rig.bridge_id}/accounts/claude/login"
    blocked = await rig.api("POST", base, json={"method": "web"})
    assert blocked.status_code == 403, blocked.text
    acked = await rig.api(
        "PUT", f"/bridges/{rig.bridge_id}/settings", json={"claude_subscription_ack": True}
    )
    assert acked.status_code == 200, acked.text
    started = await rig.api("POST", base, json={"method": "web"})
    assert started.status_code == 200, started.text
    assert started.json()["state"] == "pending" and started.json()["needs_code"] is True
    assert started.json()["verification_url"].startswith("https://")
    assert (await rig.api("GET", base)).json()["needs_code"] is True
    incomplete = await rig.api("POST", f"{base}/code", json={"code": "abc"})
    assert incomplete.status_code == 422, incomplete.text
    done = await rig.api("POST", f"{base}/code", json={"code": "good#s"})
    assert done.status_code == 200, done.text
    assert (done.json()["state"], done.json()["needs_code"]) == ("done", False)
    again = await rig.api("POST", f"{base}/code", json={"code": "good#s"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "CONFLICT"
    logs = (await env.get("/api/v1/audit-logs", limit=50))["items"]
    submitted = [e for e in logs if e["action"] == "llm_bridge.account_login_code"]
    assert submitted and submitted[0]["action_label"] == "提交 Claude 网页授权登录的授权码"
    assert "good#s" not in str(logs)


async def test_managers_control_their_own_platforms_and_accounts(rig: Rig) -> None:
    """人类的例子：1 号管理器控制 claude / workbuddy，2 号多一个 codex；各自一套账号。"""
    env = rig.env
    await rig.spawn("bridge2.test", SECOND_TOKEN)

    # 新增时选控制的平台：同时验证地址和令牌，连不上、令牌不对、版本太旧都不登记
    def body(name: str, url: str, token: str, platforms: list[str]) -> dict[str, Any]:
        return {"name": name, "base_url": url, "token": token, "platforms": platforms}

    for url, token, code, http in (
        (SECOND_URL, BRIDGE_TOKEN, "BRIDGE_AUTH_FAILED", 503),  # 1 号的令牌用不到 2 号上
        ("http://nowhere.test:8787", SECOND_TOKEN, "EXTERNAL_UNAVAILABLE", 503),
        (OLD_URL, SECOND_TOKEN, "BRIDGE_OUTDATED", 409),
    ):
        refused = await rig.api("POST", "/bridges", json=body("2号", url, token, ["claude"]))
        assert (refused.status_code, refused.json()["error"]["code"]) == (http, code), refused.text
    empty = await rig.api("POST", "/bridges", json=body("2号", SECOND_URL, SECOND_TOKEN, []))
    assert empty.status_code == 422
    assert [b["name"] for b in (await rig.api("GET", "/bridges")).json()["bridges"]] == ["主桥"]

    second = await rig.api(
        "POST",
        "/bridges",
        json=body("2号", SECOND_URL, SECOND_TOKEN, ["claude", "workbuddy", "codex"]),
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    assert SECOND_TOKEN not in second.text
    # 已经登记的管理器在设置里改控制的平台
    first = await rig.api(
        "PUT", f"/bridges/{rig.bridge_id}/settings", json={"platforms": ["claude", "workbuddy"]}
    )
    assert first.status_code == 200 and first.json()["platforms"] == ["claude", "workbuddy"]
    none = await rig.api("PUT", f"/bridges/{rig.bridge_id}/settings", json={"platforms": []})
    assert none.status_code == 422

    one = (await rig.api("GET", f"/bridges/{rig.bridge_id}/status")).json()
    two = (await rig.api("GET", f"/bridges/{second_id}/status")).json()
    assert one["platforms"] == ["claude", "workbuddy"]
    assert two["platforms"] == two["settings"]["platforms"] == ["codex", "claude", "workbuddy"]
    assert [(a["slot"], a["enabled"]) for a in one["accounts"]] == [
        ("openai", False),
        ("claude", True),
        ("workbuddy", True),
    ]
    assert all(a["enabled"] for a in two["accounts"])
    codex_on = {
        b: {c["mode"]: c for c in s["channels"] if c["platform"] == "codex"}
        for b, s in (("one", one), ("two", two))
    }
    assert not codex_on["one"]["chat"]["enabled"] and not codex_on["one"]["chat"]["available"]
    assert codex_on["two"]["chat"]["enabled"] and codex_on["two"]["chat"]["available"]
    # 没有单独的 ChatGPT 平台了（T-0280）：纯聊天就是 codex.chat
    assert "chatgpt" not in {c["platform"] for c in two["channels"]}

    # 每个管理器单独维护一套账号：同一个平台在两个管理器上登录不同的账号
    for bridge_id, account in (
        (rig.bridge_id, "alice@example.com"),
        (second_id, "bob@example.com"),
    ):
        assert (
            await rig.api("POST", f"/bridges/{bridge_id}/accounts/claude/reset")
        ).status_code == 200
        login = await rig.api(
            "POST",
            f"/bridges/{bridge_id}/accounts/claude/login",
            json={"method": "api_key", "api_key": account},
        )
        assert login.status_code == 200, login.text
    identities = []
    for bridge_id in (rig.bridge_id, second_id):
        status = (await rig.api("GET", f"/bridges/{bridge_id}/status")).json()
        identities.append(status["accounts"][1]["identity"])
    assert identities == ["a***@example.com", "b***@example.com"]
    # 1 号不控制 Codex：不能在它上面登录 ChatGPT / Codex 的账号
    blocked = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/accounts/openai/login",
        json={"method": "api_key", "api_key": "k" * 20},
    )
    assert blocked.status_code == 409 and "控制的平台" in blocked.json()["error"]["message"]

    # 智能体：能用多个管理器时，不填 bridge 的状态工具列出每一个和它控制的平台
    instance = await new_instance(env, name="alpha")
    token = await issue_token(env, instance)
    await rig.grant(instance, can_write=True)
    overview = ok(await call(env, token, "llm_bridge_status", {}))
    assert {b["bridge"]: (b["reachable"], b["platforms"]) for b in overview["bridges"]} == {
        "主桥": (True, ["claude", "workbuddy"]),
        "2号": (True, ["codex", "claude", "workbuddy"]),
    }
    assert set(overview["bridges"][0]["accounts"]) == {"claude", "workbuddy"}
    need_name = tool_error(
        await call(env, token, "llm_bridge_session_create", {"platform": "claude", "mode": "chat"})
    )
    assert need_name["code"] == "VALIDATION_FAILED"
    assert "主桥" in need_name["message"] and "2号" in need_name["message"]

    async def create(bridge: str | None, platform: str) -> dict[str, Any]:
        arguments = {"platform": platform, "mode": "chat"}
        if bridge:
            arguments["bridge"] = bridge
        return await call(env, token, "llm_bridge_session_create", arguments)

    off = tool_error(await create("主桥", "codex"))
    assert off["code"] == "NOT_SUPPORTED" and "没有启用 codex" in off["message"]
    # 2 号控制 Codex（这里没有登录，所以是“还没有登录”，不是“没有启用”）
    not_logged_in = tool_error(await create("2号", "codex"))
    assert not_logged_in["code"] == "EXTERNAL_UNAVAILABLE"
    assert "没有启用" not in not_logged_in["message"]
    on_one = ok(await create("主桥", "claude"))
    on_two = ok(await create("2号", "claude"))
    # 会话各在各的管理器上
    for bridge, mine in (("主桥", on_one), ("2号", on_two)):
        listed = ok(await call(env, token, "llm_bridge_session_list", {"bridge": bridge}))
        assert [s["session_id"] for s in listed["sessions"]] == [mine["session_id"]]

    # 按实例限定能用的管理器：只给 2 号
    unknown = await rig.api(
        "PUT", f"/grants/{instance}", json={"enabled": True, "bridge_ids": [str(uuid.uuid4())]}
    )
    assert unknown.status_code == 422
    await rig.grant(instance, can_write=True, bridge_ids=[second_id])
    grants = (await rig.api("GET", "/grants")).json()["grants"]
    assert grants[0]["bridge_ids"] == [second_id]
    only = await call(env, token, "llm_bridge_status", {})
    assert only["structuredContent"]["bridge"] == "2号" and "bridges" not in ok(only)
    assert ok(await create(None, "claude"))["platform"] == "claude"  # 只有一个可用：不用填 bridge
    hidden = tool_error(await create("主桥", "claude"))
    assert hidden["code"] == "NOT_FOUND"

    # 删除 2 号：只允许用它的实例一个都不能用了，不会变成“都能用”
    assert (await rig.api("DELETE", f"/bridges/{second_id}")).status_code == 204
    grants = (await rig.api("GET", "/grants")).json()["grants"]
    assert grants[0]["bridge_ids"] == [] and grants[0]["enabled"] is True
    closed = tool_error(await call(env, token, "llm_bridge_status", {}))
    assert closed["code"] == "FORBIDDEN_CAPABILITY" and "MCP 管理器" in closed["message"]
    # 改回“都允许”
    await rig.grant(instance, can_write=True, bridge_ids=None)
    assert ok(await call(env, token, "llm_bridge_status", {}))["platforms"] == [
        "claude",
        "workbuddy",
    ]

    actions = [r for r in await records(env) if r["tool_name"].startswith("llm_bridge_")]
    assert actions, "写工具要有执行记录"


@pytest.mark.integration
async def test_tools_need_a_grant(rig: Rig) -> None:
    env = rig.env
    instance = await new_instance(env, name="alpha")
    token = await issue_token(env, instance)

    async def listed() -> set[str]:
        tools = (await rpc(env, token, "tools/list")).json()["result"]["tools"]
        return {t["name"] for t in tools} & ALL_TOOLS

    # 没授权：看不到，也调用不了
    assert await listed() == set()
    denied = tool_error(await call(env, token, "llm_bridge_status", {}))
    assert denied["code"] == "FORBIDDEN_CAPABILITY"
    grants = (await rig.api("GET", "/grants")).json()["grants"]
    assert [(g["instance_name"], g["enabled"]) for g in grants] == [("alpha", False)]

    # 只打开总开关：只有读工具
    await rig.grant(instance)
    assert await listed() == READ_TOOLS
    overview = ok(await call(env, token, "llm_bridge_status", {}))
    assert overview["accounts"]["claude"]["state"] == "logged_in"
    blocked = tool_error(
        await call(env, token, "llm_bridge_session_create", {"platform": "claude", "mode": "chat"})
    )
    assert blocked["code"] == "FORBIDDEN_CAPABILITY"

    # 打开“新建和发送”，但只允许 Codex
    await rig.grant(instance, can_write=True, platforms=["codex"])
    assert await listed() == ALL_TOOLS
    wrong_platform = tool_error(
        await call(env, token, "llm_bridge_session_create", {"platform": "claude", "mode": "chat"})
    )
    assert (
        wrong_platform["code"] == "FORBIDDEN_CAPABILITY" and "claude" in wrong_platform["message"]
    )

    # 收回权限：其余权限一并收回
    off = await rig.api("PUT", f"/grants/{instance}", json={"enabled": False, "can_write": True})
    assert off.json()["can_write"] is False
    assert await listed() == set()

    # 模块停用后和没有这个工具一样
    await rig.grant(instance, can_write=True)
    disable = await env.client.patch(
        "/api/v1/modules/llm_bridge", json={"enabled": False}, headers=env.headers(write=True)
    )
    assert disable.status_code == 200
    assert await listed() == set()
    gone = tool_error(await call(env, token, "llm_bridge_status", {}))
    assert gone["code"] == "NOT_FOUND"


@pytest.mark.integration
async def test_requirement_example_end_to_end(rig: Rig) -> None:
    """需求里的例子：1 号向同一个会话发两条，2 号另开会话，互不可见；执行记录和通知都有。"""
    env = rig.env
    one = await rig.agent("openclaw-1", can_write=True)
    two = await rig.agent("openclaw-2", can_write=True)

    models = ok(await call(env, one, "llm_bridge_models", {"platform": "claude", "mode": "chat"}))
    assert [m["id"] for m in models["models"]] == ["fake-1", "fake-2"]
    created = ok(
        await call(
            env,
            one,
            "llm_bridge_session_create",
            {"platform": "claude", "mode": "chat", "model": "fake-2"},
        )
    )
    session = created["session_id"]
    assert created["owner"] == "openclaw-1" and created["model"] == "fake-2"

    async def send(token: str, sid: str, text: str, rid: str) -> dict[str, Any]:
        args = {"session_id": sid, "text": text, "request_id": rid, "wait_seconds": 10}
        return await call(env, token, "llm_bridge_send", args)

    first = ok(await send(one, session, "你好", "r1"))
    second = ok(await send(one, session, "大家好", "r2"))
    assert (first["status"], first["result"]) == ("completed", "[fake-2] 你好")
    assert second["result"] == "[fake-2] 大家好"
    # 同一个 request_id 重试：不会再发一遍
    assert ok(await send(one, session, "你好", "r1"))["turn_id"] == first["turn_id"]

    other = ok(
        await call(env, two, "llm_bridge_session_create", {"platform": "claude", "mode": "chat"})
    )
    assert ok(await send(two, other["session_id"], "你好", "r1"))["result"] == "[fake-1] 你好"

    # 互不可见
    mine = ok(await call(env, one, "llm_bridge_session_list", {}))
    assert [s["session_id"] for s in mine["sessions"]] == [session]
    hidden = tool_error(await call(env, two, "llm_bridge_session_get", {"session_id": session}))
    assert hidden["code"] == "NOT_FOUND"
    assert tool_error(await send(two, session, "插话", "r9"))["code"] == "NOT_FOUND"
    # 没有“账号范围列表”权限
    scoped = tool_error(await call(env, two, "llm_bridge_session_list", {"scope": "account"}))
    assert scoped["code"] == "FORBIDDEN_CAPABILITY"
    boss = await rig.agent("boss", can_write=True, can_account=True, can_takeover=True)
    everything = ok(await call(env, boss, "llm_bridge_session_list", {"scope": "account"}))
    assert {s["owner"] for s in everything["sessions"]} == {"openclaw-1", "openclaw-2"}
    detail = ok(await call(env, boss, "llm_bridge_session_get", {"session_id": session}))
    assert [m["text"] for m in detail["messages"]][:2] == ["你好", "[fake-2] 你好"]

    # 归档后不能再发
    archived = ok(await call(env, one, "llm_bridge_session_archive", {"session_id": session}))
    assert archived["status"] == "archived"
    assert tool_error(await send(one, session, "还在吗", "r3"))["code"] == "CONFLICT"

    # 执行记录：读、写都有；消息正文不含会话桥返回的内容
    rows = await records(env, tool="llm_bridge_send")
    assert {r["status"] for r in rows} == {"succeeded", "failed"}
    assert any("发送消息：completed" in r["summary"] for r in rows)
    assert await records(env, tool="llm_bridge_session_list")
    # 管理页面看得到会话和回合
    sessions = (await rig.api("GET", f"/bridges/{rig.bridge_id}/sessions")).json()["sessions"]
    assert {s["owner"] for s in sessions} == {"openclaw-1", "openclaw-2"}
    turns = (await rig.api("GET", f"/bridges/{rig.bridge_id}/sessions/{session}/turns")).json()
    assert [t["text"] for t in turns["turns"]] == ["大家好", "你好"]
    # 新建会话通知了管理员
    notes = await env.get("/api/v1/notifications")
    assert any(n["type"] == "llm_bridge.session_created" for n in notes["items"])


@pytest.mark.integration
async def test_grant_limits_follow_the_session_and_protections_hold(rig: Rig) -> None:
    """例外九写的保护（T-0247）：平台限制跟着会话走、开发环境要管理员勾选、发出去的内容过密钥扫描；
    接手别人的会话和被限额拒绝都通知管理员；新建会话带幂等号不会建出两个。"""
    env = rig.env
    owner = await rig.agent("openclaw-1", can_write=True)

    async def send(token: str, sid: str, text: str, rid: str) -> dict[str, Any]:
        args = {"session_id": sid, "text": text, "request_id": rid, "wait_seconds": 10}
        return await call(env, token, "llm_bridge_send", args)

    args = {"platform": "claude", "mode": "chat", "request_id": "create-1"}
    created = ok(await call(env, owner, "llm_bridge_session_create", args))
    session = created["session_id"]
    # 同一个幂等号再建一次：还是这个会话
    assert ok(await call(env, owner, "llm_bridge_session_create", args))["session_id"] == session
    first = ok(await send(owner, session, "你好", "r1"))

    # 只给了 Codex 的实例：有接手权限，也碰不了 Claude 的会话（这些调用都不带 platform 参数）
    limited = await rig.agent(
        "codex-only", can_write=True, can_account=True, can_takeover=True, platforms=["codex"]
    )
    for name, arguments in (
        ("llm_bridge_session_get", {"session_id": session}),
        ("llm_bridge_turn_get", {"turn_id": first["turn_id"]}),
        ("llm_bridge_turn_cancel", {"turn_id": first["turn_id"]}),
        ("llm_bridge_queue", {"session_id": session}),
        ("llm_bridge_session_archive", {"session_id": session}),
    ):
        assert tool_error(await call(env, limited, name, arguments))["code"] == "NOT_FOUND", name
    assert tool_error(await send(limited, session, "插话", "x1"))["code"] == "NOT_FOUND"
    seen = ok(await call(env, limited, "llm_bridge_session_list", {"scope": "account"}))
    assert seen["sessions"] == []
    overview = ok(await call(env, limited, "llm_bridge_status", {}))
    assert "claude" not in overview["accounts"]

    # 发出去的内容像密钥：不发、不建回合
    secret = "ghp_" + "a1B2c3D4e5" * 4
    blocked = tool_error(await send(owner, session, f"用这个令牌 {secret}", "s1"))
    assert blocked["code"] == "VALIDATION_FAILED" and "密钥" in blocked["message"]
    assert secret not in str(blocked)
    detail = ok(await call(env, owner, "llm_bridge_session_get", {"session_id": session}))
    assert [m["text"] for m in detail["messages"]] == ["你好", "[fake-1] 你好"]
    # 人在聊天页发也一样
    typed = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/chat/sessions/{session}/messages",
        json={"text": f"token: {secret}", "request_id": "h1", "takeover": True},
    )
    assert typed.status_code == 422 and secret not in typed.text

    # 开发环境：管理员没勾选的看不到、用不了；勾选之后才行
    made = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/environments",
        json={"platform": "claude", "name": "空目录", "sandbox": "exec"},
    )
    assert made.status_code == 201, made.text
    env_id = made.json()["id"]
    dev = {"platform": "claude", "mode": "dev"}
    assert ok(await call(env, owner, "llm_bridge_environments", dev))["environments"] == []
    refused = tool_error(
        await call(env, owner, "llm_bridge_session_create", {**dev, "environment": env_id})
    )
    assert refused["code"] == "FORBIDDEN_CAPABILITY" and "开发环境" in refused["message"]
    instance = next(
        g["instance_id"]
        for g in (await rig.api("GET", "/grants")).json()["grants"]
        if g["instance_name"] == "openclaw-1"
    )
    await rig.grant(instance, can_write=True, environment_ids=[env_id])
    listed = ok(await call(env, owner, "llm_bridge_environments", dev))["environments"]
    assert [e["id"] for e in listed] == [env_id]
    built = ok(await call(env, owner, "llm_bridge_session_create", {**dev, "environment": env_id}))
    assert built["mode"] == "dev"
    # 没有“新建和发送”时开发环境一并收回
    off = await rig.api(
        "PUT", f"/grants/{instance}", json={"enabled": True, "environment_ids": [env_id]}
    )
    assert off.json()["environment_ids"] == []
    await rig.grant(instance, can_write=True)

    # 接手别人的会话发消息：通知管理员，链接直接到那个会话
    boss = await rig.agent("boss", can_write=True, can_takeover=True)
    taken = ok(await send(boss, session, "接手", "b1"))
    assert taken["takeover"] is True
    notes = (await env.get("/api/v1/notifications"))["items"]
    takeover = next(n for n in notes if n["type"] == "llm_bridge.takeover_sent")
    assert "boss" in takeover["title"]
    assert takeover["link"] == f"/llm_bridge?m={rig.bridge_id}&s={session}&t={taken['turn_id']}"
    made_note = next(n for n in notes if n["type"] == "llm_bridge.session_created")
    assert made_note["link"].startswith(f"/llm_bridge?m={rig.bridge_id}&s=bs_")

    # 被发送限额拒绝：通知管理员（同一个实例一个小时只一条）
    limit = await rig.api("PUT", f"/bridges/{rig.bridge_id}/settings", json={"send_per_hour": 1})
    assert limit.status_code == 200, limit.text
    for request_id in ("o2", "o3"):
        over = tool_error(await send(owner, session, "再来", request_id))
        assert over["code"] == "RATE_LIMITED"
    notes = (await env.get("/api/v1/notifications"))["items"]
    limited_notes = [n for n in notes if n["type"] == "llm_bridge.rate_limited"]
    assert len(limited_notes) == 1 and "openclaw-1" in limited_notes[0]["title"]


@pytest.mark.integration
async def test_provenance_and_session_index(rig: Rig) -> None:
    """出处跟着会话和回合走；执行记录里有会话和回合的编号；工作台的索引跨管理器、不含内容（T-0248）。"""
    env = rig.env
    agent = await rig.agent("openclaw-1", can_write=True)
    created = ok(
        await call(env, agent, "llm_bridge_session_create", {"platform": "claude", "mode": "chat"})
    )
    session = created["session_id"]
    assert created["source"]["kind"] == "agent"
    assert created["source"]["instance_name"] == "openclaw-1" and created["source"]["tasks"] == []
    sent = ok(
        await call(
            env,
            agent,
            "llm_bridge_send",
            {
                "session_id": session,
                "text": "不该进索引的内容",
                "request_id": "r1",
                "wait_seconds": 10,
            },
        )
    )
    # 统一的回合状态：用时、出处都在
    assert sent["status"] == "completed" and sent["timing"]["ran_seconds"] >= 0
    assert sent["source"]["instance_name"] == "openclaw-1"
    # 执行记录能点到会话的那一回合
    found = await records(env)
    record = next(r for r in found if "发送消息" in r["summary"])
    made = next(r for r in found if "新建会话" in r["summary"])
    assert made["arguments"]["result"] == {"bridge_id": rig.bridge_id, "session_id": session}
    assert record["arguments"]["result"] == {
        "bridge_id": rig.bridge_id,
        "session_id": session,
        "turn_id": sent["turn_id"],
    }
    # 人在聊天页建的会话：出处是这个人
    typed = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/chat/sessions",
        json={"platform": "claude", "mode": "chat", "title": "我自己问的"},
    )
    assert typed.status_code == 201, typed.text

    # 索引：后台任务还没跑时是空的；同步之后两个会话都在，按最后活动倒序
    assert (await rig.api("GET", "/sessions")).json()["sessions"] == []
    # 聊天页左栏的会话数也来自索引：还没同步过时不知道（空），不当成 0
    assert (await rig.api("GET", "/bridges")).json()["bridges"][0]["session_count"] is None
    listed = (await rig.api("GET", "/sessions", params={"sync": True})).json()
    assert (await rig.api("GET", "/bridges")).json()["bridges"][0]["session_count"] == 2
    assert listed["sync_failed"] == [] and listed["next_offset"] is None
    by_owner = {s["owner_kind"]: s for s in listed["sessions"]}
    assert set(by_owner) == {"agent", "user"}
    mine = by_owner["agent"]
    assert (mine["session_id"], mine["bridge_name"], mine["owner"]) == (
        session,
        "主桥",
        "openclaw-1",
    )
    assert (mine["turns_total"], mine["turns_failed"], mine["busy"]) == (1, 0, False)
    assert mine["source"]["instance_name"] == "openclaw-1" and mine["last_turn_at"]
    assert (
        by_owner["user"]["title"] == "我自己问的" and by_owner["user"]["source"]["kind"] == "user"
    )
    assert "不该进索引的内容" not in str(listed).replace(mine["title"], "")
    only = (await rig.api("GET", "/sessions", params={"owner_kind": "agent"})).json()["sessions"]
    assert [s["session_id"] for s in only] == [session]

    # 再发一条失败的：定时任务跑一遍，索引跟着变；登记的 updated_at 不因为同步而变
    before = (await rig.api("GET", "/bridges")).json()["bridges"][0]["updated_at"]
    failed = ok(
        await call(
            env,
            agent,
            "llm_bridge_send",
            {"session_id": session, "text": "fail:invalid", "request_id": "r2", "wait_seconds": 10},
        )
    )
    assert failed["status"] == "failed" and failed["failure"]["retryable"] is False
    from app.modules.llm_bridge import jobs

    assert await jobs.index_tick({"sync": env.sync}) == 1
    again = (await rig.api("GET", "/sessions", params={"owner_kind": "agent"})).json()["sessions"]
    assert (again[0]["turns_total"], again[0]["turns_failed"]) == (2, 1)
    # 聊天页的会话列表：每个会话带最近一次提问的开头一段（索引里没有内容）
    chats = (await rig.api("GET", f"/bridges/{rig.bridge_id}/chat/sessions")).json()["sessions"]
    assert {c["session_id"]: c["preview"] for c in chats}[session] == "fail:invalid"
    assert "fail:invalid" not in str(again)
    assert (await rig.api("GET", "/bridges")).json()["bridges"][0]["updated_at"] == before
    # 没有变化时什么都不同步
    assert await jobs.index_tick({"sync": env.sync}) == 0
    # 管理器连不上：不影响已有的记录，说明哪个没同步上
    rig.down[0] = True
    offline = (await rig.api("GET", "/sessions", params={"sync": True})).json()
    assert offline["sync_failed"] == ["主桥"] and len(offline["sessions"]) == 2
    rig.down[0] = False


@pytest.mark.integration
async def test_agent_gets_woken_sized_results_and_clear_errors(rig: Rig) -> None:
    """智能体这条链路（T-0250）：一步新建并发送、结束后叫醒、结果默认精简、错误说明能不能重试、
    按编号读产出的文件、状态里不带账号是谁。"""
    from sqlalchemy import select

    from app.models.im import AgentWakeup
    from app.modules.llm_bridge import watches

    env = rig.env
    agent = await rig.agent("openclaw-1", can_write=True)

    async def wakeups() -> list[str]:
        async with env.sync.sessions() as db:
            rows = await db.scalars(select(AgentWakeup).where(AgentWakeup.source == "llm_bridge"))
            return [w.prompt for w in rows]

    # 一步完成：没给会话就先新建一个；带 notify、又没等到结果 → 回合结束后被叫醒
    sent = ok(
        await call(
            env,
            agent,
            "llm_bridge_send",
            {
                "platform": "claude",
                "text": "sleep:0.4",
                "request_id": "n1",
                "wait_seconds": 0,
                "notify": True,
            },
        )
    )
    assert sent["created_session"] is True and sent["will_notify"] is True
    assert sent["status"] in ("queued", "running")
    session, turn = sent["session_id"], sent["turn_id"]
    assert await watches.watch_tick({"sync": env.sync}) == 0 and await wakeups() == []
    done = ok(await call(env, agent, "llm_bridge_turn_get", {"turn_id": turn, "wait_seconds": 10}))
    assert done["status"] == "completed"
    assert await watches.watch_tick({"sync": env.sync}) == 1
    (prompt,) = await wakeups()
    # 提示词里只有回合编号和状态，没有模型写的内容
    assert f"{turn}（completed）" in prompt and "sleep:0.4" not in prompt
    assert await watches.watch_tick({"sync": env.sync}) == 0  # 不重复叫
    # 同一个幂等号重发：还是那个会话那个回合，不会再建一个
    again = ok(
        await call(
            env,
            agent,
            "llm_bridge_send",
            {"platform": "claude", "text": "sleep:0.4", "request_id": "n1", "wait_seconds": 0},
        )
    )
    assert (again["session_id"], again["turn_id"]) == (session, turn)
    # 等到了结果的不用叫
    quick = ok(
        await call(
            env,
            agent,
            "llm_bridge_send",
            {"session_id": session, "text": "很快", "request_id": "n2", "notify": True},
        )
    )
    assert quick["status"] == "completed" and quick["will_notify"] is False
    # 既填会话又填新建用的参数：说清楚
    mixed = tool_error(
        await call(
            env,
            agent,
            "llm_bridge_send",
            {"session_id": session, "platform": "claude", "text": "x", "request_id": "n3"},
        )
    )
    assert mixed["code"] == "VALIDATION_FAILED" and "platform" in mixed["message"]

    # 结果默认精简：回答太长时截断，并说明怎么接着取
    long = ok(
        await call(
            env,
            agent,
            "llm_bridge_send",
            {"session_id": session, "text": "长" * 600, "request_id": "n4", "max_chars": 200},
        )
    )
    assert len(long["result"]) == 200 and long["result_next_offset"] == 200
    assert long["result_chars"] > 600 and "result_offset=200" in long["hint"]
    rest = ok(
        await call(
            env,
            agent,
            "llm_bridge_turn_get",
            {"turn_id": long["turn_id"], "result_offset": 200, "max_chars": 200_000},
        )
    )
    assert long["result"] + rest["result"] == f"[fake-1] {'长' * 600}"
    assert rest["result_next_offset"] is None
    # 过程要了才给
    assert "steps" not in rest
    detailed = ok(
        await call(
            env, agent, "llm_bridge_turn_get", {"turn_id": long["turn_id"], "detail": "steps"}
        )
    )
    assert isinstance(detailed["steps"], list)
    # 读会话：默认最新的一页，带总数；每条太长会截断
    page = ok(
        await call(
            env,
            agent,
            "llm_bridge_session_get",
            {"session_id": session, "limit": 2, "max_chars": 200},
        )
    )
    assert page["total"] == 6 and page["next_offset"] == 2
    assert [m["role"] for m in page["messages"]] == ["user", "assistant"]
    assert (
        page["messages"][0]["text_truncated"] is True and page["messages"][0]["text_chars"] == 600
    )

    # 错误说明能不能重试、多久以后
    limit = await rig.api("PUT", f"/bridges/{rig.bridge_id}/settings", json={"send_per_hour": 1})
    assert limit.status_code == 200, limit.text
    limited = tool_error(
        await call(
            env, agent, "llm_bridge_send", {"session_id": session, "text": "x", "request_id": "n5"}
        )
    )
    assert limited["code"] == "RATE_LIMITED" and limited["retryable"] is True
    assert 0 < limited["retry_after_seconds"] <= 3600
    rig.down[0] = True
    offline = tool_error(await call(env, agent, "llm_bridge_session_list", {}))
    assert offline["code"] == "EXTERNAL_UNAVAILABLE" and offline["retryable"] is True
    rig.down[0] = False
    missing = tool_error(
        await call(env, agent, "llm_bridge_turn_get", {"turn_id": "bt_" + "0" * 20})
    )
    assert missing["code"] == "NOT_FOUND" and missing["retryable"] is False

    # 状态里只有用得上的：不带账号是谁、怎么登录的
    overview = ok(await call(env, agent, "llm_bridge_status", {}))
    account = overview["accounts"]["claude"]
    assert account["state"] == "logged_in"
    assert not {"identity", "method", "first_login_at", "detail"} & set(account)


@pytest.mark.integration
async def test_records_can_be_filtered_searched_and_usage_summed(rig: Rig) -> None:
    """管理员回溯（T-0251）：按发起人、平台、有没有失败筛选；全文搜索；用量汇总。"""
    env = rig.env
    agent = await rig.agent("openclaw-1", can_write=True)

    async def send(session_id: str, text: str, rid: str) -> dict[str, Any]:
        args = {"session_id": session_id, "text": text, "request_id": rid, "wait_seconds": 10}
        return ok(await call(env, agent, "llm_bridge_send", args))

    create = {"platform": "claude", "mode": "chat"}
    good = ok(await call(env, agent, "llm_bridge_session_create", {**create, "title": "季度复盘"}))
    await send(good["session_id"], "帮我列出三个要点", "r1")
    bad = ok(await call(env, agent, "llm_bridge_session_create", {**create, "title": "失败的那个"}))
    await send(bad["session_id"], "fail:invalid", "r2")
    typed = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/chat/sessions",
        json={**create, "title": "我自己问的", "text": "天气怎么样", "request_id": "h1"},
    )
    assert typed.status_code == 201, typed.text

    async def listed(**params: Any) -> list[str]:
        out = (await rig.api("GET", "/sessions", params=params)).json()
        return sorted(s["title"] for s in out["sessions"])

    assert await listed(sync=True) == ["失败的那个", "季度复盘", "我自己问的"]
    assert await listed(owner_kind="agent") == ["失败的那个", "季度复盘"]
    assert await listed(problem=True) == ["失败的那个"]
    assert await listed(q="复盘") == ["季度复盘"]
    assert await listed(platform="codex") == [] and await listed(mode="chat", status="active")
    assert await listed(until="2000-01-01T00:00:00Z") == []
    owners = (await rig.api("GET", "/sessions/owners")).json()["owners"]
    by_kind = {o["owner_kind"]: o for o in owners}
    assert by_kind["agent"]["owner"] == "openclaw-1" and by_kind["agent"]["sessions"] == 2
    assert await listed(owner_id=by_kind["agent"]["owner_id"]) == ["失败的那个", "季度复盘"]
    assert await listed(owner_id=by_kind["user"]["owner_id"]) == ["我自己问的"]

    # 全文搜索：内容在管理器里，现问现答；结果带上索引里的会话
    found = (await rig.api("GET", "/sessions/search", params={"q": "三个要点"})).json()
    (hit,) = found["hits"]
    assert hit["session_id"] == good["session_id"] and "三个要点" in hit["snippet"]
    assert hit["session"]["title"] == "季度复盘" and hit["bridge_name"] == "主桥"
    assert found["failed"] == []
    assert (await rig.api("GET", "/sessions/search", params={"q": "x"})).status_code == 422

    # 用量：按发起人分开，失败的也算回合
    usage = (await rig.api("GET", "/usage", params={"days": 7})).json()
    rows = {r["owner_kind"]: r for r in usage["rows"]}
    assert rows["agent"]["turns"] == 2 and rows["agent"]["failed"] == 1
    assert rows["agent"]["owner"] == "openclaw-1" and rows["agent"]["tokens"] > 0
    assert rows["user"]["turns"] == 1 and rows["user"]["bridge_name"] == "主桥"
    # 没有回合报积分：积分是空的，不是 0
    assert rows["agent"]["credits"] is None
    quota = (await rig.api("GET", f"/bridges/{rig.bridge_id}/quota")).json()["platforms"]
    assert quota["claude"]["credits_5h"] is None
    # 官方程序报了这一回合消耗的积分（CodeBuddy）：工具结果、用量、最近 5 小时里都有
    spent = await send(good["session_id"], "credits:2.5 再来一问", "r3")
    assert spent["usage"]["credits"] == 2.5
    usage = (await rig.api("GET", "/usage", params={"days": 7})).json()
    assert {r["owner_kind"]: r for r in usage["rows"]}["agent"]["credits"] == 2.5
    quota = (await rig.api("GET", f"/bridges/{rig.bridge_id}/quota")).json()["platforms"]
    assert quota["claude"]["credits_5h"] == 2.5
    status = ok(await call(env, agent, "llm_bridge_status", {}))
    assert status["quota"]["claude"]["credits_5h"] == 2.5
    # 管理器连不上：说明哪个没取到
    rig.down[0] = True
    assert (await rig.api("GET", "/usage")).json()["failed"] == ["主桥"]
    assert (await rig.api("GET", "/sessions/search", params={"q": "要点"})).json() == {
        "hits": [],
        "failed": ["主桥"],
    }
    rig.down[0] = False


@pytest.mark.integration
async def test_review_trail_audit_notifications_export_and_retention(rig: Rig) -> None:
    """管理员回溯的另一半（T-0252）：智能体的写操作进审计、失败和额度暂停有通知、
    会话能导出、保留期能设置。"""
    env = rig.env
    agent = await rig.agent("openclaw-1", can_write=True)
    created = ok(
        await call(
            env,
            agent,
            "llm_bridge_session_create",
            {"platform": "claude", "mode": "chat", "title": "排查问题"},
        )
    )
    session = created["session_id"]

    async def send(text: str, rid: str) -> dict[str, Any]:
        args = {"session_id": session, "text": text, "request_id": rid, "wait_seconds": 10}
        return ok(await call(env, agent, "llm_bridge_send", args))

    first = await send("先看日志", "r1")
    # 第一次同步是补历史：不为已有的失败发通知
    assert (await rig.api("GET", "/sessions", params={"sync": True})).status_code == 200

    # 审计：智能体新建会话、发消息都有一条，带中文名，没有消息内容
    logs = (await env.get("/api/v1/audit-logs", limit=50))["items"]
    by_action = {entry["action"]: entry for entry in logs}
    made = by_action["llm_bridge.agent_session_created"]
    assert made["action_label"] == "智能体新建大模型会话"
    assert (
        made["metadata"]["instance"] == "openclaw-1" and made["metadata"]["session_id"] == session
    )
    sent = by_action["llm_bridge.agent_message_sent"]
    assert sent["metadata"]["turn_id"] == first["turn_id"] and "先看日志" not in str(sent)

    # 智能体的回合失败了：下一次同步时通知管理员，链接到那个会话；同一次失败不重复通知
    failed = await send("fail:invalid", "r2")
    assert failed["status"] == "failed"
    from app.modules.llm_bridge import jobs

    assert await jobs.index_tick({"sync": env.sync}) == 1
    assert await jobs.index_tick({"sync": env.sync}) == 0
    notes = (await env.get("/api/v1/notifications"))["items"]
    (note,) = [n for n in notes if n["type"] == "llm_bridge.turn_failed"]
    assert "openclaw-1" in note["title"] and "排查问题" in note["body"]
    assert note["link"] == f"/llm_bridge?m={rig.bridge_id}&s={session}"

    # 平台因额度暂停：通知一次；恢复后清掉记号，再暂停再通知
    engine = rig.bridge_app.state.engine
    until = time.time() + 3600
    engine.store.set_pause("claude", until, False, "quota")
    await jobs.index_tick({"sync": env.sync})
    await jobs.index_tick({"sync": env.sync})
    paused = [
        n
        for n in (await env.get("/api/v1/notifications"))["items"]
        if n["type"] == "llm_bridge.quota_paused"
    ]
    assert len(paused) == 1 and "claude" in paused[0]["title"] and "主桥" in paused[0]["title"]
    engine.store.clear_pause("claude")
    await jobs.index_tick({"sync": env.sync})
    engine.store.set_pause("claude", until + 7200, True, "quota")
    await jobs.index_tick({"sync": env.sync})
    paused = [
        n
        for n in (await env.get("/api/v1/notifications"))["items"]
        if n["type"] == "llm_bridge.quota_paused"
    ]
    assert len(paused) == 2
    engine.store.clear_pause("claude")

    # 导出：一份 Markdown，提问、回答、没做完的原因都在；导出记一条审计
    exported = await rig.api("GET", f"/bridges/{rig.bridge_id}/chat/sessions/{session}/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/markdown")
    assert "attachment" in exported.headers["content-disposition"]
    text = exported.text
    assert text.startswith("# 排查问题") and "先看日志" in text and "[fake-1] 先看日志" in text
    assert "没做完的原因" in text
    logs = (await env.get("/api/v1/audit-logs", limit=50))["items"]
    assert any(e["action"] == "llm_bridge.chat_session_exported" for e in logs)

    # 保留期：页面上能设置，写到管理器里；默认是一直保留
    status = (await rig.api("GET", f"/bridges/{rig.bridge_id}/status")).json()
    assert status["settings"]["retention_days"] == 0
    saved = await rig.api("PUT", f"/bridges/{rig.bridge_id}/settings", json={"retention_days": 90})
    assert saved.status_code == 200 and saved.json()["retention_days"] == 90
    too_long = await rig.api(
        "PUT", f"/bridges/{rig.bridge_id}/settings", json={"retention_days": 99999}
    )
    assert too_long.status_code == 422


@pytest.mark.integration
async def test_tool_failures_are_explicit(rig: Rig) -> None:
    env = rig.env
    token = await rig.agent("alpha", can_write=True)
    # 接口桩、没登录的平台、参数不对：都是明确的错误
    stub = tool_error(
        await call(env, token, "llm_bridge_session_create", {"platform": "codex", "mode": "cloud"})
    )
    assert stub["code"] == "NOT_SUPPORTED" and "接口桩" in stub["message"]
    unknown = tool_error(await call(env, token, "llm_bridge_status", {"bridge": "不存在"}))
    assert unknown["code"] == "NOT_FOUND"
    invalid = tool_error(
        await call(
            env, token, "llm_bridge_send", {"session_id": "x", "text": "", "request_id": "r"}
        )
    )
    assert invalid["code"] == "VALIDATION_FAILED"
    # 第二座桥：必须指明用哪一座
    extra = await rig.api(
        "POST", "/bridges", json={"name": "副桥", "base_url": BRIDGE_URL, "token": BRIDGE_TOKEN}
    )
    assert extra.status_code == 201
    ambiguous = tool_error(await call(env, token, "llm_bridge_session_list", {}))
    assert ambiguous["code"] == "VALIDATION_FAILED" and "主桥、副桥" in ambiguous["message"]
    # 状态工具不填 bridge：列出每一座
    both = ok(await call(env, token, "llm_bridge_status", {}))
    assert [(b["bridge"], b["reachable"]) for b in both["bridges"]] == [
        ("主桥", True),
        ("副桥", True),
    ]
    assert ok(await call(env, token, "llm_bridge_status", {"bridge": "副桥"}))["queued"] == 0
    # 会话桥连不上
    rig.down[0] = True
    offline = tool_error(await call(env, token, "llm_bridge_status", {"bridge": "主桥"}))
    assert offline["code"] == "EXTERNAL_UNAVAILABLE"
    # 列出每一座时，连不上的如实标出来，不让整次调用失败
    partial = ok(await call(env, token, "llm_bridge_status", {}))
    assert [(b["bridge"], b["reachable"]) for b in partial["bridges"]] == [
        ("主桥", False),
        ("副桥", False),
    ]
    assert all("连不上" in b["error"] for b in partial["bridges"])
    rig.down[0] = False
    # 只读权限范围的实例不能用写工具（实例自身的 scope，与模块的授权是两回事）
    reader = await new_instance(env, scope="read", name="reader")
    await rig.grant(reader, can_write=True)
    reader_token = await issue_token(env, reader)
    refused = tool_error(
        await call(
            env, reader_token, "llm_bridge_session_create", {"platform": "claude", "mode": "chat"}
        )
    )
    assert refused["code"].startswith("FORBIDDEN")


async def test_chat_only_grant_stays_away_from_codex_dev_sessions(rig: Rig) -> None:
    """管理员只给了 “Codex（只能对话）”（codex:chat，T-0280）：能和 Codex 对话，
    但有接手权限也读不到、发不了别人的 Codex 开发会话，也建不了开发会话。"""
    env = rig.env
    fake_driver = importlib.import_module("bridge.drivers.fake")
    rig.bridge_app.state.engine.drivers["openai"] = fake_driver.FakeDriver()
    made = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/environments",
        json={"platform": "codex", "name": "仓库", "sandbox": "exec"},
    )
    assert made.status_code == 201, made.text
    env_id = made.json()["id"]
    instance = await new_instance(env, name="dev-owner")
    await rig.grant(instance, can_write=True, platforms=["codex"], environment_ids=[env_id])
    owner = await issue_token(env, instance)
    dev_args = {"platform": "codex", "mode": "dev", "environment": env_id, "request_id": "d1"}
    dev = ok(await call(env, owner, "llm_bridge_session_create", dev_args))["session_id"]
    chat_args = {"platform": "codex", "mode": "chat", "request_id": "c1"}
    chat = ok(await call(env, owner, "llm_bridge_session_create", chat_args))["session_id"]

    limited = await rig.agent(
        "chat-only", can_write=True, can_account=True, can_takeover=True, platforms=["codex:chat"]
    )
    grants = (await rig.api("GET", "/grants")).json()["grants"]
    assert next(g for g in grants if g["instance_name"] == "chat-only")["platforms"] == [
        "codex:chat"
    ]
    for name, arguments in (
        ("llm_bridge_session_get", {"session_id": dev}),
        ("llm_bridge_queue", {"session_id": dev}),
        ("llm_bridge_send", {"session_id": dev, "text": "插话", "request_id": "x1"}),
    ):
        assert tool_error(await call(env, limited, name, arguments))["code"] == "NOT_FOUND", name
    ok(await call(env, limited, "llm_bridge_session_get", {"session_id": chat}))
    listed = ok(await call(env, limited, "llm_bridge_session_list", {"scope": "account"}))
    assert {s["session_id"] for s in listed["sessions"]} == {chat}
    refused = tool_error(
        await call(env, limited, "llm_bridge_session_create", {**dev_args, "request_id": "d2"})
    )
    assert refused["code"] == "FORBIDDEN_CAPABILITY" and "对话" in refused["message"]
    mine = ok(
        await call(env, limited, "llm_bridge_session_create", {**chat_args, "request_id": "c2"})
    )
    assert (mine["platform"], mine["mode"]) == ("codex", "chat")
    # 聊天页按 Codex 筛选：codex 和旧名字 chatgpt 各查一次再合并（新版本两次一样），不重复
    page = await rig.api(
        "GET", f"/bridges/{rig.bridge_id}/chat/sessions", params={"platform": "codex"}
    )
    assert page.status_code == 200, page.text
    ids = [s["session_id"] for s in page.json()["sessions"]]
    assert sorted(ids) == sorted({dev, chat, mine["session_id"]}) and len(ids) == len(set(ids))
    paged = await rig.api(
        "GET",
        f"/bridges/{rig.bridge_id}/chat/sessions",
        params={"platform": "codex", "limit": 2, "offset": 0},
    )
    assert len(paged.json()["sessions"]) == 2 and paged.json()["next_offset"] == 2

    # 授权里的旧名字 chatgpt 是整个 Codex（人类 2026-11-17 决定，T-0282）：
    # 给了开发环境就能用开发模式
    former = await rig.agent(
        "former-chatgpt", can_write=True, platforms=["chatgpt"], environment_ids=[env_id]
    )
    grants = (await rig.api("GET", "/grants")).json()["grants"]
    assert next(g for g in grants if g["instance_name"] == "former-chatgpt")["platforms"] == [
        "codex"
    ]
    made_dev = ok(
        await call(env, former, "llm_bridge_session_create", {**dev_args, "request_id": "d3"})
    )
    assert (made_dev["platform"], made_dev["mode"]) == ("codex", "dev")


async def test_dev_session_files_can_be_browsed_previewed_and_downloaded(rig: Rig) -> None:
    """开发环境里浏览会话的工作目录（T-0289）：admin 能看文件夹和文件，预览脱敏；
    下载给原文件并记审计；跳不出工作目录；官方程序里同步来的会话不能浏览。"""
    made = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/environments",
        json={"platform": "claude", "name": "仓库", "sandbox": "workspace_write"},
    )
    assert made.status_code == 201, made.text
    env_id = made.json()["id"]
    engine = rig.bridge_app.state.engine
    caller = importlib.import_module("bridge.engine").Caller("u-1", "管理员", frozenset({"write"}))
    session = await engine.create_session(caller, platform="claude", mode="dev", environment=env_id)
    session_id = session["session_id"]
    workdir = Path(engine.store.get_session(session_id)["workdir"])
    secret = "sk-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe9Dc8"
    (workdir / "docs").mkdir()
    (workdir / "docs" / "说明.md").write_text(f"# 说明\n令牌 {secret}\n", encoding="utf-8")

    base = f"/bridges/{rig.bridge_id}"
    listed = (await rig.api("GET", f"{base}/environments/{env_id}/sessions")).json()["sessions"]
    assert [(s["session_id"], s["browsable"]) for s in listed] == [(session_id, True)]
    top = (await rig.api("GET", f"{base}/sessions/{session_id}/files")).json()
    assert top["path"] == "" and top["entries"][0]["name"] == "docs"
    inner = await rig.api("GET", f"{base}/sessions/{session_id}/files", params={"path": "docs"})
    assert [e["name"] for e in inner.json()["entries"]] == ["说明.md"]
    preview = await rig.api(
        "GET", f"{base}/sessions/{session_id}/files/content", params={"path": "docs/说明.md"}
    )
    assert preview.status_code == 200 and "# 说明" in preview.json()["text"]
    assert secret not in preview.text
    download = await rig.api(
        "GET", f"{base}/sessions/{session_id}/files/download", params={"path": "docs/说明.md"}
    )
    assert download.status_code == 200 and secret in download.text
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert download.headers["x-content-type-options"] == "nosniff"
    logs = (await rig.env.get("/api/v1/audit-logs", limit=20))["items"]
    entry = next(e for e in logs if e["action"] == "llm_bridge.work_file_downloaded")
    assert entry["action_label"] == "下载开发会话工作目录里的文件"
    assert entry["metadata"]["path"] == "docs/说明.md" and secret not in str(entry)

    # 跳不出工作目录；路径过长被拦下
    escape = await rig.api("GET", f"{base}/sessions/{session_id}/files", params={"path": "../.."})
    assert escape.status_code == 422
    too_long = await rig.api(
        "GET", f"{base}/sessions/{session_id}/files", params={"path": "a" * 1001}
    )
    assert too_long.status_code == 422
    missing = await rig.api(
        "GET", f"{base}/sessions/{session_id}/files/content", params={"path": "没有.txt"}
    )
    assert missing.status_code == 404


@pytest.mark.integration
async def test_agent_timers_jobs_and_chain_wake_it_up_and_are_audited(rig: Rig) -> None:
    """长时间任务（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md，例外十）：智能体预约、开后台作业，
    直接执行；它们引出的那一回合结束后叫醒智能体；页面上能看、能停；都记审计。"""
    import asyncio
    import time

    from sqlalchemy import select

    from app.models.im import AgentWakeup
    from app.modules.llm_bridge import watches

    env = rig.env
    engine = rig.bridge_app.state.engine
    store = engine.store
    made = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/environments",
        json={
            "platform": "claude",
            "name": "能跑命令",
            "sandbox": "exec",
            "turn_timeout_seconds": 600,
        },
    )
    assert made.status_code == 201, made.text
    env_id = made.json()["id"]
    assert made.json()["turn_timeout_seconds"] == 600
    instance = await new_instance(env, name="openclaw-long")
    await rig.grant(instance, can_write=True, environment_ids=[env_id])
    agent = await issue_token(env, instance)

    async def wakeups() -> list[str]:
        async with env.sync.sessions() as db:
            rows = await db.scalars(select(AgentWakeup).where(AgentWakeup.source == "llm_bridge"))
            return [w.prompt for w in rows]

    async def until(check: Any) -> Any:
        for _ in range(200):
            value = check()
            if value:
                return value
            await asyncio.sleep(0.05)
        raise AssertionError("等不到")

    # 预约：直接执行；到点后管理器自动发出消息，那一回合结束时叫醒智能体
    chat = ok(
        await call(env, agent, "llm_bridge_session_create", {"platform": "claude", "mode": "chat"})
    )
    timer = ok(
        await call(
            env,
            agent,
            "llm_bridge_timer_create",
            {"session_id": chat["session_id"], "text": "去看 CI 结果", "after_seconds": 3600},
        )
    )
    assert timer["will_notify"] is True and timer["timer"]["status"] == "pending"
    tasks = ok(await call(env, agent, "llm_bridge_tasks", {"session_id": chat["session_id"]}))
    assert [t["id"] for t in tasks["timers"]] == [timer["timer"]["id"]]
    assert await watches.watch_tick({"sync": env.sync}) == 0
    store.update_timer(timer["timer"]["id"], run_at=time.time() - 1)
    engine.wake_scheduler()
    fired = await until(lambda: (store.get_timer(timer["timer"]["id"]) or {}).get("fired_turn_id"))
    await watches.watch_tick({"sync": env.sync})  # 跟到它引出的那一回合
    done = ok(await call(env, agent, "llm_bridge_turn_get", {"turn_id": fired, "wait_seconds": 10}))
    assert done["status"] == "completed" and "MCP 管理器自动发出" in done["result"]
    assert await watches.watch_tick({"sync": env.sync}) == 1
    assert any(f"{fired}（completed）" in prompt for prompt in await wakeups())

    # 后台作业：只在“可以执行命令”档位；结束后自动接着做，那一回合结束时叫醒
    dev = ok(
        await call(
            env,
            agent,
            "llm_bridge_session_create",
            {"platform": "claude", "mode": "dev", "environment": env_id},
        )
    )
    secret = "sk-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe9Dc8"
    blocked = tool_error(
        await call(
            env,
            agent,
            "llm_bridge_job_start",
            {"session_id": dev["session_id"], "command": f"echo {secret}"},
        )
    )
    assert blocked["code"] == "VALIDATION_FAILED" and "密钥" in blocked["message"]
    job = ok(
        await call(
            env,
            agent,
            "llm_bridge_job_start",
            {"session_id": dev["session_id"], "command": "echo built", "name": "构建"},
        )
    )
    assert job["will_notify"] is True and job["job"]["origin"] == "caller"
    # 通知管理员（例外十）
    notes = (await env.get("/api/v1/notifications", limit=50))["items"]
    started = next(n for n in notes if n["type"] == "llm_bridge.job_started")
    assert "openclaw-long" in started["title"] and "构建" in started["title"]
    # 没有“新建会话和发送”权限的实例：看不到也调用不了写工具
    reader = await new_instance(env, name="openclaw-reader")
    await rig.grant(reader, can_write=False, environment_ids=[env_id])
    reader_token = await issue_token(env, reader)
    denied = tool_error(
        await call(
            env,
            reader_token,
            "llm_bridge_job_start",
            {"session_id": dev["session_id"], "command": "echo hi"},
        )
    )
    assert denied["code"] in ("FORBIDDEN_CAPABILITY", "FORBIDDEN_TOOL", "NOT_FOUND")
    follow = await until(lambda: (store.get_job(job["job"]["id"]) or {}).get("follow_turn_id"))
    await watches.watch_tick({"sync": env.sync})
    ok(await call(env, agent, "llm_bridge_turn_get", {"turn_id": follow, "wait_seconds": 10}))
    await watches.watch_tick({"sync": env.sync})
    assert any(f"{follow}（completed）" in prompt for prompt in await wakeups())
    log = ok(await call(env, agent, "llm_bridge_job_log", {"job_id": job["job"]["id"]}))
    assert "built" in log["text"]

    # 页面：看后台任务、预约、停作业、停止 / 恢复自动接着做
    base = f"/bridges/{rig.bridge_id}"
    running = ok(
        await call(
            env,
            agent,
            "llm_bridge_job_start",
            {"session_id": dev["session_id"], "command": "sleep 30"},
        )
    )
    page = (await rig.api("GET", f"{base}/sessions/{dev['session_id']}/tasks")).json()
    assert page["jobs_allowed"] is True and page["jobs"][0]["id"] == running["job"]["id"]
    stopped = await rig.api("POST", f"{base}/jobs/{running['job']['id']}/stop", json={})
    assert stopped.status_code == 200 and stopped.json()["job"]["status"] == "stopped"
    planned = await rig.api(
        "POST",
        f"{base}/sessions/{dev['session_id']}/timers",
        json={"text": "明早看", "after_seconds": 600},
    )
    assert planned.status_code == 200, planned.text
    leaky = await rig.api(
        "POST",
        f"{base}/sessions/{dev['session_id']}/timers",
        json={"text": secret, "after_seconds": 600},
    )
    assert leaky.status_code == 422
    # 页面上开作业：命令里像有密钥的拒绝；审计只记脱敏后的作业名（T-0292 审查）
    leaky_job = await rig.api(
        "POST",
        f"{base}/sessions/{dev['session_id']}/jobs",
        json={"command": f"curl -H 'Authorization: Bearer {secret}' x", "follow_up": False},
    )
    assert leaky_job.status_code == 422
    page_job = await rig.api(
        "POST",
        f"{base}/sessions/{dev['session_id']}/jobs",
        json={"command": "sleep 30", "name": "页面开的", "follow_up": False},
    )
    assert page_job.status_code == 200 and page_job.json()["job"]["origin"] == "admin"
    await rig.api("POST", f"{base}/jobs/{page_job.json()['job']['id']}/stop", json={})
    paused = await rig.api(
        "POST", f"{base}/sessions/{dev['session_id']}/chain", json={"action": "stop"}
    )
    assert paused.json()["chain"]["status"] == "paused"
    assert store.get_timer(planned.json()["timer"]["id"])["status"] == "cancelled"
    resumed = await rig.api(
        "POST", f"{base}/sessions/{dev['session_id']}/chain", json={"action": "resume"}
    )
    assert resumed.json()["chain"]["status"] == "active"
    page_log = await rig.api("GET", f"{base}/jobs/{job['job']['id']}/log")
    assert page_log.status_code == 200 and "built" in page_log.json()["text"]

    # 审计：智能体的和页面上的都记
    logs = (await env.get("/api/v1/audit-logs", limit=50))["items"]
    actions = {e["action"]: e["action_label"] for e in logs}
    assert actions["llm_bridge.agent_timer_created"] == "智能体预约给大模型会话发消息"
    assert actions["llm_bridge.agent_job_started"] == "智能体给大模型会话开后台作业"
    assert actions["llm_bridge.job_stopped"] == "停掉大模型会话的后台作业"
    assert actions["llm_bridge.timer_created"] == "预约给大模型会话发消息"
    started_page = next(e for e in logs if e["action"] == "llm_bridge.job_started")
    assert started_page["metadata"]["name"] == "页面开的"
    assert actions["llm_bridge.chain_controlled"] == "恢复或停止大模型会话的自动接着做"
    # 审计里不记令牌、不记命令原文以外的密钥
    dumped = str([e for e in logs if e["action"].startswith("llm_bridge.")])
    assert "token" not in dumped.lower() and secret not in dumped
