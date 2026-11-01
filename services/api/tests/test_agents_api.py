"""OpenClaw 实例、凭证与密钥库（T-0054，OC-2）。真实 PostgreSQL + Redis。

覆盖 V2 契约（docs/decisions/2026-10-31-v2-oc1-contract.md）第 3、4、7 节：
只有 owner 能管理；乐观并发；停用与总开关立即生效；凭证与入口绑定；外网入口要同一实例的客户端证书；
吊销立即生效、轮换宽限期；认证失败统一 401、外网入口封禁；密钥只写不读、按授权下发、
未授权与不存在都是 NOT_FOUND、每次取用都留记录、轮换后旧值不再下发且密文清除；跨工作空间隔离。
"""

import hashlib
import io
import json
import secrets
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import FastAPI
from pydantic import SecretStr

from app.core.config import Settings
from app.domain.agents import AgentService
from app.models import AgentInstance
from tests.helpers import OWNER, ClientFactory, add_member, add_user, add_workspace, csrf, run_sql

pytestmark = pytest.mark.integration

SETTINGS: dict[str, Any] = {
    "credential_encryption_key": SecretStr(secrets.token_hex(32)),
    # 测试客户端（httpx.ASGITransport）的对端地址是 127.0.0.1，当作接入入口的 Caddy
    "agent_ingress_trusted_proxies": "127.0.0.1/32",
    "agent_intranet_url": "https://10.0.0.5",
    "agent_public_url": "https://agent.example.com",
}
ADMIN = {"email": "admin@example.com", "password": "admin password 123456"}
SECRET_VALUE = "sk-test-" + secrets.token_hex(8)


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str
    database_url: str

    @property
    def app(self) -> FastAPI:
        return cast(FastAPI, cast(httpx.ASGITransport, self.client._transport).app)

    def headers(self, *, write: bool = False) -> dict[str, str]:
        return {"X-Workspace-Id": self.workspace_id, **(csrf(self.client) if write else {})}

    async def create_instance(self, **overrides: Any) -> dict[str, Any]:
        body = {"name": "实例 A", "network_mode": "intranet", **overrides}
        response = await self.client.post(
            "/api/v1/agents/instances", json=body, headers=self.headers(write=True)
        )
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    async def create_secret(
        self, name: str = "OpenAI", value: str = SECRET_VALUE
    ) -> dict[str, Any]:
        response = await self.client.post(
            "/api/v1/agents/secrets",
            json={"name": name, "kind": "model_api_key", "value": value},
            headers=self.headers(write=True),
        )
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    async def instance(self, instance_id: str) -> dict[str, Any]:
        response = await self.client.get(
            f"/api/v1/agents/instances/{instance_id}", headers=self.headers()
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def grant(self, instance_id: str, grants: list[dict[str, str]]) -> dict[str, Any]:
        version = (await self.instance(instance_id))["version"]
        response = await self.client.put(
            f"/api/v1/agents/instances/{instance_id}/secret-grants",
            json={"version": version, "grants": grants},
            headers=self.headers(write=True),
        )
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def issue_token(self, instance_id: str, *, keep_previous: bool = True) -> str:
        """接入包（OC-2 第 3 部分）之前，直接用服务签发令牌。"""
        async with self.app.state.sessionmaker() as db:
            service = AgentService(db, self.app.state.settings)
            instance = await db.get(AgentInstance, uuid.UUID(instance_id))
            assert instance is not None
            issued = await service.issue_token(instance, keep_previous=keep_previous)
            await db.commit()
            return issued.token

    async def add_cert(self, instance_id: str, fingerprint: str) -> None:
        async with self.app.state.sessionmaker() as db:
            service = AgentService(db, self.app.state.settings)
            instance = await db.get(AgentInstance, uuid.UUID(instance_id))
            assert instance is not None
            await service.add_client_cert(
                instance,
                fingerprint=fingerprint,
                expires_at=datetime.now(UTC) + timedelta(days=90),
                keep_previous=True,
            )
            await db.commit()


def agent_headers(token: str, ingress: str = "intranet", cert: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", "X-MyWS-Ingress": ingress}
    if cert is not None:
        headers["X-MyWS-Client-Cert-SHA256"] = cert
    return headers


async def resolve(
    client: httpx.AsyncClient, token: str, ids: list[str], **kwargs: Any
) -> httpx.Response:
    return await client.post(
        "/agent-api/v1/secrets/resolve", json={"ids": ids}, headers=agent_headers(token, **kwargs)
    )


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


# ---------------------------------------------------------------- 实例管理


async def test_owner_registers_and_manages_instances(env: Env) -> None:
    created = await env.create_instance(scope="read_write", max_role="viewer")
    assert created["status"] == "pending" and created["im_ready"] is False
    assert created["im_user_id"] == "oc_" + uuid.UUID(created["id"]).hex
    assert created["im_nickname"] == "实例 A" and created["credentials"] == []
    listed = await env.client.get("/api/v1/agents/instances", headers=env.headers())
    assert listed.status_code == 200
    assert listed.json()["agents_enabled"] is True
    assert [i["id"] for i in listed.json()["items"]] == [created["id"]]

    path = f"/api/v1/agents/instances/{created['id']}"
    stale = await env.client.patch(
        path, json={"version": 99, "name": "x"}, headers=env.headers(write=True)
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    renamed = await env.client.patch(
        path,
        json={"version": created["version"], "name": "实例 B"},
        headers=env.headers(write=True),
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "实例 B"

    disabled = await env.client.post(
        f"{path}/disable",
        json={"version": renamed.json()["version"]},
        headers=env.headers(write=True),
    )
    assert disabled.json()["status"] == "disabled" and disabled.json()["disabled_at"]
    enabled = await env.client.post(
        f"{path}/enable",
        json={"version": disabled.json()["version"]},
        headers=env.headers(write=True),
    )
    assert enabled.json()["status"] == "pending" and enabled.json()["disabled_at"] is None

    actions = await run_sql(
        env.database_url,
        "SELECT array_agg(action ORDER BY created_at) FROM workspace.audit_logs"
        " WHERE resource_id = $1",
        created["id"],
    )
    assert actions == [
        "agents.instance.created",
        "agents.instance.updated",
        "agents.instance.disabled",
        "agents.instance.enabled",
    ]


async def test_only_the_owner_can_manage_instances_and_secrets(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with make_client(**SETTINGS) as owner, make_client(**SETTINGS) as admin:
        setup = await owner.post("/api/v1/setup", json=OWNER)
        workspace = setup.json()["workspaces"][0]["id"]
        user = await add_user(migrated_database_url, ADMIN["email"], ADMIN["password"])
        await add_member(migrated_database_url, uuid.UUID(workspace), user, "admin")
        login = await admin.post("/api/v1/auth/login", json=ADMIN)
        assert login.status_code == 200
        headers = {"X-Workspace-Id": workspace, **csrf(admin)}
        for method, path in [
            ("GET", "/api/v1/agents/instances"),
            ("GET", "/api/v1/agents/secrets"),
            ("GET", "/api/v1/agents/settings"),
            ("GET", "/api/v1/agents/secrets/accesses"),
        ]:
            response = await admin.request(method, path, headers=headers)
            assert response.status_code == 403, path
        created = await admin.post(
            "/api/v1/agents/instances",
            json={"name": "x", "network_mode": "intranet"},
            headers=headers,
        )
        assert created.status_code == 403


async def test_instances_are_isolated_between_workspaces(env: Env) -> None:
    instance = await env.create_instance()
    me = await env.client.get("/api/v1/me")
    other = await add_workspace(env.database_url, uuid.UUID(me.json()["user"]["id"]), "另一个")
    other_headers = {"X-Workspace-Id": str(other), **csrf(env.client)}
    hidden = await env.client.get(
        f"/api/v1/agents/instances/{instance['id']}", headers=other_headers
    )
    assert hidden.status_code == 404
    secret = await env.create_secret()
    # 在另一个工作空间里登记实例，不能授权本工作空间的密钥
    foreign = await env.client.post(
        "/api/v1/agents/instances",
        json={"name": "B", "network_mode": "intranet"},
        headers=other_headers,
    )
    granted = await env.client.put(
        f"/api/v1/agents/instances/{foreign.json()['id']}/secret-grants",
        json={
            "version": foreign.json()["version"],
            "grants": [{"secret_id": secret["id"], "ref_id": "k"}],
        },
        headers=other_headers,
    )
    assert granted.status_code == 404


# ---------------------------------------------------------------- 密钥库与取密钥


async def test_secrets_are_write_only_and_resolved_by_grant(env: Env) -> None:
    secret = await env.create_secret()
    assert SECRET_VALUE not in str(secret) and secret["value_version"] == 1
    duplicate = await env.client.post(
        "/api/v1/agents/secrets",
        json={"name": "OpenAI", "value": "other"},
        headers=env.headers(write=True),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "AGENT_SECRET_EXISTS"
    listed = await env.client.get("/api/v1/agents/secrets", headers=env.headers())
    assert SECRET_VALUE not in listed.text

    instance = await env.create_instance()
    other = await env.create_instance(name="实例 C")
    updated = await env.grant(
        instance["id"], [{"secret_id": secret["id"], "ref_id": "openai_api_key"}]
    )
    assert updated["secret_grants"] == [
        {"secret_id": secret["id"], "secret_name": "OpenAI", "ref_id": "openai_api_key"}
    ]
    token = await env.issue_token(instance["id"])
    other_token = await env.issue_token(other["id"])

    ok = await resolve(env.client, token, ["openai_api_key", "missing"])
    assert ok.status_code == 200, ok.text
    assert ok.headers["cache-control"] == "no-store"
    assert ok.json() == {
        "values": {"openai_api_key": SECRET_VALUE},
        "errors": {"missing": {"code": "NOT_FOUND"}},
    }
    # 没有授权的实例：与不存在一样是 NOT_FOUND
    denied = await resolve(env.client, other_token, ["openai_api_key"])
    assert denied.json() == {"values": {}, "errors": {"openai_api_key": {"code": "NOT_FOUND"}}}

    accesses = await env.client.get(
        "/api/v1/agents/secrets/accesses", headers=env.headers(), params={"result": "ok"}
    )
    assert [(a["ref_id"], a["value_version"]) for a in accesses.json()["items"]] == [
        ("openai_api_key", 1)
    ]
    all_accesses = await env.client.get("/api/v1/agents/secrets/accesses", headers=env.headers())
    assert len(all_accesses.json()["items"]) == 3
    assert SECRET_VALUE not in all_accesses.text

    rotated = await env.client.post(
        f"/api/v1/agents/secrets/{secret['id']}/rotate",
        json={"version": secret["version"], "value": "sk-rotated"},
        headers=env.headers(write=True),
    )
    assert rotated.status_code == 200 and rotated.json()["value_version"] == 2
    after = await resolve(env.client, token, ["openai_api_key"])
    assert after.json()["values"] == {"openai_api_key": "sk-rotated"}
    # 旧版本的密文已清除，旧值无法恢复
    kept = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.agent_secret_versions"
        " WHERE secret_id = $1 AND ciphertext_ref IS NOT NULL",
        uuid.UUID(secret["id"]),
    )
    assert kept == 1

    deleted = await env.client.delete(
        f"/api/v1/agents/secrets/{secret['id']}",
        params={"version": rotated.json()["version"]},
        headers=env.headers(write=True),
    )
    assert deleted.status_code == 204
    gone = await resolve(env.client, token, ["openai_api_key"])
    assert gone.json()["errors"] == {"openai_api_key": {"code": "NOT_FOUND"}}


async def test_resolve_is_rate_limited_per_instance(env: Env) -> None:
    instance = await env.create_instance()
    token = await env.issue_token(instance["id"])
    for _ in range(60):
        assert (await resolve(env.client, token, ["k"])).status_code == 200
    limited = await resolve(env.client, token, ["k"])
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    recorded = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.agent_secret_accesses WHERE result = 'rate_limited'",
    )
    assert recorded == 1


# ---------------------------------------------------------------- 实例认证


async def test_instance_credentials_are_checked_on_every_request(env: Env) -> None:
    instance = await env.create_instance()
    token = await env.issue_token(instance["id"])
    assert (await resolve(env.client, token, ["k"])).status_code == 200

    async def rejected(response: httpx.Response) -> None:
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "AGENT_UNAUTHORIZED"
        assert response.headers["www-authenticate"] == "Bearer"

    await rejected(await resolve(env.client, "mwa_" + secrets.token_urlsafe(32), ["k"]))
    await rejected(await resolve(env.client, token, ["k"], ingress="public"))  # 入口不对
    no_ingress = await env.client.post(
        "/agent-api/v1/secrets/resolve",
        json={"ids": ["k"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    await rejected(no_ingress)
    session_only = await env.client.post("/agent-api/v1/secrets/resolve", json={"ids": ["k"]})
    await rejected(session_only)  # 登录的浏览器会话不能当实例凭证

    path = f"/api/v1/agents/instances/{instance['id']}"
    current = await env.instance(instance["id"])
    await env.client.post(
        f"{path}/disable", json={"version": current["version"]}, headers=env.headers(write=True)
    )
    await rejected(await resolve(env.client, token, ["k"]))  # 停用立即生效
    current = await env.instance(instance["id"])
    await env.client.post(
        f"{path}/enable", json={"version": current["version"]}, headers=env.headers(write=True)
    )
    assert (await resolve(env.client, token, ["k"])).status_code == 200

    settings = (await env.client.get("/api/v1/agents/settings", headers=env.headers())).json()
    off = await env.client.patch(
        "/api/v1/agents/settings",
        json={"version": settings["version"], "agents_enabled": False},
        headers=env.headers(write=True),
    )
    assert off.status_code == 200 and off.json()["agents_enabled"] is False
    await rejected(await resolve(env.client, token, ["k"]))  # 总开关
    on = await env.client.patch(
        "/api/v1/agents/settings",
        json={"version": off.json()["version"], "agents_enabled": True},
        headers=env.headers(write=True),
    )
    assert on.status_code == 200
    assert (await resolve(env.client, token, ["k"])).status_code == 200


async def test_rotation_keeps_the_previous_token_for_a_grace_period(env: Env) -> None:
    instance = await env.create_instance()
    first = await env.issue_token(instance["id"])
    second = await env.issue_token(instance["id"], keep_previous=True)
    assert (await resolve(env.client, first, ["k"])).status_code == 200  # 宽限期内仍有效
    third = await env.issue_token(instance["id"], keep_previous=True)
    # 同时最多两份有效：最早的一份立即吊销
    assert (await resolve(env.client, first, ["k"])).status_code == 401
    assert (await resolve(env.client, second, ["k"])).status_code == 200
    fourth = await env.issue_token(instance["id"], keep_previous=False)
    for old in (second, third):
        assert (await resolve(env.client, old, ["k"])).status_code == 401
    assert (await resolve(env.client, fourth, ["k"])).status_code == 200

    current = await env.instance(instance["id"])
    active = next(c for c in current["credentials"] if c["revoked_at"] is None)
    assert fourth.startswith(active["token_prefix"])
    revoked = await env.client.post(
        f"/api/v1/agents/instances/{instance['id']}/credentials/{active['id']}/revoke",
        json={"version": current["version"]},
        headers=env.headers(write=True),
    )
    assert revoked.status_code == 200
    assert (await resolve(env.client, fourth, ["k"])).status_code == 401  # 吊销立即生效
    stored = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.agent_credentials WHERE token_hash LIKE $1",
        f"%{fourth}%",
    )
    assert stored == 0  # 只存哈希


async def test_public_ingress_requires_the_instances_client_certificate(env: Env) -> None:
    instance = await env.create_instance(network_mode="public")
    token = await env.issue_token(instance["id"])
    fingerprint = secrets.token_hex(32)
    await env.add_cert(instance["id"], fingerprint)
    other = await env.create_instance(name="B", network_mode="public")
    other_fingerprint = secrets.token_hex(32)
    await env.add_cert(other["id"], other_fingerprint)

    ok = await resolve(env.client, token, ["k"], ingress="public", cert=fingerprint.upper())
    assert ok.status_code == 200
    for cert in (None, secrets.token_hex(32), other_fingerprint):
        response = await resolve(env.client, token, ["k"], ingress="public", cert=cert)
        assert response.status_code == 401, cert
    assert (await resolve(env.client, token, ["k"], ingress="intranet")).status_code == 401

    # 外网入口：同一来源认证失败 10 次后封禁，正确的凭证也暂时进不来
    for _ in range(7):  # 前面已有 3 次外网入口的失败
        await resolve(env.client, "mwa_wrong", ["k"], ingress="public", cert=fingerprint)
    banned = await resolve(env.client, token, ["k"], ingress="public", cert=fingerprint)
    assert banned.status_code == 429 and int(banned.headers["retry-after"]) > 0


async def test_ingress_headers_are_ignored_unless_from_the_ingress_proxy(
    make_client: ClientFactory,
) -> None:
    """同一网络里的其他容器伪造 X-MyWS-Ingress / 证书指纹请求头不算数。"""
    with pytest.raises(ValueError, match="IP 地址或网段"):
        Settings(agent_ingress_trusted_proxies="caddy")
    async with make_client(
        **{**SETTINGS, "agent_ingress_trusted_proxies": "10.9.0.0/16"}
    ) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        env = Env(client, setup.json()["workspaces"][0]["id"], "")
        instance = await env.create_instance()
        token = await env.issue_token(instance["id"])
        response = await resolve(client, token, ["k"])
        assert response.status_code == 401


async def test_heartbeat_reports_versions(env: Env) -> None:
    instance = await env.create_instance()
    token = await env.issue_token(instance["id"])
    response = await env.client.post(
        "/agent-api/v1/heartbeat",
        json={"openclaw_version": "2026.9.36", "plugin_version": "0.3.3"},
        headers=agent_headers(token),
    )
    assert response.status_code == 200 and response.json()["server_time"]
    current = await env.instance(instance["id"])
    assert (current["reported_version"], current["plugin_version"]) == ("2026.9.36", "0.3.3")
    assert current["last_seen_at"] is not None
    assert current["credentials"][0]["last_used_at"] is not None


# ---------------------------------------------------------------- 接入包与内部 CA


async def _bundle(env: Env, instance_id: str, previous: str = "keep_24h") -> httpx.Response:
    version = (await env.instance(instance_id))["version"]
    return await env.client.post(
        f"/api/v1/agents/instances/{instance_id}/bundle",
        json={"version": version, "previous": previous},
        headers=env.headers(write=True),
    )


def _unzip(response: httpx.Response) -> dict[str, tuple[str, int]]:
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        return {
            info.filename: (zf.read(info).decode(), (info.external_attr >> 16) & 0o777)
            for info in zf.infolist()
        }


def _env_value(files: dict[str, tuple[str, int]], name: str) -> str:
    for line in files["myworkspace.env"][0].splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise AssertionError(name)


async def test_bundle_carries_a_fresh_token_only_once(env: Env) -> None:
    instance = await env.create_instance()
    response = await _bundle(env, instance["id"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    files = _unzip(response)
    assert set(files) == {
        "myworkspace.env",
        "state/openclaw.json",
        "bin/myworkspace-secret-resolver",
        "bin/myworkspace-openclaw-start",
        "compose.yaml",
        "CHECKLIST.md",
        "README.md",
    }
    assert files["myworkspace.env"][1] == 0o600 and files["state/openclaw.json"][1] == 0o600
    assert files["bin/myworkspace-secret-resolver"][1] == 0o755
    token = _env_value(files, "MYWS_INSTANCE_TOKEN")
    assert _env_value(files, "MYWS_AGENT_API") == "https://10.0.0.5/agent-api"
    assert _env_value(files, "OPENIM_WS_ADDR") == "wss://10.0.0.5/im/ws"
    compose = files["compose.yaml"][0]
    assert "\n  tunnel:" not in compose and "@PUBLIC" not in compose
    # 工作台也有外网入口：内网实例的本机取图代理（不带客户端证书）把外网域名转发到内网入口
    assert "\n  image-proxy:" in compose and "--disable-authentication" in compose
    assert "--target=10.0.0.5:443" in compose and "--listen=127.0.0.1:18443" in compose
    assert 'extra_hosts: ["agent.example.com:127.0.0.1"]' in compose
    assert (await resolve(env.client, token, ["k"])).status_code == 200

    me = await env.client.get("/api/v1/me")
    config = json.loads(files["state/openclaw.json"][0])
    whitelist = config["channels"]["openim"]["accounts"]["default"]["inboundWhitelist"]
    assert whitelist == ["ws_" + uuid.UUID(me.json()["user"]["id"]).hex, instance["im_user_id"]]
    assert config["tools"]["exec"]["security"] == "deny"
    # 插件的发图片 / 文件 / 视频工具默认禁用（OC-0 F3；方案第 6.2 节）
    media = {"openim_send_image", "openim_send_file", "openim_send_video"}
    assert media <= set(config["tools"]["deny"])
    # 锁定版本的 OpenClaw 把未知的键（例如 _comment）判为无效配置（T-0059 用真实 OpenClaw 验收发现）
    assert not [key for key in config if key.startswith("_")]
    assert config["plugins"] == {
        "allow": ["openclaw-channel"],
        "entries": {"openclaw-channel": {"enabled": True}},
    }
    # 插件要在第一次启动前、用临时空配置安装（配置里的 openim 渠道由插件提供）
    checklist = files["CHECKLIST.md"][0]
    assert "OPENCLAW_CONFIG_PATH=/home/node/.openclaw/plugin-install.json" in checklist

    after = await env.instance(instance["id"])
    assert after["bundle_issued_at"] is not None
    assert token not in str(after) and token.startswith(after["credentials"][0]["token_prefix"])
    audit = await run_sql(
        env.database_url,
        "SELECT metadata_redacted_json::text FROM workspace.audit_logs"
        " WHERE action = 'agents.bundle.issued'",
    )
    assert token not in audit

    rotated = _unzip(await _bundle(env, instance["id"], previous="revoke_now"))
    new_token = _env_value(rotated, "MYWS_INSTANCE_TOKEN")
    assert new_token != token
    assert (await resolve(env.client, token, ["k"])).status_code == 401
    assert (await resolve(env.client, new_token, ["k"])).status_code == 200


async def test_public_bundle_has_a_client_certificate_from_the_internal_ca(env: Env) -> None:
    instance = await env.create_instance(network_mode="public")
    files = _unzip(await _bundle(env, instance["id"]))
    assert files["tls/client.key"][1] == 0o600
    token = _env_value(files, "MYWS_INSTANCE_TOKEN")
    # 外网模式经本机隧道：公网域名在 OpenClaw 那边解析到 127.0.0.1（Caddy 要求 SNI 与 Host 一致）
    assert _env_value(files, "MYWS_AGENT_API") == "http://agent.example.com:18443/agent-api"
    assert _env_value(files, "MYWS_LOCAL_TUNNEL_HOST") == "agent.example.com"
    compose = files["compose.yaml"][0]
    assert '"agent.example.com:127.0.0.1"' in compose
    assert "--target=agent.example.com:443" in compose and "--listen=127.0.0.1:18443" in compose
    assert "ghostunnel/ghostunnel:v1.11.4@sha256:" in compose
    cert = x509.load_pem_x509_certificate(files["tls/client.crt"][0].encode())
    fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    ca_pem = await run_sql(
        env.database_url, "SELECT cert_pem FROM workspace.agent_ca WHERE retired_at IS NULL"
    )
    ca = x509.load_pem_x509_certificate(ca_pem.encode())
    cert.verify_directly_issued_by(ca)
    assert (
        cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == instance["im_user_id"]
    )
    usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(usage) == [ExtendedKeyUsageOID.CLIENT_AUTH]
    assert cert.not_valid_after_utc - cert.not_valid_before_utc <= timedelta(days=91)
    key_ref = await run_sql(env.database_url, "SELECT key_ref FROM workspace.agent_ca")
    assert key_ref.startswith("enc:v1:")  # CA 私钥只存密文

    ok = await resolve(env.client, token, ["k"], ingress="public", cert=fingerprint)
    assert ok.status_code == 200
    # 再生成一次：沿用同一套 CA，新证书也能用
    second = _unzip(await _bundle(env, instance["id"]))
    assert await run_sql(env.database_url, "SELECT count(*) FROM workspace.agent_ca") == 1
    cert2 = x509.load_pem_x509_certificate(second["tls/client.crt"][0].encode())
    cert2.verify_directly_issued_by(ca)


async def test_bundle_is_refused_for_disabled_or_unconfigured_instances(
    make_client: ClientFactory,
) -> None:
    settings = {**SETTINGS, "agent_public_url": ""}
    async with make_client(**settings) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        env = Env(client, setup.json()["workspaces"][0]["id"], "")
        public = await env.create_instance(network_mode="public")
        refused = await _bundle(env, public["id"])
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "AGENT_INGRESS_NOT_CONFIGURED"
        instance = await env.create_instance()
        await client.post(
            f"/api/v1/agents/instances/{instance['id']}/disable",
            json={"version": instance["version"]},
            headers=env.headers(write=True),
        )
        disabled = await _bundle(env, instance["id"])
        assert disabled.status_code == 409 and disabled.json()["error"]["code"] == "AGENT_DISABLED"
        stale = await client.post(
            f"/api/v1/agents/instances/{instance['id']}/bundle",
            json={"version": 1},
            headers=env.headers(write=True),
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
