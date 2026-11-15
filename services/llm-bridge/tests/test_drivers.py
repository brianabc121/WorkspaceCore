"""两个真实驱动对仿真的官方程序（tests/fakes）的测试；对真实程序的实测在 test_live.py。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Callable
from dataclasses import replace
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


async def _until(check: Callable[[], Any], limit: float = 10.0) -> None:
    """等到条件成立。

    不用固定时长的等待：官方程序是另一个进程，通知什么时候处理完取决于机器快慢
    （CI 上出现过发起登录 50 毫秒后“登录完成”的通知还没处理到）。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while True:
        result = check()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        assert loop.time() < deadline, "等了太久，条件还没成立"
        await asyncio.sleep(0.01)


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


def _login_fails(tmp_path: Path, on: bool) -> None:
    """仿真的 codex 看到这个记号文件，申请设备码时就回一个错误。"""
    marker = tmp_path / "home" / "login-fails"
    marker.parent.mkdir(parents=True, exist_ok=True)
    if on:
        marker.touch()
    else:
        marker.unlink()


async def test_codex_device_code_failure_says_why(codex: CodexDriver, tmp_path: Path) -> None:
    """申请设备码没成功（例如服务器访问不了 OpenAI）：错误里带着原因和可能的情况。"""
    _login_fails(tmp_path, True)
    with pytest.raises(DriverError) as caught:
        await codex.login("device_code", {})
    assert caught.value.kind == "platform_error"
    message = caught.value.message
    assert "向 OpenAI 申请设备码没有成功：error sending request for url" in message
    assert "auth.openai.com" in message and "API 密钥" in message
    _login_fails(tmp_path, False)
    assert (await codex.login("device_code", {})).user_code == "ABCD-1234"


async def test_codex_account_and_models(codex: CodexDriver) -> None:
    assert not (await codex.status()).logged_in
    challenge = await codex.login("device_code", {})
    assert challenge.state == "pending" and challenge.user_code == "ABCD-1234"

    async def finished() -> bool:
        state = await codex.login_state()
        return state is not None and state.state != "pending"

    await _until(finished)
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


async def test_codex_login_completed_before_the_reply_is_handled(
    codex: CodexDriver, tmp_path: Path
) -> None:
    """“登录完成”紧跟着应答到达、早于 login() 拿到应答时不能丢，否则页面一直显示“正在等待登录”。"""
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "complete-login-at-once").touch()
    challenge = await codex.login("device_code", {})
    assert challenge.state == "done"
    state = await codex.login_state()
    assert state is not None and state.state == "done"
    assert (await codex.status()).identity == "user@example.com"
    # 之后再登录一次不受上一次的通知影响
    (tmp_path / "home" / "complete-login-at-once").unlink()
    await codex.logout()
    again = await codex.login("device_code", {})
    assert again.state == "pending" and again.user_code == "ABCD-1234"

    async def finished() -> bool:
        current = await codex.login_state()
        return current is not None and current.state != "pending"

    await _until(finished)
    assert (await codex.login_state()).state == "done"


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
    await _until(lambda: native in codex._active)  # 回合开始之后才有东西可取消
    await codex.cancel(spec)
    with pytest.raises(DriverError, match="中断"):
        await task
    # 插入消息：没有回合在跑时收不下；回合在跑时用 turn/steer 送进去，回合接着做完
    assert await codex.steer(spec, "没有回合") is False
    task = asyncio.create_task(codex.run_turn(spec, "hang", Sink()))
    await _until(lambda: native in codex._active)
    assert await codex.steer(spec, "顺便看一下") is True
    assert (await task).text == "steered: 顺便看一下"
    # 进程重启后（桥重启）：先恢复线程再发送
    await codex.stop()
    assert (await codex.run_turn(spec, "重启之后", Sink())).text == "[None|readOnly] 重启之后"
    # 进程在回合进行中退出
    task = asyncio.create_task(codex.run_turn(spec, "hang", Sink()))
    await _until(lambda: native in codex._active)
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
    # 带图片的提问：按一条 stream-json 的用户消息从标准输入给，图片是 base64 的 image 内容块
    picture = tmp_path / "p.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n1234")
    with_image = replace(spec, images=((str(picture), "image/png"),))
    seen = await driver.run_turn(with_image, "看图", Sink())
    assert seen.text.endswith(":: 看图 images=1"), seen.text
    # 读文件这类工具调用和它的结果也报出来（命令、改文件照旧）
    tools = Sink()
    await driver.run_turn(spec, "read", tools)
    kinds = [kind for kind, _ in tools.events]
    assert kinds[:2] == ["tool", "tool_result"], kinds
    assert tools.events[0][1]["name"] == "Read" and "README.md" in tools.events[0][1]["input"]
    assert tools.events[1][1] == {"name": "Read", "text": "# 说明\n内容", "error": False}
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
    # 额度用完：和普通失败分开，带着重置时间；回合里报出的额度交给了 engine
    sink = Sink()
    with pytest.raises(DriverError) as limited:
        await driver.run_turn(spec, "limit", sink)
    assert (limited.value.kind, limited.value.quota, limited.value.resets_at) == (
        "rate_limited",
        True,
        1900000000.0,
    )
    quota_events = [payload for kind, payload in sink.events if kind == "quota"]
    assert quota_events[0]["windows"][0]["status"] == "exceeded"
    task = asyncio.create_task(driver.run_turn(spec, "hang", Sink()))
    await _until(lambda: (spec.native_id or "") in driver._procs)  # 进程起来之后才有东西可取消
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
    # 网页登录（国内站 / 国际站）和 API 密钥；不再提供“容器里已有的登录”
    assert driver.slot == "workbuddy"
    # 官方程序的回合输出里不带额度：页面要照实说明，而不是让人等“跑过回合后会显示”
    assert driver.reports_quota is False and _cli(tmp_path, claude_profile).reports_quota is True
    assert driver.login_methods == ("web_cn", "web_intl", "api_key")
    with pytest.raises(DriverError, match="不支持登录方式"):
        await driver.login("existing", {})
    assert [m.id for m in await driver.models("chat")] == ["auto", "fast-model", "deep-model"]
    assert not (await driver.status()).logged_in
    await driver.login("api_key", {"api_key": "cb-key"})
    status = await driver.status()
    assert status.logged_in and "发送时才能确认" in status.detail
    spec = SessionSpec(
        mode="chat", model="auto", workdir=tmp_path / "w", native_id=await driver.create(None)
    )  # type: ignore[arg-type]
    assert (await driver.run_turn(spec, "你好", Sink())).text.endswith(":: 你好")


