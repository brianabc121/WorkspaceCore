"""平台、模式与账号槽位的对应关系（docs/v2/LLM_BRIDGE_PLAN.md 第 4.3、6 节）。

一个账号槽位对应一个登录目录和一个驱动。ChatGPT 聊天和 Codex 用的是同一个 ChatGPT 登录，
所以共用槽位 openai：一座桥里它们只能是同一个账号。
"""

from __future__ import annotations

from dataclasses import dataclass

from bridge.drivers.base import Mode


@dataclass(frozen=True)
class Channel:
    platform: str
    mode: Mode
    slot: str
    available: bool
    note: str = ""


_CLOUD_CODEX = (
    "接口桩：`codex cloud exec / list / status` 的输出格式需要登录后才能核验，"
    "核验前不开放（方案 D6，第二批）"
)
_CLOUD_CLAUDE = "接口桩：Claude 云端会话没有非交互的列表和结果接口（方案第 6.3 节）"

CHANNELS: tuple[Channel, ...] = (
    Channel(
        "chatgpt",
        "chat",
        "openai",
        True,
        "用同一个 ChatGPT 登录下的 Codex 以纯聊天方式运行；不出现在 chatgpt.com",
    ),
    Channel("codex", "chat", "openai", True),
    Channel("codex", "dev", "openai", True),
    Channel("codex", "cloud", "openai", False, _CLOUD_CODEX),
    Channel("claude", "chat", "claude", True),
    Channel("claude", "dev", "claude", True),
    Channel("claude", "cloud", "claude", False, _CLOUD_CLAUDE),
    Channel("workbuddy", "chat", "workbuddy", True, "由 CodeBuddy Code 命令行驱动"),
    Channel("workbuddy", "dev", "workbuddy", True, "由 CodeBuddy Code 命令行驱动"),
)
FAKE_CHANNELS: tuple[Channel, ...] = (
    Channel("fake", "chat", "fake", True),
    Channel("fake", "dev", "fake", True),
)
SLOT_LABELS = {
    "openai": "ChatGPT / Codex",
    "claude": "Claude",
    "workbuddy": "WorkBuddy（CodeBuddy）",
    "fake": "Fake（测试）",
}


def find(channels: tuple[Channel, ...], platform: str, mode: str) -> Channel | None:
    for channel in channels:
        if channel.platform == platform and channel.mode == mode:
            return channel
    return None
