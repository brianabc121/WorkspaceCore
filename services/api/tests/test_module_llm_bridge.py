"""大模型会话桥模块（T-0228，docs/v2/LLM_BRIDGE_PLAN.md）。

真实 PostgreSQL / Redis；会话桥用的是 services/llm-bridge 的真实应用（进程内，经 ASGI），
其中 Claude 这个账号槽位换成不访问外部服务的测试驱动。这样覆盖的是整条链路：
实例 → 工作台 /mcp → 模块（权限、转发）→ 会话桥 /mcp → 驱动。
"""

import importlib
import sys
import uuid
from collections.abc import AsyncIterator
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
ALL_TOOLS = {spec.name for spec in SPECS}
READ_TOOLS = ALL_TOOLS - WRITE_TOOL_NAMES


@dataclass
class Rig:
    env: Env
    bridge_app: Any
    bridge_id: str
    down: list[bool]

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
            bridge_app.router.lifespan_context(bridge_app),
            open_env(make_client, migrated_database_url, **SETTINGS) as env,
        ):
            # Claude 槽位换成测试驱动：不需要真实登录
            bridge_app.state.engine.drivers["claude"] = fake_driver.FakeDriver()
            asgi = httpx.ASGITransport(app=bridge_app)

            async def route(request: httpx.Request) -> httpx.Response:
                if request.url.host != "bridge.test" or down[0]:
                    raise httpx.ConnectError("connection refused", request=request)
                response = await asgi.handle_async_request(request)
                body = await response.aread()
                return httpx.Response(response.status_code, headers=response.headers, content=body)

            app = env.client._transport.app  # type: ignore[attr-defined]
            app.state.connectors.transport = httpx.MockTransport(route)
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
            yield Rig(env, bridge_app, created.json()["id"], down)
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
    assert status["settings"]["concurrency"] == 2

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
    ambiguous = tool_error(await call(env, token, "llm_bridge_status", {}))
    assert ambiguous["code"] == "VALIDATION_FAILED" and "主桥、副桥" in ambiguous["message"]
    assert ok(await call(env, token, "llm_bridge_status", {"bridge": "副桥"}))["queued"] == 0
    # 会话桥连不上
    rig.down[0] = True
    offline = tool_error(await call(env, token, "llm_bridge_status", {"bridge": "主桥"}))
    assert offline["code"] == "EXTERNAL_UNAVAILABLE"
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
