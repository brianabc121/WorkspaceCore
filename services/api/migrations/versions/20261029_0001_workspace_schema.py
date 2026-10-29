"""创建 workspace schema 与 pgcrypto、pg_trgm 扩展

docs/DEVELOPMENT_SPEC.md 第 5.2 节：所有自有表放在 workspace schema；
pgcrypto 提供 gen_random_uuid() 等函数，pg_trgm 用于中文标题的模糊搜索（第 5.3 节）。
两个扩展在 PostgreSQL 13+ 都是 trusted 扩展，数据库 owner 无需超级用户即可创建。

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS workspace")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # 不使用 CASCADE：schema 里如果还有对象，宁可失败也不要静默删掉数据
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
    op.execute("DROP SCHEMA IF EXISTS workspace")
