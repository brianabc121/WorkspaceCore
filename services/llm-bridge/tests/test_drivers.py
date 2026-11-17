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
    CHAT_NOTICE,
    CliStreamDriver,
    _turn_usage,
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


async def test_codex_device_code_login_reports_each_step(
    codex: CodexDriver, tmp_path: Path
) -> None:
    """ChatGPT 设备码登录的逐步进度（T-0279）：四步的状态和说明、运行信息；验证码不记进运行信息。"""
    from bridge.drivers.codex import DEVICE_STEPS

    challenge = await codex.login("device_code", {})
    assert challenge.state == "pending" and challenge.started_at
    assert [s["key"] for s in challenge.steps] == list(DEVICE_STEPS)
    status = {s["key"]: s["status"] for s in challenge.steps}
    assert status == {
        "start": "done",
        "request_code": "done",
        "wait_user": "running",
        "finish": "pending",
    }
    # 第一次登录时本机程序还没启动：这一步写用时
    assert challenge.steps[0]["detail"].startswith("已启动（用时")
    assert challenge.steps[1]["detail"].startswith("用时")
    text = "\n".join(challenge.log)
    assert "向 OpenAI 申请设备码" in text and "https://example.test/device" in text
    assert "ABCD-1234" not in text  # 验证码只给页面

    async def finished() -> bool:
        state = await codex.login_state()
        return state is not None and state.state != "pending"

    await _until(finished)
    done = await codex.login_state()
    assert done is not None and done.state == "done" and done.finished_at
    assert [s["status"] for s in done.steps] == ["done"] * 4
    assert done.steps[3]["detail"] == "OpenAI 确认登录成功"
    assert "登录成功" in done.log[-1]

    # 再登录一次：本机程序已在运行
    await codex.logout()
    again = await codex.login("device_code", {})
    assert again.steps[0]["detail"] == "已在运行" and again.log[0] != done.log[0]
    await _until(finished)

    # 人在浏览器里没有完成授权：等你登录那一步失败，写明原因
    (tmp_path / "home" / "login-denied").touch()
    await codex.logout()
    await codex.login("device_code", {})
    await _until(finished)
    denied = await codex.login_state()
    assert denied is not None and denied.state == "failed"
    assert denied.detail == "登录没有完成：access_denied"
    assert [s["status"] for s in denied.steps] == ["done", "done", "failed", "pending"]
    assert "没有成功（access_denied）" in "\n".join(denied.log)


async def test_codex_device_code_failure_keeps_the_steps(
    codex: CodexDriver, tmp_path: Path
) -> None:
    """申请设备码没成功：查登录进度能看到失败在哪一步、官方程序回的原因。"""
    _login_fails(tmp_path, True)
    with pytest.raises(DriverError) as caught:
        await codex.login("device_code", {})
    state = await codex.login_state()
    assert state is not None and state.state == "failed" and state.finished_at
    assert state.detail == caught.value.message
    assert [s["status"] for s in state.steps] == ["done", "failed", "pending", "pending"]
    assert state.steps[1]["detail"].startswith("向 OpenAI 申请设备码没有成功：")
    assert "申请设备码出错" in "\n".join(state.log)
    assert "error sending request for url" in "\n".join(state.log)


