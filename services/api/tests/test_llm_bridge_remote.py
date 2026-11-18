"""远程开发容器：工作台这一侧（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md）。

前半是不需要数据库的小测试（版本一致、网络档、命令校验、脱敏）。

后半是整条链路，真实 PostgreSQL / Redis：
- MCP 管理器是 services/llm-bridge 的真实应用（进程内，经 ASGI），
  Claude 用真实驱动（API 密钥登录）；
- 工作台的中转（remote_tick 的两个部分）照常运行；
- 测试代替 runner-agent 走 /runner-api/v1/*：起托管进程时在本机跑管理器测试用的假命令行
  （services/llm-bridge/tests/fakes/fake_cli.py），把输出按偏移交上去。
覆盖：建远程环境（连带新建远程容器）→ 构建镜像 → 建容器 → 开会话（容器里准备目录）→ 发消息
（容器里跑 Claude，凭据随回合注入、不落库）→ 浏览工作目录（经容器服务器）→ 归档 → 删容器。
"""

import asyncio
import base64
import contextlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.models import RunnerCommand, RunnerEnvironment
from app.modules.llm_bridge import remote as remote_module
from app.modules.llm_bridge import remote_config as cfg
from app.modules.llm_bridge.models import LlmBridgeRemoteContainer
from app.modules.llm_bridge.remote import RemoteContainers, RemoteError, parse_versions
from app.modules.llm_bridge.remote_files import redact_preview
from tests.test_mcp_api import call, issue_token, new_instance
from tests.test_runner_api import SETTINGS as RUNNER_SETTINGS
from tests.test_runner_api import add_host, poll, runner_headers
from tests.workspace_support import Env, app_of, open_env

ROOT = Path(__file__).resolve().parents[3]
BRIDGE_SOURCE = ROOT / "services" / "llm-bridge"
FAKE_CLI = BRIDGE_SOURCE / "tests" / "fakes" / "fake_cli.py"
BRIDGE_URL = "http://bridge.test:8787"
BRIDGE_TOKEN = "r" * 40
API_KEY = "sk-ant-api03-" + "K" * 40


# ---------------------------------------------------------------- 小测试


def test_cli_versions_match_the_manager_image_and_docs() -> None:
    dockerfile = (BRIDGE_SOURCE / "Dockerfile").read_text()
    versions = (ROOT / "docs" / "versions.md").read_text()
    for name, version in cfg.CLI_PACKAGES.values():
        assert f"{name}@{version}" in dockerfile, name
        assert f"{name}@{version}" in versions, name
    step = next(s for s in cfg.BUILTIN_STEPS if "npm install" in s)
    for name, version in cfg.CLI_PACKAGES.values():
        assert f"{name}@{version}" in step
    assert "@sha256:" in cfg.BASE_IMAGE  # 基础镜像锁定 digest


def test_versions_are_keyed_by_program() -> None:
    output = "codex=codex-cli 0.169.0\nclaude=2.1.323 (Claude Code)\ncodebuddy=2.184.0\nx=1.2.3\n"
    assert parse_versions(output) == {
        "codex": "0.169.0",
        "claude": "2.1.323",
        "codebuddy": "2.184.0",
    }


def test_network_choices() -> None:
    assert cfg.runner_network("all", ["a.example.com"], ["git.example.com"]) == {
        "network": "all",
        "domains": [],
    }
    models = cfg.runner_network("models", ["Extra.Example.com"], ["git.example.com"])
    assert models["network"] == "allowlist"
    assert "api.anthropic.com" in models["domains"] and "chatgpt.com" in models["domains"]
    assert "git.example.com" in models["domains"] and "extra.example.com" in models["domains"]
    assert "registry.npmjs.org" not in models["domains"]
    registries = cfg.runner_network("models_registries", [], [])
    assert "registry.npmjs.org" in registries["domains"]


