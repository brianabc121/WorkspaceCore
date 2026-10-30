"""接收第三方 Webhook：先校验签名再落库（规范第 3、9 节；Sprint 3 契约第 9 节）。

- 签名：`X-Vikunja-Signature` = 该集成的 Webhook 密钥对**原始请求体**的 HMAC-SHA256（十六进制），
  常量时间比较。集成不存在、已停用或签名不对一律同一个 401；集成不存在时也做一次 HMAC，
  避免响应时间暴露集成是否存在。
- payload 不信任：Inbox 只保存事件名、时间与任务 / 项目 ID（不保存 doer 等内容）；Worker 处理时
  以 Vikunja API 读到的当前状态为准。
- 去重：`event_key = sha256(原始请求体)`（Vikunja 没有投递 ID，请求体里的 time 精确到纳秒）。
"""

import hashlib
import hmac
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.domain.connectors import ConnectorRuntime, webhook_context
from app.domain.credentials import CredentialError
from app.models import Integration
from app.repositories import inbox
from app.repositories import integrations as integration_repo

logger = logging.getLogger("app.webhooks")

MAX_BODY_BYTES = 256 * 1024
_EVENT_NAME = re.compile(r"[a-z][a-z0-9_.]{0,63}")
# 集成不存在时用来做一次同样耗时的 HMAC；值本身没有意义
_DUMMY_KEY = b"\x00" * 32


def signature_invalid() -> ApiError:
    return ApiError(401, "WEBHOOK_SIGNATURE_INVALID", "Webhook 签名无效")


def payload_too_large() -> ApiError:
    return ApiError(413, "PAYLOAD_TOO_LARGE", f"请求体超过 {MAX_BODY_BYTES // 1024} KiB")


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def extract(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """从不受信任的事件里只取处理需要的字段：(事件名, 最小 payload)。"""
    name = data.get("event_name")
    event_type = name if isinstance(name, str) and _EVENT_NAME.fullmatch(name) else "unknown"
    body = _object(data.get("data"))
    task = _object(body.get("task"))
    project = _object(body.get("project"))
    sent_at = data.get("time")
    return event_type, {
        "event_name": event_type,
        "time": sent_at[:64] if isinstance(sent_at, str) else None,
        "task_id": _int(task.get("id")),
        "project_id": _int(task.get("project_id")) or _int(project.get("id")),
    }


def _secret(runtime: ConnectorRuntime, integration: Integration | None) -> str | None:
    if integration is None or integration.status == "disabled":
        return None
    if not integration.webhook_secret_ref:
        return None
    try:
        box = runtime.credentials()
        return box.resolve(
            integration.webhook_secret_ref, context=webhook_context(integration.id)
        ).get_secret_value()
    except (ApiError, CredentialError):
        logger.error("webhook secret unreadable", extra={"integration_id": str(integration.id)})
        return None


async def receive(
    db: AsyncSession,
    runtime: ConnectorRuntime,
    *,
    integration_id: uuid.UUID,
    body: bytes,
    signature: str | None,
) -> bool:
    """校验并写入 Inbox；返回是否是新事件（重复送达返回 False，同样算接收成功）。"""
    integration = await integration_repo.get_by_id(db, integration_id)
    secret = _secret(runtime, integration)
    expected = (
        sign(secret, body) if secret else hmac.new(_DUMMY_KEY, body, hashlib.sha256).hexdigest()
    )
    provided = (signature or "").strip().lower()
    if secret is None or integration is None or not hmac.compare_digest(expected, provided):
        raise signature_invalid()
    try:
        data = json.loads(body)
    except ValueError:
        raise ApiError(400, "BAD_REQUEST", "请求体不是 JSON") from None
    if not isinstance(data, dict):
        raise ApiError(400, "BAD_REQUEST", "请求体必须是 JSON 对象")
    event_type, payload = extract(data)
    created = await inbox.insert(
        db,
        integration_id=integration.id,
        event_key=hashlib.sha256(body).hexdigest(),
        event_type=event_type,
        payload=payload,
        now=datetime.now(UTC),
    )
    await db.commit()
    logger.info(
        "webhook accepted",
        extra={
            "integration_id": str(integration.id),
            "event_type": event_type,
            "duplicate": not created,
        },
    )
    return created
