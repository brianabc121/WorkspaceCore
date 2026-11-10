"""密钥库的服务端搜索、全量匹配数与元数据详情（T-0196，接口约定第 4 节）。真实 PostgreSQL + Redis。

覆盖：q（名称 / 说明字面包含、不区分大小写，% _ 和反斜杠按字面）与 kind 在分页之前生效，
跨页也能找到最后一项；total 是同条件的全量匹配数；游标绑定工作空间与筛选，换条件 422，
旧版不带筛选的游标仍可用；列表与详情只有 owner，详情跨空间 404；
任何响应都不含明文、密文引用或历史值；grants 批量读取，查询次数不随条数增长。
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy import event

from app.domain.tasks import encode_cursor
from tests.helpers import OWNER, ClientFactory, add_member, add_user, add_workspace, csrf, run_sql
from tests.test_agents_api import SETTINGS, Env

pytestmark = pytest.mark.integration

LIST = "/api/v1/agents/secrets"
PLAINTEXT = "sk-catalog-plaintext-7f3a9c"
ROTATED = "sk-catalog-rotated-b81e04"
OUT_FIELDS = {
    "id",
    "name",
    "kind",
    "description",
    "value_version",
    "last_rotated_at",
    "grants",
    "version",
    "created_at",
    "updated_at",
}


@pytest.fixture
async def env(make_client: ClientFactory, migrated_database_url: str) -> AsyncIterator[Env]:
    async with make_client(**SETTINGS) as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"], migrated_database_url)


async def _create(
    env: Env, name: str, *, kind: str = "generic", description: str = "", value: str = PLAINTEXT
) -> dict[str, Any]:
    response = await env.client.post(
        LIST,
        json={"name": name, "kind": kind, "description": description, "value": value},
        headers=env.headers(write=True),
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def _get(env: Env, path: str = LIST, **params: Any) -> httpx.Response:
    return await env.client.get(path, params=params, headers=env.headers())


async def _list(env: Env, **params: Any) -> dict[str, Any]:
    response = await _get(env, **params)
    assert response.status_code == 200, (params, response.text)
    data: dict[str, Any] = response.json()
    return data


async def _all_pages(env: Env, **params: Any) -> tuple[list[dict[str, Any]], set[int], int]:
    """按 next_cursor 翻完同一组条件；返回全部条目、每页看到的 total 与页数。"""
    items: list[dict[str, Any]] = []
    totals: set[int] = set()
    cursor: str | None = None
    pages = 0
    while True:
        page = await _list(env, **params, **({"cursor": cursor} if cursor else {}))
        items.extend(page["items"])
        totals.add(page["total"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            return items, totals, pages
        assert pages < 100


def _names(page: dict[str, Any]) -> list[str]:
    return [item["name"] for item in page["items"]]


# ---------------------------------------------------------------- 搜索、类型与全量匹配数


async def test_search_and_kind_find_the_last_secret_beyond_the_first_page(env: Env) -> None:
    for i in range(54):
        await _create(
            env,
            f"bulk-{i:02d}",
            kind="api_token" if i % 10 == 0 else "generic",
            description="批量导入的测试密钥",
        )
    last = await _create(env, "zz-last", kind="api_token", description="支付网关 Token")

    first = await _list(env)
    assert len(first["items"]) == 50 and first["next_cursor"] is not None
    assert first["total"] == 55 and first["total_estimate"] == 55
    assert "zz-last" not in _names(first)
    everything, totals, pages = await _all_pages(env)
    assert pages == 2 and totals == {55}
    assert len({item["id"] for item in everything}) == 55
    assert everything[-1]["id"] == last["id"]

    # 最后一项不在第一页，也能按说明、按名称（不区分大小写）和按类型找到
    for params in (
        {"q": "支付网关"},
        {"q": "ZZ-LAST"},
        {"q": "token"},
        {"q": "zz", "kind": "api_token"},
    ):
        found = await _list(env, **params)
        assert [item["id"] for item in found["items"]] == [last["id"]], params
        assert found["total"] == 1 and found["next_cursor"] is None, params
    assert (await _list(env, q="zz", kind="generic"))["total"] == 0

    # 筛选在分页之前：每页的 total 都是全量匹配数，翻完正好是全部匹配
    tokens, totals, pages = await _all_pages(env, kind="api_token", limit=4)
    assert totals == {7} and pages == 2
    assert [item["name"] for item in tokens] == [
        *(f"bulk-{i:02d}" for i in range(0, 54, 10)),
        "zz-last",
    ]
    assert all(item["kind"] == "api_token" for item in tokens)
    matched, totals, _ = await _all_pages(env, q="bulk-5", limit=3)
    assert totals == {4} and [item["name"] for item in matched] == [
        "bulk-50",
        "bulk-51",
        "bulk-52",
        "bulk-53",
    ]
    described, totals, _ = await _all_pages(env, q="批量导入", kind="generic")
    assert totals == {48} and len(described) == 48

    nothing = await _list(env, q="没有这个密钥")
    assert nothing == {"items": [], "next_cursor": None, "total_estimate": 0, "total": 0}


async def test_like_wildcards_and_backslash_are_literal(env: Env) -> None:
    under = await _create(env, "a_b")
    await _create(env, "axb")
    percent = await _create(env, "discount", description="折扣 100% 生效")
    await _create(env, "plain", description="折扣 1000 生效")
    backslash = await _create(env, "windows", description=r"C:\keys\prod")
    await _create(env, "unix", description="C:/keys/prod")

    def ids(page: dict[str, Any]) -> list[str]:
        return [item["id"] for item in page["items"]]

    assert ids(await _list(env, q="a_b")) == [under["id"]]
    assert ids(await _list(env, q="_")) == [under["id"]]
    assert ids(await _list(env, q="100%")) == [percent["id"]]
    assert ids(await _list(env, q="%")) == [percent["id"]]
    assert ids(await _list(env, q="\\")) == [backslash["id"]]
    assert ids(await _list(env, q=r"\keys")) == [backslash["id"]]
    assert (await _list(env, q="%"))["total"] == 1


async def test_query_parameters_are_validated(env: Env) -> None:
    await _create(env, "OpenAI")
    # PostgreSQL 的文本不能含 NUL：按参数错误拒绝，而不是 500
    for params in (
        {"q": ""},
        {"q": "x" * 101},
        {"q": "a\x00b"},
        {"kind": "ssh_key"},
        {"limit": 0},
    ):
        response = await _get(env, **params)
        assert response.status_code == 422, (params, response.text)
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert (await _list(env, q="x" * 100))["total"] == 0
    for kind in ("model_api_key", "api_token", "generic"):
        assert (await _list(env, kind=kind))["total"] == (1 if kind == "generic" else 0)


# ---------------------------------------------------------------- 游标绑定筛选


async def test_cursor_is_bound_to_the_workspace_and_filters(env: Env) -> None:
    created = [await _create(env, f"key-{i}", kind="api_token") for i in range(5)]
    names = [s["name"] for s in created]

    filtered = await _list(env, q="key", kind="api_token", limit=2)
    cursor = filtered["next_cursor"]
    assert cursor is not None and filtered["total"] == 5
    same = await _list(env, q="key", kind="api_token", limit=2, cursor=cursor)
    assert _names(same) == names[2:4]
    # 换了 q、kind 或去掉条件，旧游标都不能接着用
    for params in (
        {"q": "key-1", "kind": "api_token"},
        {"q": "KEY", "kind": "api_token"},
        {"q": "key"},
        {"kind": "api_token"},
        {"q": "key", "kind": "generic"},
        {},
    ):
        response = await _get(env, **params, cursor=cursor)
        assert response.status_code == 422, (params, response.text)
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"

    plain = await _list(env, limit=2)
    plain_cursor = plain["next_cursor"]
    assert plain_cursor is not None
    assert _names(await _list(env, limit=2, cursor=plain_cursor)) == names[2:4]
    assert (await _get(env, q="key", cursor=plain_cursor)).status_code == 422
    assert (await _get(env, kind="api_token", cursor=plain_cursor)).status_code == 422

    # 旧版游标（只有偏移量，没有筛选摘要）：不带条件时照常可用，带条件时拒绝
    legacy = encode_cursor(2)
    assert _names(await _list(env, limit=2, cursor=legacy)) == names[2:4]
    assert (await _get(env, q="key", cursor=legacy)).status_code == 422
    assert (await _get(env, kind="api_token", cursor=legacy)).status_code == 422
    for broken in ("not-a-cursor", "e30", encode_cursor(2) + "x"):
        assert (await _get(env, cursor=broken)).status_code == 422, broken

    # 游标绑定工作空间：在另一个空间里不能用
    me = await env.client.get("/api/v1/me")
    other = await add_workspace(env.database_url, uuid.UUID(me.json()["user"]["id"]), "另一个")
    elsewhere = await env.client.get(
        LIST, params={"cursor": plain_cursor}, headers={"X-Workspace-Id": str(other)}
    )
    assert elsewhere.status_code == 422, elsewhere.text


# ---------------------------------------------------------------- 详情只有元数据


async def test_detail_returns_metadata_only_and_never_values(env: Env) -> None:
    secret = await _create(env, "Stripe", kind="api_token", description="支付", value=PLAINTEXT)
    first = await env.create_instance(name="实例 B")
    second = await env.create_instance(name="实例 A")
    await env.grant(first["id"], [{"secret_id": secret["id"], "ref_id": "stripe_key"}])
    await env.grant(second["id"], [{"secret_id": secret["id"], "ref_id": "payments"}])
    rotated = await env.client.post(
        f"{LIST}/{secret['id']}/rotate",
        json={"version": secret["version"], "value": ROTATED},
        headers=env.headers(write=True),
    )
    assert rotated.status_code == 200, rotated.text

    detail = await _get(env, f"{LIST}/{secret['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert set(body) == OUT_FIELDS
    assert body["id"] == secret["id"] and body["name"] == "Stripe"
    assert body["kind"] == "api_token" and body["description"] == "支付"
    # value_version 是值的版本，version 是这条记录的乐观并发版本，两者不同
    assert body["value_version"] == 2 and body["version"] == rotated.json()["version"]
    assert body["grants"] == [
        {"instance_id": second["id"], "instance_name": "实例 A", "ref_id": "payments"},
        {"instance_id": first["id"], "instance_name": "实例 B", "ref_id": "stripe_key"},
    ]
    listed = await _list(env, q="stripe")
    assert listed["items"] == [body]

    # 只有当前版本还有密文；它和新旧明文都不出现在任何读接口里
    ciphertext = await run_sql(
        env.database_url,
        "SELECT ciphertext_ref FROM workspace.agent_secret_versions"
        " WHERE secret_id = $1 AND ciphertext_ref IS NOT NULL",
        uuid.UUID(secret["id"]),
    )
    assert isinstance(ciphertext, str) and ciphertext
    accesses = await _get(env, f"{LIST}/accesses", secret_id=secret["id"])
    assert accesses.status_code == 200, accesses.text
    for response in (detail, await _get(env), await _get(env, q="Stripe"), accesses):
        for forbidden in (PLAINTEXT, ROTATED, ciphertext, "ciphertext", '"value"'):
            assert forbidden not in response.text, forbidden


async def test_detail_is_scoped_to_the_workspace(env: Env) -> None:
    secret = await _create(env, "OpenAI")
    path = f"{LIST}/{secret['id']}"
    me = await env.client.get("/api/v1/me")
    other = await add_workspace(env.database_url, uuid.UUID(me.json()["user"]["id"]), "另一个")
    hidden = await env.client.get(path, headers={"X-Workspace-Id": str(other)})
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "NOT_FOUND"
    assert PLAINTEXT not in hidden.text and "OpenAI" not in hidden.text
    # 另一个空间的列表与搜索也看不到它
    other_list = await env.client.get(
        LIST, params={"q": "OpenAI"}, headers={"X-Workspace-Id": str(other)}
    )
    assert other_list.json()["items"] == [] and other_list.json()["total"] == 0

    missing = await _get(env, f"{LIST}/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
    assert (await _get(env, f"{LIST}/not-a-uuid")).status_code == 422
    # 固定路径 /accesses 不会被当成 secret_id
    assert (await _get(env, f"{LIST}/accesses")).status_code == 200

    deleted = await env.client.delete(
        path, params={"version": secret["version"]}, headers=env.headers(write=True)
    )
    assert deleted.status_code == 204
    assert (await _get(env, path)).status_code == 404


async def test_only_the_owner_reads_the_catalog(
    make_client: ClientFactory, migrated_database_url: str
) -> None:
    async with make_client(**SETTINGS) as owner:
        setup = await owner.post("/api/v1/setup", json=OWNER)
        workspace = setup.json()["workspaces"][0]["id"]
        env = Env(owner, workspace, migrated_database_url)
        secret = await _create(env, "OpenAI")
        assert (await _get(env, f"{LIST}/{secret['id']}")).status_code == 200
        assert (await _list(env, q="open", kind="generic"))["total"] == 1

        for role in ("admin", "member", "viewer"):
            creds = {"email": f"{role}@example.com", "password": f"{role} password 123456"}
            user = await add_user(migrated_database_url, creds["email"], creds["password"])
            await add_member(migrated_database_url, uuid.UUID(workspace), user, role)
            async with make_client(**SETTINGS) as other:
                assert (await other.post("/api/v1/auth/login", json=creds)).status_code == 200
                headers = {"X-Workspace-Id": workspace, **csrf(other)}
                for path, params in (
                    (LIST, {}),
                    (LIST, {"q": "open", "kind": "generic"}),
                    (f"{LIST}/{secret['id']}", {}),
                    (f"{LIST}/{uuid.uuid4()}", {}),
                    (f"{LIST}/accesses", {"secret_id": secret["id"]}),
                ):
                    response = await other.get(path, params=params, headers=headers)
                    assert response.status_code == 403, (role, path, response.text)
                    assert "OpenAI" not in response.text


# ---------------------------------------------------------------- 批量读取 grants


@contextmanager
def _secret_statements(env: Env) -> Iterator[list[str]]:
    """记下读写密钥表的 SQL（登录、会话等其他查询不算）。"""
    engine = env.app.state.sessionmaker.kw["bind"].sync_engine
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "agent_secret" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_grants_are_loaded_in_one_batch(env: Env) -> None:
    instances = [await env.create_instance(name=f"实例 {i}") for i in range(3)]
    secrets_ = [await _create(env, f"key-{i}") for i in range(8)]
    for n, instance in enumerate(instances):
        await env.grant(
            instance["id"],
            [{"secret_id": s["id"], "ref_id": f"ref_{n}_{i}"} for i, s in enumerate(secrets_)],
        )

    with _secret_statements(env) as small:
        page = await _list(env, limit=1)
    assert len(page["items"]) == 1 and len(page["items"][0]["grants"]) == 3
    with _secret_statements(env) as large:
        page = await _list(env, limit=50)
    assert len(page["items"]) == 8
    assert all(len(item["grants"]) == 3 for item in page["items"])
    # 1 条和 8 条的查询次数一样：计数、分页、grants 各一次，没有逐个密钥查授权
    assert len(small) == len(large) == 3, (small, large)
    assert sum("agent_secret_grants" in s for s in large) == 1

    with _secret_statements(env) as detail:
        response = await _get(env, f"{LIST}/{secrets_[0]['id']}")
    assert response.status_code == 200 and len(response.json()["grants"]) == 3
    assert len(detail) == 2, detail