async def test_codex_exiting_while_waiting_fails_the_login(
    codex: CodexDriver, tmp_path: Path
) -> None:
    """等人登录的时候本机程序退出了：不一直显示“等待中”，写明原因。"""
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "login-hold").touch()
    challenge = await codex.login("device_code", {})
    assert challenge.state == "pending"
    await codex.stop()
    state = await codex.login_state()
    assert state is not None and state.state == "failed"
    assert [s["status"] for s in state.steps] == ["done", "done", "failed", "pending"]
    assert "退出了" in state.detail


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
    assert [s["status"] for s in challenge.steps] == ["done"] * 4
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
    kinds = [kind for kind, _ in sink.events]
    assert kinds == ["command", "files", "delta", "delta", "message"]
    # 逐字的增量拼起来就是那一段回答
    assert "".join(p["text"] for k, p in sink.events if k == "delta") == outcome.text
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
    # 聊天模式明说没有工具：不然模型会把“调用工具”写成文字，人以为它在执行
    chat_args = driver._args(spec)
    assert chat_args[chat_args.index("--append-system-prompt") + 1] == CHAT_NOTICE
    assert "cannot execute code" in CHAT_NOTICE
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
    assert [k for k, _ in sink.events] == ["delta", "delta", "message", "command", "files"]
    # 逐字的增量拼起来就是那一段回答；思考的增量、子代理的增量不算
    assert "".join(p["text"] for k, p in sink.events if k == "delta") == first.text
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
    assert "--append-system-prompt" not in driver._args(read_only)
    full = SessionSpec(
        mode="dev", model=None, workdir=tmp_path / "w", sandbox="exec", native_id=native
    )
    assert "perm=bypassPermissions" in (await driver.run_turn(full, "x", Sink())).text


async def test_cli_new_native_session_starts_without_history(tmp_path: Path) -> None:
    """清空上下文（T-0255）：换一个新的原生会话号后官方程序从头开始，旧会话的记录还在。"""
    driver = _cli(tmp_path)
    await driver.login("api_key", {"api_key": "sk-" + "ant-test-key"})
    native = await driver.create(None)  # type: ignore[arg-type]
    spec = SessionSpec(mode="chat", model="sonnet", workdir=tmp_path / "w", native_id=native)
    await driver.run_turn(spec, "第一条", Sink())
    assert "--resume" in driver._args(spec)
    assert (await driver.run_turn(spec, "history", Sink())).text.endswith("history=1")
    fresh = replace(spec, native_id=await driver.create(None))  # type: ignore[arg-type]
    assert "--session-id" in driver._args(fresh) and "--resume" not in driver._args(fresh)
    assert (await driver.run_turn(fresh, "history", Sink())).text.endswith("history=0")
    assert len(await driver.read(native, None)) == 4


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
    # CodeBuddy 没有核对过推理强度的参数：带了也不传
    assert driver.profile.effort_flag == ""
    assert not (await driver.status()).logged_in
    await driver.login("api_key", {"api_key": "cb-key"})
    status = await driver.status()
    assert status.logged_in and "发送时才能确认" in status.detail
    spec = SessionSpec(
        mode="chat", model="auto", workdir=tmp_path / "w", native_id=await driver.create(None)
    )  # type: ignore[arg-type]
    said = await driver.run_turn(replace(spec, effort="high"), "你好", Sink())
    assert said.text.endswith(":: 你好") and "effort=None" in said.text


