"""写操作的 Idempotency-Key（Sprint 3 契约第 7 节；验收场景 A06、A07）。

一条记录对应（操作者，键）。流程：
1. `begin`：第一次请求时写入 `processing` 并**立即提交**，让并发的重复请求能看到它；
   重复请求按记录的状态原样回放、报冲突或交给调用方查询未知结果。
2. 调用外部服务。
3. `succeed` / `fail` / `mark_unknown` / `discard`：写下结果，与投影的更新一起提交。

指纹 = 工作空间 + 方法 + 路径 + 规范化 JSON 请求体的 SHA-256；同一个键换了请求内容返回 409。
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import IdempotencyRequest
from app.repositories import integrations as repo

RETENTION = timedelta(hours=24)
# 超过这个时间仍是 processing，说明处理它的进程中途退出了：按结果未知处理
STALE_PROCESSING = timedelta(minutes=2)


def fingerprint(workspace_id: uuid.UUID, method: str, path: str, body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    raw = f"{workspace_id}\n{method.upper()}\n{path}\n{canonical}"
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class Begun:
    """state：new（第一次，继续执行）/ replay（原样返回）/ unknown（需要查询结果）。"""

    state: Literal["new", "replay", "unknown"]
    record: IdempotencyRequest


def _reused() -> ApiError:
    return ApiError(409, "IDEMPOTENCY_KEY_REUSED", "同一个 Idempotency-Key 已用于不同的请求")


def _in_progress() -> ApiError:
    return ApiError(
        409,
        "IDEMPOTENCY_IN_PROGRESS",
        "同一个请求仍在处理，请稍后再查询",
        headers={"Retry-After": "1"},
    )


async def begin(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    key: uuid.UUID,
    request_fingerprint: str,
    context: dict[str, Any],
) -> Begun:
    now = datetime.now(UTC)
    record = await repo.get_idempotency(db, actor_id=actor_id, key=key, lock=True)
    if record is not None and record.expires_at <= now:
        await repo.delete_idempotency(db, record.id)
        record = None
    if record is None:
        record = IdempotencyRequest(
            workspace_id=workspace_id,
            actor_id=actor_id,
            idempotency_key=key,
            request_fingerprint=request_fingerprint,
            status="processing",
            context_json={**context, "started_at": now.isoformat()},
            expires_at=now + RETENTION,
        )
        db.add(record)
        try:
            await db.commit()
        except IntegrityError:
            # 并发的同一个请求先写入了：按“仍在处理”回应
            await db.rollback()
            raise _in_progress() from None
        return Begun("new", record)
    if record.request_fingerprint != request_fingerprint:
        await db.rollback()
        raise _reused()
    if record.status in {"succeeded", "failed"}:
        # 结束事务、释放行锁；用 commit 而不是 rollback：rollback 会让已读出的记录过期
        await db.commit()
        return Begun("replay", record)
    if record.status == "processing" and now - record.updated_at < STALE_PROCESSING:
        await db.rollback()
        raise _in_progress()
    # unknown，或处理进程已退出的 processing：交给调用方查询结果（记录行仍被锁着）
    return Begun("unknown", record)


def started_at(record: IdempotencyRequest) -> datetime:
    value = record.context_json.get("started_at")
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else record.created_at
    except ValueError:
        return record.created_at


def succeed(record: IdempotencyRequest, status_code: int, body: Any) -> None:
    record.status = "succeeded"
    record.response_code = status_code
    record.response_json = {"body": body}


def fail(record: IdempotencyRequest, error: ApiError) -> None:
    """确定失败：重放时返回同样的错误（不含当时的 request_id）。"""
    record.status = "failed"
    record.response_code = error.status_code
    record.response_json = {
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
            "headers": error.headers,
        }
    }


def mark_unknown(record: IdempotencyRequest) -> None:
    record.status = "unknown"


async def discard(db: AsyncSession, record: IdempotencyRequest) -> None:
    """确定请求没有发出：删除记录，允许用同一个键重试。"""
    await repo.delete_idempotency(db, record.id)


def replay(record: IdempotencyRequest) -> tuple[int, Any]:
    """返回 (状态码, 响应体)；失败的记录直接抛出当时的错误（同样带 Idempotent-Replayed）。"""
    stored = record.response_json or {}
    if record.status == "failed":
        error = stored.get("error") or {}
        raise ApiError(
            record.response_code or 409,
            str(error.get("code") or "CONFLICT"),
            str(error.get("message") or "请求失败"),
            details=error.get("details") or {},
            headers={**(error.get("headers") or {}), "Idempotent-Replayed": "true"},
        )
    return record.response_code or 200, stored.get("body")


def not_applied() -> ApiError:
    return ApiError(
        409,
        "OPERATION_NOT_APPLIED",
        "已确认这次操作没有生效；如需重试，请重新提交（会使用新的 Idempotency-Key）",
    )
