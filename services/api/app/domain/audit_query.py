"""审计日志的读侧（T-0197，契约 docs/v2/ai-admin-redesign/…-contract.md 第 5 节）：
服务端筛选、单条详情与受控 CSV 导出。

- 只读：不改 AuditTrail 的写入，也不碰 audit_logs 的只追加保护；列表与详情不写审计。
- 一切查询限定当前工作空间（workspace_id 为 NULL 的记录永远不出现），筛选全部在 SQL 里、limit 之前。
- 游标按 created_at、id 倒序做 keyset；带 HMAC 签名，绑定工作空间与筛选条件，篡改或换了条件 422。
  游标只是位置，不是授权凭据：每次请求仍按当前角色与工作空间检查。
- 导出要求 31 天以内的时间范围，最多 10000 行：先查到 10001 行，超出就 422，绝不悄悄截断；
  文件用同一次有界查询的结果生成，导出后在同一个事务里记 audit.export_generated（只记安全摘要）。
"""

import base64
import binascii
import csv
import hashlib
import hmac
import io
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit import AuditTrail, redact
from app.domain.audit_labels import (
    AuditLabels,
    action_label,
    actions_matching_label,
    resolve_labels,
)
from app.domain.policy import WorkspaceAccess, not_found
from app.models import AuditLog
from app.repositories import audit as audit_repo
from app.repositories.audit import AuditCriteria
from app.schemas.audit import AuditLogView
from app.schemas.common import Page

ACTION_MAX = 128
RESOURCE_TYPE_MAX = 64
Q_MAX = 100
EXPORT_MAX_ROWS = 10_000
EXPORT_MAX_SPAN = timedelta(days=31)
EXPORT_EVENT = "audit.export_generated"
EXPORT_COLUMNS = (
    "id",
    "created_at_utc",
    "action",
    "action_label",
    "actor_id",
    "actor_label",
    "resource_type",
    "resource_id",
    "resource_current_name",
    "metadata_json",
)


def _invalid(name: str, message: str) -> ApiError:
    """与请求参数校验失败同样的格式：只给位置与原因，不回显输入。"""
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": ["query", name], "msg": message, "type": "value_error"}]},
    )


