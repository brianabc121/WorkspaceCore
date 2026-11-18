"""远程开发容器（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 21 节）。

测试里没有工作台和容器服务器：FakeWorkspace 像工作台一样取走远程操作，把托管进程在本机起起来
（容器里的 /workspace 换成临时目录，命令行换成 tests/fakes 里的假程序），再把输出、结束推回来。
可以走真实的管理接口（HttpLink），也可以直接对着 RemoteHub（HubLink，驱动级的测试用）。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import shutil
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from bridge import remote
from bridge.app import create_app
from bridge.config import Settings
from bridge.drivers.base import DriverError, RemoteTarget, SessionSpec
from bridge.drivers.codex import CodexDriver
from bridge.remote import RemoteHub, RemoteLogins
from bridge.store import Store
from tests.conftest import ADMIN, Bridge

FAKES = Path(__file__).parent / "fakes"

FAKE_CODEX_EXEC = r"""
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_ARGV_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "key": bool(os.environ.get("CODEX_API_KEY")),
                          "cwd": os.getcwd()}) + "\n")
assert args[0] == "exec" and "--json" in args
assert "--dangerously-bypass-approvals-and-sandbox" in args and args[-1] == "-"
prompt = sys.stdin.read()
thread = args[args.index("resume") + 1] if "resume" in args else "thread-1"

def emit(event):
    print(json.dumps(event), flush=True)

emit({"type": "thread.started", "thread_id": thread})
emit({"type": "turn.started"})
if prompt == "quota":
    emit({"type": "error", "message": "Reconnecting... 1/5"})
    emit({"type": "turn.failed",
          "error": {"message": "You've hit your usage limit. Try again in 3h 20m."}})
    sys.exit(1)
if prompt == "crash":
    sys.stderr.write("thread panicked\n")
    sys.exit(2)
cmd = {"id": "item_1", "type": "command_execution", "command": "ls",
       "aggregated_output": "", "exit_code": None, "status": "in_progress"}
emit({"type": "item.started", "item": cmd})
emit({"type": "item.updated", "item": {**cmd, "aggregated_output": "a\n"}})
emit({"type": "item.completed",
      "item": {**cmd, "aggregated_output": "a\nb\n", "exit_code": 0, "status": "completed"}})
emit({"type": "item.completed", "item": {"id": "item_2", "type": "file_change",
      "changes": [{"path": "src/x.py", "kind": "update"}], "status": "completed"}})
emit({"type": "item.completed", "item": {"id": "item_3", "type": "agent_message",
      "text": "done: " + prompt}})
emit({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2,
      "output_tokens": 5, "reasoning_output_tokens": 1}})
