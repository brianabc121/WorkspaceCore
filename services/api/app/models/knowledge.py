"""知识入口：AFFiNE 外部地址（知识来源）、文档收藏与跨系统关联（规范第 5.1 节第 11 项）。

约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 3、4 节。收藏只保存链接里
来源地址之后的部分（suffix），完整链接 = 来源的 base_url + suffix，改来源地址后链接自动跟着改。
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KnowledgeSource(Base):
    """工作空间配置的 AFFiNE 外部地址；工作台从不访问它。V1 每个工作空间最多 1 个（服务层保证）。"""

    __tablename__ = "knowledge_sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Bookmark(Base):
    """AFFiNE 文档收藏：只有链接与手工元数据，没有文档正文。"""

    __tablename__ = "bookmarks"
    __table_args__ = (
        CheckConstraint("visibility IN ('workspace', 'private')", name="visibility"),
        CheckConstraint("link_kind IN ('workspace_doc', 'share')", name="link_kind"),
        # 去重（契约第 4.3 节）：工作空间可见的收藏每篇文档 1 条；私有收藏每个用户每篇文档 1 条
        Index(
            "uq_bookmarks_workspace_doc",
            "source_id",
            "affine_workspace_id",
            "doc_id",
            unique=True,
            postgresql_where=text("visibility = 'workspace'"),
        ),
        Index(
            "uq_bookmarks_private_doc",
            "source_id",
            "affine_workspace_id",
            "doc_id",
            "owner_id",
            unique=True,
            postgresql_where=text("visibility = 'private'"),
        ),
        Index("ix_bookmarks_workspace_updated", "workspace_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, server_default=text("''"))
    link_kind: Mapped[str] = mapped_column(Text)
    affine_workspace_id: Mapped[str] = mapped_column(Text)
    doc_id: Mapped[str] = mapped_column(Text)
    # 来源地址之后的部分：/workspace/<ws>/<doc> 或 /share/<ws>/<doc>，加上保留的查询参数
    suffix: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text, server_default=text("'workspace'"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class ResourceLink(Base):
    """跨系统关联：收藏（source）→ Vikunja 项目 / 任务的投影（target）。

    任何一方删除时级联删除；目标写 tombstone 后读取时排除，页面不留僵尸引用（A10）。
    """

    __tablename__ = "resource_links"
    __table_args__ = (
        Index("uq_resource_links_pair", "source_resource_id", "target_resource_id", unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    source_resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.bookmarks.id", ondelete="CASCADE")
    )
    target_resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.external_resources.id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[str] = mapped_column(Text, server_default=text("'related'"))
    created_at: Mapped[datetime] = _created_at()
