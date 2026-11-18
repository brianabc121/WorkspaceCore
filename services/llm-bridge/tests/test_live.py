"""对真实官方程序的实测：默认跳过。

    BRIDGE_LIVE_CLI_DIR=<装有 codex / claude / codebuddy 的目录> pytest tests/test_live.py

- Codex、CodeBuddy 的用例不需要登录，也不发出真实回合；
- Claude 的用例要显式给一个 API 密钥（BRIDGE_LIVE_CLAUDE_API_KEY），会发两条很短的消息。
结果记录在 docs/decisions/2026-11-13-llm-bridge-verification.md。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from bridge.drivers.base import DriverError, SessionSpec
from bridge.drivers.cli_stream import CliStreamDriver, claude_profile, codebuddy_profile
from bridge.drivers.codex import CodexDriver

CLI_DIR = os.environ.get("BRIDGE_LIVE_CLI_DIR")
CLAUDE_KEY = os.environ.get("BRIDGE_LIVE_CLAUDE_API_KEY")
pytestmark = pytest.mark.skipif(not CLI_DIR, reason="没有设置 BRIDGE_LIVE_CLI_DIR")


async def _sink(kind: str, payload: dict[str, Any]) -> None:
    return None


async def test_live_codex(tmp_path: Path) -> None:
    driver = CodexDriver(f"{CLI_DIR}/codex", tmp_path / "home")
    try:
        assert not (await driver.status()).logged_in
        models = await driver.models("chat")
        assert models and sum(m.default for m in models) == 1
        workdir = tmp_path / "work"
        workdir.mkdir()
        native = await driver.create(SessionSpec(mode="chat", model=models[0].id, workdir=workdir))
        # 还没有发过消息的线程不出现在列表里；读取返回空
        assert await driver.read(native, None) == []
        await driver.archive(native)
    finally:
        await driver.stop()


async def test_live_codebuddy(tmp_path: Path) -> None:
    driver = CliStreamDriver(
        codebuddy_profile(f"{CLI_DIR}/codebuddy"), tmp_path / "home" / "cfg", tmp_path / "secrets"
    )
    models = [m.id for m in await driver.models("chat")]
    assert "auto" in models and len(models) > 3
    spec = SessionSpec(mode="chat", model="auto", workdir=tmp_path / "w", native_id="live-test-1")
    with pytest.raises(DriverError) as caught:
        await driver.run_turn(spec, "你好", _sink)
    assert caught.value.kind == "not_logged_in"
    # 未登录的回合也留下了会话记录，列表读得到
    assert [n.native_id for n in await driver.list_native(10)] == ["live-test-1"]


async def test_live_claude_model_and_effort_flags(tmp_path: Path) -> None:
    """不用登录：锁定版本的帮助文本里有驱动用到的 --model 和 --effort，
    强度的可选值和 CLAUDE_MODELS 一致（T-0288）。"""
    process = await asyncio.create_subprocess_exec(
        f"{CLI_DIR}/claude",
        "--help",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "DISABLE_AUTOUPDATER": "1"},
    )
    out, _ = await asyncio.wait_for(process.communicate(), 60)
    text = out.decode("utf-8", "replace")
    assert "--model <model>" in text and "--effort <level>" in text, text[:2000]
    assert "low, medium, high, xhigh, max" in text
    levels = {e for m in claude_profile("claude").models for e in m.efforts}
    assert levels == {"low", "medium", "high", "xhigh", "max"}


@pytest.mark.skipif(not CLAUDE_KEY, reason="没有设置 BRIDGE_LIVE_CLAUDE_API_KEY")
async def test_live_claude(tmp_path: Path) -> None:
    driver = CliStreamDriver(
        claude_profile(f"{CLI_DIR}/claude"), tmp_path / "home" / "cfg", tmp_path / "secrets"
    )
    await driver.login("api_key", {"api_key": CLAUDE_KEY or ""})
    assert (await driver.status()).logged_in
    native = await driver.create(SessionSpec(mode="chat", model="haiku", workdir=tmp_path / "w"))
    spec = SessionSpec(mode="chat", model="haiku", workdir=tmp_path / "w", native_id=native)
    first = await driver.run_turn(spec, "只回答两个字：收到", _sink)
    assert "收到" in first.text and first.usage["output_tokens"] > 0
    second = await driver.run_turn(spec, "我上一条消息要你回答哪两个字？只回答那两个字", _sink)
    assert "收到" in second.text  # --resume 续上了同一个会话
    (listed,) = await driver.list_native(10)
    assert listed.native_id == native and "收到" in listed.title
    roles = [m.role for m in await driver.read(native, None)]
    assert roles == ["user", "assistant", "user", "assistant"]
