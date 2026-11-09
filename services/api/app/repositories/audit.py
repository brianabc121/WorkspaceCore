"""审计日志的数据访问：只有写入与按工作空间查询，不提供修改和删除。"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def add_many(db: AsyncSession, entries: Sequence[AuditLog]) -> None:
    db.add_all(entries)
    await db.flush()


# ---------------------------------------------------------------- 筛选（T-0197）


@dataclass(frozen=True)
class AuditCriteria:
    """已校验的筛选条件（校验在 app.domain.audit_query.AuditFilters）。

    全部在 SQL 里、limit 之前生效。

    q 只按字面匹配 action、resource_type、resource_id 与 metadata 顶层字符串 name / title，
    不碰 ip_hash 与 metadata 里的其他键；q_actions 是中文名包含 q 的内置事件。
    cutoff 是导出的截止点：只读 created_at 早于它的记录。
    """

    after: datetime | None = None
    before: datetime | None = None
    actor_id: uuid.UUID | None = None
    system_only: bool = False
    action: str | None = None
    action_prefix: str | None = None
    resource_type: str | None = None
    q: str | None = None
    q_actions: tuple[str, ...] = ()
    cutoff: datetime | None = None


def _literal(text: str) -> str:
    """LIKE 模式里按字面匹配：转义 \\、% 与 _（配合 escape="\\"）。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _metadata_text(key: str, pattern: str) -> ColumnElement[bool]:
    field = AuditLog.metadata_redacted_json[key]
    return and_(func.jsonb_typeof(field) == "string", field.astext.ilike(pattern, escape="\\"))


def _conditions(workspace_id: uuid.UUID, criteria: AuditCriteria) -> list[ColumnElement[bool]]:
    # 始终限定工作空间：workspace_id 为 NULL 的记录不会被任何条件选中
    conditions: list[ColumnElement[bool]] = [AuditLog.workspace_id == workspace_id]
    if criteria.after is not None:
        conditions.append(AuditLog.created_at >= criteria.after)
    if criteria.before is not None:
        conditions.append(AuditLog.created_at < criteria.before)
    if criteria.cutoff is not None:
        conditions.append(AuditLog.created_at < criteria.cutoff)
    if criteria.system_only:
        conditions.append(AuditLog.actor_id.is_(None))
    elif criteria.actor_id is not None:
        conditions.append(AuditLog.actor_id == criteria.actor_id)
    if criteria.action is not None:
        conditions.append(AuditLog.action == criteria.action)
    if criteria.action_prefix is not None:
        conditions.append(AuditLog.action.like(_literal(criteria.action_prefix) + "%", escape="\\"))
    if criteria.resource_type is not None:
        conditions.append(AuditLog.resource_type == criteria.resource_type)
    if criteria.q is not None:
        pattern = f"%{_literal(criteria.q)}%"
        matches = [
            AuditLog.action.ilike(pattern, escape="\\"),
            AuditLog.resource_type.ilike(pattern, escape="\\"),
            AuditLog.resource_id.ilike(pattern, escape="\\"),
            _metadata_text("name", pattern),
            _metadata_text("title", pattern),
        ]
        if criteria.q_actions:
            matches.append(AuditLog.action.in_(criteria.q_actions))
        conditions.append(or_(*matches))
    return conditions


async def list_matching(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    criteria: AuditCriteria,
    *,
    limit: int,
    before: tuple[datetime, uuid.UUID] | None = None,
) -> list[AuditLog]:
    """新的在前（created_at、id 倒序）；before 为上一页最后一条的 (created_at, id)。"""
    stmt = select(AuditLog).where(*_conditions(workspace_id, criteria))
    if before is not None:
        stmt = stmt.where(tuple_(AuditLog.created_at, AuditLog.id) < tuple_(*before))
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    return list(await db.scalars(stmt))


async def get_in_workspace(
    db: AsyncSession, workspace_id: uuid.UUID, entry_id: uuid.UUID
) -> AuditLog | None:
    entry: AuditLog | None = await db.scalar(
        select(AuditLog).where(AuditLog.id == entry_id, AuditLog.workspace_id == workspace_id)
    )
    return entry
