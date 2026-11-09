"""加密密钥轮换：把用旧密钥加密的凭证引用换成当前密钥（V2 OC-2，方案第 4.3 节）。

密文引用本身带 key_id（`enc:v1:<key_id>:…`），所以不需要额外的 key_version 列：
- 还在用旧密钥的记录就是“引用以 enc:v1: 开头、但 key_id 不是当前密钥”的那些；
- `env:` 引用（部署 Secret）不加密，不参与轮换。

做法（运维手册见 docs/deployment.md 的“加密密钥轮换”）：
1. 生成新密钥，把它设为 CREDENTIAL_ENCRYPTION_KEY，
   旧密钥移到 CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS，重启；这时新旧密文都能解密，新写入的都用新密钥；
2. 运行 `python -m app.commands.rotate_credentials`，逐条重新加密，可以重复运行；
3. 报告里“仍是旧密钥”为 0 之后，从 CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS 删掉旧密钥，再重启。

每条记录单独更新，条件是引用没有被别人改过（WHERE 列 = 旧值），不覆盖并发写入；
不改 updated_at 与 version，重新加密对业务不可见。
"""

import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Table, and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_ca import agent_ca_context
from app.domain.agent_server_credentials import agent_server_password_context
from app.domain.agent_ssh import agent_ssh_context
from app.domain.agents import agent_secret_context
from app.domain.connectors import account_context
from app.domain.credentials import CredentialBox, CredentialError
from app.models import (
    AgentCa,
    AgentSecretVersion,
    AgentSshTarget,
    IntegrationAccount,
)

_PREFIX = "enc:v1:"
BATCH = 200


@dataclass(frozen=True)
class SealedField:
    """一个保存凭证引用的列，以及它的加密用途。"""

    name: str
    model: Any
    column: str
    context: Callable[[Any], str]


FIELDS: tuple[SealedField, ...] = (
    SealedField(
        "integration_accounts.credential_ref",
        IntegrationAccount,
        "credential_ref",
        lambda row: account_context(row.id),
    ),
    SealedField(
        "agent_secret_versions.ciphertext_ref",
        AgentSecretVersion,
        "ciphertext_ref",
        lambda row: agent_secret_context(row.secret_id, row.value_version),
    ),
    SealedField(
        "agent_ca.key_ref",
        AgentCa,
        "key_ref",
        lambda row: agent_ca_context(row.id),
    ),
    SealedField(
        "agent_ssh_targets.client_key_ref",
        AgentSshTarget,
        "client_key_ref",
        lambda row: agent_ssh_context(row.id),
    ),
    # OpenClaw 服务器保存的登录密码（T-0178）：只重新加密，不改 credential_revision
    SealedField(
        "agent_ssh_targets.login_password_ref",
        AgentSshTarget,
        "login_password_ref",
        lambda row: agent_server_password_context(row.id),
    ),
)


@dataclass
class FieldReport:
    rotated: int = 0
    failed: int = 0
    # 轮换之后各 key_id 下还剩多少条（只统计 enc:v1 引用）
    by_key: Counter[str] = field(default_factory=Counter)


@dataclass
class RotationReport:
    current_key_id: str
    fields: dict[str, FieldReport]

    @property
    def remaining_old(self) -> int:
        return sum(
            n
            for report in self.fields.values()
            for key_id, n in report.by_key.items()
            if key_id != self.current_key_id
        )

    @property
    def failed(self) -> int:
        return sum(report.failed for report in self.fields.values())


async def _count_by_key(db: AsyncSession, spec: SealedField) -> Counter[str]:
    column = getattr(spec.model, spec.column)
    rows = await db.execute(select(column).where(column.like(f"{_PREFIX}%")))
    return Counter(ref[len(_PREFIX) :].partition(":")[0] for (ref,) in rows)


async def _rotate_field(
    db: AsyncSession, box: CredentialBox, spec: SealedField, dry_run: bool
) -> FieldReport:
    report = FieldReport()
    model = spec.model
    column = getattr(model, spec.column)
    table: Table = model.__table__
    keep_updated_at = {"updated_at": table.c.updated_at} if "updated_at" in table.c else {}
    # 单列主键（多数表是 id，news_settings 是 workspace_id）
    (key_column,) = table.primary_key.columns
    key = getattr(model, key_column.key)
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(model)
            .where(column.like(f"{_PREFIX}%"), column.notlike(f"{_PREFIX}{box.key_id}:%"))
            .order_by(key)
            .limit(BATCH)
        )
        if last_id is not None:
            stmt = stmt.where(key > last_id)
        rows = (await db.execute(stmt)).scalars().all()
        if not rows:
            break
        for row in rows:
            old = getattr(row, spec.column)
            try:
                new = box.reseal(old, context=spec.context(row))
            except CredentialError:
                # 缺少对应的旧密钥，或者密文损坏：报告出来，不中断其他记录
                report.failed += 1
                continue
            if dry_run:
                report.rotated += 1
                continue
            result = await db.execute(
                update(table)
                .where(
                    and_(
                        key_column == getattr(row, key_column.key),
                        table.c[spec.column] == old,
                    )
                )
                .values({spec.column: new, **keep_updated_at})
            )
            report.rotated += result.rowcount or 0  # type: ignore[attr-defined]
        last_id = getattr(rows[-1], key_column.key)
        db.expunge_all()
        if not dry_run:
            await db.commit()
    report.by_key = await _count_by_key(db, spec)
    return report


def all_fields() -> tuple[SealedField, ...]:
    """宿主自己的加密列 + 各插拔式模块在清单里声明的（sealed_fields）。"""
    from app.modules import registry  # 模块的 sealed_fields 会导入本文件的 SealedField

    return FIELDS + registry.sealed_fields()


async def rotate(db: AsyncSession, box: CredentialBox, *, dry_run: bool = False) -> RotationReport:
    reports = {spec.name: await _rotate_field(db, box, spec, dry_run) for spec in all_fields()}
    return RotationReport(current_key_id=box.key_id, fields=reports)