def test_only_the_platform_program_may_run() -> None:
    remote = RemoteContainers.__new__(RemoteContainers)
    check = remote._check_argv
    assert check("turn", "claude", ["claude", "-p"]) == [f"{cfg.CLI_BIN}/claude", "-p"]
    assert check("login", "codex", ["codex", "login", "--device-auth"])[1] == "login"
    assert check("job", None, ["bash", "-c", "exec 2>&1\nmake test"])[0] == "bash"
    for purpose, platform, argv in (
        ("turn", "claude", ["sh", "-c", "curl evil"]),
        ("turn", "claude", ["codex", "exec"]),
        ("turn", "nope", ["claude"]),
        ("login", "claude", ["claude", "-p", "hi"]),
        ("job", None, ["sh", "-c", "x"]),
        ("job", None, ["bash", "-c", "x", "extra"]),
    ):
        with pytest.raises(RemoteError):
            check(purpose, platform, argv)


def test_preview_redaction_matches_the_manager() -> None:
    text = f"key={API_KEY}\n-----BEGIN OPENSSH PRIVATE KEY-----\nabc"
    redacted = redact_preview(text, truncated=False)
    assert API_KEY not in redacted and "abc" not in redacted
    assert redact_preview("token sk-ant-api03-AAAA", truncated=True).endswith("token ")


# ---------------------------------------------------------------- 整条链路


