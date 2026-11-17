"""去掉单列的 ChatGPT 平台（T-0280）：旧名字 chatgpt 按 codex 处理，接口说明里只列现在的平台。"""

from app.modules.llm_bridge.client import platforms_header
from app.modules.llm_bridge.schemas import (
    ALL_PLATFORMS,
    LlmBridgeGrantIn,
    canonical_platform,
    canonical_platforms,
)
from app.modules.llm_bridge.tool_schemas import ChannelInput, SessionCreateInput


def test_old_name_is_accepted_as_codex() -> None:
    assert ALL_PLATFORMS == ("codex", "claude", "workbuddy")
    assert canonical_platform("chatgpt") == "codex"
    assert canonical_platform("claude") == "claude"
    assert canonical_platforms(["chatgpt", "claude", "codex"]) == ["codex", "claude"]
    assert canonical_platforms(None) == []
    # 智能体工具、页面接口收到旧名字都当成 codex
    assert SessionCreateInput(platform="chatgpt", mode="chat").platform == "codex"
    grant = LlmBridgeGrantIn.model_validate({"enabled": True, "platforms": ["chatgpt", "claude"]})
    assert grant.platforms == ["codex", "claude"]


def test_descriptions_only_list_current_platforms() -> None:
    schema = ChannelInput.model_json_schema()
    assert schema["properties"]["platform"]["enum"] == ["codex", "claude", "workbuddy"]
    assert "chatgpt" not in schema["properties"]["platform"]["description"]


def test_platform_header_keeps_old_managers_working() -> None:
    """没升级的管理器那边旧会话的平台还是 chatgpt：给了 codex 就一并放行。"""
    assert platforms_header(("codex", "claude")) == "codex,claude,chatgpt"
    assert platforms_header(("claude",)) == "claude"
    assert platforms_header(()) == ""
