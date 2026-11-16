"""智能体服务器：记录最近一次登录成功时的连接延迟（T-0271）

- agent_ssh_targets 新增可空列 last_connection_latency_ms（毫秒，双精度）：
  测试连接（以及旧公钥退役）登录成功时，从同一条 SSH 连接读取内核测得的 TCP 往返时间写入；
  登录失败、换地址、换密码时清空。
  CHECK 约束：为空，或在 [0, 60000) 之内。
- 已有记录保持为空（还没有测过），不连服务器回填。

downgrade 删掉这一列与约束。

Revision ID: 0076
Revises: 0075
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workspace"
TABLE = "agent_ssh_targets"
COLUMN = "last_connection_latency_ms"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.Float(), nullable=True), schema=SCHEMA)
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_{COLUMN}"),
        TABLE,
        f"{COLUMN} IS NULL OR ({COLUMN} >= 0 AND {COLUMN} < 60000)",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(op.f(f"ck_{TABLE}_{COLUMN}"), TABLE, schema=SCHEMA, type_="check")
    op.drop_column(TABLE, COLUMN, schema=SCHEMA)
