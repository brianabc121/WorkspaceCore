"""迁移 0053（任务状态闭环，T-0166）。

- 旧任务 completion_seq = 0（不变成待查看）；0051 的 review_pending 为真的记为第 1 轮
- 阻塞两列成对、原因长度、task_result_reads 的跨空间外键、成员离开时随之删除
- downgrade 回填 review_pending（已完成、轮次 > 0、没人看过），删除新表、新列与阻塞动态；再 upgrade
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from tests.helpers import alembic_config, asyncpg_dsn

pytestmark = pytest.mark.integration


async def _exec(url: str, sql: str, *args: Any) -> Any:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def run(url: str, sql: str, *args: Any) -> Any:
    return asyncio.run(_exec(url, sql, *args))


def _workspace(url: str, name: str) -> tuple[UUID, UUID, UUID]:
    """(用户, 工作空间, 项目)"""
    user, workspace = uuid4(), uuid4()
    run(
        url,
        "INSERT INTO workspace.users(id,email,password_hash,display_name) "
        "VALUES($1,$2,'synthetic','State')",
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
    run(
        url,
        "INSERT INTO workspace.workspace_members(workspace_id,user_id,role) VALUES($1,$2,'owner')",
        workspace,
        user,
    )
    project = run(
        url,
        "INSERT INTO workspace.projects(workspace_id,title) VALUES($1,'P') RETURNING id",
        workspace,
    )[0]["id"]
    return user, workspace, project


def _task(url: str, workspace: UUID, project: UUID, title: str, **cols: Any) -> UUID:
    names = ["workspace_id", "project_id", "title", *cols]
    values = [workspace, project, title, *cols.values()]
    marks = ",".join(f"${i + 1}" for i in range(len(values)))
    row = run(
        url,
        f"INSERT INTO workspace.tasks({','.join(names)}) VALUES({marks}) RETURNING id",
        *values,
    )
    task: UUID = row[0]["id"]
    return task


def test_upgrade_keeps_old_tasks_and_downgrade_restores(temp_database_url: str) -> None:
    url = temp_database_url
    config = alembic_config(url)
    command.upgrade(config, "0052")
    user, workspace, project = _workspace(url, "states")
    old_done = _task(url, workspace, project, "旧的已完成", done=True)
    pending = _task(url, workspace, project, "等人确认", done=True, review_pending=True)
    open_task = _task(url, workspace, project, "未完成")

    command.upgrade(config, "0053")
    seqs = {
        row["id"]: row["completion_seq"]
        for row in run(url, "SELECT id, completion_seq FROM workspace.tasks")
    }
    assert seqs == {old_done: 0, pending: 1, open_task: 0}
    columns = run(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'workspace' AND table_name = 'tasks' "
        "AND column_name = 'review_pending'",
    )
    assert columns == []

    # 阻塞两列成对、原因不超过 2000 字
    with pytest.raises(asyncpg.CheckViolationError):
        run(url, "UPDATE workspace.tasks SET blocked_at = now() WHERE id = $1", open_task)
    with pytest.raises(asyncpg.CheckViolationError):
        run(
            url,
            "UPDATE workspace.tasks SET blocked_at = now(), blocked_reason = $2 WHERE id = $1",
            open_task,
            "长" * 2001,
        )
    run(
        url,
        "UPDATE workspace.tasks SET blocked_at = now(), blocked_reason = '等', blocked_by = $2 "
        "WHERE id = $1",
        open_task,
        user,
    )
    run(
        url,
        "INSERT INTO workspace.task_events(workspace_id,task_id,kind,actor_kind,actor_name) "
        "VALUES($1,$2,'blocked','user','State')",
        workspace,
        open_task,
    )

    # 查看记录：任务与成员必须在同一个工作空间
    _, other_workspace, _ = _workspace(url, "other")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        run(
            url,
            "INSERT INTO workspace.task_result_reads(task_id,user_id,workspace_id,"
            "seen_completion_seq) VALUES($1,$2,$3,1)",
            pending,
            user,
            other_workspace,
        )
    run(
        url,
        "INSERT INTO workspace.task_result_reads(task_id,user_id,workspace_id,seen_completion_seq) "
        "VALUES($1,$2,$3,1)",
        pending,
        user,
        workspace,
    )
    # 成员离开工作空间：他的查看记录随之删除
    run(
        url,
        "DELETE FROM workspace.workspace_members WHERE workspace_id = $1 AND user_id = $2",
        workspace,
        user,
    )
    assert run(url, "SELECT count(*) AS n FROM workspace.task_result_reads")[0]["n"] == 0

    command.downgrade(config, "0052")
    restored = {
        row["id"]: row["review_pending"]
        for row in run(url, "SELECT id, review_pending FROM workspace.tasks")
    }
    assert restored == {old_done: False, pending: True, open_task: False}
    assert run(url, "SELECT kind FROM workspace.task_events WHERE kind = 'blocked'") == []
    tables = run(
        url,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'workspace' AND table_name = 'task_result_reads'",
    )
    assert tables == []

    command.upgrade(config, "head")
    again = {
        row["id"]: row["completion_seq"]
        for row in run(url, "SELECT id, completion_seq FROM workspace.tasks")
    }
    assert again == {old_done: 0, pending: 1, open_task: 0}
