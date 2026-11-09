"""容器服务器：环境的最新 CPU 读数与逐项上报时间（T-0185，加法迁移）

依据 docs/v2/container-server-redesign/ 的指标与接口约定第 4 节、
docs/decisions/2026-11-09-runner-container-metrics.md：

- runner_environments.cpu_used_cores / cpu_window_seconds：runner-agent 差分出的
  最近一个窗口的平均核数与窗口秒数，两者同时有值或同时为空；
- cpu_reported_at / mem_reported_at / disk_reported_at：平台收到对应读数的服务端时间。

全部可为空，旧记录不回填（旧的 mem_bytes / disk_bytes 没有可核实的上报时间，
页面显示为“已过期、时间未知”）。
不建历史表。downgrade 只删新列与约束，旧的 mem_bytes / disk_bytes 不动。

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "runner_environments"
CHECKS = {
    "cpu_used_cores": "cpu_used_cores IS NULL OR cpu_used_cores >= 0",
    "cpu_window_seconds": (
        "cpu_window_seconds IS NULL OR (cpu_window_seconds >= 1 AND cpu_window_seconds <= 180)"
    ),
    "cpu_pair": "(cpu_used_cores IS NULL) = (cpu_window_seconds IS NULL)",
}


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("cpu_used_cores", sa.Float()), schema="workspace")
    op.add_column(TABLE, sa.Column("cpu_window_seconds", sa.Float()), schema="workspace")
    for name in ("cpu_reported_at", "mem_reported_at", "disk_reported_at"):
        op.add_column(TABLE, sa.Column(name, sa.DateTime(timezone=True)), schema="workspace")
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, condition, schema="workspace")


def downgrade() -> None:
    for name in CHECKS:
        op.drop_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, schema="workspace", type_="check")
    for name in (
        "disk_reported_at",
        "mem_reported_at",
        "cpu_reported_at",
        "cpu_window_seconds",
        "cpu_used_cores",
    ):
        op.drop_column(TABLE, name, schema="workspace")
