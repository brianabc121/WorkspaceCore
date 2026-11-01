"""IM 审批卡片（V2 OC-5，契约第 6 节；OC-5 ADR 第 11 节）。

新的待审批建好后由 worker 发卡片（作业 send_approval_cards），给本工作空间每个 owner / admin
发一条：
- 发送方是这个工作空间的通知账号 `wn_<工作空间>`，会话是 `sn_<两个 ID 按字典序>`
  （OpenIM 的通知会话），每个人、每个工作空间各一个，不会把别的工作空间的卡片混进来；
- 消息是 OA 通知（contentType 1400），消息的 ex 带记录 ID，投影据此把它识别为 `approval_card`；
- 卡片只是提醒和入口：批准、拒绝仍走工作台的审批接口（会话 + CSRF + If-Match，高危要密码），
  在 IM 里回复文字不算批准（契约第 6 节）。
发送是尽力而为：失败只记日志，通知中心里的 approval_requested 不受影响；
同一条记录、同一个人只发一次（Redis 标记，发送失败时撤销，下次重试可以补发）。
"""

import json
import logging
import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.im import APPROVAL_NOTICE_NICKNAME, ImAccounts
from app.integrations.base import ConnectorError
from app.integrations.openim import OpenIMClient
from app.models import AiToolExecution

logger = logging.getLogger("app.im")

CARD_KIND = "approval_card"
SENT_TTL_SECONDS = 24 * 3600


def card_ex(execution_id: uuid.UUID) -> str:
    return json.dumps({"kind": CARD_KIND, "execution_id": str(execution_id)}, separators=(",", ":"))


def execution_id_from_ex(raw: object) -> uuid.UUID | None:
    """消息 ex 里的审批记录 ID；不是审批卡片时返回 None。"""
    if not isinstance(raw, str) or not raw or len(raw) > 512:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("kind") != CARD_KIND:
        return None
    try:
        return uuid.UUID(str(data.get("execution_id")))
    except ValueError:
        return None


def _source_line(row: AiToolExecution) -> str | None:
    """卡片上写明由谁、在哪触发（方案第 4.6 节）；推断不到时不写。"""
    source = row.source or {}
    name = source.get("triggered_by_name")
    conversation = source.get("conversation_id")
    if not isinstance(name, str) or not name or not isinstance(conversation, str):
        return None
    where = "在群聊里 @ 它" if conversation.startswith("sg_") else "私聊它"
    return f"由 {name} {where}触发（根据实例最近收到的消息推断）。"


def card_text(row: AiToolExecution) -> str:
    lines = [f"「{row.instance_name}」请求：{row.summary}"]
    source_line = _source_line(row)
    if source_line:
        lines.append(source_line)
    if row.risk_level == "critical":
        lines.append("高危操作，批准时需要重新输入密码。")
    lines.append("15 分钟内没人处理会自动拒绝。在工作台的审批中心或这张卡片上处理。")
    return "\n".join(lines)


async def send_cards(
    db: AsyncSession, client: OpenIMClient, redis: Redis | None, execution_id: uuid.UUID
) -> int:
    """给本工作空间的 owner / admin 发审批卡片；返回发出的条数。记录已处理时什么也不做。"""
    row = await db.get(AiToolExecution, execution_id)
    if row is None or row.status != "pending_approval":
        return 0
    accounts = ImAccounts(db, client)
    try:
        notice = await accounts.ensure_workspace_notice(row.workspace_id)
    except ConnectorError as error:
        logger.warning(
            "approval card skipped: notice account unavailable",
            extra={"execution_id": str(row.id), "error_kind": error.kind},
        )
        return 0
    text, ex = card_text(row), card_ex(row.id)
    sent = 0
    for user in await accounts.workspace_admins(row.workspace_id):
        key = f"approval_card:{row.id}:{user.id}"
        if redis is not None and not await redis.set(key, "1", nx=True, ex=SENT_TTL_SECONDS):
            continue
        try:
            im_user_id = await accounts.ensure_user(user)
            await client.send_oa_notification(
                send_id=notice, recv_id=im_user_id, name=APPROVAL_NOTICE_NICKNAME, text=text, ex=ex
            )
        except ConnectorError as error:
            if redis is not None:
                await redis.delete(key)
            logger.warning(
                "approval card failed",
                extra={"execution_id": str(row.id), "error_kind": error.kind},
            )
            continue
        sent += 1
    await db.commit()  # ensure_user 可能新登记了管理员的 IM 账号
    return sent
