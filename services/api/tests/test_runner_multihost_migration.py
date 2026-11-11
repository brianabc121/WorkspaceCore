"""多机迁移不丢弃旧环境与配置；有多机记录时禁止破坏性回退。"""

import uuid

import pytest
from alembic import command

from tests.helpers import alembic_config
from tests.test_runner_metrics_migration import _seed, run

pytestmark = pytest.mark.integration


def test_multihost_upgrade_downgrade_preserves_environment_and_refuses_multi_host(
    temp_database_url,
):
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0060")
    env_id = _seed(temp_database_url)
    command.upgrade(config, "0061")
    row = run(temp_database_url, "SELECT * FROM workspace.runner_environments WHERE id=$1", env_id)[
        0
    ]
    assert row["runtime_backend"] == "docker"
    assert row["mem_bytes"] == 100
    host_id = uuid.uuid4()
    run(
        temp_database_url,
        "INSERT INTO workspace.runner_hosts(id,workspace_id,name,token_hash,token_prefix) "
        "VALUES($1,$2,'second','second','mwr_second')",
        host_id,
        row["workspace_id"],
    )
    with pytest.raises(RuntimeError, match="不能安全回退"):
        command.downgrade(config, "0060")
    assert len(run(temp_database_url, "SELECT id FROM workspace.runner_hosts")) == 2
    assert run(
        temp_database_url, "SELECT id FROM workspace.runner_environments WHERE id=$1", env_id
    )
    run(temp_database_url, "DELETE FROM workspace.runner_hosts WHERE id=$1", host_id)
    command.downgrade(config, "0060")
    assert (
        run(
            temp_database_url,
            "SELECT mem_bytes FROM workspace.runner_environments WHERE id=$1",
            env_id,
        )[0]["mem_bytes"]
        == 100
    )
    command.upgrade(config, "0061")
