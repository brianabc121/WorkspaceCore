"""去掉单列的 ChatGPT 平台（T-0280）：旧名字 chatgpt 按 codex 处理，接口说明里只列现在的平台；
原来只能聊天的授权保持只能聊天；没升级的管理器照样能用。"""

from typing import Any

import pytest

from app.modules.llm_bridge.client import (
    BridgeCaller,
    BridgeCallError,
    create_session,
    platforms_header,
)
from app.modules.llm_bridge.schemas import (
    ALL_PLATFORMS,
    LlmBridgeGrantIn,
    canonical_platform,
    canonical_platforms,
    grant_allows,
    grant_platforms,
)
from app.modules.llm_bridge.service import legacy_channels
from app.modules.llm_bridge.tool_schemas import ChannelInput, SessionCreateInput


def test_old_name_is_accepted_as_codex() -> None:
    assert ALL_PLATFORMS == ("codex", "claude", "workbuddy")
    assert canonical_platform("chatgpt") == "codex"
    assert canonical_platform("claude") == "claude"
    assert canonical_platforms(["chatgpt", "claude", "codex"]) == ["codex", "claude"]
    assert canonical_platforms(None) == []
    # 智能体工具、页面接口收到旧名字都当成 codex
    assert SessionCreateInput(platform="chatgpt", mode="chat").platform == "codex"


def test_descriptions_only_list_current_platforms() -> None:
    schema = ChannelInput.model_json_schema()
    assert schema["properties"]["platform"]["enum"] == ["codex", "claude", "workbuddy"]
    assert "chatgpt" not in schema["properties"]["platform"]["description"]


def test_a_chatgpt_grant_becomes_all_of_codex() -> None:
    """授权里的旧名字 chatgpt 按整个 Codex 处理（人类 2026-11-17 决定，T-0282）；
    只能对话的 codex:chat 由管理员在页面上特意选。"""
    grant = LlmBridgeGrantIn.model_validate({"enabled": True, "platforms": ["chatgpt", "claude"]})
    assert grant.platforms == ["codex", "claude"]
    assert grant_allows(["chatgpt"], "codex", "dev")
    assert grant_platforms(["chatgpt", "codex"]) == ["codex"]
    assert grant_platforms(["codex:chat", "chatgpt"]) == ["codex"]  # 整个 Codex 已经包括对话
    assert grant_platforms(["codex:chat", "codex:chat"]) == ["codex:chat"]
    only_chat = ["codex:chat"]
    assert grant_allows(only_chat, "codex", "chat")
    assert grant_allows(only_chat, "chatgpt", "chat")
    assert not grant_allows(only_chat, "codex", "dev")
    assert grant_allows(only_chat, "codex")  # 平台一级（列会话、看额度）能看到
    assert not grant_allows(only_chat, "claude", "chat")
    assert grant_allows(["codex"], "codex", "dev")
    assert grant_allows([], "claude", "dev")  # 空列表是都允许
    with pytest.raises(ValueError):
        LlmBridgeGrantIn.model_validate({"enabled": True, "platforms": ["claude:chat"]})


def test_platform_header_keeps_old_managers_working() -> None:
    """没升级的管理器那边旧会话的平台还是 chatgpt：给了 codex（或只能对话）就一并带上它。"""
    assert platforms_header(("codex", "claude")) == "codex,claude,chatgpt"
    # 只能对话：旧管理器认不出 codex:chat，只认 chatgpt —— 正好是原来的范围
    assert platforms_header(("codex:chat",)) == "codex:chat,chatgpt"
    assert platforms_header(("claude",)) == "claude"
    assert platforms_header(()) == ""


def test_old_manager_channels_are_merged_into_codex() -> None:
    """没升级的管理器只开了 chatgpt：并进 codex.chat，页面上照样能和 Codex 对话。"""
    old = [
        {
            "platform": "chatgpt",
            "mode": "chat",
            "slot": "openai",
            "enabled": True,
            "available": True,
        },
        {
            "platform": "codex",
            "mode": "chat",
            "slot": "openai",
            "enabled": False,
            "available": False,
        },
        {"platform": "codex", "mode": "dev", "slot": "openai", "enabled": False, "available": True},
        {"platform": "fake", "mode": "chat", "slot": "fake", "enabled": True, "available": True},
    ]
    merged = legacy_channels(old)
    assert [(c["platform"], c["mode"], c["enabled"], c["available"]) for c in merged] == [
        ("codex", "chat", True, True),
        ("codex", "dev", False, True),
    ]
    only_old = legacy_channels([old[0]])
    assert [(c["platform"], c["mode"]) for c in only_old] == [("codex", "chat")]
    assert legacy_channels(None) == []


class _Client:
    """记下发出的新建请求；按名字决定回答还是报错。"""

    def __init__(self, refuse: set[str]) -> None:
        self.refuse = refuse
        self.calls: list[str] = []

    async def tool(self, caller: BridgeCaller, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(arguments["platform"])
        if arguments["platform"] in self.refuse:
            raise BridgeCallError(
                "NOT_SUPPORTED",
                f"这座桥没有启用 {arguments['platform']}（管理员没有让它控制这个平台）",
            )
        return {"session_id": "bs_1", "platform": arguments["platform"]}


async def test_creating_a_codex_chat_on_an_old_manager() -> None:
    chat = {"platform": "codex", "mode": "chat", "request_id": "r1"}
    full = BridgeCaller("agent", "甲", ("write",), ("codex",))
    # 新版本的管理器（或旧版本开着 codex）：一次就成
    client = _Client(set())
    await create_session(client, full, chat)  # type: ignore[arg-type]
    assert client.calls == ["codex"]
    # 旧版本只开着 chatgpt：换旧名字再试一次
    client = _Client({"codex"})
    await create_session(client, full, chat)  # type: ignore[arg-type]
    assert client.calls == ["codex", "chatgpt"]
    # 两个都不行：报第一次的原因
    client = _Client({"codex", "chatgpt"})
    with pytest.raises(BridgeCallError, match="没有启用 codex"):
        await create_session(client, full, chat)  # type: ignore[arg-type]
    # 只能对话的实例：直接用旧名字（新版本当成 codex:chat，旧版本按它的 chatgpt 平台检查）
    only_chat = BridgeCaller("agent", "乙", ("write",), ("codex:chat",))
    client = _Client(set())
    await create_session(client, only_chat, chat)  # type: ignore[arg-type]
    assert client.calls == ["chatgpt"]
    # 开发模式、别的平台不换名字
    client = _Client({"codex"})
    with pytest.raises(BridgeCallError):
        await create_session(client, full, {**chat, "mode": "dev"})  # type: ignore[arg-type]
    assert client.calls == ["codex"]
