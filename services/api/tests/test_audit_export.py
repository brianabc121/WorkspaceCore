"""审计日志 CSV 导出（T-0197，契约 docs/v2/ai-admin-redesign/…-contract.md 第 5.2 节）。

覆盖：31 天与 10000 行上限（超出 422、绝不截断）、范围校验、与列表同样的筛选、
公式注入防护与 CSV 引号、IP / ip_hash / 凭据不出现、响应头、audit.export_generated 只记安全摘要。
"""

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.audit_support import (
    AuditWorld,
    add_named_user,
    all_pages,
    bulk_audit,
    count_audit,
    insert_audit,
    open_audit_world,
)
from tests.helpers import ClientFactory, add_member, run_sql

pytestmark = pytest.mark.integration

JAN = datetime(2026, 1, 1, tzinfo=UTC)
FEB = JAN + timedelta(days=31)
RANGE: dict[str, Any] = {"after": JAN.isoformat(), "before": FEB.isoformat()}
COLUMNS = [
    "id",
    "created_at_utc",
    "action",
    "action_label",
    "actor_id",
    "actor_label",
    "resource_type",
    "resource_id",
    "resource_current_name",
    "metadata_json",
]
EXPORT_EVENT = "audit.export_generated"


@pytest.fixture
async def world(
    make_client: ClientFactory, migrated_database_url: str
) -> AsyncIterator[AuditWorld]:
    async with open_audit_world(make_client, migrated_database_url) as opened:
        yield opened


def parse(response: httpx.Response) -> list[dict[str, str]]:
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    reader = csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline=""))
    header, *rows = list(reader)
    assert header == COLUMNS
    return [dict(zip(COLUMNS, row, strict=True)) for row in rows]


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ---------------------------------------------------------------- 行数与范围


async def test_exactly_10000_rows_export_and_10001_are_refused(world: AuditWorld) -> None:
    await bulk_audit(world.database_url, world.workspace, 10_000, start=JAN)
    # 范围外、别的工作空间、没有工作空间的记录不算
    await bulk_audit(world.database_url, world.workspace, 3, start=FEB)
    await bulk_audit(world.database_url, world.other_workspace, 5, start=JAN)
    await bulk_audit(world.database_url, None, 5, start=JAN)

    full = await world.export(world.owner, **RANGE)
    rows = parse(full)
    assert len(rows) == 10_000
    assert len({row["id"] for row in rows}) == 10_000
    assert rows[0]["resource_id"] == "9999"  # 新的在前
    assert rows[-1]["resource_id"] == "0"
    assert await count_audit(world.database_url, EXPORT_EVENT) == 1

    await bulk_audit(world.database_url, world.workspace, 1, start=FEB - timedelta(seconds=1))
    too_many = await world.export(world.owner, **RANGE)
    assert too_many.status_code == 422
    assert error_code(too_many) == "AUDIT_EXPORT_TOO_LARGE"
    assert too_many.json()["error"]["details"]["limit"] == 10_000
    assert "text/csv" not in too_many.headers["content-type"]
    # 失败的导出不写“已生成”
    assert await count_audit(world.database_url, EXPORT_EVENT) == 1

    # 缩小条件后可以导出
    narrowed = await world.export(world.owner, **RANGE, resource_type="bulk", q="999")
    assert {row["resource_id"] for row in parse(narrowed)} == {
        str(i) for i in range(10_000) if "999" in str(i)
    }


async def test_export_range_is_required_tz_aware_and_at_most_31_days(world: AuditWorld) -> None:
    exactly = await world.export(world.owner, **RANGE)
    assert exactly.status_code == 200
    offset = await world.export(
        world.owner, after="2026-01-01T08:00:00+08:00", before="2026-02-01T08:00:00+08:00"
    )
    assert offset.status_code == 200

    invalid: list[dict[str, Any]] = [
        {},
        {"after": JAN.isoformat()},
        {"before": FEB.isoformat()},
        {"after": "2026-01-01T00:00:00", "before": FEB.isoformat()},
        {"after": JAN.isoformat(), "before": "2026-02-01T00:00:00"},
        {"after": FEB.isoformat(), "before": JAN.isoformat()},
        {"after": JAN.isoformat(), "before": JAN.isoformat()},
        {"after": JAN.isoformat(), "before": (FEB + timedelta(seconds=1)).isoformat()},
        {**RANGE, "cursor": "abc"},
        {**RANGE, "limit": 10},
        {**RANGE, "actor_id": str(world.admin_id), "system_only": "true"},
        {**RANGE, "action": "a.b", "action_prefix": "a."},
    ]
    for params in invalid:
        response = await world.export(world.owner, **params)
        assert response.status_code == 422, params
        assert error_code(response) == "VALIDATION_FAILED", params
    assert await count_audit(world.database_url, EXPORT_EVENT) == 2


# ---------------------------------------------------------------- 与列表一致


