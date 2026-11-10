"""中转站管理（MC-3，T-0203）：旧密钥库的幂等导入与共享凭据兼容。真实 PostgreSQL + Redis。

覆盖验收矩阵（docs/v2/model-connections/docs/acceptance/2026-11-09-model-connections-tests.md）
第 2 节 M01–M07、M09、M11–M13，与后端计划的 Review Focus 1、4、5：

- 导入不复制密文、不换 ID，旧密钥 / 版本 / 授权 / 取用记录一个字节都不变，原 context 仍能解密；
- model_api_key → 待完善，api_token / generic → 待归类（不当作 OpenAI Key），解不开 → needs_review；
  不建模型、不建绑定；
- 重复运行、并行运行、中途一批失败后重跑：一对一映射，没有重复授权；
- dry-run / report 只读（只读数据库连接下运行，库里没有任何变化）；输出不含 Key 与异常原文；
- 停用 / 撤权之后旧取密钥接口与 Runner 注入都拒绝；归类为非模型凭据不恢复访问，只有 owner 显式启用；
- 旧密钥接口对托管项只读（409 MANAGED_BY_MODEL_CONNECTION），对兼容凭据的写入同步连接版本；
- 加密主密钥轮换后导入的凭据照样能读；导入的草稿首次补 URL / 协议不受旧授权限制。
"""

import asyncio
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.commands import migrate_model_connections as cli
from app.domain.agents import agent_secret_context
from app.domain.credential_rotation import rotate
from app.domain.credentials import CredentialBox, parse_key
from app.domain.model_connection_migration import (
    Mode,
    _insert_mapping,
    _Legacy,
    migrate_workspace,
    name_candidates,
)
from app.domain.model_connections import ModelConnectionService
from app.domain.runner_hosts import resolve_env_secrets
from app.models import ModelConnection, RunnerEnvironment
from tests.helpers import OWNER, ClientFactory, run_sql
from tests.test_agents_api import SETTINGS, Env, resolve

pytestmark = pytest.mark.integration

MC = "/api/v1/model-connections"
SECRETS = "/api/v1/agents/secrets"
# 导入不能改动的旧表
LEGACY_TABLES = (
    "agent_secrets",
    "agent_secret_versions",
    "agent_secret_grants",
    "agent_secret_accesses",
)
ALL_TABLES = (
    *LEGACY_TABLES,
    "model_connections",
    "model_connection_grants",
    "model_connection_models",
    "agent_model_bindings",
    "audit_logs",
)


FINGERPRINT = (
    "SELECT count(*) || ':' || md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))"
)


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


# ---------------------------------------------------------------- 小工具


