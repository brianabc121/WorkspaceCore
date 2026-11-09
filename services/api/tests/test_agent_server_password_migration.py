"""迁移 0054 与旧服务器的过渡（T-0178）。

- 加法迁移：旧服务器没有密码（不生成假密码），指纹、prepared_at、部署、实例 ID 都保留；
  有旧专用公钥 / 私钥的回填 legacy_key_cleanup_state = pending；runtime_installed 旧记录 null
  （不凭 installed_at 推断）；downgrade 只删新列，再 upgrade 重新回填
- 旧服务器经接口：显示要补录密码、不可部署，测试连接 / 初始化明确拒绝；补录后保留初始化记录与部署
- 加密密钥轮换：密码重新加密后可以解密，credential_revision 与 version 不变；
  密文挪到另一台服务器的记录上解不开
- 密码解密的规则（缺失、解不开、版本变化、env 引用）与读接口用的“是否可读”判断
"""

import asyncio
import secrets
import uuid
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import asyncssh
import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.agent_server_credentials import (
    CREDENTIAL_CHANGED,
    CREDENTIAL_REQUIRED,
    CREDENTIAL_UNREADABLE,
    ServerCredentialError,
    agent_server_password_context,
    password_readable,
    resolve_server_password,
    seal_server_password,
)
from app.domain.credential_rotation import rotate
from app.domain.credentials import CredentialBox
from app.models import AgentServer, AgentSshTarget
from tests import test_agent_ssh_install as base
from tests.helpers import add_user, add_workspace, alembic_config, asyncpg_dsn, run_sql
from tests.test_agent_ssh_install import Owner

owner = base.owner


async def _exec(url: str, sql: str, *args: Any) -> Any:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def run(url: str, sql: str, *args: Any) -> Any:
    return asyncio.run(_exec(url, sql, *args))


def _host_key() -> tuple[str, str]:
    key = asyncssh.generate_private_key("ssh-ed25519")
    return " ".join(key.export_public_key().decode().split()[:2]), key.get_fingerprint("sha256")


def _seed(url: str) -> dict[str, UUID]:
    """0053 时的数据：装过旧专用密钥、初始化过、部署了一个实例的服务器；新添加的；自动识别的。"""
    user, workspace = uuid4(), uuid4()
    run(
        url,
        "INSERT INTO workspace.users(id,email,password_hash,display_name) "
        "VALUES($1,'m@example.test','synthetic','M')",
        user,
    )
    run(
        url, "INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'W',$2)", workspace, user
    )
    ids = {"user": user, "workspace": workspace}
    for name, kind in (("legacy", "ssh"), ("fresh", "ssh"), ("auto", "auto")):
        ids[name] = uuid4()
        run(
            url,
            "INSERT INTO workspace.agent_servers(id,workspace_id,name,kind) VALUES($1,$2,$3,$4)",
            ids[name],
            workspace,
            name,
            kind,
        )
    for name, legacy in (("legacy", True), ("fresh", False)):
        public, fingerprint = _host_key()
        ids[f"{name}_target"] = uuid4()
        run(
            url,
            "INSERT INTO workspace.agent_ssh_targets(id,workspace_id,server_id,host,address,port,"
            "username,host_key_algorithm,host_key,host_key_fingerprint,host_key_confirmed_at,"
            "client_key_ref,client_public_key,prepared_at) "
            "VALUES($1,$2,$3,'203.0.113.9','203.0.113.9',22,'deploy','ssh-ed25519',$4,$5,"
            "now(),$6,$7,CASE WHEN $8 THEN now() END)",
            ids[f"{name}_target"],
            workspace,
            ids[name],
            public,
            fingerprint,
            "enc:v1:00000000:old-private-key" if legacy else None,
            _host_key()[0] if legacy else None,
            legacy,
        )
    ids["instance"] = uuid4()
    run(
        url,
        "INSERT INTO workspace.agent_instances(id,workspace_id,name,network_mode,im_user_id,"
        "im_nickname,server_id,server_manual) VALUES($1,$2,'甲','public',$3,'甲',$4,true)",
        ids["instance"],
        workspace,
        "oc_" + ids["instance"].hex,
        ids["legacy"],
    )
    run(
        url,
        "INSERT INTO workspace.agent_deployments(workspace_id,instance_id,server_id,install_dir,"
        "installed_at) VALUES($1,$2,$3,'/home/deploy/myworkspace-openclaw/abc',now())",
        workspace,
        ids["instance"],
        ids["legacy"],
    )
    return ids


