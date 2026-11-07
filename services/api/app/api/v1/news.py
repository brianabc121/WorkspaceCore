"""新闻早报的页面接口（T-0153，docs/v2/NEWS_PLAN.md 第 4、5、7、8 节）。

- 早报、热榜、RSS、搜索、收藏：成员与访客都能看；收藏 / 不感兴趣要 member（只改自己的）。
- 设置：admin 及以上能看，只有 owner 能改、能发测试推送、能让智能体现在写一份（方案 B）。
- `/internal/news/ingest/{token}`：TrendRadar 通用 Webhook 推早报的地址，只在内部网络 news 里
  （Caddy 只转发 /api/*，外面访问不到）；token 不对或没有启用时一律 404。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, Security, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_app_settings,
    get_connectors,
    get_db,
    get_redis,
    workspace_admin,
    workspace_member,
    workspace_owner,
    workspace_viewer,
)
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import news_agent
from app.domain.auth import AuthContext
from app.domain.connectors import ConnectorRuntime
from app.domain.news import MAX_BATCH_BYTES, NewsService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.news import (
    FeedbackKind,
    NewsBriefingOut,
    NewsBriefingPage,
    NewsFeedbackIn,
    NewsFeedbackOut,
    NewsFeedbackPage,
    NewsItemList,
    NewsPushTestOut,
    NewsSettingsIn,
    NewsSettingsOut,
    NewsStatusOut,
    NewsTodayOut,
    PlatformId,
)

router = APIRouter(tags=["news"], dependencies=[Security(authenticated)])
internal = APIRouter(include_in_schema=False)

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Member = Annotated[WorkspaceAccess, Depends(workspace_member)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
Context = Annotated[AuthContext, Security(authenticated)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 404, 422)


def get_news_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    runtime: Annotated[ConnectorRuntime, Depends(get_connectors)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> NewsService:
    return NewsService(db, settings, runtime, redis)


Service = Annotated[NewsService, Depends(get_news_service)]


@router.get(
    "/news/today",
    summary="今天：最新一份早报 + 每个兴趣主题今天的条目（viewer）",
    description="条目来自 TrendRadar（经 MCP 查询）；TrendRadar 连不上时 unavailable 为 true，"
    "早报照常返回。当前用户标了“不感兴趣”的条目不在这里。",
    responses=_read_errors,
)
async def news_today(access: Viewer, service: Service) -> NewsTodayOut:
    return await service.today(access)


@router.get("/news/status", summary="TrendRadar 与早报的状态（viewer）", responses=_read_errors)
async def news_status(access: Viewer, service: Service) -> NewsStatusOut:
    return await service.status(access)


@router.get("/news/briefings", summary="往期早报（viewer，按时间倒序）", responses=_read_errors)
async def list_briefings(
    access: Viewer,
    service: Service,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> NewsBriefingPage:
    return await service.list_briefings(access, cursor, limit)


@router.get("/news/briefings/{briefing_id}", summary="一份早报（viewer）", responses=_read_errors)
async def get_briefing(access: Viewer, service: Service, briefing_id: UUID) -> NewsBriefingOut:
    return await service.get_briefing(access, briefing_id)


@router.get(
    "/news/hot",
    summary="最新一批热榜（viewer）",
    description="可以只看一个平台；链接只保留 http(s)。",
    responses=_read_errors,
)
async def news_hot(
    access: Viewer,
    service: Service,
    platform: PlatformId | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> NewsItemList:
    return await service.hot(access, platform, limit)


@router.get("/news/rss", summary="最近几天的 RSS 条目（viewer）", responses=_read_errors)
async def news_rss(
    access: Viewer,
    service: Service,
    feed: Annotated[str | None, Query(max_length=40, pattern=r"^[a-z0-9][a-z0-9-]*$")] = None,
    days: Annotated[int, Query(ge=1, le=7)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> NewsItemList:
    return await service.rss(access, feed, days, limit)


@router.get("/news/search", summary="按关键词搜热榜（可含 RSS）（viewer）", responses=_read_errors)
async def news_search(
    access: Viewer,
    service: Service,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    include_rss: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> NewsItemList:
    return await service.search(access, q.strip(), days, include_rss, limit)


@router.get("/news/feedback", summary="我的收藏 / 不感兴趣（viewer）", responses=_read_errors)
async def list_feedback(
    access: Viewer,
    service: Service,
    kind: FeedbackKind = "favorite",
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> NewsFeedbackPage:
    return await service.list_feedback(access, kind, cursor, limit)


@router.post(
    "/news/feedback",
    status_code=status.HTTP_201_CREATED,
    summary="收藏或不感兴趣（member；同一条重复提交返回同一条记录）",
    description="收藏与不感兴趣互斥：标了一个就去掉另一个。exclude_word 把一个词加进全局排除"
    "（只有 owner 能用，随后重新生成配置文件）。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 422),
)
async def add_feedback(
    access: Member, context: Context, service: Service, body: NewsFeedbackIn
) -> NewsFeedbackOut:
    return await service.add_feedback(context, access, body)


@router.delete(
    "/news/feedback/{feedback_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="取消收藏 / 不感兴趣（member，只能删自己的）",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 422),
)
async def remove_feedback(access: Member, service: Service, feedback_id: UUID) -> Response:
    await service.remove_feedback(access, feedback_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/news/settings",
    summary="新闻早报设置（admin）",
    description="密钥只回“已设置”（ai_api_key_set、channels.*.secrets_set）。还没保存过时返回默认值，"
    "version 为 0。",
    responses=error_responses(401, 403, 404, 422),
)
async def get_settings(access: Admin, service: Service) -> NewsSettingsOut:
    return await service.get_settings(access)


@router.put(
    "/news/settings",
    summary="保存新闻早报设置（owner，乐观并发）",
    description="保存后按启用的工作空间重新生成 TrendRadar 的配置文件（下一次运行生效）。"
    "409：VERSION_CONFLICT 版本不一致；NEWS_TAKEN 另一个工作空间已经启用了早报"
    "（一个部署只有一个 TrendRadar）。地址只允许公网 http(s)。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def put_settings(
    access: Owner, context: Context, service: Service, body: NewsSettingsIn
) -> NewsSettingsOut:
    return await service.update_settings(context, access, body)


@router.post(
    "/news/settings/test-push",
    summary="经 TrendRadar 发一条测试推送（owner）",
    description="推到所有启用的渠道，返回每个渠道的结果。503 NEWS_NOT_DEPLOYED / "
    "EXTERNAL_UNAVAILABLE；409 NEWS_DISABLED / NO_CHANNELS；504 RESULT_UNKNOWN。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 503, 504),
)
async def test_push(access: Owner, context: Context, service: Service) -> NewsPushTestOut:
    return await service.test_push(context, access)


@router.post(
    "/news/briefings/generate",
    status_code=status.HTTP_201_CREATED,
    summary="方案 B：现在让智能体写一份早报（owner）",
    description="在“每日早报”项目里建任务并叫醒选定的实例。"
    "409：NEWS_DISABLED、NOT_AGENT_MODE、BRIEFING_PENDING（已经有一份在写）。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def generate_briefing(access: Owner, service: Service) -> NewsBriefingOut:
    return await news_agent.generate_now(service, access.workspace.id)


@internal.post("/internal/news/ingest/{token}")
async def ingest_briefing(
    request: Request,
    service: Annotated[NewsService, Depends(get_news_service)],
    token: Annotated[str, Path(min_length=32, max_length=128)],
) -> dict[str, str]:
    """TrendRadar 的通用 Webhook：每批 {"title", "content"}；按批次拼成一份早报。"""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BATCH_BYTES:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "单批不能超过 64 KB")
    return {"result": await service.ingest(token, bytes(body))}