async def add_secret(
    env: Env, name: str, *, kind: str = "model_api_key", value: str | None = None
) -> dict[str, Any]:
    response = await env.client.post(
        SECRETS,
        json={"name": name, "kind": kind, "value": value or f"value-{name}"},
        headers=env.headers(write=True),
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def migrate(
    env: Env, mode: Mode = "apply", *, batch_size: int = 200
) -> tuple[dict[str, Any], int]:
    """走命令的 run()：自己建数据库连接，与 docker compose exec 里运行一样。"""
    output, code = await cli.run(
        uuid.UUID(env.workspace_id), mode, batch_size=batch_size, settings=env.app.state.settings
    )
    text = json.dumps(output, ensure_ascii=False)
    # 输出里只有计数与类别：没有 Key、密文
    assert "enc:v1" not in text and "value-" not in text and "sk-" not in text
    return output, code


async def mappings(env: Env) -> dict[str, ModelConnection]:
    """旧密钥 ID → 映射（包括新建连接自己的内部凭据）。"""
    async with env.app.state.sessionmaker() as db:
        rows = (await db.scalars(select(ModelConnection))).all()
    return {str(r.credential_secret_id): r for r in rows}


async def connection(env: Env, connection_id: uuid.UUID | str) -> dict[str, Any]:
    response = await env.client.get(f"{MC}/{connection_id}", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def patch_connection(env: Env, connection_id: uuid.UUID | str, **body: Any) -> httpx.Response:
    version = (await connection(env, connection_id))["version"]
    return await env.client.patch(
        f"{MC}/{connection_id}",
        json={"version": version, **body},
        headers=env.headers(write=True),
    )


async def set_enabled(env: Env, connection_id: uuid.UUID | str, enabled: bool) -> None:
    response = await patch_connection(env, connection_id, enabled=enabled, confirm_impact=True)
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is enabled


async def classify(
    env: Env, connection_id: uuid.UUID | str, purpose: str, *, version: int | None = None
) -> httpx.Response:
    if version is None:
        version = (await connection(env, connection_id))["version"]
    return await env.client.post(
        f"{MC}/migration/entries/{connection_id}/classify",
        json={"version": version, "purpose": purpose},
        headers=env.headers(write=True),
    )


async def put_connection_grants(
    env: Env, connection_id: uuid.UUID | str, instance_ids: list[str]
) -> httpx.Response:
    version = (await connection(env, connection_id))["version"]
    return await env.client.put(
        f"{MC}/{connection_id}/grants",
        json={"version": version, "instance_ids": instance_ids},
        headers=env.headers(write=True),
    )


async def replace_grants(
    env: Env, instance_id: str, grants: list[dict[str, str]]
) -> httpx.Response:
    version = (await env.instance(instance_id))["version"]
    return await env.client.put(
        f"/api/v1/agents/instances/{instance_id}/secret-grants",
        json={"version": version, "grants": grants},
        headers=env.headers(write=True),
    )


async def legacy_secret(env: Env, secret_id: str) -> dict[str, Any]:
    response = await env.client.get(f"{SECRETS}/{secret_id}", headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def runner_values(
    env: Env, instance_id: str, refs: list[str], *, for_redaction: bool = False
) -> dict[str, str]:
    """Runner 给这个实例的环境注入的密钥（不落库的临时环境对象）。"""
    environment = RunnerEnvironment(
        owner_instance_id=uuid.UUID(instance_id), spec={"secrets": refs}
    )
    async with env.app.state.sessionmaker() as db:
        return await resolve_env_secrets(
            db, env.app.state.settings, environment, record=False, for_redaction=for_redaction
        )


async def values_of(env: Env, token: str, refs: list[str]) -> dict[str, str]:
    response = await resolve(env.client, token, refs)
    assert response.status_code == 200, response.text
    values: dict[str, str] = response.json()["values"]
    return values


async def fingerprints(database_url: str, tables: tuple[str, ...]) -> dict[str, str]:
    """每张表全部行的摘要：只要有一行、一个字段变了就不同。"""
    out: dict[str, str] = {}
    for table in tables:
        # 表名来自本文件里的常量
        out[table] = await run_sql(database_url, f"{FINGERPRINT} FROM workspace.{table} t")
    return out


async def connection_grants(env: Env, connection_id: uuid.UUID | str) -> set[str]:
    rows = await run_sql(
        env.database_url,
        "SELECT coalesce(array_agg(instance_id::text), '{}') FROM workspace.model_connection_grants"
        " WHERE connection_id = $1",
        uuid.UUID(str(connection_id)),
    )
    return set(rows)


async def legacy_grant_count(env: Env, secret_id: str, instance_id: str) -> int:
    count: int = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.agent_secret_grants"
        " WHERE secret_id = $1 AND instance_id = $2",
        uuid.UUID(secret_id),
        uuid.UUID(instance_id),
    )
    return count


async def add_binding(env: Env, instance_id: str, connection_id: uuid.UUID) -> None:
    """直接写一条原生主模型绑定（绑定接口属于后续任务）。"""
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.agent_model_bindings"
        " (instance_id, workspace_id, connection_id, model_id, apply_state)"
        " VALUES ($1, $2, $3, 'gpt-4.1', 'pending')",
        uuid.UUID(instance_id),
        uuid.UUID(env.workspace_id),
        connection_id,
    )


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ---------------------------------------------------------------- M05：ID、版本、授权与记录不变


async def test_migration_preserves_ids_versions_grants_and_accesses(env: Env) -> None:
    model = await add_secret(env, "OpenAI", value="sk-model-1")
    github = await add_secret(env, "GitHub", kind="api_token", value="ghp-token-1")
    a = await env.create_instance(name="实例 A")
    b = await env.create_instance(name="实例 B")
    # 同一实例对同一密钥两个引用名：连接授权按实例去重
    await env.grant(
        a["id"],
        [
            {"secret_id": model["id"], "ref_id": "openai_api_key"},
            {"secret_id": model["id"], "ref_id": "fallback_key"},
            {"secret_id": github["id"], "ref_id": "gh_token"},
        ],
    )
    await env.grant(b["id"], [{"secret_id": model["id"], "ref_id": "openai_api_key"}])
    rotated = await env.client.post(
        f"{SECRETS}/{model['id']}/rotate",
        json={"version": model["version"], "value": "sk-model-2"},
        headers=env.headers(write=True),
    )
    assert rotated.status_code == 200 and rotated.json()["value_version"] == 2
    token = await env.issue_token(a["id"])
    assert await values_of(env, token, ["openai_api_key", "gh_token"]) == {
        "openai_api_key": "sk-model-2",
        "gh_token": "ghp-token-1",
    }
    model_access_ids = set(
        await run_sql(
            env.database_url,
            "SELECT array_agg(id::text) FROM workspace.agent_secret_accesses WHERE secret_id = $1",
            uuid.UUID(model["id"]),
        )
    )
    assert len(model_access_ids) == 1
    before = await fingerprints(env.database_url, LEGACY_TABLES)

    output, code = await migrate(env)
    assert code == 0, output
    assert output["mode"] == "apply" and output["workspace_id"] == env.workspace_id
    assert (output["scanned"], output["mapped"], output["already_mapped"], output["failed"]) == (
        2,
        2,
        0,
        0,
    )
    assert (output["needs_configuration"], output["needs_classification"]) == (1, 1)
    assert output["unreadable"] == 0
    assert [b["status"] for b in output["batches"]] == ["committed"]

    # 旧密钥、版本、授权、取用记录一个字节都没变（不复制密文、不换 ID）
    assert await fingerprints(env.database_url, LEGACY_TABLES) == before
    rows = await mappings(env)
    assert set(rows) == {model["id"], github["id"]}
    for secret in (model, github):
        mapped = rows[secret["id"]]
        assert str(mapped.credential_secret_id) == str(mapped.legacy_source_id) == secret["id"]
    assert await connection_grants(env, rows[model["id"]].id) == {a["id"], b["id"]}
    assert await connection_grants(env, rows[github["id"]].id) == {a["id"]}

    # 密文仍用原来的加密用途（secret_id + value_version）解密
    ref = await run_sql(
        env.database_url,
        "SELECT ciphertext_ref FROM workspace.agent_secret_versions"
        " WHERE secret_id = $1 AND value_version = 2",
        uuid.UUID(model["id"]),
    )
    box = CredentialBox.from_settings(env.app.state.settings)
    plain = box.resolve(ref, context=agent_secret_context(uuid.UUID(model["id"]), 2))
    assert plain.get_secret_value() == "sk-model-2"

    # 取用照常：旧 alias 与 Runner 注入（原允许的继续允许）
    assert await values_of(env, token, ["openai_api_key", "fallback_key", "gh_token"]) == {
        "openai_api_key": "sk-model-2",
        "fallback_key": "sk-model-2",
        "gh_token": "ghp-token-1",
    }
    assert await runner_values(env, a["id"], ["gh_token"]) == {"gh_token": "ghp-token-1"}

    # 连接页能追溯到导入前的取用记录与导入事件
    activity = await env.client.get(f"{MC}/{rows[model['id']].id}/activity", headers=env.headers())
    assert activity.status_code == 200, activity.text
    items = activity.json()["items"]
    assert model_access_ids <= {i["id"] for i in items if i["kind"] == "access"}
    assert any(i["kind"] == "access" and i["value_version"] == 2 for i in items)
    assert any(i["action"] == "model_connection.imported" for i in items)
    assert "sk-model" not in activity.text

    # 旧密钥库里能看到映射（链接到新页面），值版本与 ID 不变
    listed = await env.client.get(SECRETS, headers=env.headers())
    by_id = {s["id"]: s for s in listed.json()["items"]}
    assert by_id[model["id"]]["model_connection_id"] == str(rows[model["id"]].id)
    assert by_id[model["id"]]["migration_state"] == "needs_configuration"
    assert by_id[model["id"]]["value_version"] == 2
    assert by_id[github["id"]]["migration_state"] == "needs_classification"


# ---------------------------------------------------------------- M03：幂等、并行、可恢复


async def test_migration_is_idempotent_concurrent_and_resumable(env: Env) -> None:
    # 已有同名的连接：导入名加“（导入）”
    native = await env.client.post(
        MC,
        json={"name": "key-0", "api_key": "sk-native-0", "save_as_draft": True},
        headers=env.headers(write=True),
    )
    assert native.status_code == 201, native.text
    instance = await env.create_instance()
    kinds = ("model_api_key", "api_token", "generic")
    created = [await add_secret(env, f"key-{i}", kind=kinds[i % 3]) for i in range(7)]
    await env.grant(
        instance["id"],
        [{"secret_id": s["id"], "ref_id": f"ref_{i}"} for i, s in enumerate(created)],
    )

    # 中途一批失败：数据库触发器让 key-3 的映射插入报错；这一批回滚，其他批照常提交
    await run_sql(
        env.database_url,
        "CREATE FUNCTION workspace.t0203_fail() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF NEW.name = 'key-3' THEN RAISE EXCEPTION 'injected-failure-t0203'; END IF;"
        " RETURN NEW; END $$",
    )
    await run_sql(
        env.database_url,
        "CREATE TRIGGER t0203_fail BEFORE INSERT ON workspace.model_connections"
        " FOR EACH ROW EXECUTE FUNCTION workspace.t0203_fail()",
    )
    first, code = await migrate(env, batch_size=2)
    assert code == 1, first
    assert "injected-failure" not in json.dumps(first)
    rolled = [b for b in first["batches"] if b["status"] == "rolled_back"]
    assert len(rolled) == 1 and rolled[0]["error"] == "database_error"
    assert all(b["status"] == "committed" for b in first["batches"] if b not in rolled)
    failed = rolled[0]["failed"]
    assert failed in (1, 2) and first["failed"] == failed
    assert first["scanned"] == 7 and first["mapped"] == 7 - failed
    rows = await mappings(env)
    assert created[3]["id"] not in rows
    assert len([r for r in rows.values() if r.legacy_source_id is not None]) == 7 - failed

    # 修好之后重跑：只补没完成的，已提交的不重复
    await run_sql(env.database_url, "DROP TRIGGER t0203_fail ON workspace.model_connections")
    second, code = await migrate(env, batch_size=2)
    assert code == 0, second
    assert (second["mapped"], second["already_mapped"], second["failed"]) == (failed, 7 - failed, 0)
    rows = await mappings(env)
    assert all(s["id"] in rows for s in created)
    assert rows[created[0]["id"]].name == "key-0（导入）"
    assert rows[created[1]["id"]].name == "key-1"
    assert (second["needs_configuration"], second["needs_classification"]) == (3, 4)

    # 两个导入并行：靠唯一约束与行锁，一对一、不重复授权
    more = [await add_secret(env, f"more-{i}", kind=kinds[i % 3]) for i in range(5)]
    other = await env.create_instance(name="实例 B")
    await env.grant(
        other["id"], [{"secret_id": s["id"], "ref_id": f"more_{i}"} for i, s in enumerate(more)]
    )
    (one, code_one), (two, code_two) = await asyncio.gather(
        migrate(env, batch_size=2), migrate(env, batch_size=3)
    )
    assert code_one == code_two == 0, (one, two)
    assert one["mapped"] + two["mapped"] == 5
    assert one["failed"] == two["failed"] == 0
    third, code = await migrate(env)
    assert code == 0 and (third["mapped"], third["already_mapped"]) == (0, 12)

    totals = await run_sql(
        env.database_url,
        "SELECT array[count(*), count(DISTINCT credential_secret_id)]"
        " FROM workspace.model_connections WHERE legacy_source_id IS NOT NULL",
    )
    assert list(totals) == [12, 12]
    grant_totals = await run_sql(
        env.database_url,
        "SELECT array[count(*), count(DISTINCT (connection_id, instance_id))]"
        " FROM workspace.model_connection_grants",
    )
    assert list(grant_totals) == [12, 12]
    imported = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'model_connection.imported'",
    )
    assert imported == 12
    # 新建连接自己的内部凭据不算旧密钥
    assert third["scanned"] == 12