async def test_claude_models_and_effort(tmp_path: Path) -> None:
    """Claude 的模型取自锁定版本 2.1.323 的模型菜单：别名在前、具体版本在后，各带推理强度；
    选了推理强度就按 --effort 交给官方程序。"""
    driver = _cli(tmp_path)
    models = await driver.models("chat")
    ids = [m.id for m in models]
    assert ids[:4] == ["sonnet", "opus", "fable", "haiku"]
    latest = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-haiku-5-5"}
    assert latest <= set(ids)
    by_id = {m.id: m for m in models}
    assert [m.id for m in models if m.default] == ["sonnet"]
    assert by_id["claude-opus-5-5"].efforts == ("low", "medium", "high", "xhigh", "max")
    assert by_id["claude-sonnet-4-6"].efforts == ("low", "medium", "high", "max")
    assert by_id["claude-haiku-4-5-20251001"].efforts == ()
    await driver.login("oauth_token", {"oauth_token": "tok"})
    spec = SessionSpec(
        mode="chat",
        model="claude-opus-5-5",
        workdir=tmp_path / "w",
        effort="xhigh",
        native_id=await driver.create(None),  # type: ignore[arg-type]
    )
    said = await driver.run_turn(spec, "你好", Sink())
    assert "model=claude-opus-5-5 effort=xhigh" in said.text
    plain = await driver.run_turn(replace(spec, effort=None), "再来", Sink())
    assert "effort=None" in plain.text


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
        # 逐步进度（T-0276）：失败在“申请登录网址”那一步，原因挂在那一步；后面的步骤没轮到
        failed = await driver.login_state()
        assert failed is not None and failed.state == "failed" and failed.finished_at
        assert [(s["key"], s["status"]) for s in failed.steps] == [
            ("start", "done"),
            ("request_url", "failed"),
            ("wait_user", "pending"),
            ("finish", "pending"),
        ]
        assert "account.login.failed" in failed.steps[1]["detail"]
        assert failed.steps[0]["detail"].startswith("用时 ")
        _mark(tmp_path, "web-login-fails", on=False)

        challenge = await driver.login("web_intl", {})
        assert challenge.state == "pending" and challenge.user_code is None
        assert challenge.verification_url == "https://example.test/login?site=external"
        assert [s["status"] for s in challenge.steps] == ["done", "done", "running", "pending"]
        assert challenge.started_at and not challenge.finished_at
        # 运行信息：这边做了什么、官方程序本机服务的输出；访问口令替换成 ***
        password = driver._web._server.password
        log = "\n".join((await driver.login_state()).log)
        assert "--serve（只监听 127.0.0.1:" in log and "登录服务已就绪" in log
        assert "向 CodeBuddy 申请登录网址（登录方式 external）" in log
        assert "Gateway listening on" in log and "password=***" in log
        assert password not in log and "access token ***" in log
        # 人还没登录完：不算已登录
        assert (await driver.login_state()).state == "pending"
        assert not (await driver.status()).logged_in

        # 重新开始一次：上一次的等待任务被取消时不改进度（原来会写成“等了 10 分钟没有完成”），
        # 新的登录服务照常在跑
        again = await driver.login("web_intl", {})
        server = driver._web._server
        await asyncio.sleep(0.3)
        assert driver._web._server is server and server.proc.returncode is None
        assert again.state == "pending" and [s["status"] for s in again.steps][2] == "running"

        _mark(tmp_path, "web-login-done")  # 人在网页上登录完了
        done = await _login_done(driver)
        assert done.state == "done" and done.finished_at
        assert [s["status"] for s in done.steps] == ["done", "done", "done", "done"]
        assert done.steps[3]["detail"] == "账号：测试账号"
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


def test_turn_usage_prefers_the_turn_total_and_reads_credits() -> None:
    """CodeBuddy 的 result.usage 是会话累计的；这一回合的 token 和积分在 turnUsage.total 里。"""
    result = {
        "usage": {"input_tokens": 900, "output_tokens": 90},
        "_meta": {
            "codebuddy.ai/turnUsage": {
                "kind": "snapshot",
                "total": {
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                    "credits": 2.504,
                    "creditStatus": "known",
                },
            }
        },
    }
    assert _turn_usage(result) == {
        "credits": 2.5,
        "credit_status": "known",
        "input_tokens": 100,
        "output_tokens": 10,
    }
    # 没报积分：不写成 0；状态照实带着
    unknown = {"_meta": {"codebuddy.ai/turnUsage": {"total": {"creditStatus": "unknown"}}}}
    assert _turn_usage(unknown) == {
        "credit_status": "unknown",
        "input_tokens": 0,
        "output_tokens": 0,
    }
    # 不是数的积分不要；没有 turnUsage（Claude Code、旧版本）用 result.usage
    bad = {"_meta": {"codebuddy.ai/turnUsage": {"total": {"credits": "3", "creditStatus": "x"}}}}
    assert _turn_usage(bad) == {"input_tokens": 0, "output_tokens": 0}
    plain = {"usage": {"input_tokens": 5, "output_tokens": 6}}
    assert _turn_usage(plain) == {"input_tokens": 5, "output_tokens": 6}
    assert _turn_usage({"usage": None, "_meta": "x"}) == {"input_tokens": 0, "output_tokens": 0}


