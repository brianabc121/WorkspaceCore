"""迁移 0051 / 0052（首页改版，T-0163）。

- 0051：tasks.review_pending 默认 false；task_events 可以记 reviewed；
  downgrade 删掉 reviewed 动态和这一列
- 0052：只把从没改过的默认首页换成新布局；改过的保持原样；downgrade 换回；新布局与代码里的默认一致
"""

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from app.domain.dashboard_defaults import starter_widgets
from tests.helpers import alembic_config, asyncpg_dsn

pytestmark = pytest.mark.integration
VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _migration() -> Any:
    path = VERSIONS / "20261108_0052_home_layout.py"
    spec = importlib.util.spec_from_file_location("home_layout_0052", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _exec(url: str, sql: str, *args: Any) -> Any:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def run(url: str, sql: str, *args: Any) -> Any:
    return asyncio.run(_exec(url, sql, *args))


def test_new_layout_in_migration_matches_the_code() -> None:
    expected = [
        (w.widget_type, w.config.model_dump(mode="json"), w.layout.model_dump(mode="json"))
        for w in starter_widgets()
    ]
    assert expected == _migration().NEW_WIDGETS


def _widgets(url: str, dashboard: UUID) -> list[str]:
    rows = run(
        url,
        "SELECT widget_type FROM workspace.widgets WHERE dashboard_id = $1 ORDER BY widget_type",
        dashboard,
    )
    return [row["widget_type"] for row in rows]


def _add_workspace(url: str, name: str) -> UUID:
    user, workspace = uuid4(), uuid4()
    run(
        url,
        "INSERT INTO workspace.users(id,email,password_hash,display_name) "
        "VALUES($1,$2,'synthetic','Home')",
        user,
        f"{name}@example.test",
    )
    run(
        url,
        "INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,$2,$3)",
        workspace,
        name,
        user,
    )
    rows = run(
        url,
        "INSERT INTO workspace.dashboards(workspace_id,owner_id,title,is_default) "
        "VALUES($1,$2,'首页',true) RETURNING id",
        workspace,
        user,
    )
    dashboard: UUID = rows[0]["id"]
    for kind, config, layout in _migration().OLD_WIDGETS:
        run(
            url,
            "INSERT INTO workspace.widgets(dashboard_id,widget_type,config_json,layout_json) "
            "VALUES($1,$2,$3::jsonb,$4::jsonb)",
            dashboard,
            kind,
            json.dumps(config, ensure_ascii=False),
            json.dumps(layout),
        )
    return dashboard


def test_home_layout_replaces_only_untouched_homes(temp_database_url: str) -> None:
    url = temp_database_url
    config = alembic_config(url)
    command.upgrade(config, "0051")
    untouched = _add_workspace(url, "untouched")
    edited = _add_workspace(url, "edited")
    run(
        url,
        'UPDATE workspace.widgets SET config_json = \'{"text": "我的便签"}\'::jsonb '
        "WHERE dashboard_id = $1 AND widget_type = 'note'",
        edited,
    )
    old_types = sorted(kind for kind, _, _ in _migration().OLD_WIDGETS)
    new_types = sorted(kind for kind, _, _ in _migration().NEW_WIDGETS)

    command.upgrade(config, "0052")
    assert _widgets(url, untouched) == new_types
    assert _widgets(url, edited) == old_types
    versions = {
        row["id"]: row["version"]
        for row in run(url, "SELECT id, version FROM workspace.dashboards")
    }
    assert versions[untouched] == 2 and versions[edited] == 1

    command.downgrade(config, "0051")
    assert _widgets(url, untouched) == old_types
    assert _widgets(url, edited) == old_types


def test_task_review_column_and_event_kind(temp_database_url: str) -> None:
    url = temp_database_url
    config = alembic_config(url)
    command.upgrade(config, "0051")
    workspace_dashboard = _add_workspace(url, "review")
    workspace = run(
        url, "SELECT workspace_id FROM workspace.dashboards WHERE id = $1", workspace_dashboard
    )[0]["workspace_id"]
    project = run(
        url,
        "INSERT INTO workspace.projects(workspace_id,title) VALUES($1,'P') RETURNING id",
        workspace,
    )[0]["id"]
    task = run(
        url,
        "INSERT INTO workspace.tasks(workspace_id,project_id,title) VALUES($1,$2,'T') "
        "RETURNING id, review_pending",
        workspace,
        project,
    )[0]
    assert task["review_pending"] is False
    run(
        url,
        "INSERT INTO workspace.task_events(workspace_id,task_id,kind,actor_kind,actor_name) "
        "VALUES($1,$2,'reviewed','user','Home')",
        workspace,
        task["id"],
    )
    command.downgrade(config, "0050")
    columns = run(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'workspace' AND table_name = 'tasks' "
        "AND column_name = 'review_pending'",
    )
    assert columns == []
    kinds = run(url, "SELECT kind FROM workspace.task_events")
    assert kinds == []
    with pytest.raises(asyncpg.CheckViolationError):
        run(
            url,
            "INSERT INTO workspace.task_events(workspace_id,task_id,kind,actor_kind,actor_name) "
            "VALUES($1,$2,'reviewed','user','Home')",
            workspace,
            task["id"],
        )
