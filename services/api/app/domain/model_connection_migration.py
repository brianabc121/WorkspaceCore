"""旧密钥库导入为中转站管理的连接映射（T-0203，MC-3）。

依据 docs/v2/model-connections/docs/rollout/2026-11-09-model-connections-migration.md 与接口约定
“迁移分类与兼容凭据状态”：

- 每条旧密钥（agent_secrets）最多一个映射：credential_secret_id = legacy_source_id = 原密钥 ID
  （credential_secret_id 上的唯一约束保证）；不复制密文、不换 ID，
  不改 value_version、ref_id、授权与取用记录；
- model_api_key → needs_configuration；api_token / generic → needs_classification
  （不当作模型 Key，由 owner 分类）；configuration_state 都是 draft，
  当前版本解不开或没有密文 → needs_review（不写假成功，原记录保留）；
- enabled = true：保留旧授权的取用（alias 与 Runner 注入）；不表示能用于新部署——原生取用要
  ready + 绑定 + 连接授权。导入不建模型、不建绑定、不访问任何外部服务；
- 旧授权按实例去重，每个实例一条连接授权（只作追溯，不带来原生模型能力）；
  不多给任何旧授权之外的访问；已映射的项重跑时不再补授权（撤销过的不会被恢复）；
- 新建连接自己的内部凭据（migration_state = native、legacy_source_id 为空）不算旧密钥，不扫描。

三种模式（都必须指定工作空间）：

- dry_run：只读盘点；可以在内存里解密检查能否读取，不写任何记录、不保存解密结果
  （在只读的数据库连接上也能运行）；
- apply：按批导入，一批一个事务；一批出错只回滚这一批，已提交的批不受影响，后面的批继续，
  重跑时接着导入没完成的；并行运行时每批先锁住这批旧密钥（FOR UPDATE）排队，
  再由唯一约束 + ON CONFLICT DO NOTHING 兜底，不会重复；
- report：只读对账（映射、授权、绑定、取用记录的数量）。

计数的含义（MigrationReport）：
- scanned：工作空间里的旧密钥数；
- mapped：本次新建的映射数（dry_run 是将新建的数，report 为 0）；
- already_mapped：运行前已有映射的旧密钥数（并行运行时被另一次运行先建好的也算这里）；
- needs_configuration / needs_classification / unreadable：每条旧密钥运行后所处的状态
  （已有映射按库里的状态；还没映射的按 apply 会设的状态；本次失败的不计）；
  unreadable 指 configuration_state = needs_review；
- failed：本次失败回滚、留待重跑的旧密钥数。

输出只有计数与错误类别，不含 Key、密文、名称或异常原文。
"""

import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.agents import agent_secret_context
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.models import (
    AgentModelBinding,
    AgentSecret,
    AgentSecretAccess,
    AgentSecretGrant,
    AgentSecretVersion,
    ModelConnection,
    ModelConnectionGrant,
    Workspace,
)

Mode = Literal["dry_run", "apply", "report"]

DEFAULT_BATCH_SIZE = 200
MAX_BATCH_SIZE = 1000
NAME_MAX = 64
# 名称和已有连接重名时加的后缀：“名称（导入）”“名称（导入 2）”……
IMPORT_SUFFIX = "（导入）"
MAX_NAME_ATTEMPTS = 50
SECRET_KINDS = ("model_api_key", "api_token", "generic")


class WorkspaceNotFound(Exception):
    """指定的工作空间不存在。"""


@dataclass
class BatchResult:
    batch: int
    scanned: int = 0
    mapped: int = 0
    already_mapped: int = 0
    failed: int = 0
    # committed：已提交；rolled_back：整批回滚、留待重跑；read_only：dry_run / report
    status: str = "read_only"
    # 只有错误类别，不含异常原文
    error: str | None = None


@dataclass
class _Tally:
    needs_configuration: int = 0
    needs_classification: int = 0
    unreadable: int = 0

    def add(self, migration_state: str, configuration_state: str) -> None:
        if migration_state == "needs_configuration":
            self.needs_configuration += 1
        elif migration_state == "needs_classification":
            self.needs_classification += 1
        if configuration_state == "needs_review":
            self.unreadable += 1