async def test_insert_conflicts_are_resolved_without_errors(env: Env) -> None:
    """插入时撞上并行写入：名字被别的连接刚占用就换下一个导入名；这条旧密钥已被映射就返回 None。"""
    secret = await add_secret(env, "Clash")
    taken = await env.client.post(
        MC,
        json={"name": "Clash", "api_key": "sk-taken", "save_as_draft": True},
        headers=env.headers(write=True),
    )
    assert taken.status_code == 201, taken.text
    legacy = _Legacy(uuid.UUID(secret["id"]), "Clash", "model_api_key", "")
    workspace_id = uuid.UUID(env.workspace_id)
    async with env.app.state.sessionmaker() as db:
        # used 为空：模拟读名字之后别的事务才提交了同名连接
        first = await _insert_mapping(
            db,
            workspace_id,
            legacy,
            set(),
            migration_state="needs_configuration",
            configuration_state="draft",
        )
        assert first is not None
        await db.commit()
        again = await _insert_mapping(
            db,
            workspace_id,
            legacy,
            set(),
            migration_state="needs_configuration",
            configuration_state="draft",
        )
        assert again is None
        await db.rollback()
    rows = await mappings(env)
    assert rows[secret["id"]].id == first and rows[secret["id"]].name == "Clash（导入）"


def test_import_names_stay_within_the_limit() -> None:
    long = "x" * 64
    names = list(name_candidates(long))
    assert names[0] == long
    assert names[1].endswith("（导入）") and len(names[1]) == 64
    assert names[2].endswith("（导入 2）") and len(names[2]) == 64
    assert len(set(names)) == len(names)


# ---------------------------------------------------------------- 只读模式与命令行


