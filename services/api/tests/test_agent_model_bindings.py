"""OpenClaw 实例的主模型绑定、应用任务、实例取配置 / 回报 / 取 Key、接入包（T-0204 / T-0205）。

真实 PostgreSQL + Redis。覆盖契约第 3 节与 OpenClaw 方案 MC-4 / MC-5 的检查点：
- 绑定的前提：同工作空间、已授权、启用、配置完整、模型在集合内；版本冲突；
- 绑定是目标、确认后才是已应用；同一目标重复保存不加修订号；重新应用复用同一任务；
- 迟到的旧回报不能覆盖新目标；切换未确认时旧连接仍可取 Key，确认后退休；
- 实例只能取自己目标 / 已应用连接的 Key；解除绑定、停用连接、撤销授权立即拒绝取用；
- Key 更换让绑定重新应用；排队太久显示 unknown；
- 接入包：有绑定写受管 provider（无 Key）；没有绑定不写模型段（旧授权也不写，T-0209）。
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from tests.helpers import (
    OWNER,
    ClientFactory,
    add_member,
    add_user,
    add_workspace,
    csrf,
    run_sql,
)
from tests.test_agents_api import SETTINGS, Env, _bundle, _unzip, agent_headers

pytestmark = pytest.mark.integration

KEY_A = "sk-binding-key-a-4f1c"
KEY_B = "sk-binding-key-b-9d2e"


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


async def connection(env: Env, name: str, key: str, **overrides: Any) -> dict[str, Any]:
    body = {
        "name": name,
        "connection_type": "relay",
        "protocol": "openai_compatible",
        "base_url": f"https://{name.lower()}.example.com/v1",
        "api_key": key,
        "models": [{"model_id": "m-1", "display_name": "模型一"}, {"model_id": "m-2"}],
        **overrides,
    }
    response = await env.client.post(
        "/api/v1/model-connections", json=body, headers=env.headers(write=True)
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def grant(env: Env, cid: str, instance_ids: list[str]) -> None:
    current = (
        await env.client.get(f"/api/v1/model-connections/{cid}", headers=env.headers())
    ).json()
    response = await env.client.put(
        f"/api/v1/model-connections/{cid}/grants",
        json={"version": current["version"], "instance_ids": instance_ids},
        headers=env.headers(write=True),
    )
    assert response.status_code == 200, response.text


def path(instance_id: str) -> str:
    return f"/api/v1/agents/instances/{instance_id}/model-binding"


async def view(env: Env, instance_id: str) -> dict[str, Any]:
    response = await env.client.get(path(instance_id), headers=env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def put(
    env: Env, instance_id: str, cid: str, model_id: str, version: int | None
) -> httpx.Response:
    return await env.client.put(
        path(instance_id),
        json={"version": version, "connection_id": cid, "model_id": model_id},
        headers=env.headers(write=True),
    )


async def config(env: Env, token: str) -> httpx.Response:
    return await env.client.get("/agent-api/v1/model-config", headers=agent_headers(token))


async def report(
    env: Env, token: str, cfg: dict[str, Any], result: str = "applied", **extra: Any
) -> httpx.Response:
    body = {
        "binding_version": cfg["binding_version"],
        "desired_revision": cfg["desired_revision"],
        "result": result,
        **extra,
    }
    return await env.client.post(
        "/agent-api/v1/model-config/applied", json=body, headers=agent_headers(token)
    )


async def keys(env: Env, token: str, ids: list[str]) -> dict[str, Any]:
    response = await env.client.post(
        "/agent-api/v1/model-credentials/resolve", json={"ids": ids}, headers=agent_headers(token)
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data: dict[str, Any] = response.json()
    return data


def ref(cid: str) -> str:
    return "mc_" + uuid.UUID(cid).hex


async def setup_two(env: Env) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """两条连接都授权给实例甲；返回（甲、连接 A、连接 B、甲的令牌）。"""
    instance = await env.create_instance(name="甲")
    a = await connection(env, "Alpha", KEY_A)
    b = await connection(env, "Beta", KEY_B)
    await grant(env, a["id"], [instance["id"]])
    await grant(env, b["id"], [instance["id"]])
    token = await env.issue_token(instance["id"])
    return instance, a, b, token


async def test_binding_preconditions(env: Env, make_client: ClientFactory) -> None:
    instance = await env.create_instance(name="甲")
    a = await connection(env, "Alpha", KEY_A)
    # 还没授权
    response = await put(env, instance["id"], a["id"], "m-1", None)
    assert response.status_code == 409 and response.json()["error"]["code"] == "MODEL_NOT_ALLOWED"
    await grant(env, a["id"], [instance["id"]])
    # 模型不在集合里
    response = await put(env, instance["id"], a["id"], "m-404", None)
    assert response.status_code == 422 and response.json()["error"]["code"] == "MODEL_NOT_ALLOWED"
    # 连接不存在 / 首次绑定却带了版本
    assert (await put(env, instance["id"], str(uuid.uuid4()), "m-1", None)).status_code == 404
    response = await put(env, instance["id"], a["id"], "m-1", 3)
    assert response.status_code == 409 and response.json()["error"]["code"] == "VERSION_CONFLICT"
    # 停用的连接
    await run_sql(env.database_url, "UPDATE workspace.model_connections SET enabled = false")
    response = await put(env, instance["id"], a["id"], "m-1", None)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_CONNECTION_DISABLED"
    await run_sql(env.database_url, "UPDATE workspace.model_connections SET enabled = true")
    # 草稿（配置不完整）
    draft = await connection(env, "Draft", KEY_B, base_url=None, save_as_draft=True)
    await run_sql(
        env.database_url,
        "UPDATE workspace.model_connections SET enabled = true WHERE id = $1",
        uuid.UUID(draft["id"]),
    )
    await grant(env, draft["id"], [instance["id"]])
    response = await put(env, instance["id"], draft["id"], "m-1", None)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_CONNECTION_NOT_READY"
    assert (await view(env, instance["id"]))["binding"] is None

    # 另一个工作空间的 owner：看不到实例、也不能用这边的连接
    creds = {"email": "stranger@example.com", "password": "stranger password 123456"}
    user = await add_user(env.database_url, creds["email"], creds["password"])
    other_ws = str(await add_workspace(env.database_url, user, "陌生人的空间"))
    async with make_client(**SETTINGS) as stranger:
        await stranger.post("/api/v1/auth/login", json=creds)
        headers = {"X-Workspace-Id": other_ws, **csrf(stranger)}
        assert (await stranger.get(path(instance["id"]), headers=headers)).status_code == 404
        response = await stranger.put(
            path(instance["id"]),
            json={"version": None, "connection_id": a["id"], "model_id": "m-1"},
            headers=headers,
        )
        assert response.status_code == 404
        mine = (
            await stranger.post(
                "/api/v1/agents/instances",
                json={"name": "乙", "network_mode": "intranet"},
                headers=headers,
            )
        ).json()
        response = await stranger.put(
            path(mine["id"]),
            json={"version": None, "connection_id": a["id"], "model_id": "m-1"},
            headers=headers,
        )
        assert response.status_code == 404
        listed = await stranger.get("/api/v1/agents/model-assignments", headers=headers)
        assert [row["instance_name"] for row in listed.json()["items"]] == ["乙"]

    # 管理员（非 owner）不能读写
    admin = {"email": "admin@example.com", "password": "admin password 123456"}
    admin_id = await add_user(env.database_url, admin["email"], admin["password"])
    await add_member(env.database_url, uuid.UUID(env.workspace_id), admin_id, "admin")
    async with make_client(**SETTINGS) as other:
        await other.post("/api/v1/auth/login", json=admin)
        headers = {"X-Workspace-Id": env.workspace_id, **csrf(other)}
        assert (await other.get(path(instance["id"]), headers=headers)).status_code == 403
        response = await other.put(
            path(instance["id"]),
            json={"version": None, "connection_id": a["id"], "model_id": "m-1"},
            headers=headers,
        )
        assert response.status_code == 403
        listed = await other.get("/api/v1/agents/model-assignments", headers=headers)
        assert listed.status_code == 403
    assert (await put(env, instance["id"], a["id"], "m-1", None)).status_code == 200


async def test_binding_is_desired_until_the_instance_confirms(env: Env) -> None:
    instance, a, b, token = await setup_two(env)
    response = await put(env, instance["id"], a["id"], "m-1", None)
    assert response.status_code == 200, response.text
    binding = response.json()
    assert binding["apply_state"] == "pending" and binding["desired_revision"] == 1
    assert binding["applied_connection_id"] is None and binding["applied_revision"] is None
    assert binding["connection_name"] == "Alpha"
    # 同一目标再存一次：不加修订号、不重复建任务
    again = await put(env, instance["id"], a["id"], "m-1", binding["version"])
    assert again.json()["desired_revision"] == 1 and again.json()["job_id"] == binding["job_id"]
    # 重新应用复用同一任务
    apply = await env.client.post(
        path(instance["id"]) + "/apply",
        json={"version": binding["version"]},
        headers=env.headers(write=True),
    )
    assert apply.status_code == 202 and apply.json()["id"] == binding["job_id"]
    assert apply.json()["state"] == "queued" and apply.json()["attempt"] == 1

    # 实例取配置：只有非敏感字段，任务变成执行中
    got = await config(env, token)
    assert got.status_code == 200, got.text
    assert got.headers["cache-control"] == "no-store"
    cfg = got.json()
    assert cfg["state"] == "active" and cfg["api"] == "openai-completions"
    assert cfg["base_url"] == "https://alpha.example.com/v1" and cfg["model_id"] == "m-1"
    assert cfg["model_name"] == "模型一"
    assert cfg["provider_key"] == "mwc" + uuid.UUID(a["id"]).hex[:20]
    assert cfg["primary"] == f"{cfg['provider_key']}/m-1"
    assert cfg["secret_ref_id"] == ref(a["id"]) and cfg["job_state"] == "running"
    assert cfg["apply_key"] == "1.1" and cfg["managed_prefix"] == "mwc"
    assert KEY_A not in got.text
    assert (await view(env, instance["id"]))["binding"]["apply_state"] == "applying"

    # 确认已应用
    done = await report(env, token, cfg)
    assert done.status_code == 200, done.text
    assert done.json() == {"apply_state": "applied", "desired_revision": 1, "applied_revision": 1}
    state = (await view(env, instance["id"]))["binding"]
    assert state["applied_connection_id"] == a["id"] and state["applied_model_id"] == "m-1"
    assert state["applied_connection_name"] == "Alpha" and state["applied_at"] is not None
    job = await env.client.get(
        path(instance["id"]) + f"/jobs/{state['job_id']}", headers=env.headers()
    )
    assert job.json()["state"] == "succeeded"

    # 切换到 B：目标变了、已应用仍是 A；旧修订号的迟到回报 409 且不改状态
    switched = await put(env, instance["id"], b["id"], "m-2", state["version"])
    assert switched.status_code == 200, switched.text
    assert switched.json()["desired_revision"] == 2
    assert switched.json()["applied_connection_id"] == a["id"]
    late = await report(env, token, cfg)
    assert late.status_code == 409 and late.json()["error"]["code"] == "MODEL_BINDING_CONFLICT"
    assert (await view(env, instance["id"]))["binding"]["applied_revision"] == 1
    # 切换未确认：A 与 B 都能取；确认后 A 退休
    both = await keys(env, token, [ref(a["id"]), ref(b["id"])])
    assert both["values"] == {ref(a["id"]): KEY_A, ref(b["id"]): KEY_B}
    cfg2 = (await config(env, token)).json()
    assert cfg2["desired_revision"] == 2 and cfg2["secret_ref_id"] == ref(b["id"])
    failed = await report(env, token, cfg2, "failed", reason="invalid_config")
    assert failed.json()["apply_state"] == "failed"
    state = (await view(env, instance["id"]))["binding"]
    assert state["last_error_code"] == "invalid_config"
    assert state["applied_connection_id"] == a["id"]  # 失败不显示成已应用
    retry = await env.client.post(
        path(instance["id"]) + "/apply",
        json={"version": state["version"]},
        headers=env.headers(write=True),
    )
    assert retry.json()["attempt"] == 2 and retry.json()["state"] == "queued"
    cfg3 = (await config(env, token)).json()
    assert cfg3["apply_key"] == "2.2"
    assert (await report(env, token, cfg3)).json()["apply_state"] == "applied"
    after = await keys(env, token, [ref(a["id"]), ref(b["id"])])
    assert after["values"] == {ref(b["id"]): KEY_B}
    assert after["errors"] == {ref(a["id"]): {"code": "NOT_FOUND"}}

    # 每次取用都有记录（不含 Key）
    rows = await run_sql(
        env.database_url,
        "SELECT string_agg(result, ',' ORDER BY id) FROM workspace.agent_secret_accesses"
        " WHERE ref_id LIKE 'mc_%'",
    )
    assert rows.split(",").count("ok") == 3 and "not_found" in rows


async def test_instances_only_reach_their_own_credentials(env: Env) -> None:
    instance, a, b, token = await setup_two(env)
    other = await env.create_instance(name="乙")
    other_token = await env.issue_token(other["id"])
    await grant(env, a["id"], [instance["id"], other["id"]])
    binding = (await put(env, instance["id"], a["id"], "m-1", None)).json()
    # 乙已授权但没有绑定：拿不到；甲拿不到 B（不是目标也不是已应用）
    assert (await keys(env, other_token, [ref(a["id"])]))["values"] == {}
    assert (await config(env, other_token)).json()["error"]["details"]["reason"] == "no_binding"
    mine = await keys(env, token, [ref(a["id"]), ref(b["id"]), "mc_bad", "openai_api_key"])
    assert mine["values"] == {ref(a["id"]): KEY_A}
    assert set(mine["errors"]) == {ref(b["id"]), "mc_bad", "openai_api_key"}
    # 没有凭证 401
    response = await env.client.get("/agent-api/v1/model-config")
    assert response.status_code == 401

    # 停用连接：取 Key、取配置都拒绝；重新启用恢复
    await run_sql(env.database_url, "UPDATE workspace.model_connections SET enabled = false")
    assert (await keys(env, token, [ref(a["id"])]))["values"] == {}
    blocked = await config(env, token)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["reason"] == "connection_unavailable"
    await run_sql(env.database_url, "UPDATE workspace.model_connections SET enabled = true")
    assert (await keys(env, token, [ref(a["id"])]))["values"] == {ref(a["id"]): KEY_A}

    # 解除绑定：立即拒绝取 Key；配置变成 revoked；确认后已应用清空
    removed = await env.client.delete(
        path(instance["id"]),
        params={"version": binding["version"]},
        headers=env.headers(write=True),
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["revoked"] is True and removed.json()["connection_id"] is None
    assert (await keys(env, token, [ref(a["id"])]))["values"] == {}
    cfg = (await config(env, token)).json()
    assert cfg["state"] == "revoked" and cfg["provider_key"] is None
    assert (await report(env, token, cfg)).status_code == 200
    state = (await view(env, instance["id"]))["binding"]
    assert state["applied_connection_id"] is None and state["apply_state"] == "applied"


async def test_revoking_the_grant_needs_the_binding_moved_first(env: Env) -> None:
    instance, a, _, token = await setup_two(env)
    await put(env, instance["id"], a["id"], "m-1", None)
    current = (
        await env.client.get(f"/api/v1/model-connections/{a['id']}", headers=env.headers())
    ).json()
    response = await env.client.put(
        f"/api/v1/model-connections/{a['id']}/grants",
        json={"version": current["version"], "instance_ids": []},
        headers=env.headers(write=True),
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "GRANT_IN_USE"
    assert (await keys(env, token, [ref(a["id"])]))["values"] == {ref(a["id"]): KEY_A}


async def test_key_rotation_reapplies_bound_instances(env: Env) -> None:
    instance, a, _, token = await setup_two(env)
    await put(env, instance["id"], a["id"], "m-1", None)
    cfg = (await config(env, token)).json()
    await report(env, token, cfg)
    current = (
        await env.client.get(f"/api/v1/model-connections/{a['id']}", headers=env.headers())
    ).json()
    rotated = await env.client.post(
        f"/api/v1/model-connections/{a['id']}/rotate-key",
        json={"version": current["version"], "api_key": "sk-binding-rotated-77aa"},
        headers=env.headers(write=True),
    )
    assert rotated.status_code == 200, rotated.text
    state = (await view(env, instance["id"]))["binding"]
    assert state["desired_revision"] == 2 and state["apply_state"] == "pending"
    cfg2 = (await config(env, token)).json()
    assert cfg2["fingerprint"] != cfg["fingerprint"] and cfg2["apply_key"] == "2.1"
    values = (await keys(env, token, [ref(a["id"])]))["values"]
    assert values == {ref(a["id"]): "sk-binding-rotated-77aa"}


async def test_stale_jobs_show_unknown(env: Env) -> None:
    instance, a, _, _ = await setup_two(env)
    binding = (await put(env, instance["id"], a["id"], "m-1", None)).json()
    await run_sql(
        env.database_url,
        "UPDATE workspace.model_apply_jobs SET updated_at = now() - interval '11 minutes'",
    )
    state = (await view(env, instance["id"]))["binding"]
    assert state["apply_state"] == "unknown" and state["last_error_code"] == "no_report"
    job = await env.client.get(
        path(instance["id"]) + f"/jobs/{binding['job_id']}", headers=env.headers()
    )
    assert job.json()["state"] == "unknown"
    # 重新应用：重新排队
    retry = await env.client.post(
        path(instance["id"]) + "/apply",
        json={"version": state["version"]},
        headers=env.headers(write=True),
    )
    assert retry.json()["state"] == "queued" and retry.json()["attempt"] == 2


async def test_assignments_and_picker(env: Env) -> None:
    instance, a, b, _ = await setup_two(env)
    blank = await env.create_instance(name="空白")
    await put(env, instance["id"], a["id"], "m-1", None)

    page = (await env.client.get("/api/v1/agents/model-assignments", headers=env.headers())).json()
    rows = {row["instance_name"]: row for row in page["items"]}
    assert page["total"] == 2
    assert rows["甲"]["binding"]["connection_name"] == "Alpha"
    assert "legacy" not in rows["甲"]
    # 还没有设置主模型的实例：没有绑定，也没有“旧配置”状态（T-0209）
    assert rows["空白"]["binding"] is None
    blank_view = await view(env, blank["id"])
    assert blank_view["apply_state"] is None and blank_view["binding"] is None
    only_a = (
        await env.client.get(
            "/api/v1/agents/model-assignments",
            params={"connection_id": a["id"]},
            headers=env.headers(),
        )
    ).json()
    assert [row["instance_name"] for row in only_a["items"]] == ["甲"]

    options = (
        await env.client.get(
            "/api/v1/agents/model-connection-options",
            params={"instance_id": blank["id"]},
            headers=env.headers(),
        )
    ).json()
    assert {o["name"]: o["granted"] for o in options["items"]} == {"Alpha": False, "Beta": False}
    assert {o["name"]: o["model_count"] for o in options["items"]} == {"Alpha": 2, "Beta": 2}
    granted = (
        await env.client.get(
            "/api/v1/agents/model-connection-options",
            params={"instance_id": instance["id"]},
            headers=env.headers(),
        )
    ).json()
    assert all(o["granted"] for o in granted["items"])
    await run_sql(
        env.database_url,
        "UPDATE workspace.model_connections SET enabled = false WHERE id = $1",
        uuid.UUID(b["id"]),
    )
    fresh = (
        await env.client.get("/api/v1/agents/model-connection-options", headers=env.headers())
    ).json()
    assert [o["name"] for o in fresh["items"]] == ["Alpha"]


async def test_bundle_writes_the_managed_provider_without_a_key(env: Env) -> None:
    instance, a, _, _ = await setup_two(env)
    await put(env, instance["id"], a["id"], "m-1", None)
    files = _unzip(await _bundle(env, instance["id"]))
    text = files["state/openclaw.json"][0]
    assert KEY_A not in text
    config_json = json.loads(text)
    key = "mwc" + uuid.UUID(a["id"]).hex[:20]
    assert config_json["models"] == {
        "providers": {
            key: {
                "baseUrl": "https://alpha.example.com/v1",
                "api": "openai-completions",
                "apiKey": {"source": "exec", "provider": "myworkspace", "id": ref(a["id"])},
                "models": [{"id": "m-1", "name": "模型一"}],
            }
        }
    }
    assert config_json["agents"]["defaults"]["model"] == {"primary": f"{key}/m-1"}
    assert config_json["tools"]["exec"]["security"] == "deny"  # 工具限制不变
    assert files["bin/model-config.mjs"][1] == 0o755
    resolver = files["bin/myworkspace-secret-resolver"][0]
    assert "/v1/model-credentials/resolve" in resolver and "/v1/secrets/resolve" in resolver

    # 实例的旧密钥授权不会变成模型段：模型 Key 只在中转站管理的连接里（T-0209）
    other = await env.create_instance(name="只有兼容凭据")
    secret = await env.create_secret(name="GitHub")
    await env.grant(other["id"], [{"secret_id": secret["id"], "ref_id": "openai_api_key"}])
    old = json.loads(_unzip(await _bundle(env, other["id"]))["state/openclaw.json"][0])
    assert "models" not in old
    assert "model" not in old.get("agents", {}).get("defaults", {})
    # 新实例、没有绑定：不写模型段
    blank = await env.create_instance(name="空白")
    fresh = json.loads(_unzip(await _bundle(env, blank["id"]))["state/openclaw.json"][0])
    assert "models" not in fresh