async def test_export_rows_and_labels_match_the_filtered_list(world: AuditWorld) -> None:
    url, ws = world.database_url, world.workspace
    secret_id = await run_sql(
        url,
        "INSERT INTO workspace.agent_secrets (workspace_id, name) VALUES ($1, 'mail-token')"
        " RETURNING id",
        ws,
    )
    await bulk_audit(url, ws, 45, start=JAN, action="agents.ops.started", actor_id=world.admin_id)
    await bulk_audit(
        url, ws, 20, start=JAN + timedelta(days=2), action="bulk.row", actor_id=world.owner_id
    )
    await insert_audit(
        url,
        [
            {
                "workspace_id": ws,
                "actor_id": world.owner_id,
                "action": "agents.secret.rotated",
                "resource_type": "agent_secret",
                "resource_id": str(secret_id),
                "metadata": {"name": "mail-token", "value_version": 3},
                "created_at": JAN + timedelta(days=3),
            },
            {
                "workspace_id": ws,
                "action": "auth.login_failed",
                "metadata": {"reason": "invalid_password"},
                "created_at": JAN + timedelta(days=4),
            },
        ],
    )
    filters_list: list[dict[str, Any]] = [
        {"action_prefix": "agents."},
        {"actor_id": str(world.admin_id)},
        {"system_only": "true"},
        {"q": "mail-token"},
        {},
    ]
    for client in (world.owner, world.admin):
        for filters in filters_list:
            listed = await all_pages(world, client, **RANGE, **filters, limit=17)
            exported = parse(await world.export(client, **RANGE, **filters))
            assert [row["id"] for row in exported] == [item["id"] for item in listed], filters
            for row, item in zip(exported, listed, strict=True):
                assert row["action"] == item["action"]
                assert row["action_label"] == (item["action_label"] or "")
                assert row["actor_id"] == (item["actor_id"] or "")
                assert row["actor_label"] == (item["actor_label"] or "")
                assert row["resource_current_name"] == (item["resource_current_name"] or "")
                assert json.loads(row["metadata_json"]) == item["metadata"]
                created = datetime.fromisoformat(row["created_at_utc"].replace("Z", "+00:00"))
                assert created == datetime.fromisoformat(item["created_at"])
                assert row["created_at_utc"].endswith("Z")

    owner_rows = parse(await world.export(world.owner, **RANGE, q="mail-token"))
    admin_rows = parse(await world.export(world.admin, **RANGE, q="mail-token"))
    assert [row["resource_current_name"] for row in owner_rows] == ["mail-token"]
    # admin 不能借导出读 owner 专属的密钥名（记录里的 metadata 本来就有的名字照常导出）
    assert [row["resource_current_name"] for row in admin_rows] == [""]
    system = parse(await world.export(world.admin, **RANGE, system_only="true"))
    assert [row["actor_label"] for row in system] == ["系统"]


# ---------------------------------------------------------------- 单元格安全


async def test_cells_are_plain_text_and_formula_safe(world: AuditWorld) -> None:
    url, ws = world.database_url, world.workspace
    formula_user = await add_named_user(url, "formula@example.com", "=HYPERLINK(1)")
    await add_member(url, ws, formula_user, "member")
    project_id = await run_sql(
        url,
        "INSERT INTO workspace.projects (workspace_id, title) VALUES ($1, '+项目') RETURNING id",
        ws,
    )
    tricky = 'He said "hi", ok\nline2'
    cells = {
        "=1+2": "'=1+2",
        "+SUM(A1)": "'+SUM(A1)",
        "-2+3": "'-2+3",
        "@cmd": "'@cmd",
        "   =evil()": "'   =evil()",
        "\t=1": "'\t=1",
        "\r=1": "'\r=1",
        " \t@x": "' \t@x",
        tricky: tricky,
        "plain": "plain",
    }
    rows: list[dict[str, Any]] = [
        {
            "workspace_id": ws,
            "actor_id": world.owner_id,
            "action": "custom.cell",
            "resource_type": "mystery",
            "resource_id": raw,
            "metadata": {"name": raw},
            "created_at": JAN + timedelta(minutes=index),
        }
        for index, raw in enumerate(cells)
    ]
    rows.append(
        {
            "workspace_id": ws,
            "actor_id": formula_user,
            "action": "=cmd|' /C calc'!A0",
            "resource_type": "project",
            "resource_id": str(project_id),
            "created_at": JAN + timedelta(hours=5),
        }
    )
    ids = await insert_audit(url, rows)
    exported = {row["id"]: row for row in parse(await world.export(world.owner, **RANGE))}

    for entry, (raw, expected) in zip(ids, cells.items(), strict=False):
        row = exported[str(entry)]
        assert row["resource_id"] == expected, repr(raw)
        assert json.loads(row["metadata_json"]) == {"name": raw}
    last = exported[str(ids[-1])]
    assert last["action"] == "'=cmd|' /C calc'!A0"
    assert last["action_label"] == ""
    assert last["actor_label"] == "'=HYPERLINK(1)"
    assert last["resource_current_name"] == "'+项目"
    # 每个单元格去掉前导空白后都不以公式字符开头
    for row in exported.values():
        for value in row.values():
            assert not value.lstrip(" ").startswith(("=", "+", "-", "@", "\t", "\r")), value


