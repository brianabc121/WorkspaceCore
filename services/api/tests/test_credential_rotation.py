"""加密密钥轮换（V2 OC-2）：真实 PostgreSQL。没有 TEST_DATABASE_URL 时跳过。"""

import secrets
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.agents import agent_secret_context
from app.domain.connectors import account_context, webhook_context
from app.domain.credential_rotation import rotate
from app.domain.credentials import CredentialBox
from app.models import AgentSecret, AgentSecretVersion, Integration, IntegrationAccount
from tests.helpers import add_user, add_workspace, run_sql


async def _seed(url: str, box: CredentialBox) -> dict[str, uuid.UUID]:
    owner = await add_user(url, "owner@example.com", "owner password 123456")
    workspace = await add_workspace(url, owner, "W")
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            integration_id, account_id, secret_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            db.add(
                Integration(
                    id=integration_id,
                    workspace_id=workspace,
                    provider="vikunja",
                    name="Vikunja",
                    base_url="http://vikunja:3456",
                    public_url="http://vikunja.example",
                    webhook_secret_ref=box.seal("hook", context=webhook_context(integration_id)),
                )
            )
            await db.flush()
            db.add(
                IntegrationAccount(
                    id=account_id,
                    integration_id=integration_id,
                    auth_type="api_token",
                    credential_ref=box.seal("token", context=account_context(account_id)),
                )
            )
            db.add(AgentSecret(id=secret_id, workspace_id=workspace, name="OpenAI"))
            await db.flush()
            db.add(
                AgentSecretVersion(
                    workspace_id=workspace,
                    secret_id=secret_id,
                    value_version=1,
                    ciphertext_ref=box.seal("sk-1", context=agent_secret_context(secret_id, 1)),
                )
            )
            await db.commit()
            return {"integration": integration_id, "account": account_id, "secret": secret_id}
    finally:
        await engine.dispose()


async def test_rotation_reencrypts_every_sealed_field(migrated_database_url: str) -> None:
    old_key, new_key = secrets.token_bytes(32), secrets.token_bytes(32)
    old = CredentialBox(old_key)
    ids = await _seed(migrated_database_url, old)
    before = await run_sql(
        migrated_database_url,
        "SELECT updated_at FROM workspace.integrations WHERE id = $1",
        ids["integration"],
    )
    box = CredentialBox(new_key, [old_key])
    engine = create_async_engine(migrated_database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            dry = await rotate(db, box, dry_run=True)
            assert dry.remaining_old == 3 and dry.failed == 0
            report = await rotate(db, box)
            assert report.remaining_old == 0 and report.failed == 0
            assert sum(f.rotated for f in report.fields.values()) == 3
            again = await rotate(db, box)  # 可以重复运行
            assert sum(f.rotated for f in again.fields.values()) == 0
            account = (await db.execute(select(IntegrationAccount))).scalar_one()
            integration = (await db.execute(select(Integration))).scalar_one()
            version = (await db.execute(select(AgentSecretVersion))).scalar_one()
    finally:
        await engine.dispose()
    assert box.key_id_in(account.credential_ref) == box.key_id
    assert (
        box.resolve(
            account.credential_ref, context=account_context(ids["account"])
        ).get_secret_value()
        == "token"
    )
    hook = box.resolve(
        integration.webhook_secret_ref or "", context=webhook_context(ids["integration"])
    )
    assert hook.get_secret_value() == "hook"
    secret = box.resolve(
        version.ciphertext_ref or "", context=agent_secret_context(ids["secret"], 1)
    )
    assert secret.get_secret_value() == "sk-1"
    # 重新加密对业务不可见：不改 updated_at
    assert integration.updated_at == before
    # 只用新密钥（删掉旧密钥之后）也能读
    assert (
        CredentialBox(new_key)
        .resolve(account.credential_ref, context=account_context(ids["account"]))
        .get_secret_value()
        == "token"
    )


async def test_ciphertext_without_its_key_is_reported_not_dropped(
    migrated_database_url: str,
) -> None:
    unknown = CredentialBox(secrets.token_bytes(32))
    await _seed(migrated_database_url, unknown)
    box = CredentialBox(secrets.token_bytes(32))  # 没有配置那把旧密钥
    engine = create_async_engine(migrated_database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            report = await rotate(db, box)
    finally:
        await engine.dispose()
    assert report.failed == 3
    assert report.remaining_old == 3  # 原样保留，不会被清掉或写坏


@pytest.mark.parametrize("dry_run", [True])
async def test_dry_run_writes_nothing(migrated_database_url: str, dry_run: bool) -> None:
    old_key = secrets.token_bytes(32)
    await _seed(migrated_database_url, CredentialBox(old_key))
    box = CredentialBox(secrets.token_bytes(32), [old_key])
    engine = create_async_engine(migrated_database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            await rotate(db, box, dry_run=dry_run)
            report = await rotate(db, box, dry_run=True)
    finally:
        await engine.dispose()
    assert report.remaining_old == 3