def _exists(path: Path) -> bool:
    return path.exists()


def _mark(tmp_path: Path, name: str, on: bool = True) -> None:
    """仿真的官方程序看这些记号文件决定网页登录的结果。"""
    marker = tmp_path / "home" / "cfg" / name
    marker.parent.mkdir(parents=True, exist_ok=True)
    if on:
        marker.touch()
    else:
        marker.unlink(missing_ok=True)


async def _login_done(driver: Any, seconds: float = 15) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        challenge = await driver.login_state()
        if challenge is not None and challenge.state != "pending":
            return challenge
        await asyncio.sleep(0.1)
    raise AssertionError("登录一直没有结果")


async def test_codebuddy_web_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CodeBuddy 的网页登录：页面拿到网址，人在自己的浏览器里登录完，这里就变成已登录。"""
    from bridge.drivers import web_login

    monkeypatch.setattr(web_login, "POLL_SECONDS", 0.1)
    driver = _cli(tmp_path, codebuddy_profile)
    try:
        # 申请登录网址失败（例如这台服务器访问不了这个站点）：写明原因，不算已登录
        _mark(tmp_path, "web-login-fails")
        with pytest.raises(DriverError) as caught:
            await driver.login("web_cn", {})
        assert "申请登录网址没有成功：account.login.failed" in caught.value.message
        assert "平台连通性" in caught.value.message
        assert not (await driver.status()).logged_in and driver._web._server is None
        _mark(tmp_path, "web-login-fails", on=False)

        challenge = await driver.login("web_intl", {})
        assert challenge.state == "pending" and challenge.user_code is None
        assert challenge.verification_url == "https://example.test/login?site=external"
        # 人还没登录完：不算已登录
        assert (await driver.login_state()).state == "pending"
        assert not (await driver.status()).logged_in
        _mark(tmp_path, "web-login-done")  # 人在网页上登录完了
        assert (await _login_done(driver)).state == "done"
        status = await driver.status()
        assert (status.logged_in, status.identity, status.method) == (True, "测试账号", "web_intl")
        # 登录完成后本机服务已经关掉；之后的回合照常能跑
        assert driver._web._server is None
        spec = SessionSpec(
            mode="chat", model="auto", workdir=tmp_path / "w", native_id=await driver.create(None)
        )  # type: ignore[arg-type]
        assert (await driver.run_turn(spec, "你好", Sink())).text.endswith(":: 你好")

        # 退出登录：官方程序自己保存的登录也退掉
        await driver.logout()
        assert not (await driver.status()).logged_in
        assert not _exists(tmp_path / "home" / "cfg" / "logged-in")
    finally:
        await driver.stop()


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
