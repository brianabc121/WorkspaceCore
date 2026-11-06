"""邮箱按智能体分配（T-0132，docs/v2/MAIL_PLAN.md M4 修订）

- agent_mail_accounts：哪个 Gmail 邮箱分配给哪个智能体实例（管理员在 设置 → 邮件
  或实例卡片里勾选）。智能体的邮件工具只看得到分配给它的邮箱。
- mail_accounts.agents_allowed 删除：原来“允许智能体访问”是所有智能体一起开关。
  升级时把 agents_allowed = true 且没断开的邮箱分配给同一工作空间现有的全部实例，行为不变；
  之后新连接的邮箱默认不分配给任何智能体。

downgrade：重新加上 agents_allowed（有分配的邮箱为 true，其余 false），再删掉分配表。

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 复合外键要求邮箱与实例属于同一个工作空间（与 agent_secret_grants 相同的做法）
    op.create_unique_constraint(
        op.f("uq_mail_accounts_workspace_id_id"),
        "mail_accounts",
        ["workspace_id", "id"],
        schema="workspace",
    )
    op.create_table(
        "agent_mail_accounts",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("assigned_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            name=op.f("fk_agent_mail_accounts_workspace_id_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "account_id"],
            ["workspace.mail_accounts.workspace_id", "workspace.mail_accounts.id"],
            name=op.f("fk_agent_mail_accounts_workspace_id_account_id_mail_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["workspace.users.id"],
            name=op.f("fk_agent_mail_accounts_assigned_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("instance_id", "account_id", name=op.f("pk_agent_mail_accounts")),
        schema="workspace",
    )
    op.create_index(
        "ix_agent_mail_accounts_account",
        "agent_mail_accounts",
        ["account_id"],
        schema="workspace",
    )
    op.execute(
        "INSERT INTO workspace.agent_mail_accounts (workspace_id, instance_id, account_id)"
        " SELECT a.workspace_id, i.id, a.id FROM workspace.mail_accounts a"
        " JOIN workspace.agent_instances i ON i.workspace_id = a.workspace_id"
        " WHERE a.agents_allowed AND a.status <> 'disconnected'"
    )
    op.drop_column("mail_accounts", "agents_allowed", schema="workspace")


def downgrade() -> None:
    op.add_column(
        "mail_accounts",
        sa.Column("agents_allowed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        schema="workspace",
    )
    op.execute(
        "UPDATE workspace.mail_accounts a SET agents_allowed = EXISTS ("
        " SELECT 1 FROM workspace.agent_mail_accounts m WHERE m.account_id = a.id)"
    )
    op.drop_index(
        "ix_agent_mail_accounts_account", table_name="agent_mail_accounts", schema="workspace"
    )
    op.drop_table("agent_mail_accounts", schema="workspace")
    op.drop_constraint(
        op.f("uq_mail_accounts_workspace_id_id"), "mail_accounts", schema="workspace"
    )
