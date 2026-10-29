"""ORM 基类与约束命名规则。

所有自有表放在 `workspace` schema（docs/DEVELOPMENT_SPEC.md 第 5 节）。
"""

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
    # INSERT / UPDATE 时用 RETURNING 取回数据库生成的值（id、created_at、updated_at），
    # 否则之后访问这些属性会在异步会话里触发隐式加载并报错
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
