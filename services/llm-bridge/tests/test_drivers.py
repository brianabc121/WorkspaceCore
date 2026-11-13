"""两个真实驱动对仿真的官方程序（tests/fakes）的测试；对真实程序的实测在 test_live.py。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from bridge.drivers.base import DriverError, SessionSpec
from bridge.drivers.cli_stream import (
    CliStreamDriver,
    claude_profile,
    codebuddy_profile,
    parse_transcript_line,
)
from bridge.drivers.codex import CodexDriver

FAKES = Path(__file__).parent / "fakes"


def _wrapper(tmp_path: Path, script: str) -> str:
    """生成一个可执行文件，转去运行 tests/fakes 里的脚本。"""
    path = tmp_path / f"bin-{script}"
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKES / script}" "$@"\n')
    path.chmod(0o755)
    return str(path)


class Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))


@pytest.fixture
async def codex(tmp_path: Path):
    driver = CodexDriver(_wrapper(tmp_path, "fake_codex.py"), tmp_path / "home", rpc_timeout=10)
    yield driver
    await driver.stop()


async def test_codex_account_and_models(codex: CodexDriver) -> None:
    assert not (await codex.status()).logged_in
    challenge = await codex.login("device_code", {})
    assert challenge.state == "pending" and challenge.user_code == "ABCD-1234"
    await asyncio.sleep(0.05)
    assert (await codex.login_state()).state == "done"
    status = await codex.status()
    assert (status.logged_in, status.identity, status.method) == (
        True,
        "user@example.com",
        "chatgpt",
    )
    await codex.logout()
    assert not (await codex.status()).logged_in
    await codex.login("api_key", {"api_key": "sk-test"})
    assert (await codex.status()).method == "api_key"
    models = await codex.models("chat")
    assert [(m.id, m.default, m.efforts) for m in models] == [("gpt-a", True, ("low", "high"))]
    with pytest.raises(DriverError) as caught:
        await codex.login("password", {})
    assert caught.value.kind == "not_supported"


async def test_codex_turns(codex: CodexDriver, tmp_path: Path) -> None:
    spec = SessionSpec(mode="dev", model="gpt-a", workdir=tmp_path / "w", sandbox="workspace_write")
    native = await codex.create(spec)
    assert await codex.read(native, None) == []  # 还没发过消息
    spec = SessionSpec(
        mode="dev",
        model="gpt-a",
        workdir=tmp_path / "w",
        sandbox="workspace_write",
        native_id=native,
    )
    sink = Sink()
    outcome = await codex.run_turn(spec, "你好", sink)
    assert outcome.text == "[gpt-a|workspaceWrite] 你好"
    assert outcome.usage["total_tokens"] == 7
    assert outcome.commands == ["ls"] and outcome.files == ["a.txt"]
    assert [kind for kind, _ in sink.events] == ["command", "files", "message"]
    # 平台的用量限制 → rate_limited
    with pytest.raises(DriverError) as caught:
        await codex.run_turn(spec, "limit", sink)
    assert caught.value.kind == "rate_limited"
    # 万一收到审批请求：一律拒绝
    assert (await codex.run_turn(spec, "approve", sink)).text == "approval=decline"
    # 列表、读取、归档
    (listed,) = await codex.list_native(10)
    assert (listed.native_id, listed.title, listed.model) == (native, "你好", "gpt-a")
    assert [(m.role, m.text) for m in await codex.read(native, None)][:2] == [
        ("user", "你好"),
        ("assistant", "[gpt-a|workspaceWrite] 你好"),
    ]
    await codex.archive(native)
    assert await codex.list_native(10) == []


async def test_codex_cancel_and_restart(codex: CodexDriver, tmp_path: Path) -> None:
    base = SessionSpec(mode="chat", model=None, workdir=tmp_path / "w")
    native = await codex.create(base)
    state = json.loads((tmp_path / "home" / "fake-state.json").read_text())
    assert state["threads"][native]["sandbox"] == "read-only"
    assert "plain chat" in state["threads"][native]["instructions"]
    spec = SessionSpec(mode="chat", model=None, workdir=tmp_path / "w", native_id=native)
    task = asyncio.create_task(codex.run_turn(spec, "hang", Sink()))
    await asyncio.sleep(0.2)
    await codex.cancel(spec)
    with pytest.raises(DriverError, match="中断"):
        await task
    # 进程重启后（桥重启）：先恢复线程再发送
    await codex.stop()
    assert (await codex.run_turn(spec, "重启之后", Sink())).text == "[None|readOnly] 重启之后"
    # 进程在回合进行中退出
    task = asyncio.create_task(codex.run_turn(spec, "hang", Sink()))
    await asyncio.sleep(0.2)
    assert codex._proc is not None
    codex._proc.kill()
    with pytest.raises(DriverError) as caught:
        await task
    assert caught.value.kind == "unavailable"


def _cli(tmp_path: Path, profile=claude_profile) -> CliStreamDriver:
    return CliStreamDriver(
        profile(_wrapper(tmp_path, "fake_cli.py")),
        tmp_path / "home" / "cfg",
        tmp_path / "secrets",
        rpc_timeout=10,
    )


async def test_cli_login_and_env_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGE_ADMIN_TOKEN", "must-not-leak")
    driver = _cli(tmp_path)
    assert not (await driver.status()).logged_in
    spec = SessionSpec(
        mode="chat", model="sonnet", workdir=tmp_path / "w", native_id=await driver.create(None)
    )  # type: ignore[arg-type]
    with pytest.raises(DriverError) as caught:
        await driver.run_turn(spec, "你好", Sink())
    assert caught.value.kind == "not_logged_in"
    await driver.login("api_key", {"api_key": "sk-" + "ant-test-key"})
    secret = tmp_path / "secrets" / "claude.json"
    assert secret.stat().st_mode & 0o777 == 0o600
    status = await driver.status()
    # 用密钥登录：官方程序不报告是哪个账号，驱动也不拿密钥的指纹充当账号
    assert (status.logged_in, status.identity, status.method) == (True, None, "api_key")
    outcome = await driver.run_turn(spec, "你好", Sink())
    # 聊天模式不给工具；桥自己的环境变量不会传给官方程序
    assert (
        "tools=''" in outcome.text
        and "leak=None" in outcome.text
        and outcome.text.endswith(":: 你好")
    )
    await driver.logout()
    assert not secret.exists() and not (await driver.status()).logged_in


async def test_cli_turns_resume_and_transcripts(tmp_path: Path) -> None:
    driver = _cli(tmp_path)
    await driver.login("oauth_token", {"oauth_token": "tok"})
    native = await driver.create(None)  # type: ignore[arg-type]
    spec = SessionSpec(
        mode="dev",
        model="opus",
        workdir=tmp_path / "w",
        sandbox="workspace_write",
        native_id=native,
        env={"EXTRA_VAR": "from-env"},
    )
    sink = Sink()
    first = await driver.run_turn(spec, "第一条", sink)
    assert (
        "model=opus" in first.text
        and "perm=acceptEdits" in first.text
        and "extra=from-env" in first.text
    )
    assert first.commands == ["ls -la"] and first.files == ["src/a.py"]
    assert first.usage == {"input_tokens": 5, "output_tokens": 6}
    assert [k for k, _ in sink.events] == ["message", "command", "files"]
    # 第二回合自动改用 --resume（仿真程序对重复的 --session-id 会报错）
    assert (await driver.run_turn(spec, "第二条", Sink())).text.endswith(":: 第二条")
    (listed,) = await driver.list_native(10)
    assert (listed.native_id, listed.title) == (native, "第一条") and listed.workdir
    messages = await driver.read(native, None)
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]
    assert await driver.read("../../etc/passwd", None) == []
    # 各沙箱档位对应的参数
    read_only = SessionSpec(mode="dev", model=None, workdir=tmp_path / "w", native_id=native)
    assert "tools='Read,Glob,Grep'" in (await driver.run_turn(read_only, "x", Sink())).text
    full = SessionSpec(
        mode="dev", model=None, workdir=tmp_path / "w", sandbox="exec", native_id=native
    )
    assert "perm=bypassPermissions" in (await driver.run_turn(full, "x", Sink())).text


async def test_cli_failures(tmp_path: Path) -> None:
    driver = _cli(tmp_path)
    await driver.login("api_key", {"api_key": "k"})
    spec = SessionSpec(
        mode="chat", model=None, workdir=tmp_path / "w", native_id=await driver.create(None)
    )  # type: ignore[arg-type]
    with pytest.raises(DriverError) as caught:
        await driver.run_turn(spec, "crash", Sink())
    assert caught.value.kind == "platform_error" and "sk-ant" not in caught.value.message
    task = asyncio.create_task(driver.run_turn(spec, "hang", Sink()))
    await asyncio.sleep(0.3)
    await driver.cancel(spec)
    with pytest.raises(DriverError):
        await asyncio.wait_for(task, 10)
    with pytest.raises(DriverError) as missing:
        await CliStreamDriver(
            claude_profile("/nonexistent/claude"), tmp_path / "c", tmp_path / "s"
        ).run_turn(spec, "x", Sink())
    assert missing.value.kind == "unavailable"


async def test_codebuddy_profile(tmp_path: Path) -> None:
    driver = _cli(tmp_path, codebuddy_profile)
    assert driver.slot == "workbuddy" and driver.login_methods == ("api_key", "existing")
    assert [m.id for m in await driver.models("chat")] == ["auto", "fast-model", "deep-model"]
    assert not (await driver.status()).logged_in
    await driver.login("api_key", {"api_key": "cb-key"})
    status = await driver.status()
    assert status.logged_in and "发送时才能确认" in status.detail
    spec = SessionSpec(
        mode="chat", model="auto", workdir=tmp_path / "w", native_id=await driver.create(None)
    )  # type: ignore[arg-type]
    assert (await driver.run_turn(spec, "你好", Sink())).text.endswith(":: 你好")


def test_transcript_formats() -> None:
    claude = {"type": "assistant", "message": {"content": [{"type": "text", "text": "答"}]}}
    codebuddy = {
        "type": "message",
        "role": "user",
        "timestamp": 1794563854550,
        "content": [{"type": "input_text", "text": "问"}],
    }
    assert parse_transcript_line(claude).text == "答"
    message = parse_transcript_line(codebuddy)
    assert (message.role, message.text, message.at) == ("user", "问", 1794563854.55)
    assert parse_transcript_line({"type": "file-history-snapshot"}) is None
    assert (
        parse_transcript_line({"type": "user", "isMeta": True, "message": {"content": "x"}}) is None
    )
