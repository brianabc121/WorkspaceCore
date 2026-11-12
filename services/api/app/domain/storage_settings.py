"""工作空间的图片存储设置（T-0223，网页“设置 → 图片存储”；owner 修改，admin 只读）。

- 主存储 / 副本选本机（local）、Cloudflare R2、阿里云 OSS；没保存过就是本机、没有副本。
- 地址只由账户 ID / 辖区（R2）、地域（OSS）拼出各自的官方域名
  （`https://<账户>[.<辖区>].r2.cloudflarestorage.com`、
  `https://<桶>.s3.oss-<地域>.aliyuncs.com`），
  这些值和桶名都按 DNS 标签校验，填不出别的主机：工作台不会被引去访问内网或别的地址。
- 密钥用凭证密钥加密保存（用途绑定工作空间与字段）；读取只说是否已设置。
  保存时不填表示保持不变，空字符串清除。
- 保存前先用新配置对主存储与副本各做一次自检（写、读、删一个探测对象），
  不通过就不保存，返回各自的原因。
- 还存着本工作空间图片或快照的存储（in_use），它的连接信息不能清空，否则那些文件就读不到了。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from redis.asyncio import Redis
from sqlalchemy import CompoundSelect, select, union
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.knowledge_files import FileStores, check_key, check_store
from app.domain.storage_config import config_of, secret_context
from app.models import KnowledgeFile, RunnerSnapshot, StorageSettings
from app.schemas.operations import StorageCheck, StorageName
from app.schemas.storage import (
    OssSettingsOut,
    R2SettingsOut,
    StorageSettingsIn,
    StorageSettingsOut,
)

# DNS 标签：拼进主机名的值只能是这些字符，保证主机落在 R2 / OSS 的官方域名下
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ACCOUNT = re.compile(r"^[a-z0-9]{1,64}$")
_JURISDICTION = re.compile(r"^[a-z]{2,16}$")
# 桶名：R2 与 OSS 都是 3 ~ 63 个小写字母、数字、短横线，首尾是字母或数字
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_KEY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SECRET = re.compile(r"^[\x21-\x7e]{1,256}$")


def _invalid(loc: list[str | int], message: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        message,
        details={"errors": [{"loc": ["body", *loc], "msg": message}]},
    )


def _row_defaults(workspace_id: uuid.UUID) -> StorageSettings:
    return StorageSettings(
        workspace_id=workspace_id,
        primary_store="local",
        replica_store="none",
        r2_account_id="",
        r2_bucket="",
        r2_jurisdiction="",
        r2_access_key_id="",
        r2_secret_ref="",
        oss_region="",
        oss_bucket="",
        oss_access_key_id="",
        oss_secret_ref="",
        version=0,
    )


async def in_use(db: AsyncSession, workspace_id: uuid.UUID) -> list[StorageName]:
    """还存着本工作空间图片（主存储或已复制 / 待复制的副本）或快照的存储。"""
    stmt: CompoundSelect[tuple[str | None]] = union(
        select(KnowledgeFile.storage).where(KnowledgeFile.workspace_id == workspace_id),
        select(KnowledgeFile.replica).where(
            KnowledgeFile.workspace_id == workspace_id,
            KnowledgeFile.replica.is_not(None),
            KnowledgeFile.replica_state.in_(("pending", "done")),
        ),
        select(RunnerSnapshot.store).where(
            RunnerSnapshot.workspace_id == workspace_id, RunnerSnapshot.status != "deleted"
        ),
    )
    names: set[str] = {str(row[0]) for row in (await db.execute(stmt)).all() if row[0]}
    ordered: tuple[StorageName, ...] = ("local", "r2", "oss")
    return [n for n in ordered if n in names]


def _out(
    row: StorageSettings, used: list[StorageName], checks: dict[str, StorageCheck] | None = None
) -> StorageSettingsOut:
    return StorageSettingsOut(
        primary=cast(StorageName, row.primary_store),
        replica=cast(Any, row.replica_store),
        r2=R2SettingsOut(
            account_id=row.r2_account_id,
            bucket=row.r2_bucket,
            jurisdiction=row.r2_jurisdiction,
            access_key_id=row.r2_access_key_id,
            secret_set=bool(row.r2_secret_ref),
        ),
        oss=OssSettingsOut(
            region=row.oss_region,
            bucket=row.oss_bucket,
            access_key_id=row.oss_access_key_id,
            secret_set=bool(row.oss_secret_ref),
        ),
        in_use=used,
        version=row.version,
        updated_at=row.updated_at if row.version else None,
        checks=checks,
    )


class StorageSettingsService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        *,
        redis: Redis | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.transport = transport

    def _box(self) -> CredentialBox:
        try:
            return CredentialBox.from_settings(self.settings)
        except CredentialError as error:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥") from error

    async def get(self, workspace_id: uuid.UUID) -> StorageSettingsOut:
        row = await self.db.get(StorageSettings, workspace_id) or _row_defaults(workspace_id)
        return _out(row, await in_use(self.db, workspace_id))

    async def update(
        self,
        workspace_id: uuid.UUID,
        body: StorageSettingsIn,
        *,
        actor_id: uuid.UUID,
        client_ip: str | None,
    ) -> StorageSettingsOut:
        row = await self.db.get(StorageSettings, workspace_id, with_for_update=True)
        current = row.version if row is not None else 0
        if body.version != current:
            raise ApiError(
                409,
                "VERSION_CONFLICT",
                "存储设置已被其他人修改，请刷新后重试",
                details={"current_version": current},
            )
        if body.replica != "none" and body.replica == body.primary:
            raise _invalid(["replica"], "副本不能和主存储是同一个")
        is_new = row is None
        if row is None:
            row = _row_defaults(workspace_id)
        used = await in_use(self.db, workspace_id)
        chosen = {body.primary, body.replica} - {"none", "local"}
        box = self._box()

        # ---- R2
        r2 = body.r2
        account = r2.account_id.strip().lower()
        jurisdiction = r2.jurisdiction.strip().lower()
        r2_bucket = r2.bucket.strip()
        r2_key = r2.access_key_id.strip()
        if account and not _ACCOUNT.match(account):
            raise _invalid(
                ["r2", "account_id"], "账户 ID 只能是字母和数字（R2 页面右侧的 Account ID）"
            )
        if jurisdiction and not _JURISDICTION.match(jurisdiction):
            raise _invalid(["r2", "jurisdiction"], "辖区只能是小写字母，例如 eu；没有就留空")
        if r2_bucket and not _BUCKET.match(r2_bucket):
            raise _invalid(
                ["r2", "bucket"], "桶名是 3 ~ 63 个小写字母、数字或短横线，首尾是字母或数字"
            )
        if r2_key and not _KEY_ID.match(r2_key):
            raise _invalid(["r2", "access_key_id"], "Access Key ID 只能是字母、数字和 . _ -")
        r2_ref = self._secret(
            box,
            row.r2_secret_ref,
            r2.secret_access_key,
            workspace_id,
            "r2_secret",
            ["r2", "secret_access_key"],
        )
        r2_cleared = not (account and r2_bucket and r2_key and r2_ref)

        # ---- OSS
        oss = body.oss
        region = oss.region.strip().lower().removeprefix("oss-")
        oss_bucket = oss.bucket.strip()
        oss_key = oss.access_key_id.strip()
        if region and not _LABEL.match(region):
            raise _invalid(["oss", "region"], "地域只能是小写字母、数字和短横线，例如 cn-hangzhou")
        if oss_bucket and not _BUCKET.match(oss_bucket):
            raise _invalid(
                ["oss", "bucket"], "桶名是 3 ~ 63 个小写字母、数字或短横线，首尾是字母或数字"
            )
        if oss_key and not _KEY_ID.match(oss_key):
            raise _invalid(["oss", "access_key_id"], "AccessKey ID 只能是字母、数字和 . _ -")
        oss_ref = self._secret(
            box,
            row.oss_secret_ref,
            oss.access_key_secret,
            workspace_id,
            "oss_secret",
            ["oss", "access_key_secret"],
        )
        oss_cleared = not (region and oss_bucket and oss_key and oss_ref)

        # 选了的存储要填完整；还存着文件的存储不能清空
        for name, cleared, label in (("r2", r2_cleared, "R2"), ("oss", oss_cleared, "OSS")):
            if name in chosen and cleared:
                raise _invalid([name], f"选了 {label} 就要把它的连接信息填完整（包括密钥）")
            if name in used and cleared:
                raise ApiError(
                    409,
                    "STORAGE_IN_USE",
                    f"{label} 里还存着这个工作空间的图片或快照，不能清空它的连接信息",
                    details={"storage": name},
                )

        candidate = StorageSettings(
            workspace_id=workspace_id,
            primary_store=body.primary,
            replica_store=body.replica,
            r2_account_id=account,
            r2_bucket=r2_bucket,
            r2_jurisdiction=jurisdiction,
            r2_access_key_id=r2_key,
            r2_secret_ref=r2_ref,
            oss_region=region,
            oss_bucket=oss_bucket,
            oss_access_key_id=oss_key,
            oss_secret_ref=oss_ref,
        )
        # 保存前自检：主存储与副本各写、读、删一个探测对象，不通过就不保存
        stores = FileStores(self.settings, config_of(candidate, self.settings), self.transport)
        names = [body.primary] + ([body.replica] if body.replica != "none" else [])
        checks: dict[str, StorageCheck] = {}
        for name in names:
            ok, reason = await check_store(stores, name)
            checks[name] = StorageCheck(ok=ok, reason=reason, checked_at=datetime.now(UTC))
        if not all(check.ok for check in checks.values()):
            raise ApiError(
                422,
                "STORAGE_CHECK_FAILED",
                "存储自检没有通过，设置没有保存",
                details={
                    "checks": {
                        name: check.model_dump(mode="json") for name, check in checks.items()
                    }
                },
            )

        changed = sorted(
            field
            for field in (
                "primary_store",
                "replica_store",
                "r2_account_id",
                "r2_bucket",
                "r2_jurisdiction",
                "r2_access_key_id",
                "oss_region",
                "oss_bucket",
                "oss_access_key_id",
            )
            if getattr(row, field) != getattr(candidate, field)
        )
        # 换了密钥的存储（只记名字；键名不能带 secret，否则审计脱敏会把整个值遮住）
        keys_changed = sorted(
            name
            for name, old, new in (
                ("r2", row.r2_secret_ref, r2_ref),
                ("oss", row.oss_secret_ref, oss_ref),
            )
            if old != new
        )
        for field in (
            "primary_store",
            "replica_store",
            "r2_account_id",
            "r2_bucket",
            "r2_jurisdiction",
            "r2_access_key_id",
            "r2_secret_ref",
            "oss_region",
            "oss_bucket",
            "oss_access_key_id",
            "oss_secret_ref",
        ):
            setattr(row, field, getattr(candidate, field))
        row.version = current + 1
        row.updated_by = actor_id
        row.updated_at = datetime.now(UTC)
        if is_new:
            self.db.add(row)
            try:
                await self.db.flush()
            except IntegrityError as error:
                # 两个人同时第一次保存：后到的按版本冲突处理
                await self.db.rollback()
                raise ApiError(
                    409,
                    "VERSION_CONFLICT",
                    "存储设置已被其他人修改，请刷新后重试",
                    details={"current_version": 1},
                ) from error
        await AuditTrail(self.db, self.settings).record(
            "storage.settings_updated",
            workspace_ids=[workspace_id],
            actor_id=actor_id,
            resource_type="storage_settings",
            resource_id=workspace_id,
            metadata={
                "primary": body.primary,
                "replica": body.replica,
                "changed": changed,
                "keys_changed": keys_changed,
            },
            client_ip=client_ip,
        )
        await self.db.flush()
        if self.redis is not None:
            # 运维页显示的“最近一次自检”换成这次的结果
            await self.redis.set(
                check_key(workspace_id),
                json.dumps({n: c.model_dump(mode="json") for n, c in checks.items()}),
                ex=7 * 86400,
            )
        return _out(row, used, checks)

    def _secret(
        self,
        box: CredentialBox,
        old_ref: str,
        given: Any,
        workspace_id: uuid.UUID,
        name: str,
        loc: list[str | int],
    ) -> str:
        """密钥：不填保持原来的密文，空字符串清除，否则校验后加密。"""
        if given is None:
            return old_ref
        value = given.get_secret_value().strip()
        if not value:
            return ""
        if not _SECRET.match(value):
            raise _invalid(loc, "密钥里不能有空格或不可见字符")
        return box.seal(value, context=secret_context(workspace_id, name))


__all__ = ["StorageSettingsService", "in_use"]
