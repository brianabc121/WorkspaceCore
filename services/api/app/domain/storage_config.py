"""读取工作空间的图片存储配置（T-0223）。

配置在表 storage_settings（网页“设置 → 图片存储”，owner 修改，见 app/domain/storage_settings.py）。
这里只负责读：解密 R2 / OSS 的密钥，组成 `StorageConfig`；没有这一行就是本机存储、没有副本。
知识库图片（上传、读取、复制、清理、自检）和常驻环境快照都经这里按工作空间取存储。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.credentials import CredentialBox, CredentialError
from app.integrations.object_storage import OssAccount, R2Account, StorageConfig
from app.models import StorageSettings

logger = logging.getLogger(__name__)

DEFAULT = StorageConfig()


def secret_context(workspace_id: uuid.UUID, field_name: str) -> str:
    """凭证加密的用途：密文挪到别的工作空间或字段上会解密失败。"""
    return f"storage_settings:{workspace_id}:{field_name}"


def _resolve(box: CredentialBox | None, ref: str, workspace_id: uuid.UUID, name: str) -> str:
    if not ref or box is None:
        return ""
    try:
        return box.resolve(ref, context=secret_context(workspace_id, name)).get_secret_value()
    except CredentialError:
        # 换了加密密钥又没留旧密钥：当作没填，页面与自检会显示“缺少 Secret”，重新填就能用
        logger.warning(
            "storage secret unreadable", extra={"workspace_id": str(workspace_id), "field": name}
        )
        return ""


def config_of(row: StorageSettings | None, settings: Settings) -> StorageConfig:
    if row is None:
        return DEFAULT
    box: CredentialBox | None
    try:
        box = CredentialBox.from_settings(settings)
    except CredentialError:
        box = None
    return StorageConfig(
        primary=row.primary_store,
        replica=row.replica_store,
        r2=R2Account(
            account_id=row.r2_account_id,
            bucket=row.r2_bucket,
            jurisdiction=row.r2_jurisdiction,
            access_key_id=row.r2_access_key_id,
            secret_access_key=_resolve(box, row.r2_secret_ref, row.workspace_id, "r2_secret"),
        ),
        oss=OssAccount(
            region=row.oss_region,
            bucket=row.oss_bucket,
            access_key_id=row.oss_access_key_id,
            access_key_secret=_resolve(box, row.oss_secret_ref, row.workspace_id, "oss_secret"),
        ),
    )


async def load_config(
    db: AsyncSession, settings: Settings, workspace_id: uuid.UUID
) -> StorageConfig:
    return config_of(await db.get(StorageSettings, workspace_id), settings)


@dataclass
class StoreRegistry:
    """按工作空间取存储（一次请求或一次 Worker 任务里缓存）。

    `fixed` 只给测试用：所有工作空间都用同一套配置（例如指向 CI 里的 Garage）。"""

    db: AsyncSession
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None
    fixed: StorageConfig | None = None
    _cache: dict[uuid.UUID, StorageConfig] = field(default_factory=dict)

    async def config(self, workspace_id: uuid.UUID) -> StorageConfig:
        if self.fixed is not None:
            return self.fixed
        if workspace_id not in self._cache:
            self._cache[workspace_id] = await load_config(self.db, self.settings, workspace_id)
        return self._cache[workspace_id]


__all__ = ["DEFAULT", "StoreRegistry", "config_of", "load_config", "secret_context"]