@dataclass
class FakeAgent:
    """代替 runner-agent：领指令、回报结果；托管进程在本机用假命令行跑。"""

    env: Env
    token: str
    home: Path
    started: list[dict[str, Any]] = field(default_factory=list)
    recreated: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    stop: bool = False

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self.env.client.post(
            f"/runner-api/v1{path}", json=body, headers=runner_headers(self.token)
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    def local(self, path: str) -> Path:
        assert path.startswith("/workspace")
        return self.home / path.removeprefix("/workspace").lstrip("/")

    async def run_proc(self, command: dict[str, Any]) -> None:
        payload = command["payload"]
        self.started.append(payload)
        await self.post(
            f"/commands/{command['id']}/result",
            {"status": "done", "result": {"started": True, "pgid": 9}, "error": None},
        )
        proc_input = payload["proc_input"]
        assert "missing" not in proc_input
        stdin = base64.b64decode(proc_input["stdin_b64"])
        argv = list(payload["argv"])
        cwd = self.local(payload["cwd"])
        env = {
            **os.environ,
            **payload["env"],
            **proc_input["secret_env"],
            "HOME": str(self.home / ".home"),
        }
        for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "CODEBUDDY_CONFIG_DIR"):
            if key in env and env[key].startswith("/workspace"):
                env[key] = str(self.local(env[key]))
        if argv[:2] == ["sh", "-c"]:  # 准备会话目录：工作台拼的固定脚本
            # argv：sh -c <脚本> mw-prepare <会话目录> <仓库> <分支>
            folder = self.local(argv[4])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "README.md").write_text(f"repo {argv[5]} {argv[6]}\n{API_KEY}\n")
            output, code = "准备好了\n".encode(), 0
        else:
            assert argv[0] == f"{cfg.CLI_BIN}/claude"
            await asyncio.to_thread(cwd.mkdir, parents=True, exist_ok=True)
            config = Path(env["CLAUDE_CONFIG_DIR"])
            await asyncio.to_thread(config.mkdir, parents=True, exist_ok=True)
            done = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(FAKE_CLI), *argv[1:]],
                input=stdin,
                capture_output=True,
                cwd=cwd,
                env=env,
                timeout=60,
            )
            output, code = done.stdout, done.returncode
        offset = 0
        for start in range(0, len(output), 4096):
            chunk = output[start : start + 4096]
            ack = await self.post(
                f"/procs/{payload['run_id']}/output",
                {"offset": offset, "data_b64": base64.b64encode(chunk).decode()},
            )
            assert ack["acked"] == offset + len(chunk), ack
            offset = ack["acked"]
        await self.post(
            f"/procs/{payload['run_id']}/exit",
            {"exit_code": code, "state": "exited", "stderr_tail": "", "error": None},
        )

    async def run_exec(self, command: dict[str, Any]) -> None:
        argv = command["payload"]["cmd"]
        # 辅助命令在容器里自己带时限，超时也不停共享的容器（集成审查第 2 条）
        assert argv[:3] == ["timeout", "-s", "KILL"] and int(argv[3]) > 0, argv
        assert command["payload"]["keep_container"] is True
        argv = argv[4:]
        output, code = "", 0
        if argv[:2] == ["sh", "-c"] and argv[3:4] == ["mw-rm"]:
            # 真的在本机跑工作台的脚本：先挪进回收目录，再在后台删
            done = await asyncio.to_thread(
                subprocess.run,
                [
                    "/bin/sh",
                    "-c",
                    argv[2],
                    "mw-rm",
                    str(self.local(argv[4])),
                    str(self.local(argv[5])),
                ],
                capture_output=True,
                timeout=30,
            )
            code = done.returncode
        elif argv[:2] == ["sh", "-c"] and "--version" in argv[2]:
            output = "codex=codex-cli 0.169.0\nclaude=2.1.323 (Claude Code)\ncodebuddy=2.184.0\n"
        elif argv[:2] == ["sh", "-c"] and argv[3] == "mw-check":
            root = self.local(argv[4])
            found = list(root.glob(f"projects/*/{argv[5]}.jsonl"))
            output = str(found[0]) if found else ""
        elif argv[:2] == ["sh", "-c"] and argv[3] == "mw-files":
            # 浏览：真的在本机跑工作台的脚本（root 换成本机目录，realpath 的检查照样生效）
            local_root = str(self.local(argv[4]))
            done = await asyncio.to_thread(
                subprocess.run,
                ["/bin/sh", "-c", argv[2], "mw-files", local_root, argv[5]],
                capture_output=True,
                timeout=30,
            )
            output, code = done.stdout.decode(), done.returncode
        else:
            raise AssertionError(f"没想到的命令：{argv}")
        await self.post(
            f"/commands/{command['id']}/result",
            {
                "status": "done",
                "result": {
                    "exit_code": code,
                    "output_b64": base64.b64encode(output.encode()).decode(),
                    "truncated": False,
                    "timed_out": False,
                    "seconds": 0.1,
                },
                "error": None,
            },
        )

    async def run(self) -> None:
        while not self.stop:
            for command in await poll(self.env, self.token, wait=0):
                kind = command["kind"]
                if kind == "proc_start":
                    await self.run_proc(command)
                elif kind == "exec":
                    await self.run_exec(command)
                elif kind in ("create_env", "start_env", "stop_env", "delete_env"):
                    if command["payload"].get("recreate"):
                        self.recreated.append(command["payload"])
                    await self.post(
                        f"/commands/{command['id']}/result",
                        {"status": "done", "result": {"seconds": 1.0}, "error": None},
                    )
                elif kind == "build_template":
                    payload = command["payload"]
                    await self.post(
                        f"/commands/{command['id']}/result",
                        {
                            "status": "done",
                            "result": {
                                "image_ref": payload["tag"],
                                "image_digest": "sha256:" + "c" * 64,
                                "image_bytes": 1,
                                "seconds": 1.0,
                                "log": "ok",
                            },
                            "error": None,
                        },
                    )
                elif kind == "get_files":
                    path = command["payload"]["path"]
                    data = await asyncio.to_thread(self.local(f"/workspace/{path}").read_bytes)
                    await self.post(
                        f"/commands/{command['id']}/result",
                        {
                            "status": "done",
                            "result": {"b64": base64.b64encode(data).decode()},
                            "error": None,
                        },
                    )
            await asyncio.sleep(0.1)


