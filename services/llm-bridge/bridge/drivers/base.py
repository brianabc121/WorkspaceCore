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
    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        quota: bool = False,
        resets_at: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        # 额度用完造成的中断（不是普通的限速）：到重置时间后可以自动续跑
        self.quota = quota
        self.resets_at = resets_at


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
    # 这一回合随提问带上的图片：(绝对路径, 类型)。别的文件由 engine 放进工作目录，在提问里注明
    images: tuple[tuple[str, str], ...] = ()


@dataclass
class TurnOutcome:
    text: str
    native_turn_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    # 回合结束时上下文用了多少（T-0257）：{"used": token 数, "window": 窗口大小}；官方程序没报的不写
    context: dict[str, int] = field(default_factory=dict)


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

    async def steer(self, spec: SessionSpec, text: str) -> bool:
        """把一条消息送进这个会话正在跑的回合（插入消息）。

        返回 True 表示平台确认收下了；不支持、回合刚好结束、平台拒绝都返回 False，
        由 engine 降级成“中断后合并重发”。默认不支持。
        """
        return False

    async def cancel(self, spec: SessionSpec) -> None:
        return None

    # 能不能随时向平台查额度（不能的只在跑回合时由官方程序顺带报出来）
    can_query_quota = False
    # 平台给不给额度（已用百分比、重置时间）；不给的平台页面上要说明白，不让人干等
    reports_quota = True

    async def quota(self) -> list[dict[str, Any]] | None:
        """现在向平台查一次额度（各窗口的已用百分比和重置时间）。

        返回 None 表示这个平台不能随时查：它的额度只在跑回合时由官方程序顺带报出来
        （回合里用 sink("quota", {"windows": [...]}) 交给 engine）。
        """
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