async def test_dry_run_and_report_write_nothing(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    instance = await env.create_instance()
    model = await add_secret(env, "OpenAI")
    github = await add_secret(env, "GitHub", kind="api_token")
    await env.grant(
        instance["id"],
        [
            {"secret_id": model["id"], "ref_id": "openai_api_key"},
            {"secret_id": github["id"], "ref_id": "gh_token"},
        ],
    )
    _, code = await migrate(env)
    assert code == 0
    # 导入之后又加的旧密钥：一条正常，一条没有密文
    misc = await add_secret(env, "Misc", kind="generic")
    lost = await add_secret(env, "Lost")
    await env.grant(
        instance["id"],
        [
            {"secret_id": model["id"], "ref_id": "openai_api_key"},
            {"secret_id": github["id"], "ref_id": "gh_token"},
            {"secret_id": misc["id"], "ref_id": "misc"},
        ],
    )
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_secret_versions SET ciphertext_ref = NULL WHERE secret_id = $1",
        uuid.UUID(lost["id"]),
    )
    before = await fingerprints(env.database_url, ALL_TABLES)

    # 数据库层面只读的连接：dry-run / report 只要有一次写入就会报错
    settings = env.app.state.settings
    box = CredentialBox.from_settings(settings)
    engine = create_async_engine(
        env.database_url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            dry = await migrate_workspace(
                db, settings, box, uuid.UUID(env.workspace_id), mode="dry_run", batch_size=1
            )
        async with sessions() as db:
            report = await migrate_workspace(
                db, settings, box, uuid.UUID(env.workspace_id), mode="report", batch_size=3
            )
    finally:
        await engine.dispose()
    assert (dry.scanned, dry.mapped, dry.already_mapped, dry.failed) == (4, 2, 2, 0)
    assert (dry.needs_configuration, dry.needs_classification, dry.unreadable) == (2, 2, 1)
    assert len(dry.batches) == 4 and {b.status for b in dry.batches} == {"read_only"}
    assert (report.scanned, report.mapped, report.already_mapped) == (4, 0, 2)
    reconciliation = report.reconciliation
    assert reconciliation is not None
    assert reconciliation["unmapped"] == 2
    assert reconciliation["by_kind"]["model_api_key"] == {
        "mapped": 1,
        "unmapped": 1,
        "needs_configuration": 1,
    }
    assert reconciliation["legacy_grants"] == reconciliation["legacy_grant_instances"] == 2
    assert reconciliation["connection_grants"] == 2
    assert reconciliation["missing_connection_grants"] == 0
    assert reconciliation["extra_connection_grants"] == 0
    assert reconciliation["bindings"] == 0 and reconciliation["disabled"] == 0

    # 命令行（自己的连接）同样不写
    cli_dry, code = await migrate(env, "dry_run")
    assert code == 0 and cli_dry["mapped"] == 2 and cli_dry["unreadable"] == 1
    cli_report, code = await migrate(env, "report")
    assert code == 0 and cli_report["reconciliation"]["unmapped"] == 2
    assert await fingerprints(env.database_url, ALL_TABLES) == before

    # 入口：必须指定工作空间与恰好一种模式；没有“全部工作空间”
    for argv in (
        ["--dry-run"],
        ["--workspace-id", env.workspace_id],
        ["--workspace-id", env.workspace_id, "--dry-run", "--apply"],
        ["--workspace-id", "not-a-uuid", "--report"],
        ["--workspace-id", env.workspace_id, "--apply", "--batch-size", "0"],
    ):
        with pytest.raises(SystemExit) as exited:
            cli.parser().parse_args(argv)
        assert exited.value.code == 2
    missing, code = await cli.run(uuid.uuid4(), "report", settings=settings)
    assert code == 2 and missing["error"] == "workspace_not_found"
    no_key = settings.model_copy(update={"credential_encryption_key": None})
    unusable, code = await cli.run(uuid.UUID(env.workspace_id), "apply", settings=no_key)
    assert code == 2 and unusable["error"] == "encryption_unavailable"

    # main() 打印一份 JSON
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    capsys.readouterr()
    code = await asyncio.to_thread(cli.main, ["--workspace-id", env.workspace_id, "--dry-run"])
    printed = json.loads(capsys.readouterr().out)
    assert code == 0 and printed["mode"] == "dry_run" and printed["mapped"] == 2
    assert await fingerprints(env.database_url, ALL_TABLES) == before


# ---------------------------------------------------------------- M01 / M02：分类


async def test_api_token_is_not_automatically_openai(env: Env) -> None:
    instance = await env.create_instance()
    model = await add_secret(env, "OpenAI", value="sk-openai")
    stripe = await add_secret(env, "Stripe", kind="api_token", value="rk-stripe")
    misc = await add_secret(env, "Misc", kind="generic", value="misc-value")
    await env.grant(
        instance["id"],
        [
            {"secret_id": model["id"], "ref_id": "openai_api_key"},
            {"secret_id": stripe["id"], "ref_id": "stripe_key"},
            {"secret_id": misc["id"], "ref_id": "misc"},
        ],
    )
    output, code = await migrate(env)
    assert code == 0
    assert (output["needs_configuration"], output["needs_classification"]) == (1, 2)
    rows = await mappings(env)

    expected = {
        model["id"]: "needs_configuration",
        stripe["id"]: "needs_classification",
        misc["id"]: "needs_classification",
    }
    for secret_id, state in expected.items():
        out = await connection(env, rows[secret_id].id)
        assert out["migration_state"] == state
        assert out["configuration_state"] == "draft"
        # 继承 enabled = true 以保留旧授权取用；类型 / 协议 / URL 不猜
        assert out["enabled"] is True and out["credential_configured"] is True
        assert out["connection_type"] is None and out["protocol"] is None
        assert out["base_url"] is None and out["official_provider"] is None
        assert out["model_scope"] == "selected" and out["model_count"] == 0
        assert out["binding_count"] == 0 and out["grant_count"] == 1
        assert out["legacy_source_id"] == secret_id
    # 没有模型、没有绑定
    for table in ("model_connection_models", "agent_model_bindings"):
        assert await run_sql(env.database_url, f"SELECT count(*) FROM workspace.{table}") == 0  # noqa: S608

    # 待归类的出现在“全部连接”与待处理统计里；不会被当成可用模型
    listed = await env.client.get(MC, headers=env.headers())
    assert listed.json()["total"] == 3
    summary = (await env.client.get(f"{MC}/summary", headers=env.headers())).json()
    assert summary["total"] == 3 and summary["needs_attention"] == 3
    assert summary["check_passed"] == 0
    entries = await env.client.get(
        f"{MC}/migration/entries", params={"state": "needs_classification"}, headers=env.headers()
    )
    assert sorted(e["legacy_kind"] for e in entries.json()["items"]) == ["api_token", "generic"]

    # 归为模型之后也只是待完善：缺 URL / 协议 / 模型，不会 ready，不建绑定
    classified = await classify(env, rows[stripe["id"]].id, "model")
    assert classified.status_code == 200, classified.text
    assert classified.json()["connection"]["migration_state"] == "needs_configuration"
    assert classified.json()["connection"]["configuration_state"] == "draft"
    assert (
        await run_sql(env.database_url, "SELECT count(*) FROM workspace.agent_model_bindings") == 0
    )

    # 旧取用照常（导入不切断旧模型）
    token = await env.issue_token(instance["id"])
    assert await values_of(env, token, ["openai_api_key", "stripe_key", "misc"]) == {
        "openai_api_key": "sk-openai",
        "stripe_key": "rk-stripe",
        "misc": "misc-value",
    }


# ---------------------------------------------------------------- M04：解不开的旧 Key


async def test_unreadable_secret_needs_review(env: Env) -> None:
    instance = await env.create_instance()
    lost = await add_secret(env, "Lost")
    tampered = await add_secret(env, "Tampered", kind="api_token")
    fine = await add_secret(env, "Fine", kind="generic")
    await env.grant(
        instance["id"],
        [
            {"secret_id": lost["id"], "ref_id": "lost_key"},
            {"secret_id": tampered["id"], "ref_id": "tampered_key"},
        ],
    )
    # 一条没有密文，一条是用另一把密钥加密的（加密配置不对）
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_secret_versions SET ciphertext_ref = NULL WHERE secret_id = $1",
        uuid.UUID(lost["id"]),
    )
    foreign = CredentialBox(secrets.token_bytes(32)).seal(
        "other", context=agent_secret_context(uuid.UUID(tampered["id"]), 1)
    )
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_secret_versions SET ciphertext_ref = $2 WHERE secret_id = $1",
        uuid.UUID(tampered["id"]),
        foreign,
    )
    before = await fingerprints(env.database_url, LEGACY_TABLES)
    dry, code = await migrate(env, "dry_run")
    assert code == 0 and dry["unreadable"] == 2 and dry["mapped"] == 3

    output, code = await migrate(env)
    # 不可解密不是失败：照样建映射并标 needs_review，原记录保留等待补 Key
    assert code == 0 and output["failed"] == 0 and output["mapped"] == 3
    assert output["unreadable"] == 2
    assert await fingerprints(env.database_url, LEGACY_TABLES) == before
    rows = await mappings(env)
    lost_out = await connection(env, rows[lost["id"]].id)
    assert lost_out["configuration_state"] == "needs_review"
    assert lost_out["credential_configured"] is False
    assert lost_out["migration_state"] == "needs_configuration"
    tampered_out = await connection(env, rows[tampered["id"]].id)
    assert tampered_out["configuration_state"] == "needs_review"
    assert tampered_out["migration_state"] == "needs_classification"
    fine_out = await connection(env, rows[fine["id"]].id)
    assert fine_out["configuration_state"] == "draft" and fine_out["credential_configured"] is True
    report, _ = await migrate(env, "report")
    assert report["reconciliation"]["needs_review"] == 2 and report["unreadable"] == 2

    # 归类为非模型凭据不改变 needs_review；补 Key（兼容凭据在旧接口里轮换）之后才恢复
    classified = await classify(env, rows[tampered["id"]].id, "non_model")
    assert classified.status_code == 200, classified.text
    assert classified.json()["connection"]["configuration_state"] == "needs_review"
    token = await env.issue_token(instance["id"])
    assert (await resolve(env.client, token, ["lost_key", "tampered_key"])).json()["values"] == {}
    current = await legacy_secret(env, tampered["id"])
    rotated = await env.client.post(
        f"{SECRETS}/{tampered['id']}/rotate",
        json={"version": current["version"], "value": "fixed-token"},
        headers=env.headers(write=True),
    )
    assert rotated.status_code == 200, rotated.text
    fixed = await connection(env, rows[tampered["id"]].id)
    assert fixed["configuration_state"] == "draft" and fixed["credential_configured"] is True
    # 托管的模型 Key 在中转站管理里补
    refilled = await env.client.post(
        f"{MC}/{rows[lost['id']].id}/rotate-key",
        json={"version": lost_out["version"], "api_key": "sk-refilled"},
        headers=env.headers(write=True),
    )
    assert refilled.status_code == 200, refilled.text
    assert refilled.json()["configuration_state"] == "draft"
    assert await values_of(env, token, ["lost_key", "tampered_key"]) == {
        "lost_key": "sk-refilled",
        "tampered_key": "fixed-token",
    }


