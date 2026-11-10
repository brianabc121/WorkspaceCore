"""中转站管理：删除旧版兼容字段（T-0209）

人类 2026-11-10 确认工作台还没有在生产中运行，不需要旧版本升级路径：

- 删掉 model_connections.migration_state（及其检查约束）与 legacy_source_id
  （只给旧密钥库导入用，导入命令已删除）；
- agent_model_bindings.apply_state 不再有 legacy_active（实例“旧配置”状态）；
  万一测试库里还有这个值，先改成 pending（重新下发主模型）。

downgrade 把列和约束按 0056 的定义加回来（migration_state 一律回到 native）。

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_STATE_VALUES = (
    "native",
    "needs_configuration",
    "needs_classification",
    "managed",
    "non_model",
)
APPLY_STATE_VALUES = (
    "pending",
    "applying",
    "applied",
    "failed",
    "unknown",
    "manual_required",
)
OLD_APPLY_STATE_VALUES = ("legacy_active", *APPLY_STATE_VALUES)


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_model_connections_migration_state"),
        "model_connections",
        schema="workspace",
        type_="check",
    )
    op.drop_column("model_connections", "legacy_source_id", schema="workspace")
    op.drop_column("model_connections", "migration_state", schema="workspace")

    op.execute(
        "UPDATE workspace.agent_model_bindings SET apply_state = 'pending' "
        "WHERE apply_state = 'legacy_active'"
    )
    op.drop_constraint(
        op.f("ck_agent_model_bindings_apply_state"),
        "agent_model_bindings",
        schema="workspace",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_model_bindings_apply_state"),
        "agent_model_bindings",
        f"apply_state IN {_in(APPLY_STATE_VALUES)}",
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_model_bindings_apply_state"),
        "agent_model_bindings",
        schema="workspace",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_model_bindings_apply_state"),
        "agent_model_bindings",
        f"apply_state IN {_in(OLD_APPLY_STATE_VALUES)}",
        schema="workspace",
    )

    op.add_column(
        "model_connections",
        sa.Column("migration_state", sa.Text(), server_default=sa.text("'native'"), nullable=False),
        schema="workspace",
    )
    op.add_column(
        "model_connections",
        sa.Column("legacy_source_id", sa.UUID(), nullable=True),
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_model_connections_migration_state"),
        "model_connections",
        f"migration_state IN {_in(MIGRATION_STATE_VALUES)}",
        schema="workspace",
    )
