"""跨资源搜索索引（docs/DEVELOPMENT_SPEC.md 第 5.1 节第 12 项、第 5.3 节）。

约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 8 节。
这张表只由数据库触发器维护（迁移 0008）：external_resources、bookmarks、calendar_events 的
任何写入、tombstone 或删除都会同步更新它，应用代码只读不写。
只索引授权可得的文本（标题、备注、说明、标签），不含 Token 与外部文档正文。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Computed, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SearchDocument(Base):
    __tablename__ = "search_documents"
    __table_args__ = (
        Index("uq_search_documents_resource", "resource_type", "resource_id", unique=True),
        Index("ix_search_documents_workspace_type", "workspace_id", "resource_type"),
        Index("ix_search_documents_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_search_documents_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    # task / project / bookmark / event / repo / issue / pull
    resource_type: Mapped[str] = mapped_column(Text)
    # 该资源在自己接口里的 UUID（投影行、收藏、日程）
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(Text)  # vikunja / github / affine / local
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, server_default=text("''"))
    context: Mapped[str | None] = mapped_column(Text)
    # 收藏的链接在查询时由知识来源的地址拼出（改地址后不必重建索引），这里为 NULL
    source_url: Mapped[str | None] = mapped_column(Text)
    # 私有收藏的创建者；工作空间可见的资源为 NULL
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    visibility_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("""'{"scope": "workspace"}'::jsonb""")
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english'::regconfig, title || ' ' || body)", persisted=True),
    )
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
