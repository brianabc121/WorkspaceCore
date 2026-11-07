"""智能体的新闻工具 news_*（T-0153，docs/v2/NEWS_PLAN.md 第 6 节）。

只读、不审批：经 MCP 读工具的通用流程调用（app/domain/mcp_tools.py 的 `_read`，照样记执行记录、
按实例限流、检查角色）。只有启用了早报、部署了 TrendRadar 的工作空间才列出和接受这些工具。
转发给 TrendRadar MCP 的只是白名单里的只读工具（app/integrations/trendradar.py）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.domain.ai_tools import ToolCallError
from app.domain.news import NewsService, parse_items
from app.integrations.base import ConnectorError
from app.models import NewsBriefing, NewsSettings
from app.schemas.news_tools import (
    NewsBriefingInput,
    NewsBriefingItem,
    NewsBriefingOutput,
    NewsBriefingText,
    NewsByDateInput,
    NewsItemText,
    NewsKeywordText,
    NewsLatestInput,
    NewsListOutput,
    NewsRelatedInput,
    NewsRssInput,
    NewsSearchInput,
    NewsToolItem,
    NewsTrendingInput,
    NewsTrendingOutput,
    NewsTrendingTopic,
)

if TYPE_CHECKING:  # mcp_read_tools 导入本模块的 HANDLERS
    from app.domain.mcp_read_tools import ReadCall

_NO_DATA = "没有找到数据（TrendRadar 每 30 分钟抓取一次，刚启用时可能还没有）"


async def news_enabled(db: Any, settings: Any, workspace_id: Any) -> bool:
    """列工具时用：部署了 TrendRadar 且这个工作空间启用了早报。"""
    if not settings.trendradar_mcp_url:
        return False
    enabled = await db.scalar(
        select(NewsSettings.enabled).where(NewsSettings.workspace_id == workspace_id)
    )
    return bool(enabled)


async def _setup(call: ReadCall) -> tuple[NewsService, NewsSettings]:
    service = NewsService(call.db, call.settings, call.runtime, call.redis)
    row = await service.row(call.access.workspace.id)
    if not service.deployed or row is None or not row.enabled:
        raise ToolCallError("NOT_FOUND", "这个工作空间没有启用新闻早报")
    return service, row


def _item(raw: dict[str, Any]) -> NewsToolItem:
    return NewsToolItem(
        source_type=raw["source_type"],
        source_id=raw["source_id"],
        rank=raw["rank"],
        time=raw["time"],
        untrusted=NewsItemText(
            title=raw["title"],
            url=raw["url"],
            source_name=raw["source_name"],
            summary=raw["summary"],
        ),
    )


async def _payload(service: NewsService, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """None 表示没有数据。"""
    try:
        return await service.client().call(tool, args)
    except ConnectorError as error:
        if error.kind == "NOT_FOUND":
            return None
        if error.kind == "VALIDATION_FAILED":
            raise ToolCallError("VALIDATION_FAILED", str(error)) from error
        raise ToolCallError("EXTERNAL_UNAVAILABLE", "TrendRadar 暂时不可用，稍后再试") from error


async def _list(
    service: NewsService, tool: str, args: dict[str, Any], limit: int, *, rss: bool = False
) -> NewsListOutput:
    payload = await _payload(service, tool, args)
    if payload is None:
        return NewsListOutput(items=[], hint=_NO_DATA)
    items = [_item(i) for i in parse_items(payload, rss=rss)][:limit]
    return NewsListOutput(items=items, hint=None if items else _NO_DATA)


def local_today(zone: str) -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(zone)).date()


def _range(call: ReadCall, days: int) -> dict[str, str]:
    today = local_today(call.settings.news_timezone)
    return {"start": (today - timedelta(days=days - 1)).isoformat(), "end": today.isoformat()}


async def news_latest(call: ReadCall, args: NewsLatestInput) -> NewsListOutput:
    service, _ = await _setup(call)
    params: dict[str, Any] = {"limit": args.limit, "include_url": True}
    if args.platforms:
        params["platforms"] = list(args.platforms)
    return await _list(service, "get_latest_news", params, args.limit)


async def news_search(call: ReadCall, args: NewsSearchInput) -> NewsListOutput:
    service, _ = await _setup(call)
    params = {
        "query": args.query,
        "date_range": _range(call, args.days),
        "limit": args.limit,
        "include_url": True,
    }
    found = await _list(service, "search_news", params, args.limit)
    if not args.include_rss:
        return found
    # search_news 在热榜没有命中时不带 RSS（MCP 4.1.1 实测），RSS 单独用 search_rss 查
    rss_params = {
        "keyword": args.query,
        "days": args.days,
        "limit": args.limit,
        "include_summary": True,
    }
    rss = await _list(service, "search_rss", rss_params, args.limit, rss=True)
    items = found.items + rss.items
    return NewsListOutput(items=items, hint=None if items else _NO_DATA)


async def news_by_date(call: ReadCall, args: NewsByDateInput) -> NewsListOutput:
    service, _ = await _setup(call)
    end = args.date_to or args.date_from
    params: dict[str, Any] = {
        "date_range": {"start": args.date_from.isoformat(), "end": end.isoformat()},
        "limit": args.limit,
        "include_url": True,
    }
    if args.platforms:
        params["platforms"] = list(args.platforms)
    return await _list(service, "get_news_by_date", params, args.limit)


async def news_trending(call: ReadCall, args: NewsTrendingInput) -> NewsTrendingOutput:
    service, _ = await _setup(call)
    payload = await _payload(service, "get_trending_topics", {"top_n": args.top_n})
    raw_topics = payload.get("topics") if payload is not None else None
    topics = []
    for raw in raw_topics if isinstance(raw_topics, list) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("keyword"), str):
            continue
        count = raw.get("frequency")
        topics.append(
            NewsTrendingTopic(
                count=count if isinstance(count, int) else 0,
                trend=str(raw.get("trend") or "")[:20],
                untrusted=NewsKeywordText(keyword=raw["keyword"][:100]),
            )
        )
    return NewsTrendingOutput(topics=topics[: args.top_n], hint=None if topics else _NO_DATA)


async def news_rss(call: ReadCall, args: NewsRssInput) -> NewsListOutput:
    service, _ = await _setup(call)
    params: dict[str, Any] = {"days": args.days, "limit": args.limit, "include_summary": True}
    if args.feeds:
        params["feeds"] = list(args.feeds)
    return await _list(service, "get_latest_rss", params, args.limit, rss=True)


async def news_related(call: ReadCall, args: NewsRelatedInput) -> NewsListOutput:
    service, _ = await _setup(call)
    params = {
        "reference_title": args.title,
        "date_range": _range(call, args.days),
        "limit": args.limit,
        "include_url": True,
    }
    return await _list(service, "find_related_news", params, args.limit)


async def news_briefing(call: ReadCall, args: NewsBriefingInput) -> NewsBriefingOutput:
    service, _ = await _setup(call)
    day = args.day or local_today(call.settings.news_timezone)
    briefing: NewsBriefing | None = await service.latest_briefing(call.access.workspace.id, day)
    if briefing is None:
        return NewsBriefingOutput(briefing=None, hint=f"{day.isoformat()} 还没有早报")
    return NewsBriefingOutput(
        briefing=NewsBriefingItem(
            briefing_id=briefing.id,
            local_date=briefing.local_date,
            slot=briefing.slot,
            source=briefing.source,
            status=briefing.status,
            untrusted=NewsBriefingText(title=briefing.title, content=briefing.content),
        )
    )


HANDLERS: dict[str, Callable[[ReadCall, Any], Awaitable[Any]]] = {
    "news_latest": news_latest,
    "news_search": news_search,
    "news_by_date": news_by_date,
    "news_trending": news_trending,
    "news_rss": news_rss,
    "news_related": news_related,
    "news_briefing": news_briefing,
}
