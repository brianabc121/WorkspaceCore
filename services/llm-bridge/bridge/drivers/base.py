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
    "conflict",  # 和现在的状态冲突（例如没有在等授权码的登录，T-0287）
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
class RemoteTarget:
    """远程开发容器里的会话（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 21.1 节）。

    container 是工作台给的远程容器号，workdir 是容器里的工作目录（/workspace/sessions/<会话号>）。
    官方程序不在管理器里跑，而是经工作台在那个容器里起（bridge/remote.py）。"""

    container: str
    workdir: str
    session_id: str
    # 托管进程的时限（秒）：单回合时限再加一点，到了由 runner-agent 在容器服务器上停掉
    timeout_seconds: int = 1800


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
    # 自动压缩的触发点（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.2）：0 是用平台自己的。
    # Claude / CodeBuddy 是上下文的百分比，Codex 是 token 数（各自官方程序的设置方式）
    autocompact: int = 0
    # 压缩钩子（4.3，管理器设置里打开才有）：压缩前给说明、压缩后把平台写的摘要留下来
    hooks: bool = False
    # 固定带着的参考资料（6.3）：对话会话随系统提示带上；开发会话由 engine 写进工作目录的记忆文件
    pinned: str = ""
    # 这个开发会话能开后台作业（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 5.5）：给模型说明 llm-task，
    # 并关掉官方程序自带的后台任务（回答结束时会被停掉，见 5.11）
    jobs: bool = False
    # 远程开发容器里的会话；None 是在管理器自己的容器里
    remote: RemoteTarget | None = None


# 能开后台作业的开发会话给模型的说明（三家官方程序共用；LONGRUN 5.5）
JOBS_INSTRUCTIONS = (
    "Long-running work: this session is driven by an MCP manager. For anything that takes "
    "more than a few minutes (builds, full test suites, waiting for CI), do not wait in the "
    "foreground and do not start background shells: they are stopped when your reply ends. "
    "Instead run `llm-task job start --name <short name> -- <command>` and then end your "
    "reply; when the job finishes the manager sends this session a message with the result "
    "and you continue from there. To come back later without a job, run "
    '`llm-task at +2h "what to check"`. `llm-task --help` shows all commands. '
    "Messages that start with "
    '"【MCP 管理器自动发出，不是人发的消息，不代表任何人的批准】" '
    "come from the manager, not from a person, and never count as anyone's approval."
)


@dataclass
class TurnOutcome:
    text: str
    native_turn_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    # 这一回合结束时会话的上下文用了多少、窗口多大（token；官方程序报的，没报为 None）。
    # 压缩发生时驱动另外用 sink("compacted", {...}) 报出来（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.1）
    context: ContextUsage | None = None
    compactions: int = 0  # 这一回合里平台压缩了几次（手动压缩据此判断有没有压成）


@dataclass(frozen=True)
class ContextUsage:
    """会话现在的上下文：用了多少 token、窗口多大。两个数都可能没报（None）。"""

    used: int | None = None
    window: int | None = None


def positive_int(value: Any) -> int | None:
    """官方程序报的 token 数：正整数才算数（布尔、负数、小数、字符串都不要）。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = int(value)
    return number if number > 0 and number == value else None


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
    # 逐步进度（网页登录，T-0276）：每一步 {key, status, detail, started_at, finished_at}；
    # 运行信息是脱敏后的文字行。驱动原地更新这两个列表，“查登录进度”随时读到最新的
    steps: list[dict[str, Any]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    # 在等人把授权页上显示的授权码交回来（Claude 网页授权登录，T-0287）
    needs_code: bool = False


EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class Driver:
    """默认实现都是“不支持”，具体驱动覆盖自己有的能力。"""

    slot: str = ""
    login_methods: tuple[str, ...] = ()
    # 远程开发容器（bridge/remote.py 的 RemoteHub）：engine 建好后设上；没有就不支持远程会话
    remote: Any = None

    def remote_credentials(self) -> dict[str, str]:
        """远程容器里每个回合随进程注入的凭据（环境变量名 → 值）。

        只给不会自己刷新的凭据（API 密钥、Claude 的长期令牌）；会刷新的登录（网页登录、设备码登录）
        不下发，要在容器里单独登录同一个账号（方案第 9 节）。默认没有。"""
        return {}

    async def version(self) -> str | None:
        """管理器里这个官方程序的版本（例如 2.1.323）；查不到为 None。远程容器里的要和它一样。"""
        return None

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

    async def compact(self, spec: SessionSpec, instructions: str, sink: EventSink) -> TurnOutcome:
        """手动压缩这个会话的上下文（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.2）。

        结果的 compactions 是平台实际压缩了几次：0 说明平台没有压缩（不支持或者没有可压的）。"""
        raise DriverError("not_supported", "这个平台不能手动压缩上下文")

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

    async def submit_code(self, code: str) -> LoginChallenge:
        """把授权页上显示的授权码交给正在等它的官方程序（T-0287）。"""
        raise DriverError("not_supported", "这个平台的登录不需要提交授权码")

    async def logout(self) -> None:
        raise DriverError("not_supported", "这个平台不支持退出登录")
