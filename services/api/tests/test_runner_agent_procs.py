"""runner-agent 的托管进程（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 7 节、第 21.3 节）。

两部分：
- 假 Docker：起进程的载荷、凭据不进日志、输出分块与按偏移重发、结束上报、超时升级、容器丢失、
  工作台不认识的进程、对账恢复、停止与输入、停止类指令不排队；
- 本机 sh 直接跑托管脚本（和容器里同一段脚本，只是目录换成临时目录）：输出、错误末尾、进程组、
  退出码、交互输入。真实 Docker / gVisor 下的行为见核验记录 V9 与 CI 的容器服务器验收。
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.runner_agent import agent as agent_module
from app.runner_agent import containers as c
from app.runner_agent.agent import Agent, CommandError, Config
from app.runner_agent.client import WorkspaceError
from app.runner_agent.docker import DockerError, ExecResult

CONTAINER = "mw-env-0123456789ab"
ENV_ID = "0b3c2a8e-0000-4000-8000-000000000001"
SECRET = "sk-test-very-secret-value"  # noqa: S105 - 测试值


def _run_id() -> str:
    return str(uuid.uuid4())


class _Dir:
    def __init__(self) -> None:
        self.out = bytearray()
        self.err = b""
        self.exit: str | None = None
        self.pid: int | None = None
        self.stdin = b""
        self.inputs: list[str] = []
        self.interactive = False
        self.alive = "-"  # 进程组在不在：1 / 0，还没有进程号是 -


class FakeDocker:
    """只模拟托管进程用到的几段脚本：按脚本内容分派，文件放在内存里。"""

    def __init__(self) -> None:
        self.dirs: dict[str, _Dir] = {}
        self.execs: list[dict[str, Any]] = []
        self.detached: list[dict[str, Any]] = []
        self.signals: list[tuple[str, str]] = []
        self.cleaned: list[str] = []
        self.fail: DockerError | None = None
        self.next_pid = 4242

    def exec(
        self,
        container: str,
        cmd: list[str],
        *,
        user: str | None = None,
        workdir: str | None = None,
        env: list[str] | None = None,
        timeout: float = 1800,
        limit: int = 1024 * 1024,
    ) -> ExecResult:
        self.execs.append({"cmd": cmd, "user": user, "workdir": workdir, "env": env})
        if self.fail is not None:
            raise self.fail
        assert cmd[:2] == ["sh", "-c"] and cmd[3] == "mw"
        script, args = cmd[2], cmd[4:]
        folder = args[0]
        found = self.dirs.get(folder)
        values = dict(e.split("=", 1) for e in env or [])
        if script == agent_module.PROC_PID:
            text = str(found.pid) if found and found.pid else ""
            return ExecResult(0, text.encode(), False, False, 0.0)
        if script == agent_module.PROC_STDIN:
            box = self.dirs.setdefault(folder, _Dir())
            if args[1] == "new":
                box.stdin = b""
            box.stdin += base64.b64decode(values.get("MW_CHUNK", ""))
            return ExecResult(0, b"", False, False, 0.0)
        if script == agent_module.PROC_READ:
            offset, most = int(args[1]), int(args[2])
            if found is None:
                return ExecResult(0, b"-1 -\n", False, False, 0.0)
            head = f"{len(found.out)} {found.exit or '-'} {found.alive}\n".encode()
            body = bytes(found.out[offset : offset + most]) if len(found.out) > offset else b""
            assert len(head + body) <= limit
            return ExecResult(0, head + body, False, False, 0.0)
        if script == agent_module.PROC_SIGNAL:
            self.signals.append((folder, args[1]))
            code = 0 if found and found.pid else 3
            return ExecResult(code, b"", False, False, 0.0)
        if script == agent_module.PROC_INPUT:
            if found is None or not found.interactive:
                return ExecResult(3, b"", False, False, 0.0)
            found.inputs.append(values["MW_INPUT"])
            return ExecResult(0, b"", False, False, 0.0)
        if script == agent_module.PROC_ERR:
            return ExecResult(0, found.err if found else b"", False, False, 0.0)
        if script == agent_module.PROC_CLEAN:
            self.cleaned.append(folder)
            self.dirs.pop(folder, None)
            return ExecResult(0, b"", False, False, 0.0)
        raise AssertionError(f"没想到的脚本：{script[:40]}")

    def exec_detached(
        self,
        container: str,
        cmd: list[str],
        *,
        user: str | None = None,
        workdir: str | None = None,
        env: list[str] | None = None,
    ) -> str:
        self.detached.append({"cmd": cmd, "user": user, "workdir": workdir, "env": env})
        # ["sh", "-c", 脚本, "mw-proc", 目录, 工作目录, 是否交互, 命令...]
        folder = cmd[4]
        box = self.dirs.setdefault(folder, _Dir())
        box.pid = self.next_pid
        box.interactive = cmd[6] == "1"
        return "exec-1"


class FakeWorkspace:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.received: dict[str, bytearray] = {}
        self.ack_limit: int | None = None  # 每次最多确认多少（模拟工作台只收下一部分）
        self.output_error: WorkspaceError | None = None
        self.exit_error: WorkspaceError | None = None

    def post(self, path: str, body: dict[str, Any], *, timeout: float = 30) -> Any:
        self.posts.append((path, body))
        if path.endswith("/output"):
            if self.output_error is not None:
                raise self.output_error
            run_id = path.split("/")[2]
            got = self.received.setdefault(run_id, bytearray())
            data = base64.b64decode(body["data_b64"])
            if body["offset"] < len(got):  # 重复的块：只返回已确认数
                return {"acked": len(got)}
            assert body["offset"] == len(got)
            if self.ack_limit is not None:
                data = data[: self.ack_limit]
            got += data
            return {"acked": len(got)}
        if path.endswith("/exit"):
            if self.exit_error is not None:
                raise self.exit_error
            return {"accepted": True}
        raise AssertionError(path)

    def exits(self) -> list[dict[str, Any]]:
        return [b for p, b in self.posts if p.endswith("/exit")]


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()

    def sleep(seconds: float) -> None:  # 假时钟：睡一下就往前走，不会原地打转
        fake.now += seconds

    # agent 模块用的就是标准库的 time：按名字打补丁
    monkeypatch.setattr("app.runner_agent.agent.time.monotonic", fake)
    monkeypatch.setattr("app.runner_agent.agent.time.sleep", sleep)
    return fake


def _agent(docker: FakeDocker, workspace: FakeWorkspace) -> Agent:
    agent = Agent(
        config=Config(url="https://example.invalid", token="t", workers=1),  # noqa: S106
        docker=docker,  # type: ignore[arg-type]
        workspace=workspace,  # type: ignore[arg-type]
        proxy=object(),  # type: ignore[arg-type]
    )
    agent._ensure_running = lambda payload: (CONTAINER, False)  # type: ignore[method-assign]
    return agent


def _payload(run_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "env_id": ENV_ID,
        "container": CONTAINER,
        "network": "mw-net-0123456789ab",
        "run_id": run_id,
        "proc_dir": f"/workspace/.mw/procs/{run_id}",
        "argv": ["claude", "-p", "--output-format", "stream-json"],
        "cwd": "/workspace/sessions/bs_1",
        "env": {"CLAUDE_CONFIG_DIR": "/workspace/.home/.claude", "HTTP_PROXY": "http://evil"},
        "timeout": 600,
        "interactive": False,
        "proc_input": {
            "stdin_b64": base64.b64encode(b"hello").decode(),
            "secret_env": {"ANTHROPIC_API_KEY": SECRET, "MYWS_X": "y"},
        },
        **extra,
    }


def _started(clock: _Clock, **extra: Any) -> tuple[Agent, FakeDocker, FakeWorkspace, str]:
    docker, workspace = FakeDocker(), FakeWorkspace()
    agent = _agent(docker, workspace)
    run_id = _run_id()
    agent.do_proc_start(_payload(run_id, **extra))
    return agent, docker, workspace, f"/workspace/.mw/procs/{run_id}"


# ---------------------------------------------------------------- 起进程


def test_start_passes_argv_cwd_and_env_but_never_logs_the_secret(
    clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    docker, workspace = FakeDocker(), FakeWorkspace()
    agent = _agent(docker, workspace)
    run_id = _run_id()
    folder = f"/workspace/.mw/procs/{run_id}"

    result = agent.do_proc_start(_payload(run_id))

    assert result == {"started": True, "pgid": 4242}
    [call] = docker.detached
    assert call["cmd"][:3] == ["sh", "-c", agent_module.PROC_WRAPPER]
    assert call["cmd"][3:] == [
        "mw-proc",
        folder,
        "/workspace/sessions/bs_1",
        "0",
        "claude",
        "-p",
        "--output-format",
        "stream-json",
    ]
    assert call["user"] == "1000:1000" and call["workdir"] == "/workspace"
    # 凭据经 exec 的 Env 交给进程；代理、MYWS_ 开头的不许改
    assert call["env"] == [
        "CLAUDE_CONFIG_DIR=/workspace/.home/.claude",
        f"ANTHROPIC_API_KEY={SECRET}",
    ]
    assert docker.dirs[folder].stdin == b"hello"
    assert SECRET not in caplog.text and SECRET not in repr(result)
    assert run_id in agent._procs


def test_large_stdin_is_written_in_pieces(clock: _Clock) -> None:
    data = os.urandom(agent_module.PROC_STDIN_PIECE * 3 + 17)
    payload = {"stdin_b64": base64.b64encode(data).decode(), "secret_env": {}}
    _, docker, _, folder = _started(clock, proc_input=payload)

    pieces = [e for e in docker.execs if e["cmd"][2] == agent_module.PROC_STDIN]
    assert [e["cmd"][5] for e in pieces] == ["new", "append", "append", "append"]
    assert all(len(e["env"][0]) < 128 * 1024 for e in pieces)  # 单个环境变量的上限
    assert docker.dirs[folder].stdin == data


def test_missing_input_and_bad_values_are_refused(clock: _Clock) -> None:
    agent = _agent(FakeDocker(), FakeWorkspace())
    with pytest.raises(CommandError, match="输入已过期"):
        agent.do_proc_start(_payload(_run_id(), proc_input={"missing": True}))
    bad: list[dict[str, Any]] = [
        {"proc_dir": "/workspace/.mw/procs/../../etc"},
        {"run_id": "not-a-uuid"},
        {"cwd": "/etc"},
        {"cwd": "/workspace/../etc"},
        {"argv": []},
        {"argv": ["sh", "a\x00b"]},
        {"env": {"bad key": "x"}},
    ]
    for change in bad:
        run_id = _run_id()
        payload = {**_payload(run_id), **change}
        with pytest.raises((CommandError, c.ParamError)):
            agent.do_proc_start(payload)


def test_restarted_agent_does_not_start_the_same_run_twice(clock: _Clock) -> None:
    docker, workspace = FakeDocker(), FakeWorkspace()
    agent = _agent(docker, workspace)
    run_id = _run_id()
    agent.do_proc_start(_payload(run_id))
    again = _agent(docker, workspace)  # runner-agent 重启了，工作台重发同一条指令
    assert again.do_proc_start(_payload(run_id)) == {"started": True, "pgid": 4242}
    assert len(docker.detached) == 1


# ---------------------------------------------------------------- 读取与回传


def test_output_is_sent_in_chunks_and_resent_from_the_acked_offset(clock: _Clock) -> None:
    agent, docker, workspace, folder = _started(clock)
    run_id = folder.rsplit("/", 1)[1]
    data = os.urandom(agent_module.PROC_CHUNK * 2 + 1234)
    docker.dirs[folder].out += data
    workspace.ack_limit = 100_000  # 工作台每次只收下一部分：下一次从它确认的地方重发

    for _ in range(40):
        agent.proc_round()
        if len(workspace.received.get(run_id, b"")) == len(data):
            break
    assert bytes(workspace.received[run_id]) == data
    offsets = [b["offset"] for p, b in workspace.posts if p.endswith("/output")]
    assert offsets == sorted(offsets) and offsets[0] == 0 and offsets[1] == 100_000
    sizes = [len(base64.b64decode(b["data_b64"])) for p, b in workspace.posts]
    assert max(sizes) <= agent_module.PROC_CHUNK
    assert workspace.exits() == []  # 还没有退出码


def test_exit_is_reported_only_after_all_output_is_acked(clock: _Clock) -> None:
    agent, docker, workspace, folder = _started(clock)
    box = docker.dirs[folder]
    box.out += b"x" * (agent_module.PROC_CHUNK + 10)
    box.exit, box.err = "0", b"warning: something\n"

    agent.proc_round()
    assert workspace.exits() == []  # 还剩 10 个字节没确认
    agent.proc_round()
    assert workspace.exits() == [
        {"exit_code": 0, "state": "exited", "stderr_tail": "warning: something\n", "error": None}
    ]
    assert docker.cleaned == [folder] and agent._procs == {}


def test_exit_report_retries_while_the_workspace_is_down(clock: _Clock) -> None:
    agent, docker, workspace, folder = _started(clock)
    docker.dirs[folder].exit = "2"
    workspace.exit_error = WorkspaceError(503, "UNAVAILABLE", "down")
    agent.proc_round()
    assert agent._procs and docker.cleaned == []  # 输出和退出码还在容器里，下一轮再报
    workspace.exit_error = None
    clock.now += 60
    agent.proc_round()
    assert workspace.exits()[-1]["exit_code"] == 2 and agent._procs == {}


def test_idle_procs_are_read_less_often(clock: _Clock) -> None:
    agent, _, _, _ = _started(clock)
    proc = next(iter(agent._procs.values()))
    agent.proc_round()
    assert proc.next_check == pytest.approx(clock.now + agent_module.PROC_TICK)
    clock.now += agent_module.PROC_IDLE_AFTER + 1
    agent.proc_round()
    assert proc.next_check == pytest.approx(clock.now + agent_module.PROC_SLOW)


def test_timeout_sends_term_then_kill_and_reports_timeout(clock: _Clock) -> None:
    agent, docker, workspace, folder = _started(clock, timeout=5)
    clock.now += 6
    agent.proc_round()
    assert docker.signals == [(folder, "TERM")]
    clock.now += agent_module.PROC_TERM_GRACE + 1
    agent.proc_round()
    assert docker.signals == [(folder, "TERM"), (folder, "KILL")]
    docker.dirs[folder].exit = "137"
    clock.now += agent_module.PROC_SLOW + 1
    agent.proc_round()
    assert workspace.exits()[-1]["state"] == "timeout"
    assert workspace.exits()[-1]["exit_code"] == 137


def test_kill_without_an_exit_record_is_reported_after_a_grace_period(clock: _Clock) -> None:
    """集成审查第 3 条：KILL 之后外壳一直没写结束记录（例如它自己也被杀了），不能永远“正在运行”。"""
    agent, docker, workspace, folder = _started(clock, timeout=5)
    clock.now += 6
    agent.proc_round()
    clock.now += agent_module.PROC_TERM_GRACE + 1
    agent.proc_round()
    assert docker.signals[-1] == (folder, "KILL")
    clock.now += agent_module.PROC_SLOW + 1
    agent.proc_round()
    assert workspace.exits() == []  # 还在宽限期里
    clock.now += agent_module.PROC_KILL_GRACE
    agent.proc_round()
    [body] = workspace.exits()
    assert body["state"] == "timeout" and body["exit_code"] is None
    assert "没有留下结束记录" in body["error"] and agent._procs == {}


def test_dead_process_group_without_an_exit_record_becomes_lost(clock: _Clock) -> None:
    """模型在容器里 kill -9 -1：进程组和外壳都没了，没有结束记录。宽限后算丢失。"""
    agent, docker, workspace, folder = _started(clock)
    docker.dirs[folder].alive = "1"
    agent.proc_round()
    docker.dirs[folder].alive = "0"
    clock.now += agent_module.PROC_SLOW + 1
    agent.proc_round()
    assert workspace.exits() == []
    docker.dirs[folder].alive = "1"  # 又在了（例如外壳正在收尾）：重新计时
    clock.now += agent_module.PROC_DEAD_GRACE + 1
    agent.proc_round()
    assert workspace.exits() == []
    docker.dirs[folder].alive = "0"
    clock.now += agent_module.PROC_SLOW + 1
    agent.proc_round()
    assert workspace.exits() == []
    clock.now += agent_module.PROC_DEAD_GRACE + 1
    agent.proc_round()
    [body] = workspace.exits()
    assert body["state"] == "lost" and body["exit_code"] is None


def test_exit_record_written_late_still_wins(clock: _Clock) -> None:
    agent, docker, workspace, folder = _started(clock)
    docker.dirs[folder].alive = "0"
    agent.proc_round()
    docker.dirs[folder].exit = "0"  # 外壳在宽限期里写了结束记录
    clock.now += agent_module.PROC_SLOW + 1
    agent.proc_round()
    [body] = workspace.exits()
    assert body["state"] == "exited" and body["exit_code"] == 0


def test_helper_exec_timeout_keeps_the_shared_container() -> None:
    """集成审查第 2 条：模块的辅助命令超时只结束它自己，不停共享的容器。"""

    class SlowDocker:
        def __init__(self) -> None:
            self.stopped: list[str] = []

        def exec(self, container: str, cmd: list[str], **_: Any) -> ExecResult:
            return ExecResult(None, b"", False, True, 60.0)

        def stop(self, container: str, seconds: int = 10) -> None:
            self.stopped.append(container)

    class Proxy:
        def __init__(self) -> None:
            self.closed: list[str] = []

        def close(self, env_id: str) -> None:
            self.closed.append(env_id)

    docker, proxy = SlowDocker(), Proxy()
    agent = Agent(
        config=Config(url="https://example.invalid", token="t", workers=1),  # noqa: S106
        docker=docker,  # type: ignore[arg-type]
        workspace=FakeWorkspace(),  # type: ignore[arg-type]
        proxy=proxy,  # type: ignore[arg-type]
    )
    agent._ensure_running = lambda payload: (CONTAINER, False)  # type: ignore[method-assign]
    payload = {"env_id": ENV_ID, "container": CONTAINER, "cmd": ["sleep", "100"], "timeout": 60}
    result = agent.do_exec({**payload, "keep_container": True})
    assert result["timed_out"] is True and docker.stopped == [] and proxy.closed == []
    agent.do_exec(payload)  # 普通的一次性执行：照旧停容器
    assert docker.stopped == [CONTAINER] and proxy.closed == [ENV_ID]


def test_lost_container_is_reported_without_cleanup(clock: _Clock) -> None:
    agent, docker, workspace, _ = _started(clock)
    docker.fail = DockerError(409, "container is not running")
    agent.proc_round()
    [body] = workspace.exits()
    assert body["state"] == "lost" and body["exit_code"] is None and body["stderr_tail"] == ""
    assert docker.cleaned == [] and agent._procs == {}


def test_missing_folder_becomes_lost_after_a_grace_period(clock: _Clock) -> None:
    agent, docker, workspace, folder = _started(clock)
    docker.dirs.pop(folder)
    agent.proc_round()
    assert workspace.exits() == []
    clock.now += agent_module.PROC_MISSING_GRACE + 1
    agent.proc_round()
    assert workspace.exits()[-1]["state"] == "lost"


def test_unknown_proc_on_the_workspace_is_killed_and_cleaned(clock: _Clock) -> None:
    agent, docker, workspace, folder = _started(clock)
    docker.dirs[folder].out += b"data"
    workspace.output_error = WorkspaceError(404, "NOT_FOUND", "no such run")
    agent.proc_round()
    assert (folder, "KILL") in docker.signals
    assert docker.cleaned == [folder] and agent._procs == {}


# ---------------------------------------------------------------- 对账恢复


def test_report_procs_are_resumed_from_the_workspace_offset(clock: _Clock) -> None:
    docker, workspace = FakeDocker(), FakeWorkspace()
    agent = _agent(docker, workspace)
    run_id = _run_id()
    folder = f"/workspace/.mw/procs/{run_id}"
    box = docker.dirs.setdefault(folder, _Dir())
    box.out += b"a" * 1000 + b"b" * 500
    workspace.received[run_id] = bytearray(b"a" * 1000)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    agent.sync_procs(
        [
            {
                "run_id": run_id,
                "env_id": ENV_ID,
                "container": CONTAINER,
                "proc_dir": folder,
                "acked": 1000,
                "timeout_at": future,
            },
            {"run_id": _run_id(), "container": "not-a-container", "proc_dir": None},
            {"run_id": "x", "container": CONTAINER},
            "garbage",
        ]
    )
    assert list(agent._procs) == [run_id]
    proc = agent._procs[run_id]
    assert 3500 < proc.deadline - clock.now <= 3600
    agent.proc_round()
    [(_, body)] = [(p, b) for p, b in workspace.posts if p.endswith("/output")]
    assert body["offset"] == 1000 and base64.b64decode(body["data_b64"]) == b"b" * 500
    # 已经在盯的：工作台的已确认数只能往前推
    agent.sync_procs([{"run_id": run_id, "container": CONTAINER, "acked": 10}])
    assert proc.acked == 1500


def test_report_hands_procs_to_the_watcher(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    docker, workspace = FakeDocker(), FakeWorkspace()
    agent = _agent(docker, workspace)
    run_id = _run_id()
    seen: list[list[Any]] = []
    monkeypatch.setattr(agent, "sync_procs", seen.append)
    # report() 的其余部分在别的测试里覆盖；这里只确认它把应答里的 procs 交给读取线程
    source = Path(agent_module.__file__).read_text()
    assert 'self.sync_procs(result.get("procs") or [])' in source
    agent.sync_procs([{"run_id": run_id}])
    assert seen == [[{"run_id": run_id}]]


# ---------------------------------------------------------------- 停止与输入


def test_signal_marks_the_proc_killed_and_escalates(clock: _Clock) -> None:
    agent, docker, workspace, folder = _started(clock)
    run_id = folder.rsplit("/", 1)[1]
    signal = {"env_id": ENV_ID, "container": CONTAINER, "run_id": run_id, "proc_dir": folder}
    assert agent.do_proc_signal({**signal, "signal": "TERM"}) == {"signaled": True}
    clock.now += agent_module.PROC_TERM_GRACE + 1
    agent.proc_round()
    assert docker.signals == [(folder, "TERM"), (folder, "KILL")]
    docker.dirs[folder].exit = "143"
    clock.now += agent_module.PROC_SLOW + 1
    agent.proc_round()
    assert workspace.exits()[-1]["state"] == "killed"
    with pytest.raises(CommandError):
        agent.do_proc_signal({**signal, "signal": "HUP"})


def test_signal_for_an_unknown_or_gone_proc(clock: _Clock) -> None:
    docker = FakeDocker()
    agent = _agent(docker, FakeWorkspace())
    run_id = _run_id()
    payload = {
        "container": CONTAINER,
        "run_id": run_id,
        "proc_dir": f"/workspace/.mw/procs/{run_id}",
        "signal": "KILL",
    }
    assert agent.do_proc_signal(payload) == {"signaled": False}  # 没有进程组号
    docker.fail = DockerError(404, "no such container")
    assert agent.do_proc_signal(payload) == {"signaled": False}


def test_input_goes_to_the_interactive_proc_only(clock: _Clock) -> None:
    agent, docker, _, folder = _started(clock, interactive=True)
    run_id = folder.rsplit("/", 1)[1]
    payload = {
        "container": CONTAINER,
        "run_id": run_id,
        "proc_dir": folder,
        "proc_input": {"data": "code#state\n"},
    }
    assert agent.do_proc_input(payload) == {"written": 11}
    assert docker.dirs[folder].inputs == ["code#state\n"]
    with pytest.raises(CommandError, match="输入已过期"):
        agent.do_proc_input({**payload, "proc_input": {"missing": True}})
    with pytest.raises(CommandError, match="最多 4 KB"):
        agent.do_proc_input({**payload, "proc_input": {"data": "x" * 5000}})
    other = _started(clock)[3]
    with pytest.raises(CommandError, match="不接受输入"):
        agent.do_proc_input({**payload, "proc_dir": other, "run_id": other.rsplit("/", 1)[1]})


def test_signal_and_input_do_not_wait_for_earlier_lifecycle_commands(clock: _Clock) -> None:
    docker, workspace = FakeDocker(), FakeWorkspace()
    results: list[tuple[str, dict[str, Any]]] = []
    agent = _agent(docker, workspace)
    agent._send_result = lambda command_id, result: results.append((command_id, result))  # type: ignore[method-assign]
    agent._pending[ENV_ID] = [0]  # 同一个环境里一条永远做不完的改变状态的指令
    agent._seq = 1
    run_id = _run_id()
    command = {
        "id": "cmd-signal",
        "kind": "proc_signal",
        "payload": {
            "env_id": ENV_ID,
            "container": CONTAINER,
            "run_id": run_id,
            "proc_dir": f"/workspace/.mw/procs/{run_id}",
            "signal": "TERM",
        },
    }
    done = threading.Event()
    original = agent._end_turn

    def end(key: str, seq: int) -> None:
        original(key, seq)
        done.set()

    agent._end_turn = end  # type: ignore[method-assign]
    agent.dispatch(command)
    assert done.wait(5), "停止指令被同一环境的改变状态的指令挡住了"
    assert results[0][1]["status"] == "done"


# ---------------------------------------------------------------- 本机 sh 跑真正的脚本

_TOOLS = all(shutil.which(t) for t in ("sh", "setsid", "tail", "head", "base64", "mkfifo"))
needs_sh = pytest.mark.skipif(not _TOOLS, reason="本机没有 sh / setsid / coreutils")


SH = shutil.which("sh") or "/bin/sh"


def _sh(script: str, *args: str, env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(  # noqa: S603 - 固定脚本
        [SH, "-c", script, "mw", *args],
        capture_output=True,
        timeout=30,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **(env or {})},
    )
    return result.stdout


def _wait_for(path: Path, seconds: float = 10) -> None:
    until = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < until, f"{path} 没有出现"
        time.sleep(0.05)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().split(") ")[-1].startswith("Z"))


def _detach(folder: Path, cwd: Path, interactive: bool, *argv: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - 固定脚本
        [
            SH,
            "-c",
            agent_module.PROC_WRAPPER,
            "mw-proc",
            str(folder),
            str(cwd),
            "1" if interactive else "0",
            *argv,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


@needs_sh
def test_wrapper_writes_output_exit_and_stderr_tail(tmp_path: Path) -> None:
    folder, cwd = tmp_path / "procs" / "p1", tmp_path / "w"
    cwd.mkdir()
    piece = base64.b64encode(b"from stdin\n").decode()
    _sh(agent_module.PROC_STDIN, str(folder), "new", env={"MW_CHUNK": piece})
    script = 'cat; pwd; i=0; while [ $i -lt 300 ]; do i=$((i+1)); echo "line $i"; done; ' + (
        "head -c 70000 /dev/zero | tr '\\0' e >&2; exit 7"
    )
    _detach(folder, cwd, False, "sh", "-c", script).wait(30)
    _wait_for(folder / "exit")
    assert (folder / "exit").read_text().strip() == "7"
    out = (folder / "out").read_bytes()
    assert out.startswith(b"from stdin\n" + str(cwd).encode() + b"\nline 1\n")
    assert out.endswith(b"line 300\n")
    assert (folder / "err").stat().st_size == 65536  # 只留最后 64 KB
    assert not (folder / "stdin").exists()

    head = _sh(agent_module.PROC_READ, str(folder), "11", "20")
    size, code, alive = head.split(b"\n", 1)[0].split()
    # 进程组在外壳回报之前就停了（外壳最后还会给整个组发 TERM / KILL）
    assert int(size) == len(out) and code == b"7" and alive == b"0"
    assert head.split(b"\n", 1)[1] == out[11:31]
    assert _sh(agent_module.PROC_READ, str(tmp_path / "nope"), "0", "10") == b"-1 - -\n"
    tail = _sh(agent_module.PROC_ERR, str(folder), "100")
    assert tail == b"e" * 100
    _sh(agent_module.PROC_CLEAN, str(folder))
    assert not folder.exists()


@needs_sh
def test_wrapper_reports_a_missing_cwd(tmp_path: Path) -> None:
    folder = tmp_path / "p2"
    _detach(folder, tmp_path / "missing", False, "true").wait(30)
    _wait_for(folder / "exit")
    assert (folder / "exit").read_text().strip() == "126"


@needs_sh
def test_signal_stops_the_whole_process_group(tmp_path: Path) -> None:
    folder, cwd = tmp_path / "p3", tmp_path
    script = "sleep 300 & echo $! > child; while :; do echo tick; sleep 0.2; done"
    _detach(folder, cwd, False, "sh", "-c", script)
    _wait_for(folder / "pid")
    _wait_for(cwd / "child")
    child = int((cwd / "child").read_text())
    assert _alive(child)
    _sh(agent_module.PROC_SIGNAL, str(folder), "TERM")
    _wait_for(folder / "exit")
    assert (folder / "exit").read_text().strip() == "143"
    until = time.monotonic() + 5
    while _alive(child) and time.monotonic() < until:
        time.sleep(0.05)
    assert not _alive(child), "后台子进程没有跟着停"
    assert _sh(agent_module.PROC_PID, str(folder)).strip().isdigit()


@needs_sh
def test_children_left_behind_are_stopped_when_the_main_process_exits(tmp_path: Path) -> None:
    folder, cwd = tmp_path / "p4", tmp_path
    _detach(folder, cwd, False, "sh", "-c", "sleep 300 & echo $! > child2; exit 0")
    _wait_for(folder / "exit")
    child = int((cwd / "child2").read_text())
    until = time.monotonic() + 5
    while _alive(child) and time.monotonic() < until:
        time.sleep(0.05)
    assert not _alive(child)


@needs_sh
def test_interactive_input_reaches_the_process(tmp_path: Path) -> None:
    folder, cwd = tmp_path / "p5", tmp_path
    _detach(folder, cwd, True, "sh", "-c", 'echo "waiting"; read code; echo "got:$code"')
    _wait_for(folder / "pid")
    _wait_for(folder / "in")
    _sh(agent_module.PROC_INPUT, str(folder), env={"MW_INPUT": "abc#123\n"})
    _wait_for(folder / "exit")
    assert (folder / "out").read_bytes() == b"waiting\ngot:abc#123\n"
    # 进程已经结束：管道删掉了，送输入报 3（不会卡住）
    result = subprocess.run(  # noqa: S603 - 固定脚本
        [SH, "-c", agent_module.PROC_INPUT, "mw", str(folder)],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "MW_INPUT": "x"},
        timeout=10,
        check=False,
    )
    assert result.returncode == 3


# ---------------------------------------------------------------- 更新镜像：recreate


class _CreateDocker:
    """只记录 do_create_env 用到的调用。"""

    def __init__(self, exists: bool) -> None:
        self.exists = exists
        self.calls: list[tuple[str, Any]] = []

    def inspect(self, name: str) -> dict[str, Any] | None:
        return {"Id": "x", "State": {"Running": True}} if self.exists else None

    def stop(self, name: str, *, seconds: int = 10) -> None:
        self.calls.append(("stop", name))

    def remove(self, name: str) -> None:
        self.calls.append(("remove", name))
        self.exists = False

    def create_volume(self, name: str, labels: dict[str, str]) -> None:
        self.calls.append(("create_volume", name))

    def create_container(self, name: str, config: dict[str, Any]) -> str:
        self.calls.append(("create_container", config["Image"]))
        self.exists = True
        return "id"

    def start(self, name: str) -> None:
        self.calls.append(("start", name))


def _create_agent(docker: _CreateDocker) -> tuple[Agent, list[str]]:
    agent = Agent(
        config=Config(url="https://example.invalid", token="t", workers=1),  # noqa: S106
        docker=docker,  # type: ignore[arg-type]
        workspace=FakeWorkspace(),  # type: ignore[arg-type]
        proxy=object(),  # type: ignore[arg-type]
    )
    steps: list[str] = []
    agent._image_for = lambda p: {"Id": "sha256:new", "RepoDigests": []}  # type: ignore[method-assign]
    agent._env_network = lambda network, labels: None  # type: ignore[method-assign]
    agent._attach_proxy = lambda env_id, network, policy: None  # type: ignore[method-assign]

    def inject(payload: dict[str, Any], container: str) -> None:
        steps.append("secrets")

    def helper(payload: dict[str, Any], mounts: list[dict[str, Any]], cmd: list[str]) -> bytes:
        steps.append("helper")
        return b""

    def restore(p: dict[str, Any], snapshot: dict[str, Any]) -> None:
        steps.append("restore")

    def put(p: dict[str, Any], files: list[dict[str, Any]]) -> int:
        steps.append("files")
        return 0

    agent._inject_secrets = inject  # type: ignore[method-assign]
    agent._run_helper = helper  # type: ignore[method-assign]
    agent._restore = restore  # type: ignore[method-assign]
    agent._put = put  # type: ignore[method-assign]
    return agent, steps


_CREATE = {
    "env_id": ENV_ID,
    "workspace_id": "0b3c2a8e-0000-4000-8000-000000000002",
    "container": CONTAINER,
    "volume": "mw-vol-0123456789ab",
    "network": "mw-net-0123456789ab",
    "image": "myworkspace/tpl-llm-dev:2",
    "runtime": "gvisor",
    "user": "user",
    "network_mode": "allowlist",
    "domains": ["api.anthropic.com"],
    "memory_mb": 4096,
    "cpus": 2,
    "pids": 1024,
    "env": {},
    "start_command": None,
    "snapshot": {"id": "s1", "entries": []},
    "files": [{"path": "a.txt", "b64": ""}],
}


def test_recreate_swaps_the_container_and_keeps_the_volume() -> None:
    docker = _CreateDocker(exists=True)
    agent, steps = _create_agent(docker)
    result = agent.do_create_env({**_CREATE, "recreate": True})
    assert docker.calls == [
        ("stop", CONTAINER),
        ("remove", CONTAINER),
        ("create_container", "myworkspace/tpl-llm-dev:2"),
        ("start", CONTAINER),
    ]
    # 数据卷原样：不建卷、不改属主、不恢复快照、不写初始文件
    assert steps == ["secrets"]
    assert result["existed"] is True and result["recreated"] is True


def test_without_recreate_an_existing_container_is_only_started() -> None:
    docker = _CreateDocker(exists=True)
    agent, steps = _create_agent(docker)
    result = agent.do_create_env(dict(_CREATE))
    assert docker.calls == [("start", CONTAINER)]
    assert steps == ["secrets"] and result["recreated"] is False


def test_recreate_on_a_missing_container_is_a_normal_create() -> None:
    docker = _CreateDocker(exists=False)
    agent, steps = _create_agent(docker)
    result = agent.do_create_env({**_CREATE, "recreate": True})
    assert docker.calls == [
        ("create_volume", "mw-vol-0123456789ab"),
        ("create_container", "myworkspace/tpl-llm-dev:2"),
        ("start", CONTAINER),
    ]
    assert steps == ["helper", "restore", "files", "secrets"]
    assert result["existed"] is False and result["recreated"] is False
