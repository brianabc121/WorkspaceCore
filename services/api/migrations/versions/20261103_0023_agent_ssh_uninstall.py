"""OpenClaw 实例的 SSH 卸载任务（V2 T-0089 B3）

agent_ssh_jobs.kind 增加 'uninstall'：卸载（停止容器、删掉定时任务、可选删除目录）与安装共用任务表。
只放宽检查约束；downgrade 先删掉卸载任务的记录再恢复原约束（任务记录只是进度与日志）。

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_ssh_jobs_kind"), "agent_ssh_jobs", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_ssh_jobs_kind"),
        "agent_ssh_jobs",
        "kind IN ('install', 'uninstall')",
        schema="workspace",
    )


def downgrade() -> None:
    op.execute("DELETE FROM workspace.agent_ssh_jobs WHERE kind <> 'install'")
    op.drop_constraint(
        op.f("ck_agent_ssh_jobs_kind"), "agent_ssh_jobs", schema="workspace", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_ssh_jobs_kind"),
        "agent_ssh_jobs",
        "kind IN ('install')",
        schema="workspace",
    )
