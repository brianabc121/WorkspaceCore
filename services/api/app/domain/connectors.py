"""集成的运行时：构造 Vikunja 客户端、把 Connector 错误映射成接口错误（Sprint 3 契约第 8 节）。"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.credentials import CredentialBox, CredentialError
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker, ConnectorHttp, OutboundPolicy
from app.integrations.vikunja import VikunjaClient
from app.models import Integration, IntegrationAccount, SyncCursor
from app.schemas.integrations import SyncState

logger = logging.getLogger("app.integrations")

ACTIVE_STATUSES = frozenset({"active", "unavailable", "unauthorized"})


def account_context(account_id: object) -> str:
    """凭证加密的用途：密文挪到别的记录上会解密失败（Sprint 1 契约第 12 节）。"""
    return f"integration_account:{account_id}"


def webhook_context(integration_id: object) -> str:
    return f"integration_webhook:{integration_id}"


@dataclass
class ConnectorRuntime:
    """应用级共享：出站策略、熔断状态与（测试用的）传输层。"""

    settings: Settings
    breaker: CircuitBreaker
    transport: Any = None

    def http(self) -> ConnectorHttp:
        return ConnectorHttp(
            policy=OutboundPolicy(self.settings.allowed_integration_hosts),
            connect_timeout=self.settings.connector_connect_timeout_seconds,
            read_timeout=self.settings.connector_read_timeout_seconds,
            read_retries=self.settings.connector_read_retries,
            breaker=self.breaker,
            transport=self.transport,
        )

    def credentials(self) -> CredentialBox:
        try:
            return CredentialBox.from_settings(self.settings)
        except CredentialError as error:
            # 部署配置错误：Compose 中由 make env 生成，缺少时无法保存或使用任何第三方凭证
            logger.error("credential key unavailable", extra={"error_type": type(error).__name__})
            raise ApiError(
                503,
                "EXTERNAL_UNAVAILABLE",
                "服务端未配置凭证加密密钥，暂时无法使用第三方集成",
            ) from error

    def vikunja(self, integration: Integration, account: IntegrationAccount) -> VikunjaClient:
        try:
            token = self.credentials().resolve(
                account.credential_ref, context=account_context(account.id)
            )
        except CredentialError as error:
            logger.error(
                "integration credential unreadable",
                extra={"integration_id": str(integration.id), "error_type": type(error).__name__},
            )
            raise ApiError(
                502, "INTEGRATION_UNAUTHORIZED", "无法读取已保存的凭证，请重新填写 Token"
            ) from error
        return VikunjaClient(self.http(), integration.base_url, token.get_secret_value())


def api_error(error: ConnectorError) -> ApiError:
    """契约第 8 节：工作台的 401 只表示自己的会话无效，所以 Vikunja 的鉴权失败用 502。"""
    if error.kind == "AUTH_FAILED" and error.resource_level:
        return ApiError(403, "EXTERNAL_FORBIDDEN", "Vikunja 拒绝访问该资源")
    if error.kind == "AUTH_FAILED":
        return ApiError(502, "INTEGRATION_UNAUTHORIZED", "Vikunja 拒绝了 Token，请检查或更换")
    if error.kind == "NOT_FOUND":
        return ApiError(404, "NOT_FOUND", "资源不存在，或已在 Vikunja 中删除")
    if error.kind == "VALIDATION_FAILED":
        return ApiError(
            422,
            "EXTERNAL_VALIDATION_FAILED",
            "Vikunja 拒绝了请求内容",
            details={"errors": [{"loc": loc, "msg": msg} for loc, msg in error.errors]},
        )
    if error.kind == "RATE_LIMITED":
        retry = str(error.retry_after or 30)
        return ApiError(
            503, "EXTERNAL_RATE_LIMITED", "Vikunja 限流，请稍后重试", headers={"Retry-After": retry}
        )
    if error.kind == "TIMEOUT_UNKNOWN":
        return ApiError(
            504,
            "RESULT_UNKNOWN",
            "操作结果未知：请用同一个 Idempotency-Key 重新发送以查询结果",
            details={"retry_with_same_key": True},
        )
    return ApiError(503, "EXTERNAL_UNAVAILABLE", "Vikunja 暂时不可用，请稍后重试")


def integration_status_after(error: ConnectorError, current: str) -> str:
    """访问失败后集成的状态：凭证失效 → unauthorized；服务不可用 → unavailable。"""
    if error.kind == "AUTH_FAILED" and not error.resource_level:
        return "unauthorized"
    if error.kind == "UNAVAILABLE" and error.reason != "destination_not_allowed":
        return "unavailable" if current == "active" else current
    return current


def sync_state(
    integration: Integration | None,
    cursor: SyncCursor | None,
    *,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> SyncState:
    """契约第 10 节的新鲜度规则。"""
    if integration is None or integration.status == "disabled":
        return SyncState(status="not_configured", as_of=None, reason="not_configured")
    last = cursor.last_success_at if cursor else None
    if integration.status == "unauthorized":
        return SyncState(status="unauthorized", as_of=last, reason="token_rejected")
    if last is None:
        return SyncState(status="unavailable", as_of=None, reason="not_synced")
    age = (now or datetime.now(UTC)) - last
    if age <= timedelta(seconds=stale_after_seconds):
        return SyncState(status="ok", as_of=last)
    return SyncState(status="stale", as_of=last, reason="sync_overdue")
