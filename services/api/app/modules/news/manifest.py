"""新闻早报模块的清单（docs/v2/NEWS_PLAN.md；模块写法见 docs/modules/README.md）。

TrendRadar 是独立的 Compose 项目（infra/trendradar），部署前提是 TRENDRADAR_MCP_URL。
整个部署只有一套 TrendRadar，所以同一时刻只有一个工作空间能打开早报推送（模块自己的规则，
见 models.py 的部分唯一索引）；模块启用与否按工作空间，停用后入口、工具、定时任务都不再生效。
"""

from app.modules.base import CronSpec, ModuleManifest, ServiceCheckSpec

MANIFEST = ModuleManifest(
    key="news",
    name="新闻早报",
    description="每天的新闻早报、热榜、RSS、搜索与收藏；智能体可以查新闻。依赖 TrendRadar。",
    version="1.0.0",
    # 迁成模块之前所有工作空间都能看到早报页：保持原样，owner 可以在应用中心停用
    default_enabled=True,
    requires=("trendradar_mcp_url",),
    models="app.modules.news.models",
    router="app.modules.news.api:router",
    internal_router="app.modules.news.api:internal",
    secret_path_params={"/internal/news/ingest/{token}": ("{token}",)},
    tools="app.modules.news.tools:SPECS",
    tool_handlers="app.modules.news.tool_handlers:HANDLERS",
    tools_visible="app.modules.news.tool_handlers:news_enabled",
    crons=(
        CronSpec(
            "app.modules.news.jobs:news_tick",
            {"second": 33, "timeout": 120, "unique": True},
        ),
    ),
    event_topics=("news",),
    widgets=("news_briefing",),
    sealed_fields="app.modules.news.sealed:SEALED_FIELDS",
    on_toggle="app.modules.news.hooks:on_toggle",
    service_checks=(
        ServiceCheckSpec("trendradar", "TrendRadar（新闻早报）", "app.modules.news.health:probe"),
    ),
)
