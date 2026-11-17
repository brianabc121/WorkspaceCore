"""平台、模式与账号槽位的对应关系（docs/v2/LLM_BRIDGE_PLAN.md 第 4.3、6 节）。

一个账号槽位对应一个登录目录和一个驱动。Codex 用 ChatGPT 账号登录，槽位叫 openai。

原来还有一个平台 “ChatGPT”（只有聊天模式），其实就是 Codex app-server 的纯聊天通道（只读沙箱、
空目录），和 codex.chat 是同一条路。T-0280 起去掉这个平台，只留 Codex；旧名字 chatgpt 一律
按 codex 处理（canonical），旧库里的记录在 store 升级时改过来。
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

# 已经去掉的平台的旧名字 → 现在的平台
ALIASES = {"chatgpt": "codex"}


def canonical(platform: str) -> str:
    """旧名字换成现在的平台名（chatgpt → codex）；别的原样返回。"""
    return ALIASES.get(platform, platform)


def grant_token(item: str) -> str:
    """调用方“能用哪些平台”里的一项（X-Bridge-Platforms）。

    一项是平台（不限用法），或“平台:用法”（只能用这种用法，例如 codex:chat 只能对话）。
    旧名字 chatgpt 当时就是只能对话的 Codex：在这里换成 codex:chat，不放宽成整个 codex（T-0280）。
    """
    if item == "chatgpt":
        return "codex:chat"
    platform, sep, mode = item.partition(":")
    return f"{canonical(platform)}{sep}{mode}"


CHANNELS: tuple[Channel, ...] = (
    Channel(
        "codex",
        "chat",
        "openai",
        True,
        "纯聊天：只读沙箱、空目录，不读写文件、不运行命令；不出现在 chatgpt.com 的对话列表里",
    ),
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
    "openai": "Codex",
    "claude": "Claude",
    "workbuddy": "CodeBuddy",
    "fake": "Fake（测试）",
}


def find(channels: tuple[Channel, ...], platform: str, mode: str) -> Channel | None:
    platform = canonical(platform)
    for channel in channels:
        if channel.platform == platform and channel.mode == mode:
            return channel
    return None