async def test_csv_never_contains_ip_hash_ip_or_credentials(world: AuditWorld) -> None:
    secrets_in_metadata = {
        "name": "visible-name",
        "client_ip": "203.0.113.9",
        "ip": "192.0.2.1",
        "nested": {
            "source_ip": "198.51.100.7",
            "ip_hash": "deadbeef" * 8,
            "api_token": "tok-SECRET-123",
            "Authorization": "Bearer abc.def",
            "items": [{"password": "hunter2-SECRET", "keep": "ok-keep"}],
        },
        "ciphertext_ref": "vault://ref-SECRET",
    }
    [entry] = await insert_audit(
        world.database_url,
        [
            {
                "workspace_id": world.workspace,
                "actor_id": world.owner_id,
                "action": "legacy.event",
                "metadata": secrets_in_metadata,
                "ip_hash": "cafebabe" * 8,
                "created_at": JAN + timedelta(days=1),
            }
        ],
    )
    response = await world.export(world.owner, **RANGE)
    [row] = [row for row in parse(response) if row["id"] == str(entry)]
    assert json.loads(row["metadata_json"]) == {
        "name": "visible-name",
        "nested": {"items": [{"keep": "ok-keep"}]},
    }
    text = response.content.decode("utf-8-sig")
    for leaked in (
        "203.0.113.9",
        "192.0.2.1",
        "198.51.100.7",
        "deadbeef",
        "cafebabe",
        "tok-SECRET-123",
        "hunter2-SECRET",
        "Bearer abc.def",
        "vault://ref-SECRET",
        "ip_hash",
        "client_ip",
        "source_ip",
        "api_token",
        "password",
        "[REDACTED]",
    ):
        assert leaked not in text, leaked


# ---------------------------------------------------------------- 响应头与审计事件


async def test_response_headers(world: AuditWorld) -> None:
    response = await world.export(world.admin, **RANGE)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="audit-logs-20260101-20260201.csv"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert parse(response) == []


async def test_export_records_a_safe_event_after_its_cutoff(world: AuditWorld) -> None:
    now = datetime.now(UTC)
    window: dict[str, Any] = {
        "after": (now - timedelta(days=1)).isoformat(),
        "before": (now + timedelta(days=1)).isoformat(),
    }
    typed_q = "secret-search-term"
    filtered = await world.export(
        world.owner,
        **RANGE,
        q=typed_q,
        action_prefix="agents.",
        actor_id=str(world.admin_id),
    )
    assert filtered.status_code == 200

    event = await run_sql(
        world.database_url,
        "SELECT json_build_object('actor_id', actor_id, 'resource_type', resource_type,"
        " 'resource_id', resource_id, 'metadata', metadata_redacted_json, 'ws', workspace_id)"
        " FROM workspace.audit_logs WHERE action = $1",
        EXPORT_EVENT,
    )
    recorded = json.loads(event)
    assert recorded["actor_id"] == str(world.owner_id)
    assert recorded["ws"] == str(world.workspace)
    metadata = recorded["metadata"]
    assert metadata["after"] == "2026-01-01T00:00:00+00:00"
    assert metadata["before"] == "2026-02-01T00:00:00+00:00"
    assert metadata["row_count"] == 0
    assert metadata["filters"] == {
        "actor_id": str(world.admin_id),
        "system_only": False,
        "action": None,
        "action_prefix": "agents.",
        "resource_type": None,
        "q": True,
    }
    assert typed_q not in event

    # 列表、详情与刷新不写审计
    before_reads = await count_audit(world.database_url)
    listed = await world.list(world.owner, **window)
    first_id = listed.json()["items"][0]["id"]
    await world.detail(world.owner, uuid.UUID(first_id))
    await world.list(world.admin)
    assert await count_audit(world.database_url) == before_reads

    # “已生成”事件在本次导出的截止点之后：这一次导出不含它自己，下一次才含
    first = parse(await world.export(world.owner, **window))
    assert [row["action"] for row in first].count(EXPORT_EVENT) == 1  # 上面那次筛选导出
    second = parse(await world.export(world.owner, **window))
    new_ids = {row["id"] for row in second} - {row["id"] for row in first}
    assert len(second) == len(first) + 1
    [new] = [row for row in second if row["id"] in new_ids]
    assert new["action"] == EXPORT_EVENT
    assert new["action_label"] == "导出审计日志"
    assert json.loads(new["metadata_json"])["row_count"] == len(first)
    for row in second:
        assert "secret-search-term" not in row["metadata_json"]


async def test_only_owner_and_admin_can_export(world: AuditWorld) -> None:
    assert (await world.export(world.admin, **RANGE)).status_code == 200
    for client in (world.member, world.viewer):
        response = await world.export(client, **RANGE)
        assert response.status_code == 403
        assert error_code(response) == "FORBIDDEN"
    outsider = await world.export(world.outsider, **RANGE)
    assert outsider.status_code == 404
    # outsider 在自己的工作空间里可以导出，但只有自己的记录
    own = parse(await world.export(world.outsider, world.other_workspace, **RANGE))
    assert own == []
