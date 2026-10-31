"""通知中心（Sprint 5 契约第 4 节）：列表、已读，以及各处产生通知的入口。

- 通知只给当前用户看，按工作空间隔离；标别人的通知按不存在处理（不计数、不报错）。
- 正文是纯文本，不含 Token、地址、第三方响应原文。
- 去重：source_key 带上事件的标识与小时，同一件事在一小时内只通知一次（重试、并发都不会重复）。
- 写入通知时登记 notifications / overview 的失效事件，提交后经 SSE 推给页面。
"""

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.domain import events
from app.domain.policy import WorkspaceAccess
from app.models import Integration, Notification
from app.repositories import notifications as repo
from app.schemas.notifications import (
    NotificationMark,
    NotificationMarkResult,
    NotificationOut,
    NotificationPage,
)

ADMINS = ("owner", "admin")
INTEGRATIONS_LINK = "/settings/integrations"
_PROVIDER_NAMES = {"vikunja": "Vikunja", "github": "GitHub"}


def _hour() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H")


def _invalid_cursor() -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={
            "errors": [{"loc": ["query", "cursor"], "msg": "无效的游标", "type": "value_error"}]
        },
    )


def _encode_cursor(item: Notification) -> str:
    raw = json.dumps([item.created_at.isoformat(), str(item.id)]).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        raise _invalid_cursor() from None
    if not (
        isinstance(decoded, list)
        and len(decoded) == 2
        and all(isinstance(part, str) for part in decoded)
    ):
        raise _invalid_cursor()
    try:
        created_at, item_id = datetime.fromisoformat(decoded[0]), uuid.UUID(decoded[1])
    except ValueError:
        raise _invalid_cursor() from None
    if created_at.tzinfo is None:
        raise _invalid_cursor()
    return created_at, item_id


def _out(item: Notification) -> NotificationOut:
    return NotificationOut(
        id=item.id,
        type=item.type,
        severity=item.severity,
        title=item.title,
        body=item.body,
        link=item.link,
        read_at=item.read_at,
        created_at=item.created_at,
    )


def _user(access: WorkspaceAccess) -> uuid.UUID:
    if access.user_id is None:  # policy.require_role 总会填上；只为类型检查兜底
        raise ApiError(401, "UNAUTHORIZED", "请先登录")
    return access.user_id


class NotificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(
        self, access: WorkspaceAccess, *, unread_only: bool, cursor: str | None, limit: int
    ) -> NotificationPage:
        user_id, workspace_id = _user(access), access.workspace.id
        before = _decode_cursor(cursor) if cursor else None
        rows = await repo.list_page(
            self.db,
            user_id=user_id,
            workspace_id=workspace_id,
            unread_only=unread_only,
            before=before,
            limit=limit + 1,
        )
        page = rows[:limit]
        return NotificationPage(
            items=[_out(item) for item in page],
            next_cursor=_encode_cursor(page[-1]) if len(rows) > limit else None,
            total_estimate=None,
            unread_count=await repo.unread_count(
                self.db, user_id=user_id, workspace_id=workspace_id
            ),
        )

    async def mark(self, access: WorkspaceAccess, body: NotificationMark) -> NotificationMarkResult:
        user_id, workspace_id = _user(access), access.workspace.id
        updated = await repo.mark_read(
            self.db, user_id=user_id, workspace_id=workspace_id, ids=None if body.all else body.ids
        )
        if updated:
            events.mark(self.db, workspace_id, "notifications", "overview")
        await self.db.commit()
        return NotificationMarkResult(
            updated=updated,
            unread_count=await repo.unread_count(
                self.db, user_id=user_id, workspace_id=workspace_id
            ),
        )


async def unread_count(db: AsyncSession, access: WorkspaceAccess) -> int | None:
    """首页 summary.unread_notifications（与通知列表的 unread_count 一致）。"""
    if access.user_id is None:
        return None
    return await repo.unread_count(db, user_id=access.user_id, workspace_id=access.workspace.id)


# ---------------------------------------------------------------- 产生通知


async def notify_admins(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    type_: str,
    severity: str,
    title: str,
    body: str,
    link: str | None,
    key: str,
) -> int:
    """发给工作空间的 owner / admin；key 是事件的标识（去重用，不含小时）。"""
    added = await repo.add_for_roles(
        db,
        workspace_id=workspace_id,
        roles=ADMINS,
        type_=type_,
        severity=severity,
        title=title,
        body=body,
        link=link,
        source_key=f"{type_}:{key}:{_hour()}",
    )
    if added:
        events.mark(db, workspace_id, "notifications", "overview")
    return added


async def notify_user(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    type_: str,
    severity: str,
    title: str,
    body: str,
    link: str | None,
    key: str,
) -> bool:
    """发给一个用户（例如自动化的触发者）；key 是事件的标识，通常是执行记录的 ID。"""
    added = await repo.add_for_user(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        type_=type_,
        severity=severity,
        title=title,
        body=body,
        link=link,
        source_key=f"{type_}:{key}",
    )
    if added:
        events.mark(db, workspace_id, "notifications", "overview")
    return added


def _label(integration: Integration) -> str:
    provider = _PROVIDER_NAMES.get(integration.provider, integration.provider)
    return f"{provider}「{integration.name}」"


async def integration_status_changed(db: AsyncSession, integration: Integration, old: str) -> None:
    """集成状态变化后调用：授权失效、无法连接、恢复正常时通知管理员（契约第 4 节）。"""
    new = integration.status
    if new == old:
        return
    label = _label(integration)
    if new == "unauthorized":
        await notify_admins(
            db,
            integration.workspace_id,
            type_="integration_unauthorized",
            severity="error",
            title=f"{label} 的授权已失效",
            body="Token 被拒绝或已过期，同步已暂停。请在集成设置里更新 Token 并重新测试连接。",
            link=INTEGRATIONS_LINK,
            key=str(integration.id),
        )
    elif new == "unavailable" and old == "active":
        await notify_admins(
            db,
            integration.workspace_id,
            type_="sync_failed",
            severity="warning",
            title=f"{label} 暂时无法连接",
            body="同步失败，页面上的数据可能已经过期。工作台会继续按计划重试。",
            link=INTEGRATIONS_LINK,
            key=str(integration.id),
        )
    elif new == "active" and old in {"unauthorized", "unavailable"}:
        await notify_admins(
            db,
            integration.workspace_id,
            type_="integration_recovered",
            severity="info",
            title=f"{label} 已恢复正常",
            body="同步已恢复。",
            link=INTEGRATIONS_LINK,
            key=str(integration.id),
        )


async def integration_rate_limited(
    db: AsyncSession, integration: Integration, retry_after: timedelta
) -> None:
    minutes = max(1, round(retry_after.total_seconds() / 60))
    await notify_admins(
        db,
        integration.workspace_id,
        type_="integration_rate_limited",
        severity="warning",
        title=f"{_label(integration)} 被限流",
        body=f"第三方暂时拒绝了请求，约 {minutes} 分钟后自动恢复同步；"
        "在此之前页面显示的是之前的数据。",
        link=INTEGRATIONS_LINK,
        key=str(integration.id),
    )


async def purge(db: AsyncSession, *, retention: timedelta) -> int:
    """已读的保留 retention，未读的保留两倍时间。"""
    now = datetime.now(UTC)
    return await repo.purge(db, read_before=now - retention, any_before=now - 2 * retention)
