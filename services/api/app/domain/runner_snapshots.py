"""常驻环境的快照存储（T-0139，docs/v2/RUNNER_PLAN.md 第 6.5、8.5 节）。

- runner-agent 把“要保存的目录”从数据卷里按 tar 流读出来，切成最多 16 MB 的块，
  经 `/runner-api/v1` 一块一块上传；
- 工作台每块用这份快照自己的数据密钥（随机 32 字节，AES-256-GCM，
  附加数据是“快照 ID:块号”）加密后，
  存到知识库用的对象存储（本地卷或 S3 兼容），key 是 `runner/<工作空间>/<快照>/<块号>`；
- 数据密钥用工作台的凭证密钥封存在 `runner_snapshots.key_ref`（用途绑定快照 ID），不出数据库；
- 恢复时按块解密下发。快照里可能有登录态，按密钥对待：页面上不提供下载。
"""

import base64
import os
import uuid
from datetime import timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import runner_specs as specs
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.knowledge_files import FileStores
from app.integrations.base import ConnectorError
from app.integrations.object_storage import StorageNotConfigured
from app.models import RunnerEnvironment, RunnerSnapshot

PART_BYTES = 16 * 1024 * 1024
MAX_PARTS = 100_000
NONCE_BYTES = 12


def snapshot_context(snapshot_id: uuid.UUID) -> str:
    return f"runner_snapshot:{snapshot_id}"


def object_key(snapshot: RunnerSnapshot, index: int) -> str:
    return f"runner/{snapshot.workspace_id}/{snapshot.id}/{index:06d}"


def _unavailable(error: Exception) -> ApiError:
    return ApiError(503, "STORAGE_UNAVAILABLE", f"快照存储暂时不可用（{type(error).__name__}）")


class SnapshotStore:
    def __init__(
        self, db: AsyncSession, settings: Settings, stores: FileStores | None = None
    ) -> None:
        self.db = db
        self.settings = settings
        self.stores = stores or FileStores(settings)

    def _box(self) -> CredentialBox:
        try:
            return CredentialBox.from_settings(self.settings)
        except CredentialError as error:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥") from error

    def begin(self, env: RunnerEnvironment) -> RunnerSnapshot:
        """新建一份上传中的快照（随机数据密钥，封存后入库）。调用方负责提交。"""
        snapshot_id = uuid.uuid4()
        key = base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()
        snapshot = RunnerSnapshot(
            id=snapshot_id,
            workspace_id=env.workspace_id,
            environment_id=env.id,
            status="uploading",
            key_ref=self._box().seal(key, context=snapshot_context(snapshot_id)),
            store=self.settings.file_storage,
        )
        self.db.add(snapshot)
        return snapshot

    def _aead(self, snapshot: RunnerSnapshot) -> AESGCM:
        try:
            key = self._box().resolve(snapshot.key_ref, context=snapshot_context(snapshot.id))
        except CredentialError as error:
            raise ApiError(500, "SNAPSHOT_KEY_INVALID", "快照的数据密钥解不开") from error
        return AESGCM(base64.b64decode(key.get_secret_value()))

    async def put_part(self, snapshot: RunnerSnapshot, index: int, data: bytes) -> int:
        if snapshot.status != "uploading":
            raise ApiError(409, "SNAPSHOT_CLOSED", "这份快照已经结束上传")
        if not 0 <= index < MAX_PARTS or len(data) > PART_BYTES:
            raise ApiError(422, "VALIDATION_FAILED", "块号或块大小不对")
        nonce = os.urandom(NONCE_BYTES)
        sealed = nonce + self._aead(snapshot).encrypt(
            nonce, data, f"{snapshot.id}:{index}".encode()
        )
        try:
            store = self.stores.get(snapshot.store)
            await store.put(object_key(snapshot, index), sealed, "application/octet-stream")
        except (ConnectorError, OSError, StorageNotConfigured) as error:
            raise _unavailable(error) from None
        # 记下传到第几块：上传没完成时清理也知道要删哪些块
        snapshot.parts = max(snapshot.parts, index + 1)
        return len(data)

    async def get_part(self, snapshot: RunnerSnapshot, index: int) -> bytes:
        if snapshot.status != "ready":
            raise ApiError(409, "SNAPSHOT_NOT_READY", "快照还没有就绪")
        if not 0 <= index < snapshot.parts:
            raise ApiError(404, "NOT_FOUND", "没有这一块")
        try:
            sealed = await self.stores.get(snapshot.store).get(object_key(snapshot, index))
        except (ConnectorError, OSError, StorageNotConfigured) as error:
            raise _unavailable(error) from None
        if sealed is None or len(sealed) <= NONCE_BYTES:
            raise ApiError(404, "NOT_FOUND", "快照的块丢失了")
        try:
            return self._aead(snapshot).decrypt(
                sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], f"{snapshot.id}:{index}".encode()
            )
        except InvalidTag:
            raise ApiError(500, "SNAPSHOT_CORRUPT", "快照的块校验失败") from None

    async def delete(self, snapshot: RunnerSnapshot) -> None:
        """删除对象存储里的块（存储暂时不可用就留到下一次清理）。"""
        if snapshot.status == "deleted":
            return
        try:
            store = self.stores.get(snapshot.store)
            for index in range(max(snapshot.parts, 0)):
                await store.delete(object_key(snapshot, index))
        except (ConnectorError, OSError, StorageNotConfigured):
            return
        snapshot.status = "deleted"
        snapshot.expires_at = snapshot.expires_at or specs.now()

    @staticmethod
    def keep_for(snapshot: RunnerSnapshot, days: float) -> None:
        snapshot.expires_at = specs.now() + timedelta(days=days)


__all__ = ["MAX_PARTS", "PART_BYTES", "SnapshotStore", "object_key", "snapshot_context"]