# ---- 上下文与压缩（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.1）----
async def test_cli_context_usage_and_compaction(tmp_path: Path) -> None:
    """Claude Code：上下文取主模型最后一次调用的 usage，窗口取 modelUsage 里主模型的；
    compact_boundary 报成一次压缩（触发方式、压缩前后的 token 数）。子代理的调用不算。"""
    driver = _cli(tmp_path)
    await driver.login("oauth_token", {"oauth_token": "tok"})
    spec = SessionSpec(
        mode="chat", model="sonnet", workdir=tmp_path / "w", native_id=await driver.create(None)
    )  # type: ignore[arg-type]
    first = await driver.run_turn(spec, "你好", Sink())
    assert first.context is not None
    assert (first.context.used, first.context.window) == (1000, 200000)
    sink = Sink()
    second = await driver.run_turn(spec, "compact-auto", sink)
    compacted = [p for k, p in sink.events if k == "compacted"]
    assert compacted == [{"trigger": "auto", "pre_tokens": 150000, "post_tokens": 9000}]
    assert second.context is not None and second.context.used == 2000


async def test_codebuddy_context_usage_and_compaction(tmp_path: Path) -> None:
    """CodeBuddy Code：上下文在 result._meta；压缩只有“正在压缩”的状态事件，开始到结束算一次。"""
    driver = _cli(tmp_path, codebuddy_profile)
    await driver.login("api_key", {"api_key": "cb-key"})
    spec = SessionSpec(
        mode="chat", model="auto", workdir=tmp_path / "w", native_id=await driver.create(None)
    )  # type: ignore[arg-type]
    sink = Sink()
    outcome = await driver.run_turn(spec, "compact-auto", sink)
    assert [p for k, p in sink.events if k == "compacted"] == [{"trigger": "auto"}]
    assert outcome.context is not None
    assert (outcome.context.used, outcome.context.window) == (1007, 168000)


async def test_codex_context_usage_and_compaction(codex: CodexDriver, tmp_path: Path) -> None:
    """Codex：窗口是 tokenUsage.modelContextWindow，用量是最近一次的 totalTokens 去掉推理输出；
    contextCompaction 条目算一次压缩。"""
    spec = SessionSpec(mode="chat", model="gpt-a", workdir=tmp_path / "w")
    spec = replace(spec, native_id=await codex.create(spec))
    first = await codex.run_turn(spec, "你好", Sink())
    assert first.context is not None
    assert (first.context.used, first.context.window) == (5, 200000)
    sink = Sink()
    second = await codex.run_turn(spec, "compact-auto", sink)
    assert [p for k, p in sink.events if k == "compacted"] == [{"trigger": "auto"}]
    assert second.context is not None and second.context.used == 1005


# ---- 第 2 期：手动压缩、自动压缩的触发点（4.2）----
async def test_cli_manual_compaction_and_autocompact_threshold(tmp_path: Path) -> None:
    """手动压缩：把 /compact [要保留什么] 当提问发；收到压缩事件才算压成。
    触发点（百分比）按官方程序的环境变量带上；不设就不带。"""
    for profile, env in (
        (claude_profile, "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"),
        (codebuddy_profile, "CODEBUDDY_AUTOCOMPACT_PCT_OVERRIDE"),
    ):
        (tmp_path / env).mkdir()
        driver = _cli(tmp_path / env, profile)
        await driver.login("api_key", {"api_key": "k"})
        spec = SessionSpec(
            mode="chat", model=None, workdir=tmp_path / "w", native_id=await driver.create(None)
        )  # type: ignore[arg-type]
        assert " ac=None ::" in (await driver.run_turn(spec, "你好", Sink())).text
        assert driver._autocompact(replace(spec, autocompact=60)) == {env: "60"}
        again = await driver.run_turn(replace(spec, autocompact=60), "再问", Sink())
        assert " ac=60 ::" in again.text
        sink = Sink()
        outcome = await driver.compact(spec, "保留接口约定", sink)
        assert outcome.compactions == 1
        assert [p["trigger"] for k, p in sink.events if k == "compacted"] == ["manual"]
        asked = [m.text for m in await driver.read(spec.native_id or "", None) if m.role == "user"]
        assert asked[-1] == "/compact 保留接口约定"


