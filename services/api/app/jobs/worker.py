"""ARQ Worker 入口：

    arq app.jobs.worker.WorkerSettings --custom-log-dict app.jobs.worker.LOG_CONFIG

`--custom-log-dict` 让 arq 自己的日志也输出为 JSON。
健康检查：`arq app.jobs.worker.WorkerSettings --check`
（Worker 每 health_check_interval 秒写一次心跳键）。

ARQ 目前处于仅维护状态（见 docs/decisions/2026-10-29-backend-stack.md），
作业逻辑不要依赖 ARQ 特有的 API，以便将来替换。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from arq import cron, func
from arq.connections import RedisSettings
from arq.cron import CronJob
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.logging import configure_logging, dict_config
from app.core.resources import create_engine
from app.domain import (
    agent_watch,
    alerts,
    approval_cards,
    automations,
    events,
    host_metrics,
    im,
    im_projection,
    knowledge_files,
    notifications,
    project_groups,
    sync,
    task_runs,
    timeline,
)
from app.domain.ai_tools import AiToolService
from app.domain.connectors import ConnectorRuntime
from app.domain.knowledge_base import KnowledgeBase
from app.domain.operations import WORKER_HEARTBEAT_KEY, DeploymentHealth, ServiceCache
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker
from app.jobs.queue import (
    PROCESS_IM_INBOX,
    SEND_APPROVAL_CARDS,
    SYNC_INTEGRATION,
)

logger = logging.getLogger("app.worker")

LOG_CONFIG = dict_config(get_settings().log_level)
# 对账的分钟：错开整点（第 2 分钟起），间隔由 RECONCILE_INTERVAL_MINUTES 决定
_interval = get_settings().reconcile_interval_minutes
RECONCILE_MINUTES = set(range(2 % _interval, 60, _interval))


async def heartbeat(ctx: dict[str, Any]) -> str:
    """每分钟写一次心跳时间；运维页按它判断 Worker 是否在正常调度（Sprint 5 契约第 3.1 节）。"""
    now = datetime.now(UTC)
    await ctx["redis"].set(WORKER_HEARTBEAT_KEY, now.isoformat())
    if now.minute % 5 == 0:
        logger.info("worker heartbeat")
    return "ok"


def _sync(ctx: dict[str, Any]) -> sync.SyncContext:
    context: sync.SyncContext = ctx["sync"]
    return context


async def sync_integration(ctx: dict[str, Any], integration_id: str, owner: str) -> bool:
    """手动同步（POST /api/v1/integrations/{id}/sync 投递）。"""
    return await sync.run_manual_sync(_sync(ctx), uuid.UUID(integration_id), owner)


async def reconcile(ctx: dict[str, Any]) -> int:
    """GitHub 的周期对账：增量 + 定期全量（规范第 10 节）。"""
    return await sync.reconcile_all(_sync(ctx))


async def purge(ctx: dict[str, Any]) -> None:
    """清理过期的幂等记录、过期的通知、IM 消息投影与知识库回收站。"""
    context = _sync(ctx)
    await sync.purge(context)
    async with context.sessions() as db:
        retention = timedelta(days=context.settings.notification_retention_days)
        removed = await notifications.purge(db, retention=retention)
        im_messages, im_events = await im_projection.purge(
            db, message_retention=timedelta(days=context.settings.im_message_retention_days)
        )
        # 知识库回收站：放进回收站 30 天后彻底删除（T-0098）
        notes = await KnowledgeBase(db, context.settings).purge_expired()
        # 时间轴：90 天前的每分钟 token 用量、3 天前的累计值（T-0104）
        usage_minutes, usage_cursors = await timeline.purge_usage(db)
        # 服务器状态：30 天前的样本（T-0113）
        host_samples = await host_metrics.purge(db)
        await db.commit()
    if host_samples:
        logger.info("host samples purged", extra={"count": host_samples})
    if usage_minutes or usage_cursors:
        logger.info(
            "agent usage purged", extra={"minutes": usage_minutes, "cursors": usage_cursors}
        )
    if notes:
        logger.info("knowledge notes purged", extra={"count": notes})
    if removed:
        logger.info("notifications purged", extra={"count": removed})
    if im_messages or im_events:
        logger.info("im purged", extra={"messages": im_messages, "inbox_events": im_events})


async def reconcile_automations(ctx: dict[str, Any]) -> int:
    """n8n 执行记录对账（Sprint 5 契约第 6.4 节）：状态、错误摘要与结果通知。"""
    context = _sync(ctx)
    return await automations.reconcile_all(context.sessions, context.settings, context.runtime)


async def provision_im(ctx: dict[str, Any]) -> int:
    """IM 账号尚未就绪的实例再试一次（V2 OC-3）；没有部署 OpenIM 时什么也不做。"""
    context = _sync(ctx)
    client = im.openim_client(context.settings, context.runtime, ctx["redis"])
    if client is None:
        return 0
    async with context.sessions() as db:
        return await im.provision_pending(db, client)


def _im(ctx: dict[str, Any]) -> im_projection.ImContext | None:
    """没有部署 OpenIM 时为 None。"""
    context = _sync(ctx)
    client = im.openim_client(context.settings, context.runtime, ctx["redis"])
    if client is None:
        return None
    return im_projection.ImContext(sessions=context.sessions, client=client, redis=ctx["redis"])


async def process_im_inbox(ctx: dict[str, Any]) -> int:
    """处理 OpenIM 回调收件箱（回调写入后由 API 投递，另有每分钟一次的兜底）。"""
    im_ctx = _im(ctx)
    return await im_projection.process_inbox(im_ctx) if im_ctx is not None else 0


async def reconcile_im(ctx: dict[str, Any]) -> int:
    """IM 增量对账（每 30 秒）：按管理员账号的 newest_seq 补齐有变化的会话。"""
    im_ctx = _im(ctx)
    return await im_projection.reconcile(im_ctx) if im_ctx is not None else 0


async def watch_agents(ctx: dict[str, Any]) -> int:
    """实例在线状态对账（每 5 分钟）：以 OpenIM 的实时状态为准；离线超过 10 分钟提醒管理员。"""
    im_ctx = _im(ctx)
    if im_ctx is None:
        return 0
    async with im_ctx.sessions() as db:
        try:
            result = await agent_watch.reconcile_presence(
                db, im.ImAccounts(db, im_ctx.client), im_ctx.redis
            )
        except ConnectorError as error:
            logger.warning("agent presence reconcile failed", extra={"error_kind": error.kind})
            return 0
        await db.commit()
        # 离线提醒已随事务写入：提交成功后才记“已提醒”，提交失败时下一轮重新提醒
        await agent_watch.confirm_notified(im_ctx.redis, result.notified)
        return result.changed


async def reconcile_im_friends(ctx: dict[str, Any]) -> int:
    """好友关系对账（每小时）：实例与 owner / admin 缺了好友就补。"""
    im_ctx = _im(ctx)
    if im_ctx is None:
        return 0
    async with im_ctx.sessions() as db:
        restored = await agent_watch.reconcile_friendships(db, im.ImAccounts(db, im_ctx.client))
        await db.commit()
        return restored


async def warn_agent_expiry(ctx: dict[str, Any]) -> int:
    """客户端证书、IM 令牌到期提醒（每天）。不需要 OpenIM。"""
    context = _sync(ctx)
    async with context.sessions() as db:
        sent = await agent_watch.warn_expiring(db)
        await db.commit()
        return sent


async def reconcile_im_full(ctx: dict[str, Any]) -> int:
    """IM 全量对账（每天一次）：重新拉每个会话最近 1000 条，补上中间缺的。"""
    im_ctx = _im(ctx)
    return await im_projection.reconcile(im_ctx, full=True) if im_ctx is not None else 0


async def send_approval_cards(ctx: dict[str, Any], execution_id: str) -> int:
    """新的待审批：由工作空间的通知账号给 owner / admin 发 IM 审批卡片（V2 OC-5）。"""
    context = _sync(ctx)
    client = im.openim_client(context.settings, context.runtime, ctx["redis"])
    if client is None:
        return 0
    async with context.sessions() as db:
        return await approval_cards.send_cards(db, client, ctx["redis"], uuid.UUID(execution_id))


async def expire_ai_approvals(ctx: dict[str, Any]) -> int:
    """15 分钟没人处理的待审批默认拒绝（expired，V2 OC-5）。"""
    context = _sync(ctx)
    async with context.sessions() as db:
        return await AiToolService(db, context.settings, context.runtime, ctx["redis"]).expire_due()


async def check_services(ctx: dict[str, Any]) -> list[str]:
    """服务健康告警（S5-02）：api、Prometheus、Grafana 变为 down 或恢复时通知管理员。"""
    health: DeploymentHealth = ctx["health"]
    items = [await health.api_internal(), *await health.monitoring()]
    return await alerts.evaluate(ctx["redis"], _sync(ctx).sessions, items)


async def close_stale_runs(ctx: dict[str, Any]) -> int:
    """时间轴（T-0104）：实例停用、删除或离线超过 10 分钟，它正在执行的段记为中断。"""
    context = _sync(ctx)
    async with context.sessions() as db:
        ended = await task_runs.interrupt_stale_runs(db, ctx["redis"])
        await db.commit()
    return ended


async def sync_project_groups(ctx: dict[str, Any]) -> int:
    """项目群（T-0110）：建群或同步成员；OpenIM 不可用时下一分钟再试。没有部署 OpenIM 时不做。"""
    context = _sync(ctx)
    client = im.openim_client(context.settings, context.runtime, ctx["redis"])
    if client is None:
        return 0
    async with context.sessions() as db:
        return await project_groups.ProjectGroupSync(db, context.settings, client).run_due()


async def check_host_alerts(ctx: dict[str, Any]) -> int:
    """服务器状态告警（T-0113）：磁盘、内存、负载、在线却不上报；恢复前只提醒一次。"""
    context = _sync(ctx)
    async with context.sessions() as db:
        return await host_metrics.check_alerts(db)


async def replicate_files(ctx: dict[str, Any]) -> int:
    """知识库图片：把待复制的图片写到副本（T-0103）。没配置副本时什么也不做。"""
    context = _sync(ctx)
    stores = knowledge_files.FileStores(context.settings)
    async with context.sessions() as db:
        done = await knowledge_files.replicate_pending(db, stores)
        await db.commit()
    if done:
        logger.info("kb files replicated", extra={"count": done})
    return done


async def cleanup_files(ctx: dict[str, Any]) -> int:
    """知识库图片：删除没有笔记引用、上传超过 24 小时的图片（T-0103）。"""
    context = _sync(ctx)
    stores = knowledge_files.FileStores(context.settings)
    async with context.sessions() as db:
        removed = await knowledge_files.cleanup_unreferenced(db, stores)
        await db.commit()
    if removed:
        logger.info("kb files cleaned up", extra={"count": removed})
    return removed


async def check_file_storage(ctx: dict[str, Any]) -> bool:
    """知识库图片存储自检：写、读、删一个探测对象；启动时和每天跑一次，失败写警告日志（T-0103）。"""
    context = _sync(ctx)
    results = await knowledge_files.run_checks(
        knowledge_files.FileStores(context.settings), ctx["redis"]
    )
    return all(check.ok for check in results.values())


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    ctx["engine"] = engine
    # ctx["redis"] 是 ARQ 的连接（redis.asyncio.Redis 的子类），同步锁与 API 共用同一个 Redis
    bus = events.EventBus(ctx["redis"])
    runtime = ConnectorRuntime(settings, CircuitBreaker())
    ctx["events"] = bus
    ctx["sync"] = sync.SyncContext(
        sessions=async_sessionmaker(engine, expire_on_commit=False, info=bus.session_info()),
        runtime=runtime,
        redis=ctx["redis"],
        settings=settings,
    )
    ctx["health"] = DeploymentHealth(ctx["redis"], settings, runtime, {}, ServiceCache())
    logger.info("worker started")


async def shutdown(ctx: dict[str, Any]) -> None:
    bus = ctx.get("events")
    if bus is not None:
        await bus.drain()
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()
    logger.info("worker stopped")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        heartbeat,
        func(sync_integration, name=SYNC_INTEGRATION, timeout=900),
        func(process_im_inbox, name=PROCESS_IM_INBOX),
        func(send_approval_cards, name=SEND_APPROVAL_CARDS),
    ]
    cron_jobs: ClassVar[list[CronJob]] = [
        cron(heartbeat, second=0, run_at_startup=True),
        cron(reconcile, minute=RECONCILE_MINUTES, timeout=900),
        cron(purge, minute=17),
        cron(check_services, second=45, timeout=60),
        cron(reconcile_automations, second=15, timeout=120),
        cron(provision_im, minute=set(range(4, 60, 5)), second=40, timeout=120),
        cron(process_im_inbox, second=50, timeout=120),
        cron(reconcile_im, second={5, 35}, timeout=60),
        cron(expire_ai_approvals, second=25, timeout=60),
        cron(reconcile_im_full, hour=19, minute=23, timeout=1800),
        cron(watch_agents, minute=set(range(2, 60, 5)), second=10, timeout=120),
        cron(reconcile_im_friends, minute=41, timeout=600),
        cron(warn_agent_expiry, hour=1, minute=7, timeout=120),
        cron(replicate_files, second=55, timeout=300),
        cron(close_stale_runs, second=20, timeout=60),
        cron(sync_project_groups, second=30, timeout=120),
        cron(check_host_alerts, second=40, timeout=60),
        cron(cleanup_files, hour=3, minute=37, timeout=1800),
        cron(check_file_storage, hour=4, minute=13, run_at_startup=True, timeout=180),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    on_startup = startup
    on_shutdown = shutdown
    health_check_interval = 60
