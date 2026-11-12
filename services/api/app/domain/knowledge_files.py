"""知识库图片（T-0103，docs/v2/TIMELINE_PLAN.md 第 4 节）。

- 上传：只收 PNG / JPEG / GIF / WebP（按文件头判断，不信任文件名与 Content-Type，SVG 不收），
  单张不超过 FILE_MAX_MB，每个工作空间不超过 FILE_QUOTA_MB；JPEG 去掉 Exif 等元数据（保留方向）；
  同一工作空间同一内容只存一份。member 以上可以上传。
- 存储：每个工作空间在网页“设置 → 图片存储”里配置主存储与副本
  （T-0223，app/domain/storage_config.py；没配置就是本机）。写主存储成功就返回；
  配置了副本时由 Worker 异步复制，失败重试。
  读取先读文件写入时的存储，失败再读主存储与副本，并核对内容哈希。
- 读取一律经工作台：`GET /api/v1/knowledge/files/<id>`，viewer 以上，
  按工作空间隔离（别的工作空间 404）。
- 引用：保存笔记时记下正文引用的本工作空间图片；历史版本里引用过的保留（current = false）。
  没有任何引用、上传超过 24 小时的图片由 Worker 每天清理（主存储与副本都删）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
from redis.asyncio import Redis
from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.images import sniff, strip_jpeg_metadata
from app.domain.policy import WorkspaceAccess, not_found, require_role
from app.domain.ratelimit import WindowLimiter
from app.domain.storage_config import StoreRegistry, load_config
from app.integrations.base import ConnectorError
from app.integrations.object_storage import (
    ObjectStore,
    StorageConfig,
    StorageNotConfigured,
    build_store,
    self_check,
)
from app.models import KnowledgeFile, KnowledgeFileRef, StorageSettings, Workspace
from app.schemas.operations import StorageCheck, StorageLocation, StorageName, StorageStatus
from app.schemas.workspaces import Role

logger = logging.getLogger(__name__)

FILE_URL = re.compile(r"/api/v1/knowledge/files/([0-9a-fA-F-]{36})")
UPLOADS_PER_MINUTE = 30
REPLICA_MAX_ATTEMPTS = 10
CLEANUP_AFTER = timedelta(hours=24)


def file_url(file_id: uuid.UUID) -> str:
    return f"/api/v1/knowledge/files/{file_id}"


def object_key(workspace_id: uuid.UUID, sha256: str) -> str:
    return f"kb/{workspace_id}/{sha256}"


def referenced_ids(body: str) -> set[uuid.UUID]:
    found: set[uuid.UUID] = set()
    for match in FILE_URL.finditer(body):
        try:
            found.add(uuid.UUID(match.group(1)))
        except ValueError:
            continue
    return found


async def sync_refs(
    db: AsyncSession, workspace_id: uuid.UUID, note_id: uuid.UUID, body: str
) -> None:
    """按正文更新这篇笔记的图片引用：正文里有的 current = true，其余保留为历史引用。"""
    await db.execute(
        update(KnowledgeFileRef)
        .where(KnowledgeFileRef.note_id == note_id, KnowledgeFileRef.current.is_(True))
        .values(current=False)
    )
    ids = referenced_ids(body)
    if not ids:
        return
    # 只认本工作空间的图片
    valid = (
        await db.scalars(
            select(KnowledgeFile.id).where(
                KnowledgeFile.workspace_id == workspace_id, KnowledgeFile.id.in_(ids)
            )
        )
    ).all()
    for file_id in valid:
        stmt = pg_insert(KnowledgeFileRef).values(
            file_id=file_id, note_id=note_id, workspace_id=workspace_id, current=True
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["file_id", "note_id"], set_={"current": True}
            )
        )


# ---------------------------------------------------------------- 存储位置


@dataclass
class FileStores:
    """一个工作空间的主存储与副本（按它的配置建）；读取历史文件时按名字找（没配置的返回 None）。"""

    settings: Settings
    config: StorageConfig = field(default_factory=StorageConfig)
    transport: httpx.AsyncBaseTransport | None = None
    _cache: dict[str, ObjectStore] = field(default_factory=dict)

    def get(self, name: str) -> ObjectStore:
        """建名字对应的存储；配置不完整时抛 StorageNotConfigured。"""
        if name not in self._cache:
            self._cache[name] = build_store(
                name, self.config, self.settings, transport=self.transport
            )
        return self._cache[name]

    def find(self, name: str | None) -> ObjectStore | None:
        if not name or name == "none":
            return None
        try:
            return self.get(name)
        except StorageNotConfigured:
            return None

    @property
    def primary_name(self) -> str:
        return self.config.primary

    def primary(self) -> ObjectStore:
        return self.get(self.config.primary)

    def replica_name(self) -> str | None:
        name = self.config.replica
        return None if name in ("none", self.config.primary) else name


async def stores_for(
    registry: StoreRegistry, workspace_id: uuid.UUID, cache: dict[uuid.UUID, FileStores]
) -> FileStores:
    """按工作空间取 FileStores（Worker 一次处理多个工作空间的图片时缓存）。"""
    if workspace_id not in cache:
        cache[workspace_id] = FileStores(
            registry.settings, await registry.config(workspace_id), registry.transport
        )
    return cache[workspace_id]


def _storage_error(error: Exception) -> ApiError:
    if isinstance(error, StorageNotConfigured):
        return ApiError(
            503,
            "STORAGE_NOT_CONFIGURED",
            f"图片存储没有配置完整（{error.name} 缺少 {', '.join(error.missing)}）",
        )
    kind = error.kind if isinstance(error, ConnectorError) else "UNAVAILABLE"
    return ApiError(503, "STORAGE_UNAVAILABLE", "图片存储暂时不可用", details={"kind": kind})


@dataclass(frozen=True)
class FileData:
    data: bytes
    mime: str
    sha256: str


class FileService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        *,
        redis: Redis | None = None,
        stores: FileStores | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        # 测试可以直接给一套存储；否则按图片所属的工作空间读配置
        self._fixed = stores
        self.transport = transport

    async def stores(self, workspace_id: uuid.UUID) -> FileStores:
        if self._fixed is not None:
            return self._fixed
        config = await load_config(self.db, self.settings, workspace_id)
        return FileStores(self.settings, config, self.transport)

    @property
    def max_bytes(self) -> int:
        return self.settings.file_max_mb * 1024 * 1024

    async def upload(self, access: WorkspaceAccess, data: bytes) -> KnowledgeFile:
        workspace_id = access.workspace.id
        if self.redis is not None:
            retry = await WindowLimiter(self.redis, 60).hit(
                f"kb_upload:{access.user_id}", UPLOADS_PER_MINUTE
            )
            if retry is not None:
                raise ApiError(
                    429,
                    "RATE_LIMITED",
                    "上传过于频繁，请稍后再试",
                    headers={"Retry-After": str(retry)},
                )
        if len(data) > self.max_bytes:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", f"图片不能超过 {self.settings.file_max_mb} MB")
        info = sniff(data)
        if info is None:
            raise ApiError(
                422,
                "UNSUPPORTED_IMAGE",
                "只支持 PNG、JPEG、GIF、WebP 图片",
                details={"errors": [{"loc": ["body"], "msg": "不是支持的图片"}]},
            )
        if info.mime == "image/jpeg":
            data = strip_jpeg_metadata(data)
        digest = hashlib.sha256(data).hexdigest()
        existing = await self._by_hash(workspace_id, digest)
        if existing is not None:
            return existing
        used = await self.db.scalar(
            select(func.coalesce(func.sum(KnowledgeFile.size), 0)).where(
                KnowledgeFile.workspace_id == workspace_id
            )
        )
        quota = self.settings.file_quota_mb * 1024 * 1024
        if int(used or 0) + len(data) > quota:
            raise ApiError(
                413,
                "QUOTA_EXCEEDED",
                f"这个工作空间的图片已用满 {self.settings.file_quota_mb} MB，"
                "删掉不用的笔记里的图片后，过一天会自动清理",
            )
        stores = await self.stores(workspace_id)
        try:
            store = stores.primary()
            await store.put(object_key(workspace_id, digest), data, info.mime)
        except (StorageNotConfigured, ConnectorError) as error:
            logger.warning(
                "kb file upload failed",
                extra={"storage": stores.primary_name, "error_kind": _kind(error)},
            )
            raise _storage_error(error) from error
        replica = stores.replica_name()
        stmt = (
            pg_insert(KnowledgeFile)
            .values(
                workspace_id=workspace_id,
                sha256=digest,
                mime=info.mime,
                size=len(data),
                width=info.width,
                height=info.height,
                storage=store.name,
                replica=replica,
                replica_state="pending" if replica else "none",
                created_by_user=access.user_id,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "sha256"])
        )
        await self.db.execute(stmt)
        row = await self._by_hash(workspace_id, digest)
        if row is None:  # pragma: no cover - 刚插入或并发插入，必然存在
            raise ApiError(503, "STORAGE_UNAVAILABLE", "图片存储暂时不可用")
        return row

    async def _by_hash(self, workspace_id: uuid.UUID, digest: str) -> KnowledgeFile | None:
        return await self.db.scalar(
            select(KnowledgeFile).where(
                KnowledgeFile.workspace_id == workspace_id, KnowledgeFile.sha256 == digest
            )
        )

    async def get(self, user_id: uuid.UUID, file_id: uuid.UUID) -> KnowledgeFile:
        """按图片所属的工作空间校验当前用户（viewer 以上）。不是成员与不存在一样返回 404。

        不要求 X-Workspace-Id：笔记正文里的 `<img src>` 由浏览器直接加载，带不了自定义请求头；
        会话 Cookie 是 SameSite=Lax，别的网站嵌入的图片请求不带会话。
        """
        row = await self.db.get(KnowledgeFile, file_id)
        if row is None:
            raise not_found()
        await require_role(
            self.db, user_id=user_id, workspace_id=row.workspace_id, minimum=Role.viewer
        )
        return row

    async def read(self, row: KnowledgeFile) -> FileData:
        """依次读文件写入时的存储、当前主存储、副本；内容哈希对不上的跳过。"""
        stores = await self.stores(row.workspace_id)
        names: list[str] = []
        for name in (
            row.storage,
            stores.primary_name,
            row.replica,
            stores.replica_name(),
        ):
            if name and name != "none" and name not in names:
                names.append(name)
        key = object_key(row.workspace_id, row.sha256)
        failed: Exception | None = None
        for name in names:
            store = stores.find(name)
            if store is None:
                continue
            try:
                data = await store.get(key)
            except ConnectorError as error:
                logger.warning(
                    "kb file read failed", extra={"storage": name, "error_kind": error.kind}
                )
                failed = error
                continue
            if data is not None and hashlib.sha256(data).hexdigest() == row.sha256:
                return FileData(data=data, mime=row.mime, sha256=row.sha256)
        if failed is not None:
            raise _storage_error(failed)
        raise ApiError(404, "FILE_MISSING", "图片文件找不到了")


def _kind(error: Exception) -> str:
    return error.kind if isinstance(error, ConnectorError) else "NOT_CONFIGURED"


# ---------------------------------------------------------------- Worker：复制与清理


async def replicate_pending(db: AsyncSession, registry: StoreRegistry, *, limit: int = 20) -> int:
    """把待复制的图片写到副本；失败记下原因，下次重试，超过次数标为 failed。"""
    # 后来才配置副本：之前上传的图片也补一份（按各工作空间自己的副本）
    if registry.fixed is not None:
        targets: list[tuple[uuid.UUID | None, str]] = []
        if (replica := FileStores(registry.settings, registry.fixed).replica_name()) is not None:
            targets.append((None, replica))
    else:
        targets = [
            (workspace_id, name)
            for workspace_id, name, primary in (
                await db.execute(
                    select(
                        StorageSettings.workspace_id,
                        StorageSettings.replica_store,
                        StorageSettings.primary_store,
                    ).where(StorageSettings.replica_store != "none")
                )
            ).all()
            if name != primary
        ]
    for workspace_id, replica in targets:
        backfill = update(KnowledgeFile).where(
            KnowledgeFile.replica.is_(None), KnowledgeFile.storage != replica
        )
        if workspace_id is not None:
            backfill = backfill.where(KnowledgeFile.workspace_id == workspace_id)
        await db.execute(
            backfill.values(replica=replica, replica_state="pending", replica_attempts=0)
        )
    rows = (
        await db.scalars(
            select(KnowledgeFile)
            .where(KnowledgeFile.replica_state == "pending")
            .order_by(KnowledgeFile.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    cache: dict[uuid.UUID, FileStores] = {}
    done = 0
    for row in rows:
        stores = await stores_for(registry, row.workspace_id, cache)
        key = object_key(row.workspace_id, row.sha256)
        source, target = stores.find(row.storage), stores.find(row.replica)
        if source is None or target is None:
            row.replica_state, row.replica_error = "failed", "not_configured"
            continue
        try:
            data = await source.get(key)
            if data is None:
                row.replica_state, row.replica_error = "failed", "source_missing"
                continue
            await target.put(key, data, row.mime)
        except ConnectorError as error:
            row.replica_attempts += 1
            row.replica_error = (error.reason or error.kind)[:64]
            if row.replica_attempts >= REPLICA_MAX_ATTEMPTS:
                row.replica_state = "failed"
            logger.warning(
                "kb file replicate failed",
                extra={
                    "storage": row.replica,
                    "error_kind": error.kind,
                    "attempt": row.replica_attempts,
                },
            )
            continue
        row.replica_state, row.replica_error = "done", None
        done += 1
    return done


async def cleanup_unreferenced(
    db: AsyncSession,
    registry: StoreRegistry,
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> int:
    """删除没有任何笔记引用、上传超过 24 小时的图片（主存储与副本）。存储删不掉的留到下次。"""
    cutoff = (now or datetime.now(UTC)) - CLEANUP_AFTER
    rows = (
        await db.scalars(
            select(KnowledgeFile)
            .where(
                KnowledgeFile.created_at < cutoff,
                ~exists().where(KnowledgeFileRef.file_id == KnowledgeFile.id),
            )
            .order_by(KnowledgeFile.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    cache: dict[uuid.UUID, FileStores] = {}
    removed = 0
    for row in rows:
        stores = await stores_for(registry, row.workspace_id, cache)
        key = object_key(row.workspace_id, row.sha256)
        try:
            for name in {row.storage, row.replica}:
                store = stores.find(name)
                if store is not None:
                    await store.delete(key)
        except ConnectorError as error:
            logger.warning(
                "kb file cleanup failed", extra={"storage": name, "error_kind": error.kind}
            )
            continue
        await db.delete(row)
        removed += 1
    return removed


# ---------------------------------------------------------------- 运维


@dataclass(frozen=True)
class StoreState:
    name: str
    where: str | None
    configured: bool
    missing: list[str]


def store_state(stores: FileStores, name: str) -> StoreState:
    try:
        store = stores.get(name)
    except StorageNotConfigured as error:
        return StoreState(name=name, where=None, configured=False, missing=error.missing)
    return StoreState(name=name, where=store.where, configured=True, missing=[])


async def usage(db: AsyncSession, workspace_id: uuid.UUID) -> tuple[int, int, int, int]:
    """这个工作空间的（图片数, 已用字节, 待复制, 复制失败）。"""
    row = (
        await db.execute(
            select(
                func.count(KnowledgeFile.id),
                func.coalesce(func.sum(KnowledgeFile.size), 0),
                func.count(KnowledgeFile.id).filter(KnowledgeFile.replica_state == "pending"),
                func.count(KnowledgeFile.id).filter(KnowledgeFile.replica_state == "failed"),
            ).where(KnowledgeFile.workspace_id == workspace_id)
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])


async def check_store(stores: FileStores, name: str) -> tuple[bool, str | None]:
    """写一个探测对象、读回、删除。返回（是否成功, 失败原因：错误分类或 S3 错误码）。"""
    try:
        await self_check(stores.get(name))
    except StorageNotConfigured as error:
        return False, f"缺少 {', '.join(error.missing)}"
    except ConnectorError as error:
        return False, error.reason or error.kind
    return True, None


def check_key(workspace_id: uuid.UUID) -> str:
    return f"kb_storage:check:{workspace_id}"


async def run_checks(
    stores: FileStores, redis: Redis | None, workspace_id: uuid.UUID
) -> dict[str, StorageCheck]:
    """对这个工作空间的主存储与副本做一次自检，结果存进 Redis（运维页显示最近一次）。"""
    results: dict[str, StorageCheck] = {}
    names: list[str] = [stores.primary_name]
    if (replica := stores.replica_name()) is not None:
        names.append(replica)
    for name in names:
        ok, reason = await check_store(stores, name)
        results[name] = StorageCheck(ok=ok, reason=reason, checked_at=datetime.now(UTC))
        if not ok:
            logger.warning(
                "kb storage self-check failed",
                extra={"storage": name, "reason": reason, "workspace_id": str(workspace_id)},
            )
    if redis is not None:
        payload = {name: check.model_dump(mode="json") for name, check in results.items()}
        await redis.set(check_key(workspace_id), json.dumps(payload), ex=7 * 86400)
    return results


async def run_all_checks(registry: StoreRegistry, redis: Redis | None) -> dict[str, bool]:
    """Worker：每个工作空间各自检一次（启动时与每天）。返回 {工作空间: 是否都通过}。"""
    results: dict[str, bool] = {}
    cache: dict[uuid.UUID, FileStores] = {}
    for workspace_id in (await registry.db.scalars(select(Workspace.id))).all():
        stores = await stores_for(registry, workspace_id, cache)
        checks = await run_checks(stores, redis, workspace_id)
        results[str(workspace_id)] = all(check.ok for check in checks.values())
    return results


async def _last_checks(redis: Redis | None, workspace_id: uuid.UUID) -> dict[str, StorageCheck]:
    if redis is None:
        return {}
    raw = await redis.get(check_key(workspace_id))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {name: StorageCheck.model_validate(value) for name, value in data.items()}
    except (ValueError, TypeError):
        return {}


async def storage_status(
    db: AsyncSession,
    stores: FileStores,
    redis: Redis | None,
    workspace_id: uuid.UUID,
    checks: dict[str, StorageCheck] | None = None,
) -> StorageStatus:
    settings = stores.settings
    checks = checks if checks is not None else await _last_checks(redis, workspace_id)

    def location(name: str) -> StorageLocation:
        state = store_state(stores, name)
        return StorageLocation(
            name=cast(StorageName, name),
            configured=state.configured,
            where=state.where if name != "local" else None,
            missing=state.missing,
            check=checks.get(name),
        )

    replica = stores.replica_name()
    files, used, pending, failed = await usage(db, workspace_id)
    return StorageStatus(
        primary=location(stores.primary_name),
        replica=location(replica) if replica else None,
        files=files,
        used_bytes=used,
        quota_bytes=settings.file_quota_mb * 1024 * 1024,
        pending_replication=pending,
        failed_replication=failed,
    )