async def test_codex_manual_compaction_and_autocompact_config(
    codex: CodexDriver, tmp_path: Path
) -> None:
    """Codex：thread/compact/start（带不了说明）；
    触发点按 config.model_auto_compact_token_limit 带上。"""
    spec = SessionSpec(mode="chat", model="gpt-a", workdir=tmp_path / "w", autocompact=150000)
    spec = replace(spec, native_id=await codex.create(spec))
    sink = Sink()
    outcome = await codex.compact(spec, "保留接口约定", sink)
    assert outcome.compactions == 1 and [k for k, _ in sink.events] == ["compacted"]
    state = json.loads((tmp_path / "home" / "fake-state.json").read_text())
    assert state["threads"][spec.native_id]["config"] == {"model_auto_compact_token_limit": 150000}
    plain = SessionSpec(mode="chat", model="gpt-a", workdir=tmp_path / "w")
    plain_id = await codex.create(plain)
    state = json.loads((tmp_path / "home" / "fake-state.json").read_text())
    assert state["threads"][plain_id]["config"] is None


# ---- 第 4 期：压缩钩子、固定带着的参考资料（4.3、6.3）----
def test_hook_script_says_what_to_keep_and_leaves_the_summary(tmp_path: Path) -> None:
    """钩子脚本：压缩前输出固定的说明；压缩后把摘要写到管理器能读的地方；出错安静退出。"""
    import subprocess

    from bridge import hooks

    script = hooks.install(tmp_path)
    run = [sys.executable, "-I", str(script)]
    before = subprocess.run([*run, "pre_compact"], input="{}", capture_output=True, text=True)
    assert before.returncode == 0 and before.stdout == hooks.PRESERVE
    payload = {"session_id": "abc-1", "trigger": "auto", "compact_summary": "结论和决定：A"}
    env = {"BRIDGE_HOOK_DIR": str(tmp_path / "hooks")}
    after = subprocess.run(
        [*run, "post_compact"], input=json.dumps(payload), capture_output=True, text=True, env=env
    )
    assert after.returncode == 0 and after.stdout == ""
    line = json.loads((tmp_path / "hooks" / "summaries" / "abc-1.jsonl").read_text())
    assert (line["trigger"], line["summary"]) == ("auto", "结论和决定：A")
    # 会话号不合法、输入不是 JSON：什么也不写，也不让压缩失败
    for bad in ('{"session_id": "../x", "compact_summary": "y"}', "not json"):
        done = subprocess.run(
            [*run, "post_compact"], input=bad, capture_output=True, text=True, env=env
        )
        assert done.returncode == 0
    assert sorted(p.name for p in (tmp_path / "hooks" / "summaries").iterdir()) == ["abc-1.jsonl"]


async def test_cli_hooks_hand_back_the_platform_summary(tmp_path: Path) -> None:
    """打开钩子：按回合经 --settings 带上；压缩时官方程序跑钩子，
    摘要留给管理器取走（取一次就没了）。
    对话会话的固定参考资料跟着系统提示带上。"""
    from bridge import hooks

    driver = _cli(tmp_path)
    driver.hook_script = hooks.install(tmp_path / "data")
    driver.hook_dir = driver.hook_script.parent
    await driver.login("api_key", {"api_key": "k"})
    spec = SessionSpec(
        mode="chat",
        model=None,
        workdir=tmp_path / "w",
        native_id=await driver.create(None),  # type: ignore[arg-type]
        pinned="《部署约定》先备份",
    )
    args = driver._args(spec)
    assert "--settings" not in args
    assert args[args.index("--append-system-prompt") + 1].endswith("\n\n《部署约定》先备份")
    with_hooks = replace(spec, hooks=True)
    assert "--settings" in driver._args(with_hooks)
    await driver.compact(with_hooks, "", Sink())
    (summary,) = driver.take_summaries(spec.native_id or "")
    assert summary["trigger"] == "manual" and summary["summary"].startswith(
        "结论和决定：先备份再升级"
    )
    assert hooks.PRESERVE in summary["summary"]  # 压缩前的说明并进了压缩说明
    assert driver.take_summaries(spec.native_id or "") == []
