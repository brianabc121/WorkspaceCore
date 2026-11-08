"""新闻早报给智能体的工具（docs/v2/NEWS_PLAN.md 第 6 节）：只读，模块启用且早报打开时才列给实例。

处理函数在 tool_handlers.py；这里只放工具描述，供 app/schemas/ai_tools.py 汇总（不能导入它）。
"""

from app.modules.news.tool_schemas import (
    DESCRIPTIONS,
    NewsBriefingInput,
    NewsBriefingOutput,
    NewsByDateInput,
    NewsLatestInput,
    NewsListOutput,
    NewsRelatedInput,
    NewsRssInput,
    NewsSearchInput,
    NewsTrendingInput,
    NewsTrendingOutput,
)
from app.schemas.tool_spec import ToolSpec, make_spec

SPECS: tuple[ToolSpec, ...] = tuple(
    make_spec(name, "read", input_model, output_model, DESCRIPTIONS[name])
    for name, input_model, output_model in (
        ("news_latest", NewsLatestInput, NewsListOutput),
        ("news_search", NewsSearchInput, NewsListOutput),
        ("news_by_date", NewsByDateInput, NewsListOutput),
        ("news_trending", NewsTrendingInput, NewsTrendingOutput),
        ("news_rss", NewsRssInput, NewsListOutput),
        ("news_related", NewsRelatedInput, NewsListOutput),
        ("news_briefing", NewsBriefingInput, NewsBriefingOutput),
    )
)
