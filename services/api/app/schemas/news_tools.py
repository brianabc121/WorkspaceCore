"""智能体的新闻工具 news_*（T-0153，docs/v2/NEWS_PLAN.md 第 6 节）。

只读、不审批；只列给启用了新闻早报的工作空间的实例。工作台把它们转发给 TrendRadar MCP 的一部分
只读工具（核验记录第 4 节），参数在这里校验；`news_briefing` 读工作台自己的数据库。
标题、链接、来源名、摘要、早报正文都来自第三方，放在 `untrusted` 里。
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.news import FeedId, PlatformId

NEWS_LIMIT = 50


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewsLatestInput(_Input):
    platforms: list[PlatformId] = Field(
        default_factory=list, max_length=11, description="只看这些平台；不给就是全部"
    )
    limit: int = Field(default=20, ge=1, le=NEWS_LIMIT)


class NewsSearchInput(_Input):
    query: Annotated[str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)]
    days: int = Field(default=1, ge=1, le=30, description="最近几天（含今天）")
    include_rss: bool = Field(default=True, description="同时搜 RSS")
    limit: int = Field(default=20, ge=1, le=NEWS_LIMIT)


class NewsByDateInput(_Input):
    date_from: date = Field(description="开始日期（按早报设置的时区）")
    date_to: date | None = Field(default=None, description="结束日期；不给就是同一天，最多 31 天")
    platforms: list[PlatformId] = Field(default_factory=list, max_length=11)
    limit: int = Field(default=20, ge=1, le=NEWS_LIMIT)

    @model_validator(mode="after")
    def _range(self) -> "NewsByDateInput":
        end = self.date_to or self.date_from
        if end < self.date_from:
            raise ValueError("date_to 不能早于 date_from")
        if (end - self.date_from).days > 30:
            raise ValueError("日期范围最多 31 天")
        return self


class NewsTrendingInput(_Input):
    top_n: int = Field(default=10, ge=1, le=30)


class NewsRssInput(_Input):
    days: int = Field(default=1, ge=1, le=7)
    feeds: list[FeedId] = Field(default_factory=list, max_length=30, description="RSS 源的 ID")
    limit: int = Field(default=20, ge=1, le=NEWS_LIMIT)


class NewsRelatedInput(_Input):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
    days: int = Field(default=7, ge=1, le=30)
    limit: int = Field(default=10, ge=1, le=NEWS_LIMIT)


class NewsBriefingInput(_Input):
    day: date | None = Field(default=None, description="哪一天（按早报设置的时区）；不给就是今天")


class NewsItemText(BaseModel):
    title: str
    url: str
    source_name: str
    summary: str | None = None


class NewsToolItem(BaseModel):
    source_type: Literal["hotlist", "rss"]
    source_id: str = Field(description="平台 ID 或 RSS 源 ID")
    rank: int | None = Field(description="热榜排名")
    time: str | None
    untrusted: NewsItemText


class NewsListOutput(BaseModel):
    items: list[NewsToolItem]
    note: str | None = Field(default=None, description="没有数据等说明")


class NewsKeywordText(BaseModel):
    keyword: str


class NewsTrendingTopic(BaseModel):
    count: int = Field(description="出现次数")
    trend: str
    untrusted: NewsKeywordText


class NewsTrendingOutput(BaseModel):
    topics: list[NewsTrendingTopic]
    note: str | None = None


class NewsBriefingText(BaseModel):
    title: str
    content: str


class NewsBriefingItem(BaseModel):
    briefing_id: UUID
    local_date: date
    slot: str
    source: Literal["trendradar", "agent"]
    status: Literal["receiving", "complete", "pending", "failed"]
    untrusted: NewsBriefingText


class NewsBriefingOutput(BaseModel):
    briefing: NewsBriefingItem | None
    note: str | None = None


DESCRIPTIONS = {
    "news_latest": "最新一批热榜（知乎、微博、华尔街见闻等），可以只看某几个平台；每条带原文链接",
    "news_search": "按关键词搜最近几天的热榜（可同时搜 RSS），例如“最近一周关于某公司的报道”",
    "news_by_date": "某一天或某段日期的热榜",
    "news_trending": "当前热点话题统计（按早报设置的兴趣词）",
    "news_rss": "最近几天订阅的 RSS 条目",
    "news_related": "与一条新闻标题相关的其他报道",
    "news_briefing": "今天或某一天的新闻早报（工作台保存的完整正文）",
}