"""


def _wrapper(tmp_path: Path, name: str, script: Path) -> str:
    path = tmp_path / f"bin-{name}"
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    path.chmod(0o755)
    return str(path)


async def _until(check: Callable[[], Any], limit: float = 15.0) -> None:
    loop = asyncio.get_running_loop()
    until = loop.time() + limit
    while not check():
        assert loop.time() < until, "等太久了"
        await asyncio.sleep(0.05)


# ---- 两种接法：走管理接口，或直接对着 RemoteHub ----
class HttpLink:
    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge

    async def take(self, wait: float) -> list[dict[str, Any]]:
        response = await self.bridge.admin("GET", "/admin/remote/ops", params={"wait": wait})
        assert response.status_code == 200, response.text
        ops: list[dict[str, Any]] = response.json()["ops"]
        return ops

    async def result(self, op_id: str, body: dict[str, Any]) -> None:
        response = await self.bridge.admin("POST", f"/admin/remote/ops/{op_id}/result", json=body)
        assert response.status_code in (204, 404), response.text

    async def output(self, proc_id: str, offset: int, data: bytes) -> int | None:
        body = {"offset": offset, "data_b64": base64.b64encode(data).decode()}
        response = await self.bridge.admin(
            "POST", f"/admin/remote/procs/{proc_id}/output", json=body
        )
        if response.status_code == 404:
            return None
        acked: int = response.json()["acked"]
        return acked

    async def exit(self, proc_id: str, body: dict[str, Any]) -> None:
        await self.bridge.admin("POST", f"/admin/remote/procs/{proc_id}/exit", json=body)


class HubLink:
    def __init__(self, hub: RemoteHub) -> None:
        self.hub = hub

    async def take(self, wait: float) -> list[dict[str, Any]]:
        return await self.hub.take(wait, 20)

    async def result(self, op_id: str, body: dict[str, Any]) -> None:
        self.hub.result(op_id, body["ok"], body.get("data"), body.get("error"))

    async def output(self, proc_id: str, offset: int, data: bytes) -> int | None:
        try:
            return self.hub.output(proc_id, offset, data)
        except KeyError:
            return None

    async def exit(self, proc_id: str, body: dict[str, Any]) -> None:
        self.hub.exit(proc_id, body)


class FakeWorkspace:
    """像工作台和 runner-agent 一样做远程操作：容器里的 /workspace 是 root 这个临时目录。"""

    def __init__(self, link: Any, root: Path, programs: dict[str, str]) -> None:
        self.link = link
        self.root = root
        self.programs = programs
        self.versions: dict[str, str] = {}
        self.ops: list[dict[str, Any]] = []
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self.extra_env: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for task in list(self._tasks):
            task.cancel()
        for proc in self.procs.values():
            if proc.returncode is None:
                proc.kill()

    def path(self, inside: str) -> Path:
        assert inside == "/workspace" or inside.startswith("/workspace/"), inside
        return self.root / inside.removeprefix("/workspace").lstrip("/")

    def started(self, purpose: str) -> list[dict[str, Any]]:
        return [
            o["args"]
            for o in self.ops
            if o["kind"] == "start_proc" and o["args"]["purpose"] == purpose
        ]

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _loop(self) -> None:
        while True:
            for op in await self.link.take(0.3):
                self.ops.append(op)
                self._spawn(self._handle(op))

    async def _handle(self, op: dict[str, Any]) -> None:
        try:
            data = await self._do(op["kind"], op["args"])
        except Exception as error:  # 模拟工作台回报失败
            await self.link.result(
                op["id"], {"ok": False, "error": {"code": "UNAVAILABLE", "message": str(error)}}
            )
            return
        await self.link.result(op["id"], {"ok": True, "data": data})

    async def _do(self, kind: str, args: dict[str, Any]) -> dict[str, Any]:
        if kind == "start_proc":
            return await self._start(args)
        if kind == "signal_proc":
            proc = self.procs.get(args["proc_id"])
            if proc is not None and proc.returncode is None:
                proc.kill() if args["signal"] == "KILL" else proc.terminate()
            return {}
        if kind == "input_proc":
            proc = self.procs[args["proc_id"]]
            assert proc.stdin is not None
            proc.stdin.write(args["data"].encode())
            await proc.stdin.drain()
            return {}
        if kind == "put_files":
            base = self.path(f"/workspace/sessions/{args['session_id']}")
            for item in args["files"]:
                target = base / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(item["b64"]))
            return {"written": len(args["files"])}
        if kind == "helper":
            name = args["name"]
            if name == "versions":
                return {"exit_code": 0, "output": "", "versions": self.versions}
            if name == "remove_session":
                shutil.rmtree(self.path(f"/workspace/sessions/{args['session_id']}"), True)
                return {"exit_code": 0, "output": ""}
            if name == "transcript_exists":
                config = {"claude": ".claude", "workbuddy": ".codebuddy"}[args["platform"]]
                found = list(
                    (self.root / ".home" / config).glob(f"projects/*/{args['native_id']}.jsonl")
                )
                return {"exit_code": 0, "output": "", "exists": bool(found)}
        raise RuntimeError(f"不支持的远程操作 {kind}")

    async def _start(self, args: dict[str, Any]) -> dict[str, Any]:
        proc_id, cwd = args["proc_id"], self.path(args["cwd"])
        cwd.mkdir(parents=True, exist_ok=True)
        if args["purpose"] == "prepare":
            failing = "fail" in str(args.get("repo_url") or "")
            self._spawn(
                self.link.exit(
                    proc_id,
                    {
                        "exit_code": 1 if failing else 0,
                        "state": "exited",
                        "stderr_tail": "fatal: repository not found" if failing else "",
                    },
                )
            )
            return {"run_id": str(uuid.uuid4())}
        argv = list(args["argv"])
        argv[0] = self.programs.get(argv[0], argv[0])
        env = {
            k: (str(self.path(v)) if v.startswith("/workspace") else v)
            for k, v in args["env"].items()
            if k != "PATH"
        }
        env.update(PATH=os.environ.get("PATH", ""), **args.get("secret_env", {}), **self.extra_env)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        proc.stdin.write(base64.b64decode(args["stdin_b64"]))
        await proc.stdin.drain()
        if not args.get("interactive"):
            proc.stdin.close()
        self.procs[proc_id] = proc
        self._spawn(self._pump(proc_id, proc))
        return {"run_id": str(uuid.uuid4())}

    async def _pump(self, proc_id: str, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None and proc.stderr is not None
        offset = 0
        while chunk := await proc.stdout.read(4096):
            acked = await self.link.output(proc_id, offset, chunk)
            if acked is None:  # 管理器不认这个进程了：停掉它
                proc.kill()
                return
            offset = acked
        code = await proc.wait()
        tail = (await proc.stderr.read()).decode(errors="replace")[-2000:]
        await self.link.exit(proc_id, {"exit_code": code, "state": "exited", "stderr_tail": tail})


# ---- 管理器级：走管理接口 ----
@pytest.fixture
async def remote_bridge(tmp_path: Path) -> AsyncIterator[tuple[Bridge, FakeWorkspace]]:
    claude = _wrapper(tmp_path, "claude", FAKES / "fake_cli.py")
    settings = Settings(
        data_dir=tmp_path / "data",
        admin_token=ADMIN,
        enable_fake=True,
        resume_margin_seconds=0,
        claude_bin=claude,
        codebuddy_bin=claude,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bridge") as client:
            app.state.engine.store.set_setting("min_start_interval_seconds", 0)
            bridge = Bridge(client, app.state.engine)
            workspace = FakeWorkspace(
                HttpLink(bridge), tmp_path / "container", {"claude": claude, "codebuddy": claude}
            )
            workspace.start()
            try:
                yield bridge, workspace
            finally:
                await workspace.stop()


async def _remote_env(bridge: Bridge, **extra: Any) -> str:
    body = {
        "platform": "claude",
        "mode": "dev",
        "name": "远程",
        "sandbox": "exec",
        "location": "remote",
        "remote_container": "c-1",
        "remote_label": "容器服务器 A · 远程容器 1",
        **extra,
    }
    response = await bridge.admin("POST", "/admin/environments", json=body)
    assert response.status_code == 200, response.text
    env_id: str = response.json()["id"]
    return env_id


async def _login_claude_key(bridge: Bridge) -> None:
    response = await bridge.admin(
        "POST", "/admin/accounts/claude/login", json={"method": "api_key", "api_key": "sk-test-key"}
    )
    assert response.status_code == 200, response.text


async def test_remote_endpoints_need_the_admin_token(bridge: Bridge) -> None:
    for method, path in (
        ("GET", "/admin/remote/ops"),
        ("POST", "/admin/remote/ops/ro_x/result"),
        ("POST", "/admin/remote/procs/rp_x/output"),
        ("POST", "/admin/remote/procs/rp_x/exit"),
        ("GET", "/admin/remote/containers/c-1"),
        ("POST", "/admin/remote/containers/c-1/login/claude"),
    ):
        response = await bridge.client.request(
            method, path, headers={"Authorization": "Bearer wrong"}, json={}
        )
        assert response.status_code == 401, (path, response.text)
    # 不认识的操作和进程：404（工作台据此停掉进程）
    assert (
        await bridge.admin("POST", "/admin/remote/ops/ro_x/result", json={"ok": True})
    ).status_code == 404
    body = {"offset": 0, "data_b64": base64.b64encode(b"x").decode()}
    assert (
        await bridge.admin("POST", "/admin/remote/procs/rp_x/output", json=body)
    ).status_code == 404
    assert (
        await bridge.admin("POST", "/admin/remote/procs/rp_x/exit", json={"exit_code": 0})
    ).status_code == 404


async def test_remote_environment_validation(bridge: Bridge) -> None:
    base = {"platform": "claude", "mode": "dev", "name": "x", "sandbox": "exec"}
    response = await bridge.admin(
        "POST", "/admin/environments", json={**base, "location": "remote"}
    )
    assert response.status_code == 422 and "remote_container" in response.text
    # 远程 Codex 只有“可以执行命令”这一档（V2）
    response = await bridge.admin(
        "POST",
        "/admin/environments",
        json={
            **base,
            "platform": "codex",
            "sandbox": "workspace_write",
            "location": "remote",
            "remote_container": "c-1",
        },
    )
    assert response.status_code == 422 and "可以执行命令" in response.text
    env_id = await _remote_env(bridge)
    listed = (await bridge.admin("GET", "/admin/environments")).json()["environments"]
    env = next(e for e in listed if e["id"] == env_id)
    assert env["location"] == "remote" and env["remote_container"] == "c-1"
    assert env["remote_label"] == "容器服务器 A · 远程容器 1"
    local = next(e for e in listed if e["id"] != env_id) if len(listed) > 1 else None
    assert local is None or local["location"] == "local"


async def test_remote_claude_session_end_to_end(
    remote_bridge: tuple[Bridge, FakeWorkspace],
) -> None:
    bridge, workspace = remote_bridge
    await _login_claude_key(bridge)
    env_id = await _remote_env(bridge, repo_url="https://example.com/repo.git", branch="main")
    container = await bridge.admin("GET", "/admin/remote/containers/c-1")
    assert container.json()["platforms"]["claude"]["state"] == "injected"

    made = await bridge.tool(
        "oc-1", "llm_session_create", {"platform": "claude", "mode": "dev", "environment": env_id}
    )
    session_id = made["session_id"]
    assert made["remote"] == {"container": "c-1", "path": f"/workspace/sessions/{session_id}"}
    prepare = workspace.started("prepare")[0]
    assert prepare["repo_url"] == "https://example.com/repo.git" and prepare["branch"] == "main"
    assert prepare["cwd"] == f"/workspace/sessions/{session_id}"

    first = await bridge.tool(
        "oc-1",
        "llm_send",
        {"session_id": session_id, "text": "你好", "request_id": "r1", "wait_seconds": 20},
    )
    assert first["status"] == "completed", first
    turn = workspace.started("turn")[0]
    assert turn["argv"][0] == "claude" and "--session-id" in turn["argv"]
    assert "--permission-mode" in turn["argv"] and "bypassPermissions" in turn["argv"]
    assert "--settings" not in turn["argv"]  # 远程会话不带压缩钩子
    assert turn["cwd"] == f"/workspace/sessions/{session_id}"
    assert turn["env"]["HOME"] == "/workspace/.home"
    assert turn["env"]["CLAUDE_CONFIG_DIR"] == "/workspace/.home/.claude"
    assert turn["env"]["PATH"].startswith("/opt/mw/cli/bin")
    assert turn["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert turn["secret_env"] == {"ANTHROPIC_API_KEY": "sk-test-key"}
    assert "ANTHROPIC_API_KEY" not in turn["env"]
    # 凭据和提问不进库
    stored = bridge.engine.store._all("SELECT op_args FROM remote_ops")
    assert "sk-test-key" not in json.dumps(stored, ensure_ascii=False)
    assert "你好" not in json.dumps(stored, ensure_ascii=False)

    second = await bridge.tool(
        "oc-1",
        "llm_send",
        {"session_id": session_id, "text": "再来", "request_id": "r2", "wait_seconds": 20},
    )
    assert second["status"] == "completed", second
    assert "--resume" in workspace.started("turn")[1]["argv"]  # 容器里已经有会话记录：续接

    # 文件浏览由工作台做；会话列表里标出远程目录
    files = await bridge.admin("GET", f"/admin/sessions/{session_id}/files")
    assert files.status_code == 409 and files.json()["error"]["code"] == "REMOTE_SESSION"
    sessions = await bridge.admin("GET", f"/admin/environments/{env_id}/sessions")
    row = sessions.json()["sessions"][0]
    assert row["browsable"] is True and row["remote"]["container"] == "c-1"

    archived = await bridge.tool("oc-1", "llm_session_archive", {"session_id": session_id})
    assert archived["status"] == "archived"
    await _until(lambda: not workspace.path(f"/workspace/sessions/{session_id}").exists())


async def test_remote_prepare_failure_and_login_required(
    remote_bridge: tuple[Bridge, FakeWorkspace],
) -> None:
    bridge, _workspace = remote_bridge
    await _login_claude_key(bridge)
    env_id = await _remote_env(bridge, repo_url="https://example.com/fail.git")
    made = await bridge.tool(
        "oc-1", "llm_session_create", {"platform": "claude", "mode": "dev", "environment": env_id}
    )
    assert made["error"]["code"] == "UNAVAILABLE"
    assert "repository not found" in made["error"]["message"]

    # 管理器退出登录后（会刷新的登录不下发）：要先在容器里登录
    await bridge.admin("POST", "/admin/accounts/claude/logout")
    state = (await bridge.admin("GET", "/admin/remote/containers/c-1")).json()
    assert state["platforms"]["claude"]["state"] == "needs_login"
    assert state["platforms"]["claude"]["can_login"] is True
    codebuddy = await bridge.admin(
        "POST", "/admin/remote/containers/c-1/login/workbuddy", json={"action": "start"}
    )
    assert codebuddy.status_code == 422 and "API 密钥" in codebuddy.text


async def test_remote_version_mismatch_fails_the_turn(
    remote_bridge: tuple[Bridge, FakeWorkspace],
) -> None:
    bridge, workspace = remote_bridge
    await _login_claude_key(bridge)
    env_id = await _remote_env(bridge)
    made = await bridge.tool(
        "oc-1", "llm_session_create", {"platform": "claude", "mode": "dev", "environment": env_id}
    )
    bridge.engine._local_versions["claude"] = "2.1.323"
    workspace.versions = {"claude": "2.1.300"}
    result = await bridge.tool(
        "oc-1",
        "llm_send",
        {"session_id": made["session_id"], "text": "你好", "request_id": "r1", "wait_seconds": 20},
    )
    assert result["status"] == "failed"
    assert "更新镜像" in json.dumps(result, ensure_ascii=False)
    assert not workspace.started("turn")


async def test_remote_jobs_run_in_the_container(
    remote_bridge: tuple[Bridge, FakeWorkspace],
) -> None:
    bridge, workspace = remote_bridge
    await _login_claude_key(bridge)
    env_id = await _remote_env(bridge)
    made = await bridge.tool(
        "oc-1", "llm_session_create", {"platform": "claude", "mode": "dev", "environment": env_id}
    )
    session_id = made["session_id"]
    response = await bridge.admin(
        "POST",
        f"/admin/sessions/{session_id}/jobs",
        json={"command": "echo hello; echo oops >&2", "name": "试一下", "follow_up": False},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job"]["id"]
    job = workspace.started("job")[0]
    assert job["argv"][:2] == ["bash", "-c"] and job["argv"][2].endswith(
        "echo hello; echo oops >&2"
    )
    # 和本地作业一样只给白名单里的环境变量（T-0292 / T-0293）：没有管理令牌、回合令牌和平台凭据
    assert set(job["env"]) <= {"HOME", "PATH", "LANG", "LLM_TASK_JOB_ID"}
    assert not job["secret_env"]
    assert ADMIN not in json.dumps(job) and "sk-test-key" not in json.dumps(job)

    async def finished() -> bool:
        row = bridge.engine.store.get_job(job_id)
        return row is not None and row["status"] != "running"

    loop = asyncio.get_running_loop()
    until = loop.time() + 15
    while not await finished():
        assert loop.time() < until
        await asyncio.sleep(0.05)
    log = (await bridge.admin("GET", f"/admin/jobs/{job_id}/log")).json()
    assert "hello" in log["text"] and "oops" in log["text"]
    assert bridge.engine.store.get_job(job_id)["status"] == "succeeded"

    # 会话里的 llm-task 在远程会话里还没接上（方案第 14 节）
    session = bridge.engine.store.get_session(session_id)
    assert session is not None
    from bridge.errors import BridgeError

    with pytest.raises(BridgeError) as caught:
        await bridge.engine.longrun.start_job(
            session, origin="session", caller_id="x", by="model", command="true"
        )
    assert caught.value.code == "NOT_SUPPORTED" and "llm-task" in caught.value.message
    # 远程会话不写回合令牌：容器里连不到管理器，也就没有能被读走的令牌
    assert bridge.engine.longrun.open_token(session, "turn-x") is None

    # 档位照样管着：不是“可以执行命令”档的远程环境不能开作业
    write_env = await _remote_env(bridge, name="远程-可写", sandbox="workspace_write")
    made = await bridge.tool(
        "oc-1",
        "llm_session_create",
        {"platform": "claude", "mode": "dev", "environment": write_env},
    )
    response = await bridge.admin(
        "POST", f"/admin/sessions/{made['session_id']}/jobs", json={"command": "true"}
    )
    assert response.status_code == 422 and "可以执行命令" in response.text
    assert len(workspace.started("job")) == 1


# ---- 驱动级：直接对着 RemoteHub ----
@pytest.fixture
async def hub(tmp_path: Path) -> AsyncIterator[RemoteHub]:
    store = Store(tmp_path / "hub.sqlite3")
    hub = RemoteHub(store, result_timeout=10)
    hub.startup()
    yield hub
    await hub.shutdown()
    store.close()


async def test_hub_output_gaps_duplicates_and_timeout(hub: RemoteHub) -> None:
    started = asyncio.create_task(
        hub.spawn(
            "c-1",
            purpose="turn",
            platform="claude",
            session_id="bs_1",
            argv=["claude"],
            cwd="/workspace/sessions/bs_1",
            env={},
            secret_env={"ANTHROPIC_API_KEY": "k"},
            stdin=b"hi",
        )
    )
    ops = await hub.take(5, 20)
    assert ops[0]["kind"] == "start_proc" and ops[0]["args"]["secret_env"] == {
        "ANTHROPIC_API_KEY": "k"
    }
    proc_id = ops[0]["args"]["proc_id"]
    assert hub.result(ops[0]["id"], True, {"run_id": "run-1"}, None)
    assert hub.result(ops[0]["id"], True, {}, None)  # 重复回报：认，但不再处理
    proc = await started
    assert proc.run_id == "run-1"
    assert hub.output(proc_id, 0, b"ab") == 2
    assert hub.output(proc_id, 1, b"bc") == 3  # 和已确认的重叠：只收新的那部分
    with pytest.raises(remote.Gap) as gap:
        hub.output(proc_id, 9, b"x")
    assert gap.value.acked == 3
    assert hub.exit(proc_id, {"exit_code": 0, "state": "exited", "stderr_tail": "warn"})
    assert await proc.stdout.read() == b"abc" and await proc.wait() == 0
    assert hub.exit(proc_id, {"exit_code": 0})  # 刚结束过的重复回报
    with pytest.raises(KeyError):
        hub.output(proc_id, 3, b"late")
    # 没人取走：超时报“不可用”，操作作废
    hub.result_timeout = 0.2
    with pytest.raises(DriverError) as caught:
        await hub.helper("c-1", "versions")
    assert caught.value.kind == "unavailable"
    assert await hub.take(0, 20) == []


async def test_hub_resends_unanswered_ops_and_restart_forgets_them(
    hub: RemoteHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote, "RESEND_SECONDS", 0.0)
    task = asyncio.create_task(hub.helper("c-1", "versions"))
    first = await hub.take(5, 20)
    again = await hub.take(5, 20)
    assert first[0]["id"] == again[0]["id"]  # 取走了没回结果：再给一次
    hub.result(first[0]["id"], False, None, {"code": "VALIDATION_FAILED", "message": "坏了"})
    with pytest.raises(DriverError) as caught:
        await task
    assert caught.value.kind == "invalid" and "坏了" in caught.value.message
    # 管理器重启：没做完的操作作废，运行中的远程进程记为丢失
    hub.store.create_remote_op("ro_old", "helper", "c-1", {})
    hub.store.create_remote_proc(id="rp_old", container="c-1", purpose="turn")
    hub.store.update_remote_proc("rp_old", state="running")
    again_hub = RemoteHub(hub.store)
    again_hub.startup()
    assert hub.store.get_remote_op("ro_old")["status"] == "failed"
    assert hub.store.get_remote_proc("rp_old")["state"] == "lost"


@pytest.fixture
async def codex_remote(
    tmp_path: Path, hub: RemoteHub
) -> AsyncIterator[tuple[CodexDriver, FakeWorkspace, Path]]:
    script = tmp_path / "fake_codex_exec.py"
    script.write_text(FAKE_CODEX_EXEC)
    driver = CodexDriver("codex-unused", tmp_path / "home", secrets_dir=tmp_path / "secrets")
    driver.remote = hub
    log = tmp_path / "argv.jsonl"
    workspace = FakeWorkspace(
        HubLink(hub), tmp_path / "container", {"codex": _wrapper(tmp_path, "codex", script)}
    )
    workspace.extra_env = {"FAKE_ARGV_LOG": str(log)}
    workspace.start()
    yield driver, workspace, log
    await workspace.stop()


def _remote_spec(**fields: Any) -> SessionSpec:
    target = RemoteTarget("c-1", "/workspace/sessions/bs_1", "bs_1", 60)
    return SessionSpec(
        **{
            "mode": "dev",
            "model": "gpt-5",
            "workdir": Path("/nonexistent"),
            "sandbox": "exec",
            "remote": target,
            **fields,
        }
    )


class Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]


async def test_codex_exec_replay(codex_remote: tuple[CodexDriver, FakeWorkspace, Path]) -> None:
    driver, _workspace, log = codex_remote
    assert await driver.create(_remote_spec()) == ""  # 原生会话号等第一回合
    with pytest.raises(DriverError) as caught:
        await driver.create(_remote_spec(sandbox="workspace_write"))
    assert caught.value.kind == "invalid"

    sink = Sink()
    outcome = await driver.run_turn(_remote_spec(effort="high", pinned="固定资料"), "看看", sink)
    assert outcome.text == "done: 看看"
    assert outcome.commands == ["ls"] and outcome.files == ["src/x.py"]
    assert outcome.usage == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "cached_input_tokens": 2,
    }
    assert ("native_session", {"native_id": "thread-1"}) in sink.events
    assert ("output", {"text": "a\n"}) in sink.events and ("output", {"text": "b\n"}) in sink.events
    assert {"command_started", "step_done", "command", "files", "message"} <= set(sink.kinds())
    call = json.loads(log.read_text().splitlines()[0])
    args = call["args"]
    assert args[:2] == ["exec", "--json"] and "resume" not in args
    assert args[args.index("-m") + 1] == "gpt-5"
    assert 'model_reasoning_effort="high"' in args
    assert f"developer_instructions={json.dumps('固定资料')}" in args
    assert call["cwd"].endswith("sessions/bs_1") and call["key"] is False

    # 续接：原生会话号放在 resume 后面；API 密钥随回合注入
    driver._save_key("sk-codex")
    await driver.run_turn(_remote_spec(native_id="thread-1"), "接着", Sink())
    call = json.loads(log.read_text().splitlines()[1])
    assert call["args"][call["args"].index("resume") + 1] == "thread-1" and call["key"] is True
    assert driver.remote_credentials() == {"CODEX_API_KEY": "sk-codex"}
    assert await driver.steer(_remote_spec(native_id="thread-1"), "插话") is False


async def test_codex_exec_failures(codex_remote: tuple[CodexDriver, FakeWorkspace, Path]) -> None:
    driver, _, _ = codex_remote
    sink = Sink()
    with pytest.raises(DriverError) as caught:
        await driver.run_turn(_remote_spec(), "quota", sink)
    assert caught.value.kind == "rate_limited" and caught.value.quota
    assert ("notice", {"text": "Reconnecting... 1/5"}) in sink.events
    with pytest.raises(DriverError) as caught:
        await driver.run_turn(_remote_spec(), "crash", Sink())
    assert "thread panicked" in caught.value.message
    with pytest.raises(DriverError) as caught:
        await driver.compact(_remote_spec(native_id="thread-1"), "", Sink())
    assert caught.value.kind == "not_supported"


async def test_remote_claude_login_and_account_check(tmp_path: Path, hub: RemoteHub) -> None:
    claude = _wrapper(tmp_path, "claude", FAKES / "fake_cli.py")
    workspace = FakeWorkspace(HubLink(hub), tmp_path / "container", {"claude": claude})
    workspace.start()
    registered = hashlib.sha256(b"sub@example.com").hexdigest()

    async def same_account(slot: str) -> tuple[str | None, str | None]:
        return registered, "s***@example.com"

    try:
        logins = RemoteLogins(hub, hub.store, same_account)
        challenge = await logins.start("c-1", "claude")
        assert challenge.state == "pending" and challenge.needs_code
        assert challenge.verification_url and challenge.verification_url.startswith("https://")
        login = workspace.started("login")[0]
        assert login["argv"] == ["claude", "auth", "login", "--claudeai"] and login["interactive"]
        assert login["secret_env"] == {} and login["cwd"] == "/workspace/.home"
        await logins.submit_code("c-1", "claude", "good#s1")
        await _until(lambda: (logins.state("c-1", "claude") or challenge).state != "pending")
        final = logins.state("c-1", "claude")
        assert final is not None and final.state == "done", (final.detail, final.log)
        row = hub.store.get_remote_login("c-1", "claude")
        assert row is not None and row["state"] == "ready" and row["identity_hash"] == registered

        # 容器里登录的是另一个账号：不一致
        other = RemoteLogins(hub, hub.store, lambda slot: _other())
        await other.record("c-1", "claude", "sub@example.com", "web")
        assert hub.store.get_remote_login("c-1", "claude")["state"] == "mismatch"
        # 官方程序不报账号（Codex）：算登录好了，但提醒确认
        await other.record("c-1", "openai", None, "chatgpt")
        row = hub.store.get_remote_login("c-1", "openai")
        assert row["state"] == "ready" and "确认" in row["detail"]
    finally:
        await workspace.stop()


async def _other() -> tuple[str | None, str | None]:
    return hashlib.sha256(b"someone@else.com").hexdigest(), "s***@else.com"


def test_workdir_round_trip() -> None:
    workdir = remote.remote_workdir("c-1", "bs_1")
    assert workdir == "remote:c-1:/workspace/sessions/bs_1"
    assert remote.parse_workdir(workdir) == ("c-1", "/workspace/sessions/bs_1")
    assert remote.parse_workdir("/data/work/bs_1") is None
    assert remote.parse_workdir("remote:c-1:/etc") is None
    assert remote.parse_version("codex-cli 0.169.0") == "0.169.0"
    assert remote.parse_version("2.1.323 (Claude Code)") == "2.1.323"


async def test_remote_login_through_the_admin_api(
    remote_bridge: tuple[Bridge, FakeWorkspace],
) -> None:
    bridge, workspace = remote_bridge
    state = (await bridge.admin("GET", "/admin/remote/containers/c-1")).json()
    assert state["platforms"]["claude"]["state"] == "needs_login"
    started = await bridge.admin(
        "POST", "/admin/remote/containers/c-1/login/claude", json={"action": "start"}
    )
    assert started.status_code == 200, started.text
    login = started.json()["login"]
    assert login["state"] == "pending" and login["needs_code"] and login["verification_url"]
    sent = await bridge.admin(
        "POST",
        "/admin/remote/containers/c-1/login/claude",
        json={"action": "code", "code": "good#s1"},
    )
    assert sent.status_code == 200, sent.text

    async def done() -> bool:
        current = await bridge.admin("GET", "/admin/remote/containers/c-1/login/claude")
        return bool(current.json()["login"]["state"] == "done")

    loop = asyncio.get_running_loop()
    until = loop.time() + 15
    while not await done():
        assert loop.time() < until
        await asyncio.sleep(0.05)
    state = (await bridge.admin("GET", "/admin/remote/containers/c-1")).json()
    assert state["platforms"]["claude"]["state"] == "ready"
    assert state["platforms"]["claude"]["identity"]  # 打码后的账号
    status = workspace.started("login")[1]
    assert status["argv"] == ["claude", "auth", "status"]
    # 容器删掉了：忘掉容器里的登录
    assert (await bridge.admin("DELETE", "/admin/remote/containers/c-1")).status_code == 200
    state = (await bridge.admin("GET", "/admin/remote/containers/c-1")).json()
    assert state["platforms"]["claude"]["state"] == "needs_login"


def test_remote_arguments_for_claude_and_codebuddy(tmp_path: Path) -> None:
    from bridge.drivers.cli_stream import CliStreamDriver, claude_profile, codebuddy_profile

    target = RemoteTarget("c-1", "/workspace/sessions/bs_1", "bs_1")
    spec = SessionSpec(
        mode="dev",
        model=None,
        workdir=Path("/nonexistent"),
        sandbox="exec",
        native_id="n-1",
        hooks=True,
        pinned="固定资料",
        remote=target,
    )
    claude = CliStreamDriver(claude_profile("claude"), tmp_path / "c", tmp_path / "s")
    claude.hook_script = tmp_path / "hook.py"
    args = claude._args(spec, resume=True)
    assert args[args.index("--resume") + 1] == "n-1"
    assert args[args.index("--append-system-prompt") + 1] == "固定资料"
    assert "--settings" not in args  # 远程会话不带压缩钩子
    codebuddy = CliStreamDriver(codebuddy_profile("codebuddy"), tmp_path / "b", tmp_path / "s")
    env = codebuddy._remote_env(spec)
    assert env["CODEBUDDY_IS_SANDBOX"] == "1" and env["CODEBUDDY_CONFIG_DIR"].startswith(
        "/workspace/"
    )
    assert "CODEBUDDY_IS_SANDBOX" not in codebuddy._remote_env(
        SessionSpec(mode="dev", model=None, workdir=Path("/x"), sandbox="read_only", remote=target)
    )