# ---------------------------------------------------------------- M13：停用 / 撤权后转兼容凭据


async def test_classify_non_model_cannot_restore_revoked_access(env: Env) -> None:
    secret = await add_secret(env, "Shared", value="sk-shared")
    a = await env.create_instance(name="实例 A")
    b = await env.create_instance(name="实例 B")
    for instance in (a, b):
        await env.grant(instance["id"], [{"secret_id": secret["id"], "ref_id": "openai_api_key"}])
    assert (await migrate(env))[1] == 0
    cid = (await mappings(env))[secret["id"]].id
    token_a = await env.issue_token(a["id"])
    token_b = await env.issue_token(b["id"])
    ref = ["openai_api_key"]

    # 停用会影响已授权的实例：先给影响摘要，确认后才生效
    unconfirmed = await patch_connection(env, cid, enabled=False)
    assert unconfirmed.status_code == 409 and error_code(unconfirmed) == "CONNECTION_IN_USE"
    await set_enabled(env, cid, False)
    assert (await resolve(env.client, token_a, ref)).json() == {
        "values": {},
        "errors": {"openai_api_key": {"code": "NOT_FOUND"}},
    }
    assert await runner_values(env, a["id"], ref) == {}
    # 只用来给输出脱敏时仍认得出这个值（停用前可能已注入容器）
    assert await runner_values(env, a["id"], ref, for_redaction=True) == {
        "openai_api_key": "sk-shared"
    }
    refused = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.agent_secret_accesses"
        " WHERE result = 'not_found' AND secret_id IS NULL AND instance_id = $1",
        uuid.UUID(a["id"]),
    )
    assert refused == 1

    # 撤销 B 的连接授权：同一事务撤掉 B 对这条密钥的旧授权
    revoked = await put_connection_grants(env, cid, [a["id"]])
    assert revoked.status_code == 200, revoked.text
    assert await legacy_grant_count(env, secret["id"], b["id"]) == 0

    # 归类为非模型凭据：映射还在、仍停用、撤销的授权不恢复
    classified = await classify(env, cid, "non_model")
    assert classified.status_code == 200, classified.text
    out = classified.json()["connection"]
    assert out["id"] == str(cid) and out["migration_state"] == "non_model"
    assert out["enabled"] is False
    assert (await mappings(env))[secret["id"]].id == cid
    for token in (token_a, token_b):
        assert (await resolve(env.client, token, ref)).json()["values"] == {}
    assert await runner_values(env, a["id"], ref) == {}
    assert await runner_values(env, b["id"], ref) == {}
    assert await legacy_grant_count(env, secret["id"], b["id"]) == 0
    assert await connection_grants(env, cid) == {a["id"]}

    # 只有 owner 显式重新启用才恢复；撤销的授权仍要单独重新授权
    await set_enabled(env, cid, True)
    assert await values_of(env, token_a, ref) == {"openai_api_key": "sk-shared"}
    assert await runner_values(env, a["id"], ref) == {"openai_api_key": "sk-shared"}
    assert (await resolve(env.client, token_b, ref)).json()["values"] == {}
    assert await runner_values(env, b["id"], ref) == {}


# ---------------------------------------------------------------- M12：model ↔ non_model


