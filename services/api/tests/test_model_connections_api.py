"""中转站管理：连接的增删改查、汇总、授权、Key 更换与模型集合（T-0201）。真实 PostgreSQL + Redis。

覆盖接口约定第 2 节与验收矩阵 A05 / A08 / A09 / A10、S01 / S02：
- 只有 owner；跨工作空间 404 且不写入；
- 任何响应都不含 Key 明文；数据库里只有加密后的内部凭据；
- URL 只收 HTTPS 协议基地址；未开放的协议、官方预设配第三方主机、未配置完整就启用都被拒绝；
- 已授权的连接不能改地址或协议；停用先返回影响摘要；有授权时不能删除；
- 版本冲突、创建幂等；更换 Key 后版本加 1、旧密文清除、配置修订号加 1；
- 模型集合整体替换、被绑定的模型不能移除；撤销授权同时撤掉这条凭据对该实例的旧授权；
- 服务端搜索 / 筛选 / 分页 / total 与全局汇总。
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.domain import model_connections
from tests.helpers import OWNER, ClientFactory, add_member, add_user, csrf, run_sql
from tests.test_agents_api import SETTINGS, Env

pytestmark = pytest.mark.integration

BASE = "/api/v1/model-connections"
KEY = "sk-model-conn-plaintext-3c7d91"
NEW_KEY = "sk-model-conn-rotated-a40f52"


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


def official(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "OpenAI 官方",
        "connection_type": "official",
        "official_provider": "openai",
        "protocol": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "api_key": KEY,
        "models": [{"model_id": "gpt-4.1"}, {"model_id": "gpt-4.1-mini"}],
        **overrides,
    }


def relay(name: str = "团队多模型中转", **overrides: Any) -> dict[str, Any]:
    return {
        "name": name,
        "connection_type": "relay",
        "protocol": "openai_compatible",
        "base_url": "https://gateway.example.com/v1/",
        "api_key": KEY,
        "models": [{"model_id": "claude-sonnet-4"}, {"model_id": "gemini-2.5-pro"}],
        **overrides,
    }


async def create(env: Env, body: dict[str, Any], **headers: str) -> httpx.Response:
    return await env.client.post(BASE, json=body, headers={**env.headers(write=True), **headers})


async def created(env: Env, body: dict[str, Any]) -> dict[str, Any]:
    response = await create(env, body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def get(env: Env, path: str = "", **params: Any) -> httpx.Response:
    return await env.client.get(BASE + path, params=params, headers=env.headers())


async def patch(env: Env, cid: str, **body: Any) -> httpx.Response:
    return await env.client.patch(f"{BASE}/{cid}", json=body, headers=env.headers(write=True))


async def current(env: Env, cid: str) -> dict[str, Any]:
    response = await get(env, f"/{cid}")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def put_grants(env: Env, cid: str, instance_ids: list[str]) -> httpx.Response:
    version = (await current(env, cid))["version"]
    return await env.client.put(
        f"{BASE}/{cid}/grants",
        json={"version": version, "instance_ids": instance_ids},
        headers=env.headers(write=True),
    )


async def secret_of(env: Env, cid: str) -> uuid.UUID:
    value = await run_sql(
        env.database_url,
        "SELECT credential_secret_id FROM workspace.model_connections WHERE id = $1",
        uuid.UUID(cid),
    )
    assert isinstance(value, uuid.UUID)
    return value


async def bind(env: Env, instance_id: str, cid: str, model_id: str) -> None:
    """直接写一条主模型绑定（绑定接口在 T-0204；这里只为验证“在用”的约束）。"""
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.agent_model_bindings"
        " (instance_id, workspace_id, connection_id, model_id, apply_state)"
        " VALUES ($1, $2, $3, $4, 'pending')",
        uuid.UUID(instance_id),
        uuid.UUID(env.workspace_id),
        uuid.UUID(cid),
        model_id,
    )


# ---------------------------------------------------------------- 权限与隔离


async def test_owner_workspace_isolation(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with make_client(**SETTINGS) as owner:
        setup = await owner.post("/api/v1/setup", json=OWNER)
        workspace = setup.json()["workspaces"][0]["id"]
        env = Env(owner, workspace, migrated_database_url)
        connection = await created(env, official())
        cid = connection["id"]

        for role in ("admin", "member", "viewer"):
            creds = {"email": f"{role}@example.com", "password": f"{role} password 123456"}
            user = await add_user(migrated_database_url, creds["email"], creds["password"])
            await add_member(migrated_database_url, uuid.UUID(workspace), user, role)
            async with make_client(**SETTINGS) as other:
                assert (await other.post("/api/v1/auth/login", json=creds)).status_code == 200
                headers = {"X-Workspace-Id": workspace, **csrf(other)}
                for method, path, body in (
                    ("GET", BASE, None),
                    ("GET", f"{BASE}/summary", None),
                    ("GET", f"{BASE}/capabilities", None),
                    ("GET", f"{BASE}/{cid}", None),
                    ("GET", f"{BASE}/{cid}/models", None),
                    ("POST", BASE, relay("越权")),
                    ("PATCH", f"{BASE}/{cid}", {"version": 1, "name": "改掉"}),
                    ("POST", f"{BASE}/{cid}/rotate-key", {"version": 1, "api_key": "x"}),
                ):
                    response = await other.request(method, path, json=body, headers=headers)
                    assert response.status_code == 403, (role, method, path, response.text)
                    assert "OpenAI" not in response.text
        assert (await current(env, cid))["name"] == "OpenAI 官方"

    # 另一个工作空间的 owner：看不到、改不了、不能把别人的实例加进授权
    async with make_client(**SETTINGS) as owner:
        await owner.post("/api/v1/auth/login", json={k: OWNER[k] for k in ("email", "password")})
        env = Env(owner, workspace, migrated_database_url)
        instance = await env.create_instance(name="甲")
    async with make_client(**SETTINGS) as stranger:
        creds = {"email": "stranger@example.com", "password": "stranger password 123456"}
        user = await add_user(migrated_database_url, creds["email"], creds["password"])
        await stranger.post("/api/v1/auth/login", json=creds)
        from tests.helpers import add_workspace

        other_ws = str(await add_workspace(migrated_database_url, user, "陌生人的空间"))
        foreign = Env(stranger, other_ws, migrated_database_url)
        assert (await get(foreign, f"/{cid}")).status_code == 404
        assert (await patch(foreign, cid, version=1, name="改掉")).status_code == 404
        mine = await created(foreign, relay("陌生人的中转"))
        response = await foreign.client.put(
            f"{BASE}/{mine['id']}/grants",
            json={"version": mine["version"], "instance_ids": [instance["id"]]},
            headers=foreign.headers(write=True),
        )
        assert response.status_code == 404, response.text
        assert (await current(foreign, mine["id"]))["grant_count"] == 0
        listed = (await get(foreign)).json()
        assert [c["name"] for c in listed["items"]] == ["陌生人的中转"]


async def test_connection_crud_never_returns_key(env: Env) -> None:
    connection = await created(env, official())
    cid = connection["id"]
    assert connection["credential_configured"] is True
    assert connection["credential_version"] == 1
    assert connection["configuration_state"] == "ready"
    assert connection["enabled"] is True
    assert "migration_state" not in connection
    assert connection["model_count"] == 2
    assert connection["model_preview"] == ["gpt-4.1", "gpt-4.1-mini"]
    assert not (
        {"api_key", "credential_ref", "credential_secret_id", "secret_id"} & connection.keys()
    )

    instance = await env.create_instance(name="甲")
    texts = [
        (await get(env)).text,
        (await get(env, f"/{cid}")).text,
        (await get(env, "/summary")).text,
        (await get(env, f"/{cid}/models")).text,
        (await patch(env, cid, version=connection["version"], description="说明")).text,
        (await put_grants(env, cid, [instance["id"]])).text,
        (await get(env, f"/{cid}/grants")).text,
        (await get(env, f"/{cid}/activity")).text,
    ]
    for text in texts:
        assert KEY not in text
    stored = await run_sql(
        env.database_url,
        "SELECT string_agg(ciphertext_ref, ',') FROM workspace.agent_secret_versions"
        " WHERE secret_id = $1",
        await secret_of(env, cid),
    )
    assert stored and KEY not in stored and stored.startswith("enc:")
    # 内部凭据不在旧密钥库的列表里（T-0203 隐藏原生连接的凭据）之外，审计里也没有 Key
    audit = await run_sql(
        env.database_url,
        "SELECT string_agg(metadata_redacted_json::text, ',') FROM workspace.audit_logs"
        " WHERE resource_type = 'model_connection'",
    )
    assert audit and KEY not in audit


async def test_list_shows_first_three_models_in_one_response(env: Env) -> None:
    body = relay("多模型中转")
    body["models"] = [{"model_id": m} for m in ("z-model", "a-model", "m-model", "b-model")]
    await created(env, body)
    await created(env, official())
    items = {c["name"]: c for c in (await get(env)).json()["items"]}
    assert items["多模型中转"]["model_count"] == 4
    assert items["多模型中转"]["model_preview"] == ["a-model", "b-model", "m-model"]
    assert items["OpenAI 官方"]["model_preview"] == ["gpt-4.1", "gpt-4.1-mini"]


# ---------------------------------------------------------------- 校验


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"base_url": "http://gateway.example.com/v1"}, "MODEL_BASE_URL_INVALID"),
        ({"base_url": "https://user:pw@gateway.example.com/v1"}, "MODEL_BASE_URL_INVALID"),
        ({"base_url": "https://gateway.example.com/v1?key=sk-abc"}, "MODEL_BASE_URL_INVALID"),
        ({"base_url": "https://gateway.example.com/v1#x"}, "MODEL_BASE_URL_INVALID"),
        ({"base_url": "https://gateway.example.com/v1/chat/completions"}, "MODEL_BASE_URL_INVALID"),
        ({"base_url": "ftp://gateway.example.com/v1"}, "MODEL_BASE_URL_INVALID"),
        ({"base_url": "https://gateway.example.com/v1/messages"}, "MODEL_BASE_URL_INVALID"),
        (
            {"base_url": "https://gateway.example.com/v1beta/models/m:generateContent"},
            "MODEL_BASE_URL_INVALID",
        ),
        ({"protocol": "anthropic_native"}, "MODEL_PROTOCOL_UNSUPPORTED"),
        ({"models": []}, "MODEL_CONNECTION_NOT_READY"),
    ],
)
async def test_create_rejects_invalid_endpoints(
    env: Env, override: dict[str, Any], code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 没有核验开放的协议（这里假设部署只开放了 openai_compatible）选了返回 422
    monkeypatch.setattr(model_connections, "SUPPORTED_PROTOCOLS", ("openai_compatible",))
    response = await create(env, relay(**override))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert KEY not in response.text
    assert (await get(env)).json()["total"] == 0
    secrets_left = await run_sql(env.database_url, "SELECT count(*) FROM workspace.agent_secrets")
    assert secrets_left == 0


async def test_official_preset_must_stay_on_official_host(env: Env) -> None:
    response = await create(env, official(base_url="https://relay.example.net/v1"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_OFFICIAL_HOST_MISMATCH"
    response = await create(env, official(connection_type="relay"))
    assert response.status_code == 422


ANTHROPIC = {
    "name": "Anthropic 官方",
    "official_provider": "anthropic",
    "protocol": "anthropic_native",
    "base_url": "https://api.anthropic.com",
    "models": [{"model_id": "claude-opus-5-5"}],
}
GOOGLE = {
    "name": "Gemini 官方",
    "official_provider": "google",
    "protocol": "gemini_native",
    "base_url": "https://generativelanguage.googleapis.com/v1beta",
    "models": [{"model_id": "gemini-2.5-flash"}],
}


@pytest.mark.parametrize("preset", [ANTHROPIC, GOOGLE], ids=["anthropic", "google"])
async def test_native_official_presets(env: Env, preset: dict[str, Any]) -> None:
    """Anthropic / Gemini 官方预设：协议与官方主机固定；
    第三方主机返回 MODEL_OFFICIAL_HOST_MISMATCH。"""
    capabilities = (await get(env, "/capabilities")).json()
    listed = {p["provider"]: p for p in capabilities["official_presets"]}
    assert listed[preset["official_provider"]]["protocol"] == preset["protocol"]
    assert listed[preset["official_provider"]]["base_url"] == preset["base_url"]

    mismatch = await create(env, official(**{**preset, "base_url": "https://relay.example.net/v1"}))
    assert mismatch.status_code == 422, mismatch.text
    assert mismatch.json()["error"]["code"] == "MODEL_OFFICIAL_HOST_MISMATCH"
    assert KEY not in mismatch.text
    wrong_protocol = await create(env, official(**{**preset, "protocol": "openai_compatible"}))
    assert wrong_protocol.status_code == 422
    assert wrong_protocol.json()["error"]["code"] == "VALIDATION_FAILED"
    assert (await get(env)).json()["total"] == 0

    connection = await created(env, official(**preset))
    assert connection["connection_type"] == "official"
    assert connection["official_provider"] == preset["official_provider"]
    assert connection["protocol"] == preset["protocol"]
    assert connection["base_url"] == preset["base_url"]
    assert connection["configuration_state"] == "ready"
    # 改成第三方主机同样被拒绝
    response = await patch(
        env,
        connection["id"],
        version=connection["version"],
        base_url="https://relay.example.net/anthropic",
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_OFFICIAL_HOST_MISMATCH"


async def test_native_protocols_are_open_for_relays(env: Env) -> None:
    anthropic = await created(
        env,
        relay(
            "Anthropic 中转",
            protocol="anthropic_native",
            base_url="https://gateway.example.com/anthropic/",
        ),
    )
    assert anthropic["protocol"] == "anthropic_native"
    assert anthropic["base_url"] == "https://gateway.example.com/anthropic"
    gemini = await created(
        env,
        relay(
            "Gemini 中转", protocol="gemini_native", base_url="https://gateway.example.com/v1beta"
        ),
    )
    assert gemini["protocol"] == "gemini_native"


async def test_base_url_is_normalized_and_duplicate_names_conflict(env: Env) -> None:
    connection = await created(env, relay(base_url="https://Gateway.Example.com/v1/"))
    assert connection["base_url"] == "https://gateway.example.com/v1"
    response = await create(env, relay())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_CONNECTION_EXISTS"


async def test_draft_is_disabled_and_cannot_be_enabled_until_ready(env: Env) -> None:
    draft = await created(
        env,
        {"name": "草稿", "api_key": KEY, "save_as_draft": True},
    )
    assert draft["configuration_state"] == "draft"
    assert draft["enabled"] is False
    assert draft["connection_type"] is None
    response = await patch(env, draft["id"], version=draft["version"], enabled=True)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_CONNECTION_NOT_READY"
    response = await patch(
        env,
        draft["id"],
        version=draft["version"],
        connection_type="relay",
        protocol="openai_compatible",
        base_url="https://relay.example.net/v1",
    )
    assert response.status_code == 200, response.text
    filled = response.json()
    assert filled["configuration_state"] == "draft"  # 还没有模型
    models = await env.client.put(
        f"{BASE}/{draft['id']}/models",
        json={
            "version": filled["version"],
            "model_scope": "selected",
            "models": [{"model_id": "m1"}],
        },
        headers=env.headers(write=True),
    )
    assert models.status_code == 200, models.text
    assert models.json()["configuration_state"] == "ready"
    enabled = await patch(env, draft["id"], version=models.json()["version"], enabled=True)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True


# ---------------------------------------------------------------- 在用约束


async def test_used_endpoint_is_immutable(env: Env) -> None:
    connection = await created(env, relay())
    cid = connection["id"]
    # 旧地址同步来的模型目录：改地址后清空（不能再拿来切换“全部可用模型”）
    await run_sql(
        env.database_url,
        "UPDATE workspace.model_connections SET discovered_models = ARRAY['old-a', 'old-b'],"
        " sync_state = 'complete', models_synced_at = now() WHERE id = $1",
        uuid.UUID(cid),
    )
    # 没有授权与绑定：可以改地址，配置修订号加 1
    response = await patch(
        env, cid, version=connection["version"], base_url="https://gateway2.example.com/v1"
    )
    assert response.status_code == 200, response.text
    assert response.json()["config_revision"] == connection["config_revision"] + 1
    page = (await get(env, f"/{cid}/models")).json()
    assert page["discovered"] == [] and page["sync_state"] is None and page["synced_at"] is None
    instance = await env.create_instance(name="甲")
    assert (await put_grants(env, cid, [instance["id"]])).status_code == 200
    latest = await current(env, cid)
    for change in (
        {"base_url": "https://evil.example.com/v1"},
        {"base_url": "https://gateway2.example.com/v2"},  # 同主机换路径也算换目标
    ):
        response = await patch(env, cid, version=latest["version"], **change)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CONNECTION_ENDPOINT_IN_USE"
    # 名称与说明仍可改
    response = await patch(env, cid, version=latest["version"], name="改名后的中转")
    assert response.status_code == 200
    assert (await current(env, cid))["base_url"] == "https://gateway2.example.com/v1"


async def test_disable_requires_confirming_impact(env: Env) -> None:
    connection = await created(env, relay())
    cid = connection["id"]
    instance = await env.create_instance(name="甲")
    assert (await put_grants(env, cid, [instance["id"]])).status_code == 200
    latest = await current(env, cid)
    response = await patch(env, cid, version=latest["version"], enabled=False)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "CONNECTION_IN_USE"
    assert [i["instance_id"] for i in error["details"]["impact"]["instances"]] == [instance["id"]]
    assert (await current(env, cid))["enabled"] is True
    response = await patch(env, cid, version=latest["version"], enabled=False, confirm_impact=True)
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is False
    # 停用不删除授权，也不改配置完整度
    assert response.json()["grant_count"] == 1
    assert response.json()["configuration_state"] == "ready"


async def test_delete_used_connection_conflicts(env: Env) -> None:
    connection = await created(env, relay())
    cid = connection["id"]
    secret_id = await secret_of(env, cid)
    instance = await env.create_instance(name="甲")
    assert (await put_grants(env, cid, [instance["id"]])).status_code == 200
    version = (await current(env, cid))["version"]
    response = await env.client.delete(
        f"{BASE}/{cid}", params={"version": version}, headers=env.headers(write=True)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONNECTION_IN_USE"
    assert (await put_grants(env, cid, [])).status_code == 200
    version = (await current(env, cid))["version"]
    response = await env.client.delete(
        f"{BASE}/{cid}", params={"version": version}, headers=env.headers(write=True)
    )
    assert response.status_code == 204, response.text
    assert (await get(env, f"/{cid}")).status_code == 404
    left = await run_sql(
        env.database_url, "SELECT count(*) FROM workspace.agent_secrets WHERE id = $1", secret_id
    )
    assert left == 0
    # 实例不受影响
    assert (await env.instance(instance["id"]))["name"] == "甲"


async def test_version_conflict_and_idempotent_create(env: Env) -> None:
    key = str(uuid.uuid4())
    first = await create(env, relay(), **{"Idempotency-Key": key})
    assert first.status_code == 201, first.text
    again = await create(env, relay(), **{"Idempotency-Key": key})
    assert again.status_code == 201, again.text
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.json()["id"] == first.json()["id"]
    assert await run_sql(env.database_url, "SELECT count(*) FROM workspace.agent_secrets") == 1
    reused = await create(env, relay(name="另一个"), **{"Idempotency-Key": key})
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    cid = first.json()["id"]
    stale = first.json()["version"]
    assert (await patch(env, cid, version=stale, name="新名字")).status_code == 200
    response = await patch(env, cid, version=stale, name="旧版本覆盖")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert (await current(env, cid))["name"] == "新名字"


async def test_rotate_key_bumps_versions_and_destroys_old_ciphertext(env: Env) -> None:
    connection = await created(env, relay())
    cid = connection["id"]
    secret_id = await secret_of(env, cid)
    old = await run_sql(
        env.database_url,
        "SELECT ciphertext_ref FROM workspace.agent_secret_versions WHERE secret_id = $1",
        secret_id,
    )
    # 之前推理检测通过的模型：换 Key 后不再算“已验证”
    await run_sql(
        env.database_url,
        "UPDATE workspace.model_connection_models SET availability = 'verified'"
        " WHERE connection_id = $1",
        uuid.UUID(cid),
    )
    response = await env.client.post(
        f"{BASE}/{cid}/rotate-key",
        json={"version": connection["version"], "api_key": NEW_KEY},
        headers=env.headers(write=True),
    )
    assert response.status_code == 200, response.text
    models = (await get(env, f"/{cid}/models")).json()["items"]
    assert {m["availability"] for m in models} == {"listed"}
    out = response.json()
    assert out["credential_version"] == 2
    assert out["config_revision"] == connection["config_revision"] + 1
    assert out["last_check"] is None
    assert NEW_KEY not in response.text
    rows = await run_sql(
        env.database_url,
        "SELECT json_agg(json_build_object('v', value_version, 'c', ciphertext_ref))"
        " FROM workspace.agent_secret_versions WHERE secret_id = $1",
        secret_id,
    )
    import json

    versions = {r["v"]: r["c"] for r in json.loads(rows)}
    assert versions[1] is None and versions[2] and versions[2] != old
    # 空 Key 不能提交（不能意外清空）
    response = await env.client.post(
        f"{BASE}/{cid}/rotate-key",
        json={"version": out["version"], "api_key": ""},
        headers=env.headers(write=True),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------- 模型集合


async def test_models_replace_and_in_use_models(env: Env) -> None:
    connection = await created(env, relay())
    cid = connection["id"]
    instance = await env.create_instance(name="甲")
    await bind(env, instance["id"], cid, "claude-sonnet-4")

    async def replace(scope: str, models: list[str]) -> httpx.Response:
        version = (await current(env, cid))["version"]
        return await env.client.put(
            f"{BASE}/{cid}/models",
            json={
                "version": version,
                "model_scope": scope,
                "models": [{"model_id": m} for m in models],
            },
            headers=env.headers(write=True),
        )

    response = await replace("selected", ["gemini-2.5-pro"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_IN_USE"
    assert instance["id"] in response.json()["error"]["details"]["models"]["claude-sonnet-4"]
    response = await replace("selected", [])
    assert response.status_code == 422
    response = await replace("selected", ["a", "a"])
    assert response.status_code == 422
    response = await replace("all_available", [])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_SYNC_REQUIRED"
    response = await replace("selected", ["claude-sonnet-4", "GPT-4o", "openai/gpt-4.1"])
    assert response.status_code == 200, response.text
    page = (await get(env, f"/{cid}/models")).json()
    assert [m["model_id"] for m in page["items"]] == ["GPT-4o", "claude-sonnet-4", "openai/gpt-4.1"]
    assert page["total"] == 3
    assert {m["availability"] for m in page["items"]} == {"listed"}
    # 空白与控制字符不是合法的模型 ID
    assert (await replace("selected", ["has space"])).status_code == 422


# ---------------------------------------------------------------- 授权


async def test_revoking_a_grant_in_use_is_refused(env: Env) -> None:
    connection = await created(env, relay())
    cid = connection["id"]
    a = await env.create_instance(name="甲")
    b = await env.create_instance(name="乙")
    assert (await put_grants(env, cid, [a["id"], b["id"]])).status_code == 200
    await bind(env, b["id"], cid, "claude-sonnet-4")
    response = await put_grants(env, cid, [a["id"]])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GRANT_IN_USE"
    response = await put_grants(env, cid, [b["id"]])
    assert response.status_code == 200, response.text
    grants = (await get(env, f"/{cid}/grants")).json()
    assert [(g["instance_name"], g["bound"]) for g in grants["items"]] == [("乙", True)]


async def test_connection_key_stays_out_of_the_secret_vault(env: Env) -> None:
    """模型服务的 Key 只在中转站管理的连接里（T-0209）：密钥库看不到、改不了、授权不了，
    也不能在密钥库里新建 model_api_key。"""
    connection = await created(env, relay())
    secret_id = str(await secret_of(env, connection["id"]))
    instance = await env.create_instance(name="甲")
    vault = "/api/v1/agents/secrets"

    listed = (await env.client.get(vault, headers=env.headers())).json()
    assert listed["total"] == 0 and listed["items"] == []
    detail = await env.client.get(f"{vault}/{secret_id}", headers=env.headers())
    assert detail.status_code == 404
    rotate = await env.client.post(
        f"{vault}/{secret_id}/rotate",
        json={"version": 1, "value": "sk-other-value-123456"},
        headers=env.headers(write=True),
    )
    assert rotate.status_code == 404
    removed = await env.client.delete(
        f"{vault}/{secret_id}", params={"version": 1}, headers=env.headers(write=True)
    )
    assert removed.status_code == 404
    granted = await env.client.put(
        f"/api/v1/agents/instances/{instance['id']}/secret-grants",
        json={
            "version": instance["version"],
            "grants": [{"secret_id": secret_id, "ref_id": "model_key"}],
        },
        headers=env.headers(write=True),
    )
    assert granted.status_code == 404, granted.text

    # 取用记录：实例取连接 Key（mc_ 引用）记在连接的活动里，不出现在密钥库的取用记录
    for ref_id, sid, result in (
        (f"mc_{uuid.UUID(connection['id']).hex}", uuid.UUID(secret_id), "ok"),
        ("mc_ffffffffffffffffffffffffffffffff", None, "not_found"),
        ("github_token", None, "not_found"),
    ):
        await run_sql(
            env.database_url,
            "INSERT INTO workspace.agent_secret_accesses"
            " (workspace_id, instance_id, instance_name, ref_id, secret_id, value_version, result)"
            " VALUES ($1, $2, '甲', $3, $4, 1, $5)",
            uuid.UUID(env.workspace_id),
            uuid.UUID(instance["id"]),
            ref_id,
            sid,
            result,
        )
    accesses = (await env.client.get(f"{vault}/accesses", headers=env.headers())).json()
    assert [a["ref_id"] for a in accesses["items"]] == ["github_token"]
    activity = (await get(env, f"/{connection['id']}/activity")).json()
    assert [i["kind"] for i in activity["items"]].count("access") == 1, activity

    created_key = await env.client.post(
        vault,
        json={"name": "OpenAI", "kind": "model_api_key", "value": "sk-vault-model-key-1234"},
        headers=env.headers(write=True),
    )
    assert created_key.status_code == 422
    assert created_key.json()["error"]["details"] == {"field": "kind"}


# ---------------------------------------------------------------- 搜索、分页、汇总


async def test_list_filters_total_and_summary(env: Env) -> None:
    await created(env, official())
    await created(env, relay())
    await created(env, relay("备用模型连接", base_url="https://backup.example.com/v1"))
    for i in range(3):
        await created(env, {"name": f"草稿 {i}", "api_key": KEY, "save_as_draft": True})

    page = (await get(env, limit=2)).json()
    assert page["total"] == 6 and len(page["items"]) == 2 and page["next_cursor"]
    seen = [c["id"] for c in page["items"]]
    cursor = page["next_cursor"]
    while cursor:
        page = (await get(env, limit=2, cursor=cursor)).json()
        assert page["total"] == 6
        seen += [c["id"] for c in page["items"]]
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen)) == 6

    assert (await get(env, q="backup.example")).json()["total"] == 1
    assert (await get(env, q="GEMINI-2.5")).json()["total"] == 2  # 模型 ID，不区分大小写
    assert (await get(env, q="100%")).json()["total"] == 0
    assert (await get(env, type="official")).json()["total"] == 1
    assert (await get(env, configuration_state="draft")).json()["total"] == 3
    assert (await get(env, enabled=False)).json()["total"] == 3
    assert (await get(env, check_status="never")).json()["total"] == 6
    first = (await get(env, limit=2, type="relay")).json()
    wrong = await get(env, limit=2, type="official", cursor=first["next_cursor"] or "x")
    assert wrong.status_code == 422

    summary = (await get(env, "/summary")).json()
    assert summary["total"] == 6
    assert summary["check_passed"] == 0  # 还没有检测，不能显示“检测通过”
    assert summary["needs_attention"] == 3
    assert summary["unique_granted_instances"] == 0
    capabilities = (await get(env, "/capabilities")).json()
    assert capabilities["supported_protocols"] == [
        "openai_compatible",
        "anthropic_native",
        "gemini_native",
    ]
    assert [p["provider"] for p in capabilities["official_presets"]] == [
        "openai",
        "anthropic",
        "google",
    ]


async def test_activity_lists_audit_events(env: Env) -> None:
    connection = await created(env, relay())
    cid = connection["id"]
    await patch(env, cid, version=connection["version"], description="说明")
    activity = (await get(env, f"/{cid}/activity")).json()
    actions = [i["action"] for i in activity["items"]]
    assert actions[:2] == ["model_connection.updated", "model_connection.created"]
    assert all(i["kind"] == "audit" for i in activity["items"])
