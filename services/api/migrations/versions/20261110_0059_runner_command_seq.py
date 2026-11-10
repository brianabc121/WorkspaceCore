"""运行服务器指令的写入顺序（T-0213）

runner_commands.created_at 的默认值是 now()，也就是事务开始的时间：同一个事务里写的几条指令
（例如用到归档的环境时先写“重建”、再写“执行”）created_at 完全相同，下发时只按 created_at
排序，先后就由行在表里的位置决定，“执行”可能先下发、在容器还没重建时就执行，报“容器不存在”。

加一列 seq（自增，写入时由数据库生成），下发时按 created_at、seq 排序。只加列：已有的行由
数据库补上序号（它们之间的先后不重要，都早于以后写入的指令）。

downgrade 删掉这一列。

Revision ID: 0059
Revises: 0058
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runner_commands",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_column("runner_commands", "seq", schema="workspace")
