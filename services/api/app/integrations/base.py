"""Connector 的能力分层与统一错误分类（docs/DEVELOPMENT_SPEC.md 第 7 节）。

各 Connector 只处理外部协议与归一化；把外部响应翻译成这里的分类后交给领域层，
由领域层按 Sprint 3 契约第 8 节映射成 HTTP 错误。错误对象里不保存外部响应原文。

能力按 Protocol 分层，Connector 只实现自己支持的部分（规范：不要求 AFFiNE 实现不支持的能力）：
- `Connector`：标识与健康检查（可达性、实例版本）
- `Readable`：全量 / 增量读取项目与任务（投影同步与对账）
- `Writable`：任务写入；从不自动重试，结果未知时调用方用 `find_created_tasks` 等查询（契约第 7 节）
- `WebhookCapable`：能否为资源注册 Webhook（注册与签名校验在 T-0021）
- Searchable：V1 不需要——搜索走本地投影
领域层依赖这些 Protocol，而不是具体的客户端类。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, TypeVar

# 规范第 7 节的分类，加上 NOT_FOUND（外部资源不存在：读取时作为 tombstone 的依据）
ConnectorErrorKind = Literal[
    "AUTH_FAILED",
    "RATE_LIMITED",
    "TIMEOUT_UNKNOWN",
    "UNAVAILABLE",
    "NOT_SUPPORTED",
    "CONFLICT",
    "VALIDATION_FAILED",
    "NOT_FOUND",
]


class ConnectorError(Exception):
    """访问第三方失败。

    - `resource_level`：AUTH_FAILED 时区分“凭证失效”（False，影响整个集成）
      与“这个资源无权访问”（True）
    - `not_sent`：确定请求没有到达对方（连接被拒、DNS 失败、熔断），写操作可以安全地重试
    - `errors`：VALIDATION_FAILED 时的 (位置, 原因) 列表；不含对方回显的输入值
    - `reason`：机器可读的细分原因（例如 invalid_response、destination_not_allowed），不含敏感信息
    """

    def __init__(
        self,
        kind: ConnectorErrorKind,
        message: str,
        *,
        status: int | None = None,
        retry_after: int | None = None,
        resource_level: bool = False,
        not_sent: bool = False,
        errors: list[tuple[str, str]] | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: ConnectorErrorKind = kind
        self.status = status
        self.retry_after = retry_after
        self.resource_level = resource_level
        self.not_sent = not_sent
        self.errors = errors or []
        self.reason = reason

    def __repr__(self) -> str:
        return f"ConnectorError({self.kind}, status={self.status}, reason={self.reason})"


@dataclass(frozen=True)
class InstanceInfo:
    version: str | None


ProjectT = TypeVar("ProjectT")
TaskT = TypeVar("TaskT")


class Connector(Protocol):
    provider: str

    async def info(self) -> InstanceInfo: ...


class Readable(Connector, Protocol[ProjectT, TaskT]):
    async def list_projects(self, *, include_archived: bool = True) -> list[ProjectT]: ...

    async def list_tasks(self, *, updated_since: datetime | None = None) -> list[TaskT]: ...

    async def get_task(self, task_id: int) -> TaskT: ...


class Writable(Readable[ProjectT, TaskT], Protocol[ProjectT, TaskT]):
    async def create_task(
        self,
        project_id: int,
        *,
        title: str,
        description: str,
        priority: int,
        due_at: datetime | None,
    ) -> TaskT: ...

    async def update_task(self, task_id: int, changes: Mapping[str, Any]) -> TaskT: ...

    async def delete_task(self, task_id: int) -> None: ...

    async def find_created_tasks(
        self, project_id: int, *, title: str, created_since: datetime
    ) -> list[TaskT]: ...


class WebhookCapable(Protocol):
    async def can_manage_webhooks(self, project_id: int) -> bool: ...
