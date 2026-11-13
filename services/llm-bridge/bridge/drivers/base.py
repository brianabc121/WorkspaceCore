"""驱动接口：每个账号槽位一个驱动，形状参照 ACP（新建 / 发送 / 取消 / 列表 / 读取 / 归档）。

驱动只做“和官方程序对话”这一件事；会话归属、排队、幂等、限额都在 engine.py。
不支持的操作抛 DriverError("not_supported")，不假装成功。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ErrorKind = Literal[
    "not_logged_in",  # 没登录或凭据失效
    "rate_limited",  # 平台的用量或速率限制
    "invalid",  # 参数或模型不对
    "not_supported",  # 这个通道没有这项能力
    "platform_error",  # 平台或官方程序出错
    "timeout",  # 超时（发送类操作的结果未知）
    "unavailable",  # 官方程序起不来
]

Mode = Literal["chat", "dev", "cloud"]
Sandbox = Literal["read_only", "workspace_write", "exec"]


class DriverError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class AccountStatus:
    logged_in: bool
    identity: str | None = None  # 账号标识（邮箱等）；取不到时为 None
    method: str | None = None  # chatgpt / api_key / oauth_token / ...
    detail: str = ""


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    description: str = ""
    default: bool = False
    efforts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionSpec:
    """engine 交给驱动的一个会话。"""

    mode: Mode
    model: str | None
    workdir: Path
    sandbox: Sandbox = "read_only"
    network: bool = False
    effort: str | None = None
    native_id: str | None = None
    turn_count: int = 0
    env: dict[str, str] = field(default_factory=dict)  # 管理员给环境勾选的变量


@dataclass
class TurnOutcome:
    text: str
    native_turn_id: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NativeSession:
    native_id: str
    title: str
    model: str | None
    workdir: str | None
    created_at: float | None
    updated_at: float | None


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    text: str
    at: float | None = None


@dataclass(frozen=True)
class LoginChallenge:
    """登录进行到哪一步：需要用户去网页输入验证码时给出网址和验证码。"""

    state: Literal["pending", "done", "failed"]
    verification_url: str | None = None
    user_code: str | None = None
    detail: str = ""


EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class Driver:
    """默认实现都是“不支持”，具体驱动覆盖自己有的能力。"""

    slot: str = ""
    login_methods: tuple[str, ...] = ()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def supports(self, mode: Mode) -> bool:
        return False

    async def status(self) -> AccountStatus:
        raise DriverError("not_supported", "这个平台查不了登录状态")

    async def models(self, mode: Mode) -> list[ModelInfo]:
        return []

    async def create(self, spec: SessionSpec) -> str:
        """新建会话，返回平台原生会话号。"""
        raise DriverError("not_supported", "这个通道不能新建会话")

    async def run_turn(self, spec: SessionSpec, text: str, sink: EventSink) -> TurnOutcome:
        raise DriverError("not_supported", "这个通道不能发送消息")

    async def cancel(self, spec: SessionSpec) -> None:
        return None

    async def list_native(self, limit: int) -> list[NativeSession]:
        raise DriverError("not_supported", "这个平台没有会话列表")

    async def read(self, native_id: str, workdir: str | None) -> list[Message]:
        raise DriverError("not_supported", "这个平台读不了会话内容")

    async def archive(self, native_id: str) -> None:
        return None

    async def login(self, method: str, payload: dict[str, str]) -> LoginChallenge:
        raise DriverError("not_supported", "这个平台不支持这种登录方式")

    async def login_state(self) -> LoginChallenge | None:
        return None

    async def logout(self) -> None:
        raise DriverError("not_supported", "这个平台不支持退出登录")