@dataclass
class Rig:
    env: Env
    bridge_app: Any
    bridge_id: str
    agent: FakeAgent

    async def api(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return await self.env.client.request(
            method,
            f"/api/v1/llm_bridge{path}",
            headers=self.env.headers(write=method != "GET"),
            **kwargs,
        )


@pytest.fixture
async def rig(make_client: Any, migrated_database_url: str, tmp_path: Path) -> AsyncIterator[Rig]:
    sys.path.insert(0, str(BRIDGE_SOURCE))
    try:
        bridge_app_module = importlib.import_module("bridge.app")
        bridge_config = importlib.import_module("bridge.config")
        # 管理器本机的 claude 用管理器测试的假命令行（CI 机器上没有装 claude）：
        # 只用来查登录状态；回合在“容器”里跑（FakeAgent）
        wrapper = tmp_path / "bin-claude"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLI}" "$@"\n')
        wrapper.chmod(0o755)
        bridge_app = bridge_app_module.create_app(
            bridge_config.Settings(
                data_dir=tmp_path / "bridge", admin_token=BRIDGE_TOKEN, claude_bin=str(wrapper)
            )
        )
        async with (
            bridge_app.router.lifespan_context(bridge_app),
            open_env(make_client, migrated_database_url, **RUNNER_SETTINGS) as env,
        ):
            asgi = httpx.ASGITransport(app=bridge_app)

            async def route(request: httpx.Request) -> httpx.Response:
                if request.url.host != "bridge.test":
                    raise httpx.ConnectError("connection refused", request=request)
                response = await asgi.handle_async_request(request)
                body = await response.aread()
                return httpx.Response(response.status_code, headers=response.headers, content=body)

            # 管理器自己的命令行版本：按锁定版本（这台机器上装的 claude 可能是别的版本）
            for slot, driver in bridge_app.state.engine.drivers.items():
                pinned = {"openai": "codex", "claude": "claude", "workbuddy": "workbuddy"}[slot]

                async def version(value: str = cfg.CLI_VERSIONS[pinned]) -> str:
                    return value

                driver.version = version
            app_of(env.client).state.connectors.transport = httpx.MockTransport(route)
            enabled = await env.client.patch(
                "/api/v1/modules/llm_bridge",
                json={"enabled": True},
                headers=env.headers(write=True),
            )
            assert enabled.status_code == 200, enabled.text
            created = await env.client.post(
                "/api/v1/llm_bridge/bridges",
                json={"name": "主桥", "base_url": BRIDGE_URL, "token": BRIDGE_TOKEN},
                headers=env.headers(write=True),
            )
            assert created.status_code == 201, created.text
            token = await add_host(env)
            agent = FakeAgent(env, token, tmp_path / "container")
            task = asyncio.create_task(agent.run())
            try:
                yield Rig(env, bridge_app, created.json()["id"], agent)
            finally:
                agent.stop = True
                with contextlib.suppress(asyncio.CancelledError):
                    task.cancel()
                    await task
    finally:
        sys.path.remove(str(BRIDGE_SOURCE))
        for name in [m for m in sys.modules if m == "bridge" or m.startswith("bridge.")]:
            del sys.modules[name]


async def _until(check: Any, seconds: float = 20) -> None:
    loop = asyncio.get_running_loop()
    until = loop.time() + seconds
    while loop.time() < until:
        if await check():
            return
        await asyncio.sleep(0.2)
    raise AssertionError("等不到")