async def test_classification_roundtrip_preserves_identity_and_governance(env: Env) -> None:
    secret = await add_secret(env, "Token", kind="api_token", value="tok-1")
    instance = await env.create_instance()
    await env.grant(instance["id"], [{"secret_id": secret["id"], "ref_id": "svc_token"}])
    assert (await migrate(env))[1] == 0
    cid = (await mappings(env))[secret["id"]].id
    grant_id = await run_sql(
        env.database_url,
        "SELECT id FROM workspace.agent_secret_grants WHERE secret_id = $1",
        uuid.UUID(secret["id"]),
    )
    legacy_version = (await legacy_secret(env, secret["id"]))["version"]
    start = await connection(env, cid)

    stale = await classify(env, cid, "model", version=start["version"] + 5)
    assert stale.status_code == 409 and error_code(stale) == "VERSION_CONFLICT"
    for step, (purpose, state) in enumerate(
        (
            ("model", "needs_configuration"),
            ("non_model", "non_model"),
            ("model", "needs_configuration"),
        ),
        start=1,
    ):
        response = await classify(env, cid, purpose)
        assert response.status_code == 200, response.text
        entry = response.json()
        assert entry["entry_id"] == str(cid) and entry["connection"]["id"] == str(cid)
        assert entry["connection"]["migration_state"] == state
        assert entry["connection"]["version"] == start["version"] + step
        assert entry["legacy_grant_refs"] == ["svc_token"] and entry["legacy_kind"] == "api_token"
        if purpose == "non_model":
            # 非模型凭据不在主连接列表与统计里，在“迁移与兼容凭据”里
            listed = await env.client.get(MC, headers=env.headers())
            assert listed.json()["total"] == 0
            summary = (await env.client.get(f"{MC}/summary", headers=env.headers())).json()
            assert summary["total"] == 0 and summary["non_model"] == 1
        # 同一条密钥、同一个映射、同一条旧授权；旧密钥接口的版本一起变（冲突能被察觉）
        rows = await mappings(env)
        assert len(rows) == 1 and rows[secret["id"]].id == cid
        assert await connection_grants(env, cid) == {instance["id"]}
        assert (
            await run_sql(
                env.database_url,
                "SELECT id FROM workspace.agent_secret_grants WHERE secret_id = $1",
                uuid.UUID(secret["id"]),
            )
            == grant_id
        )
        assert (await legacy_secret(env, secret["id"]))["version"] == legacy_version + step
    assert await run_sql(env.database_url, "SELECT count(*) FROM workspace.agent_secrets") == 1

    # 已有原生主模型绑定：不能改判非模型
    await add_binding(env, instance["id"], cid)
    blocked = await classify(env, cid, "non_model")
    assert blocked.status_code == 409 and error_code(blocked) == "MODEL_BINDING_CONFLICT"
    assert (await connection(env, cid))["migration_state"] == "needs_configuration"
    # 待完善的托管项在旧接口里只读，不能绕过连接改 Key
    current = await legacy_secret(env, secret["id"])
    bypass = await env.client.post(
        f"{SECRETS}/{secret['id']}/rotate",
        json={"version": current["version"], "value": "tok-2"},
        headers=env.headers(write=True),
    )
    assert bypass.status_code == 409 and error_code(bypass) == "MANAGED_BY_MODEL_CONNECTION"

    # 解除绑定后归为非模型；停用后旧接口的写入要过版本检查，且不会恢复取用
    await run_sql(env.database_url, "DELETE FROM workspace.agent_model_bindings")
    assert (await classify(env, cid, "non_model")).status_code == 200
    await set_enabled(env, cid, False)
    current = await legacy_secret(env, secret["id"])
    stale_rotate = await env.client.post(
        f"{SECRETS}/{secret['id']}/rotate",
        json={"version": current["version"] + 1, "value": "tok-2"},
        headers=env.headers(write=True),
    )
    assert stale_rotate.status_code == 409 and error_code(stale_rotate) == "VERSION_CONFLICT"
    rotated = await env.client.post(
        f"{SECRETS}/{secret['id']}/rotate",
        json={"version": current["version"], "value": "tok-2"},
        headers=env.headers(write=True),
    )
    assert rotated.status_code == 200, rotated.text
    token = await env.issue_token(instance["id"])
    assert (await resolve(env.client, token, ["svc_token"])).json()["values"] == {}
    assert (await connection(env, cid))["enabled"] is False


# ---------------------------------------------------------------- M06 / M09：旧接口与 Runner


async def test_legacy_and_runner_follow_managed_policy(env: Env) -> None:
    model = await add_secret(env, "OpenAI", value="sk-model")
    github = await add_secret(env, "GitHub", kind="api_token", value="ghp-value")
    a = await env.create_instance(name="实例 A")
    b = await env.create_instance(name="实例 B")
    c = await env.create_instance(name="实例 C")
    await env.grant(
        a["id"],
        [
            {"secret_id": model["id"], "ref_id": "openai_api_key"},
            {"secret_id": github["id"], "ref_id": "gh_token"},
        ],
    )
    await env.grant(b["id"], [{"secret_id": model["id"], "ref_id": "openai_api_key"}])
    assert (await migrate(env))[1] == 0
    rows = await mappings(env)
    model_cid, github_cid = rows[model["id"]].id, rows[github["id"]].id
    assert (await classify(env, github_cid, "non_model")).status_code == 200
    token_a, token_b, token_c = [await env.issue_token(i["id"]) for i in (a, b, c)]
    both = ["openai_api_key", "gh_token"]
    everything = {"openai_api_key": "sk-model", "gh_token": "ghp-value"}

    # 导入之后：原来允许的继续允许，原来没授权的继续拒绝（权限没有扩大）
    assert await values_of(env, token_a, both) == everything
    assert await runner_values(env, a["id"], both) == everything
    assert await values_of(env, token_b, ["openai_api_key"]) == {"openai_api_key": "sk-model"}
    assert await values_of(env, token_c, both) == {}
    assert await runner_values(env, c["id"], both) == {}

    # 受管模型连接停用：旧接口与 Runner 都不能绕过；非模型凭据不受影响
    await set_enabled(env, model_cid, False)
    assert await values_of(env, token_a, both) == {"gh_token": "ghp-value"}
    assert await runner_values(env, a["id"], both) == {"gh_token": "ghp-value"}
    assert await values_of(env, token_b, ["openai_api_key"]) == {}
    # 非模型凭据单独停用同样拒绝
    await set_enabled(env, github_cid, False)
    assert await values_of(env, token_a, both) == {}
    assert await runner_values(env, a["id"], both) == {}
    await set_enabled(env, model_cid, True)
    await set_enabled(env, github_cid, True)
    assert await values_of(env, token_a, both) == everything

    # 撤销 A 的连接授权：A 的旧授权一起撤掉；共用这把 Key 的 B 不受影响（M09）
    revoked = await put_connection_grants(env, model_cid, [b["id"]])
    assert revoked.status_code == 200, revoked.text
    assert await values_of(env, token_a, both) == {"gh_token": "ghp-value"}
    assert await runner_values(env, a["id"], both) == {"gh_token": "ghp-value"}
    assert await values_of(env, token_b, ["openai_api_key"]) == {"openai_api_key": "sk-model"}
    assert await legacy_grant_count(env, model["id"], b["id"]) == 1


# ---------------------------------------------------------------- 旧密钥接口的写入


