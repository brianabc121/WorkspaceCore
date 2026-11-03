"""OpenClaw 实例的 SSH 自动部署目标（V2 T-0089 B1）

owner 填写服务器地址、端口与账号后，工作台只读取对方的主机公钥并显示 SHA256 指纹；
owner 核对后确认，之后的连接只接受这把公钥。登录密码从不保存；安装时生成的专用私钥
（B2）以 `enc:v1:` 引用保存在 client_key_ref。每个实例最多一台目标服务器。
新建一张表，downgrade 删除即可回退。

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_ssh_targets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("host_key_algorithm", sa.Text(), nullable=False),
        sa.Column("host_key", sa.Text(), nullable=False),
        sa.Column("host_key_fingerprint", sa.Text(), nullable=False),
        sa.Column("host_key_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("host_key_confirmed_by", sa.UUID(), nullable=True),
        sa.Column("client_key_ref", sa.Text(), nullable=True),
        sa.Column("client_public_key", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name=op.f("ck_agent_ssh_targets_port")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["workspace.users.id"],
            name=op.f("fk_agent_ssh_targets_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["host_key_confirmed_by"],
            ["workspace.users.id"],
            name=op.f("fk_agent_ssh_targets_host_key_confirmed_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            name=op.f("fk_agent_ssh_targets_workspace_id_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_agent_ssh_targets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_ssh_targets")),
        sa.UniqueConstraint("instance_id", name=op.f("uq_agent_ssh_targets_instance_id")),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("agent_ssh_targets", schema="workspace")
