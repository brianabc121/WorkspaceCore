"""第三方服务的 Webhook 回调（Sprint 3）。

不用会话与 CSRF，只认签名：X-Vikunja-Signature 是用该集成的 Webhook 密钥对原始请求体做的
HMAC-SHA256。集成不存在、已停用、签名缺失或不对一律返回同一个 401，不泄露集成是否存在。
校验通过后写入 Webhook Inbox 并立即返回 202，处理在 Worker 里异步进行。
约定见 docs/decisions/2026-10-30-sprint-3-tasks-contract.md 第 9 节。
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_connectors, get_db, get_jobs
from app.domain import webhooks
from app.domain.connectors import ConnectorRuntime
from app.jobs.queue import PROCESS_WEBHOOKS, JobQueue
from app.schemas.common import error_responses
from app.schemas.tasks import WebhookAccepted

logger = logging.getLogger("app.webhooks")

router = APIRouter(tags=["webhooks"])

# 签名针对原始请求体，处理函数自己读取 body，不交给 Pydantic 解析；这里只为文档声明请求体
_VIKUNJA_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "description": "Vikunja 的事件：{event_name, time, data}"
                "（核验 ADR 第 6 节）；内容不受信任",
            }
        }
    },
}


@router.post(
    "/webhooks/vikunja/{integration_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="接收 Vikunja 的 Webhook（签名校验，不用会话）",
    description="请求体上限 256 KiB；同一事件重复送达同样返回 202（按请求体哈希去重）。",
    responses=error_responses(400, 401, 413, 422),
    openapi_extra={"requestBody": _VIKUNJA_BODY},
)
async def receive_vikunja_webhook(
    integration_id: UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    connectors: Annotated[ConnectorRuntime, Depends(get_connectors)],
    jobs: Annotated[JobQueue, Depends(get_jobs)],
    signature: Annotated[str | None, Header(alias="X-Vikunja-Signature")] = None,
) -> WebhookAccepted:
    body = await _read_body(request)
    created = await webhooks.receive(
        db, connectors, integration_id=integration_id, body=body, signature=signature
    )
    if created:
        try:
            await jobs.enqueue(PROCESS_WEBHOOKS)
        except Exception as error:
            # 事件已经落库：Worker 每分钟的兜底轮询会处理它
            logger.warning("enqueue failed", extra={"error_type": type(error).__name__})
    return WebhookAccepted()


async def _read_body(request: Request) -> bytes:
    """读原始请求体（签名针对原始字节），超过上限立即停止读取。"""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > webhooks.MAX_BODY_BYTES:
        raise webhooks.payload_too_large()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > webhooks.MAX_BODY_BYTES:
            raise webhooks.payload_too_large()
    return bytes(body)
