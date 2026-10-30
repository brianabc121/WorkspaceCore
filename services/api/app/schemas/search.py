"""跨资源搜索（docs/DEVELOPMENT_SPEC.md 第 5.1 节第 12 项、第 5.3 节）。

约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 8 节：
只返回当前用户在当前工作空间
可见、且未删除的资源（A10、A11）；中文用 ILIKE + pg_trgm，不分词，相关性有限。
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import Page

SearchType = Literal["task", "project", "bookmark", "event", "repo", "issue", "pull"]
SearchProvider = Literal["vikunja", "github", "affine", "local"]
SearchQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SearchResultOut(BaseModel):
    resource_type: SearchType
    id: UUID = Field(description="该资源在自己接口里的 UUID")
    title: str
    context: str | None = Field(
        description="辅助信息：任务的项目名、Issue / PR 的仓库名、事件的日期等"
    )
    provider: SearchProvider
    source_url: str | None = Field(description="外部原生界面的链接；本地日历事件为 null")
    updated_at: datetime | None
    match: Literal["title", "text"] = Field(
        description="title：标题命中；text：只在备注、说明等正文命中"
    )


class SearchPage(Page[SearchResultOut]):
    """total_estimate 恒为 null。"""