def _targets(url: str) -> dict[UUID, dict[str, Any]]:
    return {
        row["server_id"]: dict(row)
        for row in run(url, "SELECT * FROM workspace.agent_ssh_targets ORDER BY server_id")
    }


def _columns(url: str, table: str) -> set[str]:
    return {
        row["column_name"]
        for row in run(
            url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'workspace' AND table_name = $1",
            table,
        )
    }


NEW_TARGET_COLUMNS = {
    "login_password_ref",
    "credential_revision",
    "last_connection_at",
    "last_connection_reason",
    "legacy_key_cleanup_state",
}


@pytest.mark.integration
def test_upgrade_keeps_legacy_servers_and_downgrade_restores(temp_database_url: str) -> None:
    url = temp_database_url
    config = alembic_config(url)
    command.upgrade(config, "0053")
    ids = _seed(url)
    before = _targets(url)
    deployment_before = dict(run(url, "SELECT * FROM workspace.agent_deployments")[0])

    command.upgrade(config, "0054")
    after = _targets(url)
    legacy, fresh = after[ids["legacy"]], after[ids["fresh"]]
    # 旧服务器：没有密码（不生成假密码），等补录；旧公钥待退役
    assert legacy["login_password_ref"] is None and fresh["login_password_ref"] is None
    assert legacy["credential_revision"] == 0 and fresh["credential_revision"] == 0
    assert legacy["last_connection_at"] is None and legacy["last_connection_reason"] is None
    assert legacy["legacy_key_cleanup_state"] == "pending"
    assert fresh["legacy_key_cleanup_state"] == "not_needed"
    # 指纹、确认、prepared_at、旧私钥 / 公钥原样保留（迁移里不清空）
    for server_id, row in after.items():
        for column, value in before[server_id].items():
            assert row[column] == value, column
    deployment = dict(run(url, "SELECT * FROM workspace.agent_deployments")[0])
    assert deployment["runtime_installed"] is None, "不凭 installed_at 推断现在还装着"
    for column, value in deployment_before.items():
        assert deployment[column] == value, column
    instance = run(url, "SELECT id, server_id FROM workspace.agent_instances")[0]
    assert instance["id"] == ids["instance"] and instance["server_id"] == ids["legacy"]

    # 约束：白名单与非负
    for sql in (
        "UPDATE workspace.agent_ssh_targets SET legacy_key_cleanup_state = 'gone'",
        "UPDATE workspace.agent_ssh_targets SET last_connection_reason = 'whatever'",
        "UPDATE workspace.agent_ssh_targets SET credential_revision = -1",
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            run(url, sql)
    run(
        url,
        "UPDATE workspace.agent_ssh_targets SET last_connection_reason = 'auth_failed',"
        " legacy_key_cleanup_state = 'manual_required' WHERE server_id = $1",
        ids["legacy"],
    )

    command.downgrade(config, "0053")
    assert not (_columns(url, "agent_ssh_targets") & NEW_TARGET_COLUMNS)
    assert "runtime_installed" not in _columns(url, "agent_deployments")
    downgraded = _targets(url)
    for server_id, row in downgraded.items():
        assert row == before[server_id]

    command.upgrade(config, "0054")
    again = _targets(url)
    assert again[ids["legacy"]]["legacy_key_cleanup_state"] == "pending"
    assert again[ids["fresh"]]["legacy_key_cleanup_state"] == "not_needed"
    assert _columns(url, "agent_ssh_targets") >= NEW_TARGET_COLUMNS


# ---------------------------------------------------------------- 旧服务器经接口


@pytest.mark.integration
async def test_legacy_server_requires_password_and_keeps_its_instances(owner: Owner) -> None:
    url = owner.database_url
    workspace = UUID(owner.workspace)
    server_id, target_id = uuid4(), uuid4()
    public, fingerprint = _host_key()
    await run_sql(
        url,
        "INSERT INTO workspace.agent_servers(id,workspace_id,name,kind) VALUES($1,$2,'旧','ssh')",
        server_id,
        workspace,
    )
    await run_sql(
        url,
        "INSERT INTO workspace.agent_ssh_targets(id,workspace_id,server_id,host,address,port,"
        "username,host_key_algorithm,host_key,host_key_fingerprint,host_key_confirmed_at,"
        "client_key_ref,client_public_key,prepared_at,legacy_key_cleanup_state) "
        "VALUES($1,$2,$3,'203.0.113.9','203.0.113.9',22,'deploy','ssh-ed25519',$4,$5,now(),"
        "'enc:v1:00000000:old-private-key',$6,now(),'pending')",
        target_id,
        workspace,
        server_id,
        public,
        fingerprint,
        _host_key()[0],
    )
    instance_id = await owner.instance()
    await run_sql(
        url,
        "UPDATE workspace.agent_instances SET server_id = $1, server_manual = true WHERE id = $2",
        server_id,
        UUID(instance_id),
    )
    await run_sql(
        url,
        "INSERT INTO workspace.agent_deployments(workspace_id,instance_id,server_id,install_dir,"
        "installed_at) VALUES($1,$2,$3,'/srv/x',now())",
        workspace,
        UUID(instance_id),
        server_id,
    )
    headers = owner.headers()
    listed = (await owner.client.get("/api/v1/agents/servers", headers=headers)).json()
    out = listed["items"][0]
    ssh_info = out["ssh"]
    assert ssh_info["password_set"] is False and ssh_info["credential_revision"] == 0
    assert ssh_info["connection_status"] == "credentials_required"
    assert ssh_info["legacy_key_cleanup_state"] == "pending"
    # 旧方案的专用公钥：只作为迁移兼容信息，不能让它“可以部署”
    assert ssh_info["client_key_installed"] is True
    assert out["initialization_status"] == "ready", "历史 prepared_at 保留环境就绪的证据"
    assert out["deployable"] is False and out["deploy_block_reason"] == "credentials_required"
    assert out["managed_deployments"] == 1 and out["successful_deployments"] is None
    assert out["deployment_counts_partial"] is True
    assert listed["summary"]["needs_credentials_count"] == 1
    assert listed["summary"]["successful_deployments"] is None

    for path, body in (
        (f"/api/v1/agents/servers/{server_id}/test-connection", {"version": out["version"]}),
        (f"/api/v1/agents/servers/{server_id}/prepare", {"version": out["version"]}),
    ):
        refused = await owner.client.post(path, json=body, headers=headers)
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "AGENT_SERVER_CREDENTIAL_REQUIRED"

    supplied = await owner.client.put(
        f"/api/v1/agents/servers/{server_id}/credentials",
        json={"version": out["version"], "login_password": "supplied later"},
        headers=headers,
    )
    assert supplied.status_code == 200, supplied.text
    after = supplied.json()["server"]
    assert after["ssh"]["password_set"] is True and after["ssh"]["credential_revision"] == 1
    assert after["ssh"]["prepared_at"] == ssh_info["prepared_at"]
    assert after["ssh"]["connection_status"] == "unknown"
    # 旧公钥仍待退役（要密码登录后定向删除，T-0179）；实例与部署记录不动
    assert after["ssh"]["legacy_key_cleanup_state"] == "pending"
    assert after["deploy_block_reason"] is None and after["deployable"] is True
    shown = await owner.client.get(f"/api/v1/agents/instances/{instance_id}", headers=headers)
    assert shown.json()["server_id"] == str(server_id)
    kept = await run_sql(
        url, "SELECT client_key_ref FROM workspace.agent_ssh_targets WHERE id = $1", target_id
    )
    assert kept == "enc:v1:00000000:old-private-key"


# ---------------------------------------------------------------- 密钥轮换


@pytest.mark.integration
async def test_rotation_reencrypts_the_password_without_bumping_revision(
    migrated_database_url: str,
) -> None:
    url = migrated_database_url
    old_key, new_key = secrets.token_bytes(32), secrets.token_bytes(32)
    old = CredentialBox(old_key)
    user = await add_user(url, "rot@example.com", "rotation password 123")
    workspace = await add_workspace(url, user, "W")
    engine = create_async_engine(url)
    ids = {}
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            for name in ("a", "b"):
                server_id, target_id = uuid.uuid4(), uuid.uuid4()
                db.add(AgentServer(id=server_id, workspace_id=workspace, name=name, kind="ssh"))
                await db.flush()
                db.add(
                    AgentSshTarget(
                        id=target_id,
                        workspace_id=workspace,
                        server_id=server_id,
                        host="203.0.113.5",
                        address="203.0.113.5",
                        port=22 if name == "a" else 2222,
                        username="deploy",
                        host_key_algorithm="ssh-ed25519",
                        host_key="ssh-ed25519 AAAA",
                        host_key_fingerprint="SHA256:x",
                        login_password_ref=seal_server_password(
                            old, target_id, SecretStr(f" pw {name} ")
                        ),
                        credential_revision=3,
                    )
                )
                ids[name] = target_id
            await db.commit()
        before = {
            row["id"]: dict(row)
            for row in await _exec(
                url, "SELECT id, version, updated_at FROM workspace.agent_ssh_targets"
            )
        }
        box = CredentialBox(new_key, [old_key])
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            report = await rotate(db, box)
            assert report.failed == 0 and report.remaining_old == 0
            assert report.fields["agent_ssh_targets.login_password_ref"].rotated == 2
            targets = {t.id: t for t in (await db.execute(select(AgentSshTarget))).scalars().all()}
    finally:
        await engine.dispose()
    only_new = CredentialBox(new_key)
    for name, target_id in ids.items():
        target = targets[target_id]
        assert only_new.key_id_in(target.login_password_ref or "") == only_new.key_id
        assert target.credential_revision == 3
        assert target.version == before[target_id]["version"]
        assert target.updated_at == before[target_id]["updated_at"]
        password = resolve_server_password(only_new, target, expected_revision=3)
        assert password.get_secret_value() == f" pw {name} "
    # 把 a 的密文挪到 b 的记录上：用途绑定不同，解不开
    moved = targets[ids["b"]]
    moved.login_password_ref = targets[ids["a"]].login_password_ref
    with pytest.raises(ServerCredentialError) as caught:
        resolve_server_password(only_new, moved)
    assert caught.value.code == CREDENTIAL_UNREADABLE


# ---------------------------------------------------------------- 解密规则（单元）


def _target(**values: Any) -> AgentSshTarget:
    target = AgentSshTarget(id=uuid.uuid4(), credential_revision=1, login_password_ref=None)
    for name, value in values.items():
        setattr(target, name, value)
    return target


def test_resolve_server_password_rules() -> None:
    key = secrets.token_bytes(32)
    box = CredentialBox(key)
    target = _target()
    with pytest.raises(ServerCredentialError) as missing:
        resolve_server_password(box, target)
    assert missing.value.code == CREDENTIAL_REQUIRED
    assert missing.value.api().status_code == 409

    target.login_password_ref = seal_server_password(box, target.id, SecretStr("secret pw"))
    assert resolve_server_password(box, target, expected_revision=1).get_secret_value() == (
        "secret pw"
    )
    with pytest.raises(ServerCredentialError) as changed:
        resolve_server_password(box, target, expected_revision=0)
    assert changed.value.code == CREDENTIAL_CHANGED
    # 别的密钥加密的：解不开；env 引用不是保存的密码
    with pytest.raises(ServerCredentialError) as other:
        resolve_server_password(CredentialBox(secrets.token_bytes(32)), target)
    assert other.value.code == CREDENTIAL_UNREADABLE
    env_target = _target(login_password_ref="env:WS_SECRET_SERVER_PASSWORD")  # noqa: S106
    with pytest.raises(ServerCredentialError) as env:
        resolve_server_password(box, env_target)
    assert env.value.code == CREDENTIAL_UNREADABLE
    # 错误信息不含明文、密文
    for error in (missing.value, changed.value, other.value, env.value):
        assert "secret pw" not in str(error) and "enc:v1" not in str(error)

    assert agent_server_password_context(target.id) == f"agent_server_password:{target.id}"
    assert password_readable(box, target.login_password_ref) is True
    assert password_readable(None, target.login_password_ref) is False
    assert (
        password_readable(CredentialBox(secrets.token_bytes(32)), target.login_password_ref)
        is False
    )
    rotated = CredentialBox(secrets.token_bytes(32), [key])
    assert password_readable(rotated, target.login_password_ref) is True
    assert password_readable(box, "env:WS_SECRET_X") is False
    assert password_readable(box, None) is False
