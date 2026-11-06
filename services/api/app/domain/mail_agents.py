"""邮箱分配给智能体（T-0132，docs/v2/MAIL_PLAN.md M4）。

- 管理员可以在 设置 → 邮件（按邮箱勾选智能体）或实例卡片（按智能体勾选邮箱）里分配，两处写同一张表；
- 改分配时两边的 version 都加 1，另一处没刷新的页面再保存会得到 409，不会悄悄盖掉；
- 断开邮箱时删除它的全部分配（重新连接后要重新分配）；
- 智能体的邮件工具只看得到分配给它、且没断开的邮箱（mail_tools._accounts）。
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.integrations import gmail
from app.models import AgentInstance, AgentMailAccount, MailAccount
from app.schemas.mail import AgentMailAccountRef, MailAgentRef


def account_ref(account: MailAccount) -> AgentMailAccountRef:
    return AgentMailAccountRef(
        id=account.id,
        email=account.email,
        status=account.status,
        can_send=gmail.SCOPE_SEND in (account.scopes or []),
    )


async def accounts_by_instance(
    db: AsyncSession, instance_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[AgentMailAccountRef]]:
    out: dict[uuid.UUID, list[AgentMailAccountRef]] = {i: [] for i in instance_ids}
    if not instance_ids:
        return out
    rows = await db.execute(
        select(AgentMailAccount.instance_id, MailAccount)
        .join(MailAccount, MailAccount.id == AgentMailAccount.account_id)
        .where(
            AgentMailAccount.instance_id.in_(instance_ids),
            MailAccount.status != "disconnected",
        )
        .order_by(MailAccount.created_at)
    )
    for instance_id, account in rows.all():
        out[instance_id].append(account_ref(account))
    return out


async def agents_by_account(
    db: AsyncSession, account_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[MailAgentRef]]:
    out: dict[uuid.UUID, list[MailAgentRef]] = {a: [] for a in account_ids}
    if not account_ids:
        return out
    rows = await db.execute(
        select(AgentMailAccount.account_id, AgentInstance.id, AgentInstance.name)
        .join(AgentInstance, AgentInstance.id == AgentMailAccount.instance_id)
        .where(AgentMailAccount.account_id.in_(account_ids))
        .order_by(AgentInstance.name, AgentInstance.id)
    )
    for account_id, instance_id, name in rows.all():
        out[account_id].append(MailAgentRef(id=instance_id, name=name))
    return out


async def _accounts_of(db: AsyncSession, instance_id: uuid.UUID) -> set[uuid.UUID]:
    query = select(AgentMailAccount.account_id).where(AgentMailAccount.instance_id == instance_id)
    return set(await db.scalars(query))


async def _instances_of(db: AsyncSession, account_id: uuid.UUID) -> set[uuid.UUID]:
    query = select(AgentMailAccount.instance_id).where(AgentMailAccount.account_id == account_id)
    return set(await db.scalars(query))


async def set_accounts_for_instance(
    db: AsyncSession,
    instance: AgentInstance,
    account_ids: Sequence[uuid.UUID],
    *,
    user_id: uuid.UUID | None,
) -> tuple[list[MailAccount], list[MailAccount]]:
    """实例这边整组替换（实例卡片）。返回（新增的邮箱，取消的邮箱）；变化的邮箱 version 加 1。

    调用方负责实例自己的 version 检查与加 1。"""
    wanted = set(account_ids)
    current = await _accounts_of(db, instance.id)
    changed_ids = wanted ^ current
    if not changed_ids:
        return [], []
    accounts = {
        a.id: a
        for a in await db.scalars(
            select(MailAccount).where(
                MailAccount.id.in_(changed_ids), MailAccount.workspace_id == instance.workspace_id
            )
        )
    }
    added = [accounts[i] for i in wanted - current if i in accounts]
    if len(added) != len(wanted - current) or any(a.status == "disconnected" for a in added):
        raise ApiError(422, "VALIDATION_FAILED", "选的邮箱不存在或已断开，请刷新后再选")
    removed_ids = current - wanted
    if added:
        await db.execute(
            insert(AgentMailAccount)
            .values(
                [
                    {
                        "workspace_id": instance.workspace_id,
                        "instance_id": instance.id,
                        "account_id": a.id,
                        "assigned_by": user_id,
                    }
                    for a in added
                ]
            )
            .on_conflict_do_nothing()
        )
    if removed_ids:
        await db.execute(
            delete(AgentMailAccount).where(
                AgentMailAccount.instance_id == instance.id,
                AgentMailAccount.account_id.in_(removed_ids),
            )
        )
    removed = [accounts[i] for i in removed_ids if i in accounts]
    for account in (*added, *removed):
        account.version += 1
    return added, removed


async def set_instances_for_account(
    db: AsyncSession,
    account: MailAccount,
    instance_ids: Sequence[uuid.UUID],
    *,
    user_id: uuid.UUID | None,
) -> tuple[list[AgentInstance], list[AgentInstance]]:
    """邮箱这边整组替换（设置 → 邮件）。返回（新增的实例，取消的实例）；变化的实例 version 加 1。

    调用方负责邮箱自己的 version 检查与加 1。"""
    wanted = set(instance_ids)
    current = await _instances_of(db, account.id)
    changed_ids = wanted ^ current
    if not changed_ids:
        return [], []
    instances = {
        i.id: i
        for i in await db.scalars(
            select(AgentInstance).where(
                AgentInstance.id.in_(changed_ids),
                AgentInstance.workspace_id == account.workspace_id,
            )
        )
    }
    added = [instances[i] for i in wanted - current if i in instances]
    if len(added) != len(wanted - current):
        raise ApiError(422, "VALIDATION_FAILED", "选的智能体不存在，请刷新后再选")
    removed_ids = current - wanted
    if added:
        await db.execute(
            insert(AgentMailAccount)
            .values(
                [
                    {
                        "workspace_id": account.workspace_id,
                        "instance_id": i.id,
                        "account_id": account.id,
                        "assigned_by": user_id,
                    }
                    for i in added
                ]
            )
            .on_conflict_do_nothing()
        )
    if removed_ids:
        await db.execute(
            delete(AgentMailAccount).where(
                AgentMailAccount.account_id == account.id,
                AgentMailAccount.instance_id.in_(removed_ids),
            )
        )
    removed = [instances[i] for i in removed_ids if i in instances]
    for instance in (*added, *removed):
        instance.version += 1
    return added, removed


async def clear_account(db: AsyncSession, account_id: uuid.UUID) -> int:
    """断开邮箱时删除它的全部分配，返回删掉的条数。"""
    result = await db.execute(
        delete(AgentMailAccount)
        .where(AgentMailAccount.account_id == account_id)
        .returning(AgentMailAccount.instance_id)
    )
    return len(result.all())
