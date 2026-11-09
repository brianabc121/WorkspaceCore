"""OpenClaw 服务器的登录密码加密保存与连接检测（T-0178，加法迁移）

依据 docs/v2/agent-servers-redesign/ 的接口与迁移约定第 2 节、
docs/decisions/2026-11-09-agent-server-password-auth.md：

- agent_ssh_targets.login_password_ref：CredentialBox 密文引用（用途 agent_server_password:<id>）。
  旧记录为 null（旧密码从未保存，不能从专用私钥还原），提示补录；
- credential_revision：设置 / 替换密码加 1（默认 0；主密钥轮换不加）；
- last_connection_at / last_connection_reason：最近一次显式连接检测（白名单原因）；
- legacy_key_cleanup_state：旧专用公钥的退役状态。已有 client_key_ref 或 client_public_key 的
  旧记录回填 pending，其余 not_needed。本迁移不清空、不删除旧私钥 / 公钥列
  （只在显式退役成功后清空）；
- agent_deployments.runtime_installed：部署成功 true、卸载容器保留目录 false；旧记录 null（未知），
  不凭 installed_at 推断。

downgrade 只删新列与约束；已经主动退役的远端公钥、已清理的私钥不会因此恢复
（降级不是连接迁移的撤销按钮）。

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASONS = "('connected', 'auth_failed', 'unreachable', 'host_key_changed', 'credential_unreadable')"
CLEANUP_STATES = "('not_needed', 'pending', 'done', 'manual_required')"


def upgrade() -> None:
    op.add_column(
        "agent_ssh_targets", sa.Column("login_password_ref", sa.Text()), schema="workspace"
    )
    op.add_column(
        "agent_ssh_targets",
        sa.Column("credential_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        schema="workspace",
    )
    op.add_column(
        "agent_ssh_targets",
        sa.Column("last_connection_at", sa.DateTime(timezone=True)),
        schema="workspace",
    )
    op.add_column(
        "agent_ssh_targets", sa.Column("last_connection_reason", sa.Text()), schema="workspace"
    )
    op.add_column(
        "agent_ssh_targets",
        sa.Column(
            "legacy_key_cleanup_state",
            sa.Text(),
            server_default=sa.text("'not_needed'"),
            nullable=False,
        ),
        schema="workspace",
    )
    # 有历史专用公钥 / 私钥的服务器：等补录密码后定向退役
    op.execute(
        "UPDATE workspace.agent_ssh_targets SET legacy_key_cleanup_state = 'pending'"
        " WHERE client_key_ref IS NOT NULL OR client_public_key IS NOT NULL"
    )
    op.create_check_constraint(
        op.f("ck_agent_ssh_targets_credential_revision"),
        "agent_ssh_targets",
        "credential_revision >= 0",
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_agent_ssh_targets_last_connection_reason"),
        "agent_ssh_targets",
        f"last_connection_reason IS NULL OR last_connection_reason IN {REASONS}",
        schema="workspace",
    )
    op.create_check_constraint(
        op.f("ck_agent_ssh_targets_legacy_key_cleanup_state"),
        "agent_ssh_targets",
        f"legacy_key_cleanup_state IN {CLEANUP_STATES}",
        schema="workspace",
    )
    op.add_column(
        "agent_deployments", sa.Column("runtime_installed", sa.Boolean()), schema="workspace"
    )


def downgrade() -> None:
    op.drop_column("agent_deployments", "runtime_installed", schema="workspace")
    for name in ("legacy_key_cleanup_state", "last_connection_reason", "credential_revision"):
        op.drop_constraint(
            op.f(f"ck_agent_ssh_targets_{name}"),
            "agent_ssh_targets",
            type_="check",
            schema="workspace",
        )
    for column in (
        "legacy_key_cleanup_state",
        "last_connection_reason",
        "last_connection_at",
        "credential_revision",
        "login_password_ref",
    ):
        op.drop_column("agent_ssh_targets", column, schema="workspace")
