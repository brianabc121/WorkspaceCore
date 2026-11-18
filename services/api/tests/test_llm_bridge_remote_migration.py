"""迁移 0079（云端远程容器）：已有数据的默认值、升级 → 降级 → 再升级（T-0295）。

- 已有的容器服务器环境都算实例申请的（owner_kind=instance），不打“不自动归档”；
  已有的运行都是一次性执行（kind=exec），已确认偏移是 0。
- 模块建的环境必须带 owner_ref（数据库约束）。
- 降级删掉新列和两张模块表，已有的行原样保留；之后能再升级。
"""

from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.helpers import alembic_config
from tests.test_runner_metrics_migration import _seed, run

pytestmark = pytest.mark.integration

ENV_COLUMNS = ("owner_kind", "owner_ref", "owner_label", "no_auto_archive")
RUN_COLUMNS = ("kind", "owner_kind", "owner_ref", "acked_offset")
TABLES = ("llm_bridge_remote_containers", "llm_bridge_remote_procs")


def _columns(url: str, table: str) -> set[str]:
    rows = run(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'workspace' AND table_name = $1",
        table,
    )
    return {r["column_name"] for r in rows}


def _tables(url: str) -> set[str]:
    rows = run(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'workspace'")
    return {r["tablename"] for r in rows}


def test_migration_0079_defaults_existing_rows_and_round_trips(temp_database_url: str) -> None:
    url = temp_database_url
    config = alembic_config(url)
    command.upgrade(config, "0078")
    env = _seed(url)
    workspace = run(url, "SELECT workspace_id FROM workspace.runner_environments WHERE id=$1", env)[
        0
    ]["workspace_id"]
    run_id = uuid4()
    run(
        url,
        "INSERT INTO workspace.runner_runs(id,workspace_id,environment_id,command,timeout_seconds,"
        "status,exit_code,output,finished_at) "
        "VALUES($1,$2,$3,'echo hi',60,'succeeded',0,'hi',now())",
        run_id,
        workspace,
        env,
    )
    env_before = dict(run(url, "SELECT * FROM workspace.runner_environments WHERE id=$1", env)[0])
    run_before = dict(run(url, "SELECT * FROM workspace.runner_runs WHERE id=$1", run_id)[0])

    command.upgrade(config, "0079")
    assert set(ENV_COLUMNS) <= _columns(url, "runner_environments")
    assert set(RUN_COLUMNS) <= _columns(url, "runner_runs")
    assert "owner_kind" in _columns(url, "runner_templates")  # 模块的模板只有模块能改
    assert set(TABLES) <= _tables(url)
    env_row = run(url, "SELECT * FROM workspace.runner_environments WHERE id=$1", env)[0]
    assert env_row["owner_kind"] == "instance"
    assert env_row["owner_ref"] is None and env_row["owner_label"] is None
    assert env_row["no_auto_archive"] is False
    assert {k: env_row[k] for k in env_before} == env_before
    run_row = run(url, "SELECT * FROM workspace.runner_runs WHERE id=$1", run_id)[0]
    assert run_row["kind"] == "exec" and run_row["acked_offset"] == 0
    assert run_row["owner_kind"] is None and run_row["owner_ref"] is None
    assert {k: run_row[k] for k in run_before} == run_before

    # 模块建的环境必须写明是谁的
    with pytest.raises(asyncpg.CheckViolationError):
        run(
            url,
            "UPDATE workspace.runner_environments SET owner_kind='llm_bridge' WHERE id=$1",
            env,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        run(url, "UPDATE workspace.runner_runs SET kind='shell' WHERE id=$1", run_id)
    # 变成远程开发容器的样子，再降级：行还在，只是没有了归属
    run(
        url,
        "UPDATE workspace.runner_environments SET owner_kind='llm_bridge',owner_ref='b:c',"
        "owner_label='管理器 1',no_auto_archive=true WHERE id=$1",
        env,
    )
    run(
        url,
        "UPDATE workspace.runner_runs SET kind='proc',owner_kind='llm_bridge',owner_ref='rp_1',"
        "acked_offset=2 WHERE id=$1",
        run_id,
    )

    command.downgrade(config, "0078")
    assert not set(ENV_COLUMNS) & _columns(url, "runner_environments")
    assert not set(RUN_COLUMNS) & _columns(url, "runner_runs")
    assert "owner_kind" not in _columns(url, "runner_templates")
    assert not set(TABLES) & _tables(url)
    assert (
        dict(run(url, "SELECT * FROM workspace.runner_environments WHERE id=$1", env)[0])
        == env_before
    )
    assert (
        dict(run(url, "SELECT * FROM workspace.runner_runs WHERE id=$1", run_id)[0]) == run_before
    )

    command.upgrade(config, "0079")
    env_row = run(url, "SELECT * FROM workspace.runner_environments WHERE id=$1", env)[0]
    assert env_row["owner_kind"] == "instance" and env_row["no_auto_archive"] is False
    assert run(url, "SELECT kind FROM workspace.runner_runs WHERE id=$1", run_id)[0]["kind"] == (
        "exec"
    )