@pytest.mark.integration
async def test_remote_environment_end_to_end(rig: Rig) -> None:
    env = rig.env
    hosts = (await rig.api("GET", "/remote/targets")).json()["hosts"]
    assert len(hosts) == 1 and hosts[0]["available"] is True
    login = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/accounts/claude/login",
        json={"method": "api_key", "api_key": API_KEY},
    )
    assert login.status_code == 200, login.text

    # Codex 在远程容器里只有“可执行命令”档
    refused = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/environments",
        json={
            "platform": "codex",
            "name": "x",
            "sandbox": "read_only",
            "location": "remote",
            "new_container": {"name": "c", "host_id": hosts[0]["id"]},
        },
    )
    assert refused.status_code == 422

    create_body = {
        "request_id": "web-env-1",
        "platform": "claude",
        "name": "远程开发",
        "sandbox": "exec",
        "repo_url": "https://git.example.com/team/app.git",
        "branch": "main",
        "location": "remote",
        "new_container": {"name": "开发机", "host_id": hosts[0]["id"], "network": "models"},
    }
    made = await rig.api("POST", f"/bridges/{rig.bridge_id}/environments", json=create_body)
    assert made.status_code == 201, made.text
    environment = made.json()
    assert environment["location"] == "remote" and environment["remote_label"].endswith("开发机")
    container_id = environment["remote_container"]
    # 同一个幂等号再提交一次（例如网络断了重试）：还是那个环境，不会再建一个环境或容器
    again = await rig.api("POST", f"/bridges/{rig.bridge_id}/environments", json=create_body)
    assert again.status_code == 201 and again.json()["id"] == environment["id"]
    containers = (await rig.api("GET", f"/bridges/{rig.bridge_id}/containers")).json()
    assert [c["id"] for c in containers["containers"]] == [container_id]
    container_body = {"request_id": "web-c-1", "name": "第二台", "host_id": hosts[0]["id"]}
    first = await rig.api("POST", f"/bridges/{rig.bridge_id}/containers", json=container_body)
    second = await rig.api("POST", f"/bridges/{rig.bridge_id}/containers", json=container_body)
    assert first.status_code == 201 and second.status_code == 201, (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    removed = await rig.api("DELETE", f"/bridges/{rig.bridge_id}/containers/{first.json()['id']}")
    assert removed.status_code == 204, removed.text

    # 镜像构建好了以后，定时任务接着建容器（网络放行了仓库所在的主机）
    sync = env.sync

    async def provisioned() -> bool:
        await remote_module._housekeeping(sync)
        listed = (await rig.api("GET", f"/bridges/{rig.bridge_id}/containers")).json()
        return bool(listed["containers"][0]["state"] == "running")

    await _until(provisioned)
    app = app_of(env.client)
    async with app.state.sessionmaker() as db:
        stored = await db.scalar(
            select(RunnerEnvironment).where(RunnerEnvironment.owner_ref == container_id)
        )
        assert stored is not None and stored.owner_kind == "llm_bridge"
        assert stored.spec["network"] == "allowlist"
        assert "git.example.com" in stored.spec["domains"]
        assert "api.anthropic.com" in stored.spec["domains"]

    # 中转在后台跑（定时任务的长轮询部分）
    loop = asyncio.get_running_loop()
    relay = asyncio.create_task(
        remote_module._relay_bridge(sync, uuid.UUID(rig.bridge_id), loop.time() + 90)
    )
    try:
        instance = await new_instance(env, name="openclaw-remote")
        token = await issue_token(env, instance)
        granted = await rig.api(
            "PUT",
            f"/grants/{instance}",
            json={"enabled": True, "can_write": True, "environment_ids": [environment["id"]]},
        )
        assert granted.status_code == 200, granted.text
        dev = {"platform": "claude", "mode": "dev", "environment": environment["id"]}
        created = await call(env, token, "llm_bridge_session_create", dev)
        assert created["isError"] is False, created
        session = created["structuredContent"]["data"]["session_id"]
        prepare = next(p for p in rig.agent.started if p["argv"][:2] == ["sh", "-c"])
        assert prepare["argv"][4:] == [
            f"/workspace/sessions/{session}",
            "https://git.example.com/team/app.git",
            "main",
        ]

        sent = await call(
            env,
            token,
            "llm_bridge_send",
            {"session_id": session, "text": "你好", "request_id": "r1", "wait_seconds": 30},
        )
        assert sent["isError"] is False, sent
        data = sent["structuredContent"]["data"]
        assert data["status"] == "completed", data.get("failure")
        turn = next(p for p in rig.agent.started if p["argv"][0].endswith("/claude"))
        assert turn["cwd"] == f"/workspace/sessions/{session}"
        assert "HOME" not in turn["env"] and turn["proc_input"]["secret_env"] == {
            "ANTHROPIC_API_KEY": API_KEY
        }
        # 凭据、提问都不在工作台的数据库里
        async with app.state.sessionmaker() as db:
            payloads = [c.payload for c in await db.scalars(select(RunnerCommand))]
        dumped = json.dumps(payloads, ensure_ascii=False)
        assert API_KEY not in dumped and "你好" not in dumped

        # 浏览工作目录：经容器服务器；预览脱敏，下载原文件
        listed = await rig.api("GET", f"/bridges/{rig.bridge_id}/sessions/{session}/files")
        assert listed.status_code == 200, listed.text
        assert "README.md" in [e["name"] for e in listed.json()["entries"]]
        preview = await rig.api(
            "GET",
            f"/bridges/{rig.bridge_id}/sessions/{session}/files/content",
            params={"path": "README.md"},
        )
        assert preview.status_code == 200, preview.text
        assert (
            API_KEY not in preview.text and "repo https://git.example.com" in preview.json()["text"]
        )
        escape = await rig.api(
            "GET",
            f"/bridges/{rig.bridge_id}/sessions/{session}/files/content",
            params={"path": "../../.home/x"},
        )
        assert escape.status_code == 422
        os.symlink(rig.agent.home / ".home", rig.agent.local(f"/workspace/sessions/{session}/home"))
        (rig.agent.home / ".home").mkdir(exist_ok=True)
        (rig.agent.home / ".home" / "secret").write_text(API_KEY)
        linked = await rig.api(
            "GET",
            f"/bridges/{rig.bridge_id}/sessions/{session}/files/content",
            params={"path": "home/secret"},
        )
        assert linked.status_code in (403, 404, 422), linked.text
        assert API_KEY not in linked.text
        download = await rig.api(
            "GET",
            f"/bridges/{rig.bridge_id}/sessions/{session}/files/download",
            params={"path": "README.md"},
        )
        assert download.status_code == 200 and API_KEY.encode() in download.content

        archived = await call(env, token, "llm_bridge_session_archive", {"session_id": session})
        assert archived["isError"] is False, archived
        assert re.match(r"^bs_", session)
    finally:
        relay.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay

    # 更新镜像：重建好了才改记下的版本；中间显示“正在更新”，版本仍算不一致
    async with app.state.sessionmaker() as db:
        row = await db.get(LlmBridgeRemoteContainer, uuid.UUID(container_id))
        assert row is not None
        row.cli_versions = {**row.cli_versions, "claude": "2.1.300"}
        await db.commit()
    current = (await rig.api("GET", f"/bridges/{rig.bridge_id}/containers")).json()["containers"][0]
    assert current["versions_ok"] is False
    updated = await rig.api(
        "POST",
        f"/bridges/{rig.bridge_id}/containers/{container_id}/actions",
        json={"action": "update_image", "version": current["version"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["versions_ok"] is False and updated.json()["updating_image"] is True

    async def updated_image() -> bool:
        await remote_module._housekeeping(sync)
        item = (await rig.api("GET", f"/bridges/{rig.bridge_id}/containers")).json()["containers"][
            0
        ]
        return bool(item["versions_ok"] and not item["updating_image"])

    await _until(updated_image)
    assert any(p.get("recreate") for p in rig.agent.recreated)

    # 还有环境用这个容器时不能删；删掉环境之后可以
    containers = (await rig.api("GET", f"/bridges/{rig.bridge_id}/containers")).json()
    version = containers["containers"][0]["version"]
    blocked = await rig.api("DELETE", f"/bridges/{rig.bridge_id}/containers/{container_id}")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "CONTAINER_IN_USE"
    removed = await rig.api("DELETE", f"/bridges/{rig.bridge_id}/environments/{environment['id']}")
    assert removed.status_code == 204, removed.text
    deleted = await rig.api("DELETE", f"/bridges/{rig.bridge_id}/containers/{container_id}")
    assert deleted.status_code == 204, deleted.text
    assert version >= 1


# ---- 派生镜像补装基础命令（人类 2026-11-18：“可以 给我安装上”）----


def _tools_env(tmp_path: Path, drop: set[str], manager: str | None, fixes: bool) -> Path:
    """一个只有指定命令的 PATH：drop 里的命令故意不放；manager 是假的包管理器（记下参数，
    fixes=True 时把缺的命令补上）；rm 也是假的，免得测试动到本机的 /var/lib/apt/lists。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    for name in cfg.BASE_TOOLS:
        real = shutil.which(name)
        if name not in drop and real:
            (bin_dir / name).symlink_to(real)
    log = tmp_path / "calls.log"
    (bin_dir / "rm").write_text(f'#!/bin/sh\necho "rm $*" >> "{log}"\n')
    if manager is not None:
        restore = "".join(
            f'"{shutil.which("ln")}" -sf "{shutil.which(name)}" "{bin_dir}/{name}"\n'
            for name in sorted(drop)
        )
        (bin_dir / manager).write_text(
            f'#!/bin/sh\necho "{manager} $*" >> "{log}"\n'
            + (f'case "$*" in *install*) {restore};; esac\n' if fixes else "")
        )
    for path in bin_dir.iterdir():
        if not path.is_symlink():
            path.chmod(0o755)
    return bin_dir


def _run_tools(bin_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - 跑工作台自己写死的脚本
        ["/bin/sh", "-c", cfg.TOOLS_SCRIPT],
        env={"PATH": str(bin_dir)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_derived_images_install_only_the_missing_base_tools(tmp_path: Path) -> None:
    steps = cfg.derived_steps("myworkspace/tpl-0123:1")
    assert steps[0] == "USER root" and steps[1] == f"RUN {cfg.TOOLS_SCRIPT}"
    assert steps[2].startswith("COPY --from=myworkspace/tpl-0123:1 ")

    # 都有：什么也不装
    done = _run_tools(_tools_env(tmp_path / "all", set(), "apt-get", fixes=True))
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "all" / "calls.log").exists()

    # 缺 timeout 和 setsid：只装 coreutils、util-linux，装完再查一遍
    done = _run_tools(_tools_env(tmp_path / "apt", {"timeout", "setsid"}, "apt-get", fixes=True))
    assert done.returncode == 0, done.stderr
    calls = (tmp_path / "apt" / "calls.log").read_text().splitlines()
    assert calls[:2] == [
        "apt-get update",
        "apt-get install -y --no-install-recommends coreutils util-linux",
    ]
    assert len(calls) == 3 and calls[2].startswith("rm -rf /var/lib/apt/lists/")

    # dnf 系：缺 find 只装 findutils（不碰 coreutils，免得和 coreutils-single 冲突）
    done = _run_tools(_tools_env(tmp_path / "dnf", {"find"}, "microdnf", fixes=True))
    assert done.returncode == 0, done.stderr
    calls = (tmp_path / "dnf" / "calls.log").read_text().splitlines()
    assert calls == ["microdnf install -y findutils", "microdnf clean all"]

    # 没有包管理器：构建失败，写明缺什么
    done = _run_tools(_tools_env(tmp_path / "none", {"timeout"}, None, fixes=False))
    assert done.returncode == 1 and "没有能补装的包管理器" in done.stderr
    assert "timeout" in done.stdout

    # 包管理器“装了”但还是没有：也失败
    done = _run_tools(_tools_env(tmp_path / "broken", {"realpath"}, "apt-get", fixes=False))
    assert done.returncode == 1 and "补装之后还缺：realpath" in done.stderr