@dataclass
class MigrationReport:
    mode: Mode
    workspace_id: uuid.UUID
    batch_size: int
    scanned: int = 0
    mapped: int = 0
    already_mapped: int = 0
    needs_configuration: int = 0
    needs_classification: int = 0
    unreadable: int = 0
    failed: int = 0
    batches: list[BatchResult] = field(default_factory=list)
    # 只有 report 模式有：对账明细（都是计数）
    reconciliation: dict[str, Any] | None = None

    def merge(self, tally: _Tally) -> None:
        self.needs_configuration += tally.needs_configuration
        self.needs_classification += tally.needs_classification
        self.unreadable += tally.unreadable

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["workspace_id"] = str(self.workspace_id)
        if self.reconciliation is None:
            data.pop("reconciliation")
        return data


@dataclass(frozen=True)
class _Legacy:
    id: uuid.UUID
    name: str
    kind: str
    description: str


def planned_states(kind: str, readable: bool) -> tuple[str, str]:
    """导入时设的 (migration_state, configuration_state)。"""
    migration_state = "needs_configuration" if kind == "model_api_key" else "needs_classification"
    return migration_state, ("draft" if readable else "needs_review")


def _readable(box: CredentialBox, secret_id: uuid.UUID, version: int, ref: str | None) -> bool:
    """当前版本能否用原来的加密用途解开（只在内存里解密，不保存、不输出）。"""
    if not ref:
        return False
    try:
        box.resolve(ref, context=agent_secret_context(secret_id, version))
    except CredentialError:
        return False
    return True


def _with_suffix(base: str, suffix: str) -> str:
    return base[: NAME_MAX - len(suffix)].rstrip() + suffix


def name_candidates(base: str) -> Iterator[str]:
    """导入的连接名：先用原名；和已有连接重名时加“（导入）”“（导入 2）”……，都不超过 64 个字符。"""
    yield base[:NAME_MAX]
    yield _with_suffix(base, IMPORT_SUFFIX)
    for n in range(2, MAX_NAME_ATTEMPTS + 2):
        yield _with_suffix(base, f"（导入 {n}）")


class _NameExhausted(Exception):
    """可用的导入名都被占了。"""


def _error_category(error: BaseException) -> str:
    if isinstance(error, IntegrityError):
        return "integrity_conflict"
    if isinstance(error, DBAPIError | SQLAlchemyError):
        return "database_error"
    if isinstance(error, CredentialError):
        return "credential_error"
    if isinstance(error, _NameExhausted):
        return "name_conflict"
    return "unexpected_error"


def _native_secret() -> Any:
    """新建连接自己的内部凭据（不是旧密钥，不扫描）。"""
    return (
        select(ModelConnection.id)
        .where(
            ModelConnection.credential_secret_id == AgentSecret.id,
            ModelConnection.legacy_source_id.is_(None),
        )
        .exists()
    )


