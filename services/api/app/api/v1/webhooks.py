"""第三方服务的 Webhook 回调（Sprint 3）。

不用会话与 CSRF，只认签名：X-Vikunja-Signature 是用该集成的 Webhook 密钥对原始请求体做的
HMAC-SHA256。集成不存在、已停用、签名缺失或不对一律返回同一个 401，不泄露集成是否存在。
校验通过后写入 Webhook Inbox 并立即返回 202，处理在 Worker 里异步进行。
约定见 docs/decisions/2026-10-30-sprint-3-tasks-contract.md 第 9 节。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, status

from app.core.errors import not_implemented
from app.schemas.common import error_responses
from app.schemas.tasks import WebhookAccepted

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
    signature: Annotated[str | None, Header(alias="X-Vikunja-Signature")] = None,
) -> WebhookAccepted:
    raise not_implemented("T-0021")
