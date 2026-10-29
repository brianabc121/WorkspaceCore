"""ORM 模型。所有自有表放在 `workspace` schema（docs/DEVELOPMENT_SPEC.md 第 5 节）。"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 固定约束命名，Alembic 自动生成的迁移才稳定、可读
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema="workspace", naming_convention=NAMING_CONVENTION)