def _aware(name: str, value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise _invalid(name, "时间必须带时区")
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


# 过滤摘要里只保留标识符样子的值（事件名、对象类型）；别的写成 [FILTERED]，不把任意输入写进审计
_SAFE_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def _summary_value(value: str | None) -> str | None:
    if value is None:
        return None
    return value if _SAFE_VALUE.fullmatch(value) else "[FILTERED]"


@dataclass(frozen=True)
class AuditFilters:
    """审计日志的筛选条件；创建时校验，不合格抛 422 VALIDATION_FAILED。

    after / before：带时区，成对给出，after 含、before 不含且 after < before（内部统一成 UTC）。
    actor_id 与 system_only（只看 actor_id 为空的系统事件）互斥；
    action（精确）与 action_prefix（前缀）互斥。
    q：1–100 字，字面匹配 action、resource_type、resource_id 与 metadata 里的 name / title；
    中文名包含 q 的内置事件也算命中。
    """

    after: datetime | None = None
    before: datetime | None = None
    actor_id: uuid.UUID | None = None
    system_only: bool = False
    action: str | None = None
    action_prefix: str | None = None
    resource_type: str | None = None
    q: str | None = None

    def __post_init__(self) -> None:
        after, before = _aware("after", self.after), _aware("before", self.before)
        if (after is None) != (before is None):
            raise _invalid("after" if after is None else "before", "after 与 before 要一起给出")
        if after is not None and before is not None and after >= before:
            raise _invalid("before", "before 必须晚于 after")
        object.__setattr__(self, "after", after)
        object.__setattr__(self, "before", before)
        if self.system_only and self.actor_id is not None:
            raise _invalid("system_only", "system_only 与 actor_id 不能同时使用")
        if self.action is not None and self.action_prefix is not None:
            raise _invalid("action_prefix", "action 与 action_prefix 不能同时使用")
        for name, value, limit in (
            ("action", self.action, ACTION_MAX),
            ("action_prefix", self.action_prefix, ACTION_MAX),
            ("resource_type", self.resource_type, RESOURCE_TYPE_MAX),
            ("q", self.q, Q_MAX),
        ):
            if value is not None and not 1 <= len(value) <= limit:
                raise _invalid(name, f"长度应为 1 到 {limit} 个字符")

    def criteria(self, *, cutoff: datetime | None = None) -> AuditCriteria:
        return AuditCriteria(
            after=self.after,
            before=self.before,
            actor_id=self.actor_id,
            system_only=self.system_only,
            action=self.action,
            action_prefix=self.action_prefix,
            resource_type=self.resource_type,
            q=self.q,
            q_actions=tuple(actions_matching_label(self.q)) if self.q else (),
            cutoff=cutoff,
        )

    def canonical(self) -> str:
        """游标签名用：同样的条件得到同样的文本（时间统一成 UTC）。"""
        return json.dumps(
            [
                _iso(self.after),
                _iso(self.before),
                str(self.actor_id) if self.actor_id else None,
                self.system_only,
                self.action,
                self.action_prefix,
                self.resource_type,
                self.q,
            ],
            ensure_ascii=False,
        )

    def summary(self) -> dict[str, Any]:
        """写进 audit.export_generated 的安全摘要：不含搜索词原文，任意文字不原样落库。"""
        return {
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "system_only": self.system_only,
            "action": _summary_value(self.action),
            "action_prefix": _summary_value(self.action_prefix),
            "resource_type": _summary_value(self.resource_type),
            "q": self.q is not None,
        }


@dataclass(frozen=True)
class AuditExportFile:
    filename: str
    content: bytes
    row_count: int
    as_of: datetime


# ---------------------------------------------------------------- metadata 清理

# IP 相关的键：ip、client_ip、source_ip、ip_hash、ipAddress、remote_addr、x-forwarded-for……
_IP_KEY = re.compile(r"(?:^|[^a-z0-9])ip(?:[^a-z0-9]|$)|ip_?hash|ip_?addr|remote_?addr|forwarded")
# 凭据类的键（覆盖 audit.redact 的敏感键，另加密文引用）
_CREDENTIAL_KEY = re.compile(
    r"pass(word|wd)?|secret|token|cookie|authori[sz]ation|credential|api[-_]?key"
    r"|private[-_]?key|ciphertext|bearer"
)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _hidden(key: str, *, credentials: bool) -> bool:
    normalized = _CAMEL.sub("_", key).lower()
    if _IP_KEY.search(normalized):
        return True
    return credentials and _CREDENTIAL_KEY.search(normalized) is not None


def _scrub(value: Any, *, credentials: bool) -> Any:
    if isinstance(value, dict):
        return {
            key: _scrub(item, credentials=credentials)
            for key, item in value.items()
            if not _hidden(str(key), credentials=credentials)
        }
    if isinstance(value, list):
        return [_scrub(item, credentials=credentials) for item in value]
    return value


def view_metadata(metadata: Any) -> dict[str, Any]:
    """列表与详情：写入时已脱敏；再过一遍 redact（旧数据兜底），并去掉 IP 类的键。"""
    cleaned = _scrub(redact(metadata), credentials=False) if isinstance(metadata, dict) else {}
    return cleaned if isinstance(cleaned, dict) else {}


def export_metadata(metadata: Any) -> dict[str, Any]:
    """导出：在 view_metadata 之外，把凭据类的键整个去掉（连 [REDACTED] 也不留）。"""
    cleaned = _scrub(redact(metadata), credentials=True) if isinstance(metadata, dict) else {}
    return cleaned if isinstance(cleaned, dict) else {}


# ---------------------------------------------------------------- CSV

_FORMULA_START = ("=", "+", "-", "@")


def csv_cell(value: str | None) -> str:
    """公式注入防护：去掉前导空白后以 = + - @ 开头，或本身以制表符 / 回车开头的，前面加 '。"""
    text = "" if value is None else value
    if text[:1] in ("\t", "\r") or text.lstrip().startswith(_FORMULA_START):
        return "'" + text
    return text


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _render_csv(entries: Sequence[AuditLog], labels: AuditLabels) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for entry in entries:
        metadata = json.dumps(
            export_metadata(entry.metadata_redacted_json), ensure_ascii=False, sort_keys=True
        )
        writer.writerow(
            [
                csv_cell(value)
                for value in (
                    str(entry.id),
                    _utc_text(entry.created_at),
                    entry.action,
                    action_label(entry.action),
                    str(entry.actor_id) if entry.actor_id else None,
                    labels.actor(entry.actor_id),
                    entry.resource_type,
                    entry.resource_id,
                    labels.resource(entry.resource_type, entry.resource_id),
                    metadata,
                )
            ]
        )
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


# ---------------------------------------------------------------- 服务


def _view(entry: AuditLog, labels: AuditLabels) -> AuditLogView:
    return AuditLogView(
        id=entry.id,
        action=entry.action,
        actor_id=entry.actor_id,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        metadata=view_metadata(entry.metadata_redacted_json),
        created_at=entry.created_at,
        action_label=action_label(entry.action),
        actor_label=labels.actor(entry.actor_id),
        resource_current_name=labels.resource(entry.resource_type, entry.resource_id),
    )


def _bad_cursor() -> ApiError:
    return _invalid("cursor", "无效的游标，或筛选条件已变化：请从第一页重新加载")


class AuditQueryService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # ------------------------------------------------------------ 游标

    def _sign(self, access: WorkspaceAccess, filters: AuditFilters, at: str, entry: str) -> str:
        message = json.dumps([str(access.workspace.id), filters.canonical(), at, entry])
        key = self.settings.session_key
        return hmac.new(key, b"audit-cursor\x00" + message.encode(), hashlib.sha256).hexdigest()

    def _encode_cursor(
        self, access: WorkspaceAccess, filters: AuditFilters, entry: AuditLog
    ) -> str:
        at, entry_id = entry.created_at.astimezone(UTC).isoformat(), str(entry.id)
        payload = {"t": at, "i": entry_id, "s": self._sign(access, filters, at, entry_id)}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def _decode_cursor(
        self, access: WorkspaceAccess, filters: AuditFilters, cursor: str
    ) -> tuple[datetime, uuid.UUID]:
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        except (ValueError, binascii.Error):
            raise _bad_cursor() from None
        if not (
            isinstance(data, dict)
            and set(data) == {"t", "i", "s"}
            and all(isinstance(part, str) for part in data.values())
        ):
            raise _bad_cursor()
        expected = self._sign(access, filters, data["t"], data["i"])
        if not hmac.compare_digest(expected, data["s"]):
            raise _bad_cursor()
        try:
            at, entry_id = datetime.fromisoformat(data["t"]), uuid.UUID(data["i"])
        except ValueError:
            raise _bad_cursor() from None
        if at.tzinfo is None:
            raise _bad_cursor()
        return at, entry_id

    # ------------------------------------------------------------ 列表与详情

    async def list(
        self, access: WorkspaceAccess, filters: AuditFilters, *, cursor: str | None, limit: int
    ) -> Page[AuditLogView]:
        position = self._decode_cursor(access, filters, cursor) if cursor else None
        rows = await audit_repo.list_matching(
            self.db, access.workspace.id, filters.criteria(), limit=limit + 1, before=position
        )
        page, more = rows[:limit], len(rows) > limit
        labels = await resolve_labels(self.db, access, page)
        return Page(
            items=[_view(entry, labels) for entry in page],
            next_cursor=self._encode_cursor(access, filters, page[-1]) if more else None,
            total_estimate=None,
        )

    async def detail(self, access: WorkspaceAccess, entry_id: uuid.UUID) -> AuditLogView:
        entry = await audit_repo.get_in_workspace(self.db, access.workspace.id, entry_id)
        if entry is None:
            raise not_found()
        return _view(entry, await resolve_labels(self.db, access, [entry]))

    # ------------------------------------------------------------ 导出

    async def export_csv(
        self, access: WorkspaceAccess, filters: AuditFilters, *, client_ip: str | None = None
    ) -> AuditExportFile:
        after, before = filters.after, filters.before
        if after is None or before is None:
            raise _invalid("after", "导出必须给出 after 与 before")
        if before - after > EXPORT_MAX_SPAN:
            raise _invalid("before", "导出的时间范围最长 31 天，请缩小范围")
        # 截止点：本事务的开始时间。稍后写入的 export_generated 不会出现在这份文件里
        as_of = (await self.db.execute(select(func.now()))).scalar_one()
        rows = await audit_repo.list_matching(
            self.db,
            access.workspace.id,
            filters.criteria(cutoff=as_of),
            limit=EXPORT_MAX_ROWS + 1,
        )
        if len(rows) > EXPORT_MAX_ROWS:
            raise ApiError(
                422,
                "AUDIT_EXPORT_TOO_LARGE",
                f"符合条件的记录超过 {EXPORT_MAX_ROWS} 条，请缩小时间范围或增加筛选条件",
                details={"limit": EXPORT_MAX_ROWS},
            )
        content = _render_csv(rows, await resolve_labels(self.db, access, rows))
        await AuditTrail(self.db, self.settings).record(
            EXPORT_EVENT,
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="audit_log",
            metadata={
                "after": _iso(after),
                "before": _iso(before),
                "as_of": _iso(as_of),
                "row_count": len(rows),
                "filters": filters.summary(),
            },
            client_ip=client_ip,
        )
        await self.db.commit()
        return AuditExportFile(
            filename=f"audit-logs-{after:%Y%m%d}-{before:%Y%m%d}.csv",
            content=content,
            row_count=len(rows),
            as_of=as_of,
        )