async def test_legacy_secret_writes_are_governed(env: Env) -> None:
    model = await add_secret(env, "OpenAI", value="sk-model")
    stripe = await add_secret(env, "Stripe", kind="api_token", value="rk-1")
    a = await env.create_instance(name="实例 A")
    b = await env.create_instance(name="实例 B")
    await env.grant(
        a["id"],
        [
            {"secret_id": model["id"], "ref_id": "openai_api_key"},
            {"secret_id": stripe["id"], "ref_id": "stripe_key"},
        ],
    )
    assert (await migrate(env))[1] == 0
    rows = await mappings(env)
    stripe_cid = rows[stripe["id"]].id
    native = await env.client.post(
        MC,
        json={"name": "原生连接", "api_key": "sk-native", "save_as_draft": True},
        headers=env.headers(write=True),
    )
    assert native.status_code == 201, native.text
    native_secret = str(
        await run_sql(
            env.database_url,
            "SELECT credential_secret_id FROM workspace.model_connections WHERE id = $1",
            uuid.UUID(native.json()["id"]),
        )
    )

    # 新建连接自己的内部凭据不在旧密钥库里
    listed = (await env.client.get(SECRETS, headers=env.headers())).json()
    assert [s["name"] for s in listed["items"]] == ["OpenAI", "Stripe"] and listed["total"] == 2
    hidden = await env.client.get(f"{SECRETS}/{native_secret}", headers=env.headers())
    assert hidden.status_code == 404

    # 托管项（待完善 / 新建）：改名、轮换、删除、新增授权都 409
    for secret_id in (model["id"], native_secret):
        version = int(
            await run_sql(
                env.database_url,
                "SELECT version FROM workspace.agent_secrets WHERE id = $1",
                uuid.UUID(secret_id),
            )
        )
        for response in (
            await env.client.patch(
                f"{SECRETS}/{secret_id}",
                json={"version": version, "name": "renamed"},
                headers=env.headers(write=True),
            ),
            await env.client.post(
                f"{SECRETS}/{secret_id}/rotate",
                json={"version": version, "value": "sk-bypass"},
                headers=env.headers(write=True),
            ),
            await env.client.delete(
                f"{SECRETS}/{secret_id}",
                params={"version": version},
                headers=env.headers(write=True),
            ),
            await replace_grants(env, b["id"], [{"secret_id": secret_id, "ref_id": "sneaky"}]),
        ):
            assert response.status_code == 409, response.text
            assert error_code(response) == "MANAGED_BY_MODEL_CONNECTION"
    assert await legacy_grant_count(env, model["id"], b["id"]) == 0
    # 已有的托管授权原样保留时可以改别的引用；把它换个 ref_id 也算新增
    kept = await replace_grants(
        env,
        a["id"],
        [
            {"secret_id": model["id"], "ref_id": "openai_api_key"},
            {"secret_id": stripe["id"], "ref_id": "payments"},
        ],
    )
    assert kept.status_code == 200, kept.text
    renamed_ref = await replace_grants(
        env,
        a["id"],
        [
            {"secret_id": model["id"], "ref_id": "llm_key"},
            {"secret_id": stripe["id"], "ref_id": "payments"},
        ],
    )
    assert renamed_ref.status_code == 409
    assert renamed_ref.json()["error"]["details"]["ref_ids"] == ["llm_key"]
    token_a = await env.issue_token(a["id"])
    assert await values_of(env, token_a, ["openai_api_key", "payments"]) == {
        "openai_api_key": "sk-model",
        "payments": "rk-1",
    }

    # 兼容凭据（待归类）：旧接口照样能改，但要过版本检查，连接版本一起变
    start = await connection(env, stripe_cid)
    current = await legacy_secret(env, stripe["id"])
    assert current["model_connection_id"] == str(stripe_cid)
    assert current["migration_state"] == "needs_classification"
    stale = await env.client.patch(
        f"{SECRETS}/{stripe['id']}",
        json={"version": current["version"] + 1, "name": "Stripe live"},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409 and error_code(stale) == "VERSION_CONFLICT"
    renamed = await env.client.patch(
        f"{SECRETS}/{stripe['id']}",
        json={"version": current["version"], "name": "Stripe live"},
        headers=env.headers(write=True),
    )
    assert renamed.status_code == 200, renamed.text
    assert (await connection(env, stripe_cid))["version"] == start["version"] + 1
    rotated = await env.client.post(
        f"{SECRETS}/{stripe['id']}/rotate",
        json={"version": renamed.json()["version"], "value": "rk-2"},
        headers=env.headers(write=True),
    )
    assert rotated.status_code == 200 and rotated.json()["value_version"] == 2
    after = await connection(env, stripe_cid)
    assert after["version"] == start["version"] + 2
    assert after["config_revision"] == start["config_revision"] + 1
    assert after["credential_version"] == 2 and after["last_check"] is None
    assert await values_of(env, token_a, ["payments"]) == {"payments": "rk-2"}

    # 旧授权增删同步连接授权（只作追溯）
    added = await replace_grants(
        env, b["id"], [{"secret_id": stripe["id"], "ref_id": "stripe_key"}]
    )
    assert added.status_code == 200, added.text
    assert await connection_grants(env, stripe_cid) == {a["id"], b["id"]}
    synced = await connection(env, stripe_cid)
    assert synced["version"] == after["version"] + 1
    assert (await replace_grants(env, b["id"], [])).status_code == 200
    assert await connection_grants(env, stripe_cid) == {a["id"]}

    # 停用之后旧接口轮换照样要过版本检查，且不会恢复取用
    await set_enabled(env, stripe_cid, False)
    current = await legacy_secret(env, stripe["id"])
    again = await env.client.post(
        f"{SECRETS}/{stripe['id']}/rotate",
        json={"version": current["version"], "value": "rk-3"},
        headers=env.headers(write=True),
    )
    assert again.status_code == 200, again.text
    assert await values_of(env, token_a, ["payments"]) == {}
    assert await runner_values(env, a["id"], ["payments"]) == {}

    # 删除：中转站管理里单独授权的实例要先撤销；引用清零后连同映射一起删
    extra = await put_connection_grants(env, stripe_cid, [a["id"], b["id"]])
    assert extra.status_code == 200, extra.text
    current = await legacy_secret(env, stripe["id"])
    refused = await env.client.delete(
        f"{SECRETS}/{stripe['id']}",
        params={"version": current["version"]},
        headers=env.headers(write=True),
    )
    assert refused.status_code == 409 and error_code(refused) == "CONNECTION_IN_USE"
    assert refused.json()["error"]["details"]["instance_ids"] == [b["id"]]
    assert (await put_connection_grants(env, stripe_cid, [a["id"]])).status_code == 200
    accesses = await run_sql(
        env.database_url, "SELECT count(*) FROM workspace.agent_secret_accesses"
    )
    deleted = await env.client.delete(
        f"{SECRETS}/{stripe['id']}",
        params={"version": current["version"]},
        headers=env.headers(write=True),
    )
    assert deleted.status_code == 204, deleted.text
    assert stripe["id"] not in await mappings(env)
    gone = await env.client.get(f"{MC}/{stripe_cid}", headers=env.headers())
    assert gone.status_code == 404
    # 取用记录保留（密钥 ID 置空，与以前删除密钥一样）
    assert (
        await run_sql(env.database_url, "SELECT count(*) FROM workspace.agent_secret_accesses")
        == accesses
    )

    # 有原生绑定的兼容凭据不能删
    misc = await add_secret(env, "Misc", kind="generic")
    assert (await migrate(env))[1] == 0
    misc_cid = (await mappings(env))[misc["id"]].id
    await add_binding(env, b["id"], misc_cid)
    current = await legacy_secret(env, misc["id"])
    bound = await env.client.delete(
        f"{SECRETS}/{misc['id']}",
        params={"version": current["version"]},
        headers=env.headers(write=True),
    )
    assert bound.status_code == 409 and error_code(bound) == "CONNECTION_IN_USE"
    assert bound.json()["error"]["details"]["bound_instances"] == 1


# ---------------------------------------------------------------- M07：加密主密钥轮换


async def test_master_key_rotation_keeps_imported_credentials_readable(env: Env) -> None:
    instance = await env.create_instance()
    model = await add_secret(env, "OpenAI", value="sk-before-rotation")
    github = await add_secret(env, "GitHub", kind="api_token", value="ghp-before-rotation")
    await env.grant(
        instance["id"],
        [
            {"secret_id": model["id"], "ref_id": "openai_api_key"},
            {"secret_id": github["id"], "ref_id": "gh_token"},
        ],
    )
    assert (await migrate(env))[1] == 0
    before = await mappings(env)

    settings = env.app.state.settings
    assert settings.credential_encryption_key is not None
    old_key = parse_key(settings.credential_encryption_key.get_secret_value())
    new_key = secrets.token_bytes(32)
    async with env.app.state.sessionmaker() as db:
        report = await rotate(db, CredentialBox(new_key, [old_key]))
    assert report.failed == 0 and report.remaining_old == 0
    assert report.fields["agent_secret_versions.ciphertext_ref"].rotated == 2

    # 删掉旧密钥之后：原 context 仍能解密，连接、旧取用路径、Runner 都能读
    new_settings = settings.model_copy(
        update={
            "credential_encryption_key": SecretStr(new_key.hex()),
            "credential_encryption_keys_previous": None,
        }
    )
    new_box = CredentialBox(new_key)
    for secret, value in ((model, "sk-before-rotation"), (github, "ghp-before-rotation")):
        ref = await run_sql(
            env.database_url,
            "SELECT ciphertext_ref FROM workspace.agent_secret_versions WHERE secret_id = $1",
            uuid.UUID(secret["id"]),
        )
        assert new_box.key_id_in(ref) == new_box.key_id
        plain = new_box.resolve(ref, context=agent_secret_context(uuid.UUID(secret["id"]), 1))
        assert plain.get_secret_value() == value
    environment = RunnerEnvironment(
        owner_instance_id=uuid.UUID(instance["id"]),
        spec={"secrets": ["openai_api_key", "gh_token"]},
    )
    async with env.app.state.sessionmaker() as db:
        service = ModelConnectionService(db, new_settings)
        for mapped in before.values():
            current = await db.get(ModelConnection, mapped.id)
            assert current is not None and await service.credential_readable(current)
        assert await resolve_env_secrets(db, new_settings, environment, record=False) == {
            "openai_api_key": "sk-before-rotation",
            "gh_token": "ghp-before-rotation",
        }
    # 重新加密对映射不可见；用新密钥重跑导入仍是一对一、都可读
    after = await mappings(env)
    assert {k: (v.id, v.version, v.config_revision) for k, v in after.items()} == {
        k: (v.id, v.version, v.config_revision) for k, v in before.items()
    }
    output, code = await cli.run(uuid.UUID(env.workspace_id), "dry_run", settings=new_settings)
    assert code == 0 and output["unreadable"] == 0 and output["already_mapped"] == 2


# ---------------------------------------------------------------- M11：导入草稿首次补 URL / 协议


async def test_imported_draft_with_legacy_grants_can_complete_endpoint(env: Env) -> None:
    instance = await env.create_instance()
    secret = await add_secret(env, "Relay", value="sk-relay")
    await env.grant(instance["id"], [{"secret_id": secret["id"], "ref_id": "openai_api_key"}])
    assert (await migrate(env))[1] == 0
    cid = (await mappings(env))[secret["id"]].id
    token = await env.issue_token(instance["id"])
    grants_before = await fingerprints(env.database_url, ("agent_secret_grants",))
    start = await connection(env, cid)
    assert start["grant_count"] == 1

    # 第一次从空补齐：保留旧授权也允许，只存元数据，不改旧运行时
    completed = await patch_connection(
        env,
        cid,
        connection_type="relay",
        base_url="https://relay.example.com/v1/",
        protocol="openai_compatible",
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["base_url"] == "https://relay.example.com/v1"
    assert body["protocol"] == "openai_compatible" and body["connection_type"] == "relay"
    assert body["config_revision"] == start["config_revision"] + 1
    assert body["configuration_state"] == "draft" and body["grant_count"] == 1
    assert await fingerprints(env.database_url, ("agent_secret_grants",)) == grants_before
    assert await values_of(env, token, ["openai_api_key"]) == {"openai_api_key": "sk-relay"}

    # 已配置完整且有授权：之后再改地址就是换目标，409
    moved = await patch_connection(env, cid, base_url="https://other.example.com/v1")
    assert moved.status_code == 409 and error_code(moved) == "CONNECTION_ENDPOINT_IN_USE"
    assert (await connection(env, cid))["base_url"] == "https://relay.example.com/v1"

    # 补上模型后 ready → managed；旧 alias 仍照常，旧接口对它只读
    version = (await connection(env, cid))["version"]
    models = await env.client.put(
        f"{MC}/{cid}/models",
        json={"version": version, "model_scope": "selected", "models": [{"model_id": "gpt-4.1"}]},
        headers=env.headers(write=True),
    )
    assert models.status_code == 200, models.text
    assert models.json()["configuration_state"] == "ready"
    assert models.json()["migration_state"] == "managed"
    assert await values_of(env, token, ["openai_api_key"]) == {"openai_api_key": "sk-relay"}
    current = await legacy_secret(env, secret["id"])
    assert current["migration_state"] == "managed"
    blocked = await env.client.patch(
        f"{SECRETS}/{secret['id']}",
        json={"version": current["version"], "name": "renamed"},
        headers=env.headers(write=True),
    )
    assert blocked.status_code == 409 and error_code(blocked) == "MANAGED_BY_MODEL_CONNECTION"


async def test_owner_runs_the_import_from_the_page(env: Env) -> None:
    """页面上的“导入旧密钥”（POST migration/run）：只处理当前工作空间，预览不写，
    导入可重复，只回计数。"""
    model = await add_secret(env, "old-openai", value="sk-page-import-" + secrets.token_hex(6))
    await add_secret(env, "old-token", kind="api_token")

    async def run_mode(mode: str) -> dict[str, Any]:
        response = await env.client.post(
            f"{MC}/migration/run", json={"mode": mode}, headers=env.headers(write=True)
        )
        assert response.status_code == 200, response.text
        assert "sk-page-import" not in response.text and "old-openai" not in response.text
        data: dict[str, Any] = response.json()
        return data

    preview = await run_mode("dry_run")
    assert (preview["scanned"], preview["mapped"], preview["already_mapped"]) == (2, 2, 0)
    assert (preview["needs_configuration"], preview["needs_classification"]) == (1, 1)
    assert await mappings(env) == {}
    applied = await run_mode("apply")
    assert (applied["mapped"], applied["failed"]) == (2, 0)
    assert {b["status"] for b in applied["batches"]} == {"committed"}
    again = await run_mode("apply")
    assert (again["mapped"], again["already_mapped"]) == (0, 2)
    report = await run_mode("report")
    assert report["reconciliation"] is not None and report["reconciliation"]["unmapped"] == 0
    assert str(model["id"]) in {str(k) for k in (await mappings(env))}
    # 页面上的导入项计数随之更新
    entries = await env.client.get(f"{MC}/migration/entries", headers=env.headers())
    assert entries.status_code == 200 and entries.json()["total"] == 2
    # 只有 owner；没有 CSRF 令牌的请求被拒绝
    no_csrf = await env.client.post(
        f"{MC}/migration/run", json={"mode": "apply"}, headers=env.headers()
    )
    assert no_csrf.status_code == 403
    invalid = await env.client.post(
        f"{MC}/migration/run", json={"mode": "all"}, headers=env.headers(write=True)
    )
    assert invalid.status_code == 422
