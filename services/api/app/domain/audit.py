"""审计日志：写入（脱敏、IP 哈希、按工作空间分发）与查询（游标分页）。

约定见 docs/decisions/2026-10-30-sprint-1-auth-contract.md 第 11 节：
- 工作空间事件（例如修改工作空间设置）写入该工作空间；
- 账户事件（登录、登出、修改密码、撤销会话、修改个人资料）为用户当时所在的每个工作空间各写一条，
  查询始终限定 workspace_id；与任何工作空间都无关的事件写 workspace_id = NULL，不通过 API 暴露。
"""

import base64
import binascii
import json
import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from itertools import islice
from typing import Any

from pydantic import SecretBytes, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import security
from app.models import AuditLog
from app.repositories import audit as audit_repo
from app.repositories import workspaces as workspace_repo
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|secret|token|cookie|authorization|credential|api[-_]?key|private[-_]?key",
    re.IGNORECASE,
)
_MAX_DEPTH = 4
_MAX_ITEMS = 50
_MAX_KEY = 64
_MAX_STRING = 256


def redact(value: Any, depth: int = 0) -> Any:
    """写入审计日志前的脱敏：敏感键的值替换为 [REDACTED]，并限制嵌套深度、元素个数和字符串长度。"""
    if isinstance(value, SecretStr | SecretBytes):
        return REDACTED
    if isinstance(value, Mapping | list | tuple | set | frozenset) and depth >= _MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        return {
            str(key)[:_MAX_KEY]: REDACTED
            if _SENSITIVE_KEY.search(str(key))
            else redact(item, depth + 1)
            for key, item in islice(value.items(), _MAX_ITEMS)
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact(item, depth + 1) for item in islice(value, _MAX_ITEMS)]
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    text = str(value)
    return text if len(text) <= _MAX_STRING else text[:_MAX_STRING] + "…"


class AuditTrail:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def record(
        self,
        action: str,
        *,
        workspace_ids: Iterable[uuid.UUID | None],
        actor_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        client_ip: str | None = None,
    ) -> None:
        """在调用方的事务里写入，与业务改动一起提交。"""
        targets = list(workspace_ids) or [None]
        redacted = redact(dict(metadata or {}))
        ip = security.ip_hash(self.settings.session_key, client_ip) if client_ip else None
        await audit_repo.add_many(
            self.db,
            [
                AuditLog(
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id is not None else None,
                    metadata_redacted_json=redacted,
                    ip_hash=ip,
                )
                for workspace_id in targets
            ],
        )

    async def record_for_account(
        self,
        action: str,
        *,
        user_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        metadata: Mapping[str, Any] | None = None,
        client_ip: str | None = None,
        resource_type: str = "user",
        resource_id: uuid.UUID | None = None,
    ) -> None:
        """账户事件：写入该用户当前所在的每个工作空间。"""
        await self.record(
            action,
            workspace_ids=await workspace_repo.ids_for_user(self.db, user_id),
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id or user_id,
            metadata=metadata,
            client_ip=client_ip,
        )


# ---------------------------------------------------------------- 查询


def _encode_cursor(entry: AuditLog) -> str:
    raw = json.dumps([entry.created_at.isoformat(), str(entry.id)]).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _invalid_cursor() -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={
            "errors": [{"loc": ["query", "cursor"], "msg": "无效的游标", "type": "value_error"}]
        },
    )


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created_at, entry_id = json.loads(raw)
        position = (datetime.fromisoformat(created_at), uuid.UUID(entry_id))
    except (ValueError, TypeError, binascii.Error):
        raise _invalid_cursor() from None
    if position[0].tzinfo is None:
        raise _invalid_cursor()
    return position


def _to_out(entry: AuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=entry.id,
        action=entry.action,
        actor_id=entry.actor_id,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        metadata=entry.metadata_redacted_json,
        created_at=entry.created_at,
    )


async def list_page(
    db: AsyncSession, workspace_id: uuid.UUID, *, limit: int, cursor: str | None
) -> Page[AuditLogOut]:
    before = _decode_cursor(cursor) if cursor else None
    rows: Sequence[AuditLog] = await audit_repo.list_for_workspace(
        db, workspace_id, limit=limit + 1, before=before
    )
    page, more = rows[:limit], len(rows) > limit
    return Page(
        items=[_to_out(entry) for entry in page],
        next_cursor=_encode_cursor(page[-1]) if more else None,
        total_estimate=None,
    )