async def migrate_workspace(
    db: AsyncSession,
    settings: Settings,
    box: CredentialBox,
    workspace_id: uuid.UUID,
    *,
    mode: Mode,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> MigrationReport:
    """按批扫描这个工作空间的旧密钥；apply 时导入还没映射的。见模块说明。"""
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError("batch_size 超出范围")
    if await db.get(Workspace, workspace_id) is None:
        raise WorkspaceNotFound
    report = MigrationReport(mode=mode, workspace_id=workspace_id, batch_size=batch_size)
    reconciliation_unmapped = 0
    unmapped_by_kind: dict[str, int] = {}
    last_id: uuid.UUID | None = None
    index = 0
    while True:
        query = (
            select(AgentSecret.id, AgentSecret.name, AgentSecret.kind, AgentSecret.description)
            .where(AgentSecret.workspace_id == workspace_id, ~_native_secret())
            .order_by(AgentSecret.id)
            .limit(batch_size)
        )
        if last_id is not None:
            query = query.where(AgentSecret.id > last_id)
        rows = [_Legacy(*row) for row in (await db.execute(query)).all()]
        if not rows:
            break
        last_id = rows[-1].id
        index += 1
        batch = BatchResult(batch=index, scanned=len(rows))
        report.scanned += len(rows)
        tally = _Tally()
        if mode == "apply":
            try:
                await _apply_batch(db, settings, box, workspace_id, rows, batch, tally)
                await db.commit()
                batch.status = "committed"
            except Exception as error:
                # 一批出错只回滚这一批，只记错误类别
                await db.rollback()
                batch.status = "rolled_back"
                batch.error = _error_category(error)
                # 回滚后重新数：这一批里已有映射的（可能是并行运行建的）算已映射，其余留待重跑
                tally, batch.already_mapped = await _existing_tally(db, [r.id for r in rows])
                batch.mapped = 0
                batch.failed = batch.scanned - batch.already_mapped
        else:
            pending = await _plan_batch(db, box, rows, batch, tally)
            if mode == "dry_run":
                batch.mapped = len(pending)
            else:
                reconciliation_unmapped += len(pending)
                for legacy in pending:
                    unmapped_by_kind[legacy.kind] = unmapped_by_kind.get(legacy.kind, 0) + 1
            # 只读模式：结束这一批的只读事务，不留快照
            await db.rollback()
        report.mapped += batch.mapped
        report.already_mapped += batch.already_mapped
        report.failed += batch.failed
        report.merge(tally)
        report.batches.append(batch)
    if mode == "report":
        report.reconciliation = await _reconcile(
            db, workspace_id, unmapped=reconciliation_unmapped, unmapped_by_kind=unmapped_by_kind
        )
        await db.rollback()
    return report


async def _existing_tally(db: AsyncSession, secret_ids: list[uuid.UUID]) -> tuple[_Tally, int]:
    """按库里的状态统计这些旧密钥中已有映射的，返回 (状态计数, 条数)。"""
    existing = await _mappings(db, secret_ids)
    await db.rollback()
    tally = _Tally()
    for migration_state, configuration_state in existing.values():
        tally.add(migration_state, configuration_state)
    return tally, len(existing)


async def _mappings(
    db: AsyncSession, secret_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    return {
        secret_id: (migration_state, configuration_state)
        for secret_id, migration_state, configuration_state in (
            await db.execute(
                select(
                    ModelConnection.credential_secret_id,
                    ModelConnection.migration_state,
                    ModelConnection.configuration_state,
                ).where(ModelConnection.credential_secret_id.in_(secret_ids))
            )
        ).all()
    }


async def _readability(
    db: AsyncSession, box: CredentialBox, secret_ids: list[uuid.UUID]
) -> dict[uuid.UUID, bool]:
    """当前版本的密文能否解开（没有当前版本或没有密文都算不能）。"""
    if not secret_ids:
        return {}
    found = {
        secret_id: (version, ref)
        for secret_id, version, ref in (
            await db.execute(
                select(
                    AgentSecret.id, AgentSecret.current_version, AgentSecretVersion.ciphertext_ref
                )
                .outerjoin(
                    AgentSecretVersion,
                    (AgentSecretVersion.secret_id == AgentSecret.id)
                    & (AgentSecretVersion.value_version == AgentSecret.current_version),
                )
                .where(AgentSecret.id.in_(secret_ids))
            )
        ).all()
    }
    out: dict[uuid.UUID, bool] = {}
    for secret_id in secret_ids:
        version, ref = found.get(secret_id, (0, None))
        out[secret_id] = _readable(box, secret_id, version, ref)
    return out


async def _plan_batch(
    db: AsyncSession,
    box: CredentialBox,
    rows: list[_Legacy],
    batch: BatchResult,
    tally: _Tally,
) -> list[_Legacy]:
    """只读：已有映射按库里的状态计数，没映射的按 apply 会设的状态计数，返回没映射的。"""
    existing = await _mappings(db, [r.id for r in rows])
    for migration_state, configuration_state in existing.values():
        tally.add(migration_state, configuration_state)
    batch.already_mapped = len(existing)
    pending = [r for r in rows if r.id not in existing]
    readable = await _readability(db, box, [r.id for r in pending])
    for legacy in pending:
        tally.add(*planned_states(legacy.kind, readable[legacy.id]))
    return pending


async def _apply_batch(
    db: AsyncSession,
    settings: Settings,
    box: CredentialBox,
    workspace_id: uuid.UUID,
    rows: list[_Legacy],
    batch: BatchResult,
    tally: _Tally,
) -> None:
    """在当前事务里导入这一批还没映射的旧密钥（调用方提交或回滚）。

    先按 ID 顺序锁住这一批旧密钥（FOR UPDATE）：并行的导入、旧接口对同一条密钥的写入与授权修改
    在这里排队，锁到之后再读映射与旧授权，读到的都是最新提交的。期间被删掉的旧密钥跳过。"""
    present = set(
        (
            await db.scalars(
                select(AgentSecret.id)
                .where(AgentSecret.id.in_([r.id for r in rows]))
                .order_by(AgentSecret.id)
                .with_for_update()
            )
        ).all()
    )
    rows = [r for r in rows if r.id in present]
    existing = await _mappings(db, [r.id for r in rows])
    for migration_state, configuration_state in existing.values():
        tally.add(migration_state, configuration_state)
    batch.already_mapped = len(existing)
    pending = [r for r in rows if r.id not in existing]
    if not pending:
        return
    readable = await _readability(db, box, [r.id for r in pending])
    instances: dict[uuid.UUID, set[uuid.UUID]] = {}
    for secret_id, instance_id in (
        await db.execute(
            select(AgentSecretGrant.secret_id, AgentSecretGrant.instance_id).where(
                AgentSecretGrant.secret_id.in_([r.id for r in pending])
            )
        )
    ).all():
        instances.setdefault(secret_id, set()).add(instance_id)
    used = set(
        (
            await db.scalars(
                select(ModelConnection.name).where(ModelConnection.workspace_id == workspace_id)
            )
        ).all()
    )
    audit = AuditTrail(db, settings)
    for legacy in pending:
        migration_state, configuration_state = planned_states(legacy.kind, readable[legacy.id])
        connection_id = await _insert_mapping(
            db,
            workspace_id,
            legacy,
            used,
            migration_state=migration_state,
            configuration_state=configuration_state,
        )
        if connection_id is None:
            # 并行运行先建好了映射：算已映射，按它的状态计数
            current = (await _mappings(db, [legacy.id]))[legacy.id]
            tally.add(*current)
            batch.already_mapped += 1
            continue
        granted = sorted(instances.get(legacy.id, set()))
        if granted:
            await db.execute(
                pg_insert(ModelConnectionGrant)
                .values(
                    [
                        {
                            "workspace_id": workspace_id,
                            "connection_id": connection_id,
                            "instance_id": instance_id,
                        }
                        for instance_id in granted
                    ]
                )
                .on_conflict_do_nothing()
            )
        await audit.record(
            "model_connection.imported",
            workspace_ids=[workspace_id],
            resource_type="model_connection",
            resource_id=connection_id,
            metadata={
                "legacy_kind": legacy.kind,
                "migration_state": migration_state,
                "configuration_state": configuration_state,
                "grant_count": len(granted),
            },
        )
        tally.add(migration_state, configuration_state)
        batch.mapped += 1


async def _insert_mapping(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    legacy: _Legacy,
    used: set[str],
    *,
    migration_state: str,
    configuration_state: str,
) -> uuid.UUID | None:
    """插入映射；这条旧密钥已有映射（并行运行）时返回 None。重名时换下一个导入名。"""
    for name in name_candidates(legacy.name):
        if name in used:
            continue
        used.add(name)
        new_id = (
            await db.execute(
                pg_insert(ModelConnection)
                .values(
                    workspace_id=workspace_id,
                    name=name,
                    description=legacy.description[:500],
                    connection_type=None,
                    official_provider=None,
                    protocol=None,
                    base_url=None,
                    credential_secret_id=legacy.id,
                    model_scope="selected",
                    configuration_state=configuration_state,
                    enabled=True,
                    migration_state=migration_state,
                    legacy_source_id=legacy.id,
                )
                .on_conflict_do_nothing()
                .returning(ModelConnection.id)
            )
        ).scalar_one_or_none()
        if new_id is not None:
            return new_id
        # 没插进去：要么这条旧密钥已被并行运行映射，要么名字刚被别的连接占用
        if legacy.id in await _mappings(db, [legacy.id]):
            return None
    raise _NameExhausted


async def _reconcile(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    unmapped: int,
    unmapped_by_kind: dict[str, int],
) -> dict[str, Any]:
    """只读对账：都是计数，不含名称、Key 或密文。"""
    imported = and_(
        ModelConnection.workspace_id == workspace_id,
        ModelConnection.legacy_source_id.is_not(None),
    )
    by_state: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {
        kind: {"mapped": 0, "unmapped": unmapped_by_kind.get(kind, 0)} for kind in SECRET_KINDS
    }
    for kind, migration_state, n in (
        await db.execute(
            select(AgentSecret.kind, ModelConnection.migration_state, func.count())
            .join(AgentSecret, AgentSecret.id == ModelConnection.credential_secret_id)
            .where(imported)
            .group_by(AgentSecret.kind, ModelConnection.migration_state)
        )
    ).all():
        by_state[migration_state] = by_state.get(migration_state, 0) + n
        entry = by_kind.setdefault(kind, {"mapped": 0, "unmapped": 0})
        entry["mapped"] += n
        entry[migration_state] = entry.get(migration_state, 0) + n

    async def count(query: Any) -> int:
        return int(await db.scalar(query) or 0)

    def count_rows(query: Any) -> Any:
        return select(func.count()).select_from(query.subquery())

    legacy_grants = (
        select(AgentSecretGrant.secret_id, AgentSecretGrant.instance_id)
        .join(ModelConnection, ModelConnection.credential_secret_id == AgentSecretGrant.secret_id)
        .where(imported)
    )
    has_connection_grant = (
        select(ModelConnectionGrant.id)
        .where(
            ModelConnectionGrant.connection_id == ModelConnection.id,
            ModelConnectionGrant.instance_id == AgentSecretGrant.instance_id,
        )
        .exists()
    )
    has_legacy_grant = (
        select(AgentSecretGrant.id)
        .where(
            AgentSecretGrant.secret_id == ModelConnection.credential_secret_id,
            AgentSecretGrant.instance_id == ModelConnectionGrant.instance_id,
        )
        .exists()
    )
    bound = (
        select(ModelConnection.id)
        .where(
            imported,
            or_(
                AgentModelBinding.connection_id == ModelConnection.id,
                AgentModelBinding.applied_connection_id == ModelConnection.id,
            ),
        )
        .exists()
    )
    return {
        "unmapped": unmapped,
        "by_state": by_state,
        "by_kind": by_kind,
        "needs_review": await count(
            select(func.count())
            .select_from(ModelConnection)
            .where(imported, ModelConnection.configuration_state == "needs_review")
        ),
        "disabled": await count(
            select(func.count())
            .select_from(ModelConnection)
            .where(imported, ModelConnection.enabled.is_(False))
        ),
        # 旧授权（ref 条数）与按（旧密钥, 实例）去重后的数
        "legacy_grants": await count(count_rows(legacy_grants)),
        "legacy_grant_instances": await count(count_rows(legacy_grants.distinct())),
        "connection_grants": await count(
            select(func.count())
            .select_from(ModelConnectionGrant)
            .join(ModelConnection, ModelConnection.id == ModelConnectionGrant.connection_id)
            .where(imported)
        ),
        # 有旧授权、却没有对应连接授权的（旧密钥, 实例）对
        "missing_connection_grants": await count(
            count_rows(legacy_grants.where(~has_connection_grant).distinct())
        ),
        # 连接授权里没有旧授权的实例（在中转站管理里单独授权的）
        "extra_connection_grants": await count(
            select(func.count())
            .select_from(ModelConnectionGrant)
            .join(ModelConnection, ModelConnection.id == ModelConnectionGrant.connection_id)
            .where(imported, ~has_legacy_grant)
        ),
        "bindings": await count(select(func.count()).select_from(AgentModelBinding).where(bound)),
        "accesses": await count(
            select(func.count())
            .select_from(AgentSecretAccess)
            .join(
                ModelConnection, ModelConnection.credential_secret_id == AgentSecretAccess.secret_id
            )
            .where(imported)
        ),
    }
