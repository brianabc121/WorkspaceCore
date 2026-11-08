"""新闻早报（T-0153，docs/v2/NEWS_PLAN.md）。

- 设置：每个工作空间一行；只有 owner 能改。密钥（大模型 Key、推送凭证、接收 token）用 CredentialBox
  加密后存引用，接口只回“已设置”。整个部署只有一个 TrendRadar，同一时刻只能有一个工作空间启用
  （部分唯一索引）。保存后按“当前启用的那个工作空间”重新生成配置文件（app/domain/news_config.py），
  没有启用的就生成“不抓取、不推送”的配置。
- 早报（方案 A）：TrendRadar 的通用 Webhook 把报告分批 POST 到 /internal/news/ingest/<token>
  （只在内部网络 news 里），这里按“[第 i/N 批次]”拼成一份，收齐后通知 owner。
- 热榜、RSS、搜索：经 TrendRadar MCP 的只读工具查询（app/integrations/trendradar.py），
  链接只保留 http(s)。
- 收藏 / 不感兴趣：存 news_feedback；偏好学习按最近 60 天的反馈
  调整每个主题的条数与 AI 兴趣（第 8 节）。
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
import json
import logging
import re
import secrets
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import events
from app.domain import news_config as cfg
from app.domain.audit import AuditTrail
from app.domain.auth import Actor
from app.domain.connectors import ConnectorRuntime
from app.domain.credentials import CredentialError
from app.domain.notifications import notify_owners
from app.domain.policy import WorkspaceAccess, has_role
from app.domain.ratelimit import WindowLimiter
from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp, OutboundPolicy, host_of
from app.integrations.trendradar import TrendRadarClient
from app.models import AgentInstance, NewsBriefing, NewsFeedback, NewsSettings
from app.schemas.news import (
    NewsBriefingOut,
    NewsBriefingPage,
    NewsBriefingSummary,
    NewsChannelOut,
    NewsFeedbackIn,
    NewsFeedbackOut,
    NewsFeedbackPage,
    NewsFeedIn,
    NewsItemList,
    NewsItemOut,
    NewsLearnedOut,
    NewsPlatformChoice,
    NewsPushResult,
    NewsPushTestOut,
    NewsSettingsIn,
    NewsSettingsOut,
    NewsSourceSuggestion,
    NewsStatusOut,
    NewsTodayOut,
    NewsTopicIn,
    NewsTopicItems,
    NewsTopicLearned,
)
from app.schemas.workspaces import Role

logger = logging.getLogger("app.news")

INGEST_PATH = "/internal/news/ingest/"
MAX_BATCH_BYTES = 64 * 1024
MAX_BRIEFING_CHARS = 200_000
INGEST_PER_MINUTE = 60
RECEIVE_WINDOW = timedelta(hours=2)
LEARN_DAYS = 60
SUGGEST_MIN_DISMISSES = 5
# 工作台自己经 TrendRadar 推送的消息（测试推送、方案 B）以这个开头；
# 通用 Webhook 也会推回工作台，接收时忽略
SELF_TITLE = "【工作台】"
TODAY_LIMIT = 500
TODAY_PER_TOPIC = 20
_BATCH = re.compile(r"^\s*(?:\*\*|<b>|\*)?\[第 (\d{1,3})/(\d{1,3}) 批次\](?:\*\*|</b>|\*)?\s*")
_DEFAULT_TOPICS: list[dict[str, Any]] = [
    {
        "name": "AI 与大模型",
        "keywords": ["AI", "人工智能", "大模型", "OpenAI", "DeepSeek", "Claude"],
        "required": [],
        "max_items": 10,
        "description": "人工智能、大模型与 AI 应用的新进展",
        "enabled": True,
    }
]
# 渠道 id（工作台）→ TrendRadar send_notification 的渠道名
_NOTIFY_CHANNELS = {
    "feishu": "feishu",
    "dingtalk": "dingtalk",
    "wework": "wework",
    "telegram": "telegram",
    "email": "email",
    "ntfy": "ntfy",
    "bark": "bark",
    "slack": "slack",
    "webhook": "generic_webhook",
}
_URL_SECRETS = frozenset({"webhook_url", "url"})


def _now() -> datetime:
    return datetime.now(UTC)


def secret_context(workspace_id: uuid.UUID, field: str) -> str:
    """凭证加密的用途：密文挪到别的工作空间或字段上会解密失败。"""
    return f"news_settings:{workspace_id}:{field}"


def _invalid(loc: Sequence[str | int], message: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": ["body", *loc], "msg": message, "type": "value_error"}]},
    )


# 只由数字（或 0x 十六进制）组成的“主机名”：127.1、0177.0.0.1、0x7f.1 这类 IPv4 的简写，
# ipaddress 不认，但 TrendRadar 用的 C 库解析器会把它们当成 IP（例如 127.1 → 127.0.0.1）
_NUMERIC_HOST = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]*))*")


def check_host(host: str, allowed: frozenset[str], loc: Sequence[str | int]) -> None:
    """设置里填的主机由 TrendRadar 访问：拒绝回环、链路本地、私有地址、IPv4 的简写
    与没有点的主机名（内部服务的名字）；部署时允许的主机名（NEWS_ALLOWED_HOSTS，
    只给验收的模拟服务）除外。主机名解析到哪里由 resolve_public 检查。"""
    host = host.strip().lower().strip("[]")
    if not host or host in allowed:
        return
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise _invalid(loc, "不能指向内网、回环或链路本地地址")
        return
    if _NUMERIC_HOST.fullmatch(host):
        raise _invalid(loc, "IP 地址要用完整的标准写法（例如 203.0.113.10）")
    if (
        "." not in host
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa"))
    ):
        raise _invalid(loc, "主机名必须是公网域名")


def check_url(url: str, allowed: frozenset[str], loc: Sequence[str | int]) -> str:
    """设置里填的地址：只允许不带用户名密码的 http(s)，主机按 check_host 检查。"""
    url = url.strip()
    if not url:
        return ""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise _invalid(loc, "必须是不带用户名密码的 http(s) 地址")
    check_host(host, allowed, loc)
    return url


async def resolve_public(host: str, allowed: frozenset[str], loc: Sequence[str | int]) -> None:
    """保存时把主机名解析一遍：解析到内网、回环或链路本地地址的拒绝（例如指向 127.0.0.1 的域名），
    与工作台自己的出站检查（OutboundPolicy）同一套规则。TrendRadar 访问时会再解析，
    之后才改指向内网的域名（DNS 重绑定）挡不住，见 docs/threat-model.md。"""
    host = host.strip().lower().strip("[]")
    if not host:
        return
    try:
        await OutboundPolicy(allowed).check(
            f"http://[{host}]/" if ":" in host else f"http://{host}/"
        )
    except ConnectorError as error:
        if error.reason == "dns_failed":
            raise _invalid(loc, "解析不了这个主机名，请检查拼写") from error
        raise _invalid(loc, "这个主机名解析到了内网、回环或链路本地地址") from error


def safe_link(url: object) -> str:
    """第三方给的链接只保留 http(s)：避免 javascript: 之类的地址进到页面。"""
    if not isinstance(url, str):
        return ""
    url = url.strip()
    return url if url.lower().startswith(("http://", "https://")) and len(url) <= 2000 else ""


def split_batch(content: str) -> tuple[int, int, str]:
    """TrendRadar 通用 Webhook 的批次头部（trendradar/notification/batch.py，wework 格式）：
    `**[第 i/N 批次]**` 加空行；只有一批时没有头部。"""
    match = _BATCH.match(content)
    if match is None:
        return 1, 1, content.strip()
    index, total = int(match.group(1)), int(match.group(2))
    if total < 1 or index < 1 or index > total:
        return 1, 1, content.strip()
    return index, total, content[match.end() :].strip()


def slot_for(local: datetime, push_times: Iterable[str]) -> str:
    """收到早报的时间落在哪个推送时间段 [t, t+30min) 里；不在任何时间段里为空。"""
    minutes = local.hour * 60 + local.minute
    for t in sorted(push_times):
        h, m = (int(x) for x in t.split(":"))
        start = h * 60 + m
        if start <= minutes < start + cfg.WINDOW_MINUTES:
            return t
    return ""


def _word_match(word: str, text: str) -> bool:
    word = word.split("=>", 1)[0].strip()
    if len(word) > 2 and word.startswith("/") and word.endswith("/"):
        try:
            return re.search(word[1:-1], text, re.IGNORECASE) is not None
        except re.error:
            return False
    return bool(word) and word.lower() in text.lower()


def match_topic(title: str, topics: Sequence[NewsTopicIn], exclusions: Sequence[str]) -> str | None:
    """页面上的“今天各主题”：与 frequency_words.txt 同样的规则
    （任一关键词 + 全部必含词，排除词优先）。"""
    if any(_word_match(w, title) for w in exclusions):
        return None
    for topic in topics:
        if not topic.enabled:
            continue
        if topic.keywords and not any(_word_match(k, title) for k in topic.keywords):
            continue
        if all(_word_match(r, title) for r in topic.required):
            return topic.name
    return None


def _excerpt(content: str, limit: int = 200) -> str:
    text = re.sub(r"[#*_>`\[\]()|~-]+", " ", content)
    return " ".join(text.split())[:limit]


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at, row_id = json.loads(raw)
        created_at = datetime.fromisoformat(at)
        if created_at.tzinfo is None:  # 游标来自客户端；不带时区的时间不能与 timestamptz 比较
            raise ValueError("cursor without timezone")
        return created_at, uuid.UUID(row_id)
    except (ValueError, TypeError) as error:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "请求参数校验失败",
            details={
                "errors": [{"loc": ["query", "cursor"], "msg": "无效的游标", "type": "value_error"}]
            },
        ) from error


def briefing_out(row: NewsBriefing) -> NewsBriefingOut:
    return NewsBriefingOut(
        id=row.id,
        local_date=row.local_date,
        slot=row.slot,
        source=row.source,
        title=row.title,
        content=row.content,
        status=row.status,
        parts=row.parts,
        total_parts=row.total_parts,
        error=row.error,
        task_id=row.task_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _feedback_out(row: NewsFeedback) -> NewsFeedbackOut:
    return NewsFeedbackOut(
        id=row.id,
        kind=row.kind,
        url=row.url,
        title=row.title,
        source_type=row.source_type,
        source_id=row.source_id,
        source_name=row.source_name,
        topic=row.topic,
        created_at=row.created_at,
    )


def _hot_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    url = safe_link(raw.get("url"))
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip() or not url:
        return None
    rank = raw.get("rank")
    return {
        "title": " ".join(title.split())[:500],
        "url": url,
        "mobile_url": safe_link(raw.get("mobileUrl")) or None,
        "source_type": "hotlist",
        "source_id": str(raw.get("platform") or "")[:100],
        "source_name": str(raw.get("platform_name") or raw.get("platform") or "")[:100],
        "rank": rank if isinstance(rank, int) else None,
        "time": str(raw.get("timestamp") or raw.get("date") or "")[:40] or None,
        "summary": None,
    }


def _rss_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    url = safe_link(raw.get("url"))
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip() or not url:
        return None
    summary = raw.get("summary")
    return {
        "title": " ".join(title.split())[:500],
        "url": url,
        "mobile_url": None,
        "source_type": "rss",
        "source_id": str(raw.get("feed_id") or "")[:100],
        "source_name": str(raw.get("feed_name") or raw.get("feed_id") or "")[:100],
        "rank": None,
        "time": str(raw.get("published_at") or raw.get("fetch_time") or "")[:40] or None,
        "summary": " ".join(summary.split())[:1000] if isinstance(summary, str) else None,
    }


def parse_items(payload: dict[str, Any], *, rss: bool = False) -> list[dict[str, Any]]:
    """MCP 工具返回的 data（以及 search_news 的 rss）→ 统一的条目。"""
    out: list[dict[str, Any]] = []
    data = payload.get("data")
    for raw in data if isinstance(data, list) else []:
        if isinstance(raw, dict):
            item = _rss_item(raw) if rss else _hot_item(raw)
            if item is not None:
                out.append(item)
    extra = payload.get("rss")
    for raw in extra if isinstance(extra, list) else []:
        if isinstance(raw, dict) and (item := _rss_item(raw)) is not None:
            out.append(item)
    return out


def topics_of(row: NewsSettings) -> list[NewsTopicIn]:
    out: list[NewsTopicIn] = []
    for raw in row.topics or []:
        try:
            out.append(NewsTopicIn.model_validate(raw))
        except ValueError:
            continue
    return out


def feeds_of(row: NewsSettings) -> list[NewsFeedIn]:
    out: list[NewsFeedIn] = []
    for raw in row.feeds or []:
        try:
            out.append(NewsFeedIn.model_validate(raw))
        except ValueError:
            continue
    return out


def news_client(settings: Settings, runtime: ConnectorRuntime) -> TrendRadarClient:
    """内部网络里的 TrendRadar MCP：只允许这一个主机，超时 15 秒，不重试。"""
    timeout = settings.news_timeout_seconds
    http = ConnectorHttp(
        policy=OutboundPolicy(frozenset({host_of(settings.trendradar_mcp_url)})),
        connect_timeout=min(timeout, 5.0),
        read_timeout=timeout,
        read_retries=0,
        breaker=runtime.breaker,
        transport=runtime.transport,
        breaker_scope="trendradar",
    )
    return TrendRadarClient(settings.trendradar_mcp_url, http)


@dataclass(frozen=True)
class Learned:
    computed_at: datetime
    topic_scores: dict[str, int]
    suggestions: list[NewsSourceSuggestion]

    def as_json(self) -> dict[str, Any]:
        return {
            "computed_at": self.computed_at.isoformat(),
            "topic_scores": self.topic_scores,
            "source_suggestions": [s.model_dump() for s in self.suggestions],
        }


class NewsService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runtime: ConnectorRuntime,
        redis: Redis | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.redis = redis

    @property
    def deployed(self) -> bool:
        return bool(self.settings.trendradar_mcp_url)

    def client(self) -> TrendRadarClient:
        return news_client(self.settings, self.runtime)

    # ------------------------------------------------------------ 设置

    async def row(self, workspace_id: uuid.UUID, *, lock: bool = False) -> NewsSettings | None:
        stmt = select(NewsSettings).where(NewsSettings.workspace_id == workspace_id)
        if lock:
            stmt = stmt.with_for_update()
        return await self.db.scalar(stmt)

    async def enabled_row(self) -> NewsSettings | None:
        return await self.db.scalar(select(NewsSettings).where(NewsSettings.enabled.is_(True)))

    @staticmethod
    def defaults(workspace_id: uuid.UUID) -> NewsSettings:
        """还没保存过设置时的默认值（不写库）。"""
        return NewsSettings(
            workspace_id=workspace_id,
            enabled=False,
            push_times=["07:30"],
            briefing_mode="trendradar",
            agent_instance_id=None,
            platforms=list(cfg.DEFAULT_PLATFORMS),
            newsnow_api_url="",
            feeds=[],
            topics=[dict(t) for t in _DEFAULT_TOPICS],
            exclusions=[],
            filter_method="keyword",
            ai_model="",
            ai_api_base="",
            ai_key_ref="",
            ai_analysis=True,
            ai_translation=False,
            channels={},
            channel_secrets_ref="",
            ingest_token_ref="",
            config_rev=0,
            rendered_at=None,
            render_error="",
            learned={},
            version=0,
            updated_at=None,
        )

    def _open(self, ref: str, workspace_id: uuid.UUID, field: str) -> str:
        if not ref:
            return ""
        box = self.runtime.credentials()
        return box.resolve(ref, context=secret_context(workspace_id, field)).get_secret_value()

    def _seal(self, value: str, workspace_id: uuid.UUID, field: str) -> str:
        if not value:
            return ""
        return self.runtime.credentials().seal(value, context=secret_context(workspace_id, field))

    def channel_secrets(self, row: NewsSettings) -> dict[str, dict[str, str]]:
        raw = self._open(row.channel_secrets_ref, row.workspace_id, "channels")
        if not raw:
            return {}
        data = json.loads(raw)
        return {
            str(k): {str(f): str(v) for f, v in vals.items()}
            for k, vals in data.items()
            if isinstance(vals, dict)
        }

    async def settings_out(self, row: NewsSettings) -> NewsSettingsOut:
        try:
            secrets_by_channel = self.channel_secrets(row)
        except (CredentialError, ValueError):
            secrets_by_channel = {}
        channels: dict[str, NewsChannelOut] = {}
        for name in cfg.CHANNELS:
            plain = dict(row.channels.get(name) or {})
            set_fields = sorted(k for k, v in secrets_by_channel.get(name, {}).items() if v)
            channels[name] = NewsChannelOut(
                enabled=bool(plain.get("enabled")),
                msg_type="text" if plain.get("msg_type") == "text" else "markdown",
                chat_id=str(plain.get("chat_id") or ""),
                from_addr=str(plain.get("from_addr") or ""),
                to_addr=str(plain.get("to_addr") or ""),
                smtp_server=str(plain.get("smtp_server") or ""),
                smtp_port=plain.get("smtp_port")
                if isinstance(plain.get("smtp_port"), int)
                else None,
                server_url=str(plain.get("server_url") or ""),
                secrets_set=set_fields,
            )
        other = await self.db.scalar(
            select(NewsSettings.workspace_id).where(
                NewsSettings.enabled.is_(True), NewsSettings.workspace_id != row.workspace_id
            )
        )
        return NewsSettingsOut(
            workspace_id=row.workspace_id,
            enabled=row.enabled,
            timezone=self.settings.news_timezone,
            push_times=list(row.push_times),
            briefing_mode=row.briefing_mode,
            agent_instance_id=row.agent_instance_id,
            platforms=list(row.platforms),
            newsnow_api_url=row.newsnow_api_url,
            feeds=feeds_of(row),
            topics=topics_of(row),
            exclusions=list(row.exclusions),
            filter_method=row.filter_method,
            ai_model=row.ai_model,
            ai_api_base=row.ai_api_base,
            ai_api_key_set=bool(row.ai_key_ref),
            ai_analysis=row.ai_analysis,
            ai_translation=row.ai_translation,
            channels=channels,
            version=row.version,
            config_rev=row.config_rev,
            rendered_at=row.rendered_at,
            render_error=row.render_error,
            updated_at=row.updated_at,
            deployed=self.deployed,
            taken_by_other=other is not None,
            platform_choices=[
                NewsPlatformChoice(id=pid, name=name) for pid, (name, _) in cfg.PLATFORMS.items()
            ],
            learned=self._learned_out(row),
        )

    def _learned_out(self, row: NewsSettings) -> NewsLearnedOut:
        learned = row.learned or {}
        scores = learned.get("topic_scores") or {}
        topics = [t for t in topics_of(row) if t.enabled]
        adjusted = {
            t.name: t.max_items
            for t in cfg.adjusted_topics(
                [cfg.Topic(t.name, t.keywords, t.required, t.max_items) for t in topics],
                cfg.Learned({k: int(v) for k, v in scores.items()}),
            )
        }
        suggestions = []
        for raw in learned.get("source_suggestions") or []:
            try:
                suggestions.append(NewsSourceSuggestion.model_validate(raw))
            except ValueError:
                continue
        computed = learned.get("computed_at")
        return NewsLearnedOut(
            computed_at=datetime.fromisoformat(computed) if isinstance(computed, str) else None,
            topics=[
                NewsTopicLearned(
                    name=t.name,
                    score=int(scores.get(t.name, 0)),
                    max_items=t.max_items,
                    adjusted_max_items=adjusted.get(t.name, t.max_items),
                )
                for t in topics
            ],
            source_suggestions=suggestions,
        )

    async def get_settings(self, access: WorkspaceAccess) -> NewsSettingsOut:
        row = await self.row(access.workspace.id) or self.defaults(access.workspace.id)
        return await self.settings_out(row)

    async def update_settings(
        self, actor: Actor, access: WorkspaceAccess, body: NewsSettingsIn
    ) -> NewsSettingsOut:
        workspace_id = access.workspace.id
        row = await self.row(workspace_id, lock=True)
        current = row.version if row is not None else 0
        if body.version != current:
            raise ApiError(
                409,
                "VERSION_CONFLICT",
                "设置已被其他操作修改，请刷新后重试",
                details={"current_version": current},
            )
        allowed = self.settings.allowed_news_hosts
        # 保存前把填的主机名都解析一遍（resolve_public）：(主机, 出错时指向的字段)
        to_resolve: list[tuple[str, list[str | int]]] = []

        def url_field(url: str, loc: list[str | int]) -> str:
            checked = check_url(url, allowed, loc)
            if checked:
                to_resolve.append((host_of(checked), loc))
            return checked

        newsnow = url_field(body.newsnow_api_url, ["newsnow_api_url"])
        api_base = url_field(body.ai_api_base, ["ai_api_base"])
        feeds = [
            {**f.model_dump(), "url": url_field(f.url, ["feeds", i, "url"])}
            for i, f in enumerate(body.feeds)
        ]
        for i, f in enumerate(feeds):
            if not f["url"]:
                raise _invalid(["feeds", i, "url"], "RSS 地址不能为空")
        if body.agent_instance_id is not None:
            instance = await self.db.get(AgentInstance, body.agent_instance_id)
            if instance is None or instance.workspace_id != workspace_id:
                raise _invalid(["agent_instance_id"], "没有这个智能体实例")
        if row is None:
            row = self.defaults(workspace_id)
            row.version = 0
            self.db.add(row)
        try:
            old_secrets = self.channel_secrets(row) if row.channel_secrets_ref else {}
        except (CredentialError, ValueError):
            # 解不开（换了加密密钥又没留旧密钥）：当作没设置过，页面上显示“未设置”，重新填就能保存
            logger.warning(
                "news channel secrets unreadable", extra={"workspace_id": str(workspace_id)}
            )
            old_secrets = {}
        channels: dict[str, dict[str, Any]] = {}
        new_secrets: dict[str, dict[str, str]] = {}
        given_by_name: dict[str, Any] = {str(k): v for k, v in body.channels.items()}
        for name in cfg.CHANNELS:
            given = given_by_name.get(name)
            kept = dict(old_secrets.get(name, {}))
            if given is None:
                plain = dict(row.channels.get(name) or {})
            else:
                plain = given.model_dump(exclude={"secrets"})
                plain["server_url"] = url_field(given.server_url, ["channels", name, "server_url"])
                if plain.get("smtp_server"):
                    # 邮件的 SMTP 服务器同样由 TrendRadar 连接，规则与地址一样
                    smtp_loc: list[str | int] = ["channels", name, "smtp_server"]
                    plain["smtp_server"] = str(plain["smtp_server"]).strip()
                    check_host(plain["smtp_server"], allowed, smtp_loc)
                    to_resolve.append((plain["smtp_server"], smtp_loc))
                for field_name, value in given.secrets.items():
                    if field_name not in cfg.CHANNEL_SECRETS[name]:
                        raise _invalid(["channels", name, "secrets", field_name], "没有这个字段")
                    value = value.strip()
                    if value and field_name in _URL_SECRETS:
                        value = url_field(value, ["channels", name, "secrets", field_name])
                    if value:
                        kept[field_name] = value
                    else:
                        kept.pop(field_name, None)
            channels[name] = plain
            if kept:
                new_secrets[name] = kept
            if plain.get("enabled") and not all(kept.get(f) for f in _required_secrets(name)):
                raise _invalid(["channels", name], "启用的渠道要填好凭证")
        for host, loc in dict(reversed(to_resolve)).items():  # 同一个主机只解析一次
            await resolve_public(host, allowed, loc)
        row.enabled = body.enabled
        row.push_times = sorted(body.push_times)
        row.briefing_mode = body.briefing_mode
        row.agent_instance_id = body.agent_instance_id
        row.platforms = list(body.platforms)
        row.newsnow_api_url = newsnow
        row.feeds = feeds
        row.topics = [t.model_dump() for t in body.topics]
        row.exclusions = list(dict.fromkeys(body.exclusions))
        row.filter_method = body.filter_method
        row.ai_model = body.ai_model
        row.ai_api_base = api_base
        if body.ai_api_key is not None:
            row.ai_key_ref = self._seal(body.ai_api_key.strip(), workspace_id, "ai_key")
        row.ai_analysis = body.ai_analysis
        row.ai_translation = body.ai_translation
        row.channels = channels
        row.channel_secrets_ref = (
            self._seal(json.dumps(new_secrets, ensure_ascii=False), workspace_id, "channels")
            if new_secrets
            else ""
        )
        if not row.ingest_token_ref:
            row.ingest_token_ref = self._seal(secrets.token_urlsafe(32), workspace_id, "ingest")
        row.version = current + 1
        row.updated_by = getattr(getattr(actor, "user", None), "id", None)
        try:
            await self.db.flush()
        except IntegrityError as error:
            await self.db.rollback()
            raise ApiError(
                409, "NEWS_TAKEN", "另一个工作空间已经启用了早报（一个部署只能有一个）"
            ) from error
        row.learned = (await self.learn(workspace_id)).as_json()
        await self.render_current(touch=row)
        await AuditTrail(self.db, self.settings).record(
            "news.settings_updated",
            workspace_ids=[workspace_id],
            actor_id=row.updated_by,
            resource_type="news_settings",
            resource_id=workspace_id,
            metadata={
                "enabled": row.enabled,
                "briefing_mode": row.briefing_mode,
                "push_times": row.push_times,
                "topics": len(row.topics),
                "feeds": len(row.feeds),
                "platforms": len(row.platforms),
                "channels": sorted(k for k, v in channels.items() if v.get("enabled")),
                "ai_model": row.ai_model,
            },
            client_ip=getattr(actor, "client_ip", None),
        )
        events.mark(self.db, workspace_id, "news")
        await self.db.commit()
        await self.db.refresh(row)
        return await self.settings_out(row)

    # ------------------------------------------------------------ 生成配置文件

    def render_input(self, row: NewsSettings) -> cfg.RenderInput:
        token = self._open(row.ingest_token_ref, row.workspace_id, "ingest")
        scores = (row.learned or {}).get("topic_scores") or {}
        return cfg.RenderInput(
            enabled=row.enabled,
            timezone=self.settings.news_timezone,
            push_times=list(row.push_times),
            briefing_mode=row.briefing_mode,
            platforms=list(row.platforms),
            newsnow_api_url=row.newsnow_api_url,
            feeds=[cfg.Feed(f.id, f.name, f.url, f.enabled, f.max_age_days) for f in feeds_of(row)],
            topics=[
                cfg.Topic(t.name, t.keywords, t.required, t.max_items, t.description, t.enabled)
                for t in topics_of(row)
            ],
            exclusions=list(row.exclusions),
            filter_method=row.filter_method,
            ai_model=row.ai_model,
            ai_api_base=row.ai_api_base,
            ai_api_key=self._open(row.ai_key_ref, row.workspace_id, "ai_key"),
            ai_analysis=row.ai_analysis,
            ai_translation=row.ai_translation,
            channels=dict(row.channels),
            channel_secrets=self.channel_secrets(row),
            ingest_url=(
                f"{self.settings.news_ingest_base_url}{INGEST_PATH}{token}" if token else ""
            ),
            learned=cfg.Learned({k: int(v) for k, v in scores.items()}),
        )

    async def render_current(
        self, *, touch: NewsSettings | None = None, force: bool = False
    ) -> str:
        """按当前启用的工作空间生成配置文件；没有启用的就生成“不抓取、不推送”的配置。

        没有部署 TrendRadar 时不写文件（force：部署时第一次启动前也写）。返回错误说明（成功为空）。
        """
        if not self.deployed and not force:
            return ""
        enabled = await self.enabled_row()
        error = ""
        try:
            if enabled is not None:
                inp = self.render_input(enabled)
            else:
                inp = self.render_input(self.defaults(uuid.UUID(int=0)))
            cfg.write_files(Path(self.settings.news_config_dir), cfg.render(inp))
        except (OSError, CredentialError, ApiError) as failure:
            logger.warning(
                "news config render failed", extra={"error_type": type(failure).__name__}
            )
            error = (
                "配置文件写入失败：" + type(failure).__name__
                if isinstance(failure, OSError)
                else "凭证无法解密，请检查 CREDENTIAL_ENCRYPTION_KEY"
            )
        now = _now()
        for row in {id(r): r for r in (enabled, touch) if r is not None}.values():
            row.rendered_at = now if not error else row.rendered_at
            row.render_error = error
            if not error:
                row.config_rev += 1
        return error

    # ------------------------------------------------------------ 偏好学习

    async def learn(self, workspace_id: uuid.UUID) -> Learned:
        since = _now() - timedelta(days=LEARN_DAYS)
        rows = await self.db.execute(
            select(NewsFeedback.topic, NewsFeedback.kind, func.count())
            .where(
                NewsFeedback.workspace_id == workspace_id,
                NewsFeedback.created_at >= since,
                NewsFeedback.topic != "",
            )
            .group_by(NewsFeedback.topic, NewsFeedback.kind)
        )
        scores: dict[str, int] = defaultdict(int)
        for topic, kind, count in rows:
            scores[topic] += int(count) if kind == "favorite" else -int(count)
        sources = await self.db.execute(
            select(
                NewsFeedback.source_type,
                NewsFeedback.source_id,
                func.max(NewsFeedback.source_name),
                NewsFeedback.kind,
                func.count(),
            )
            .where(
                NewsFeedback.workspace_id == workspace_id,
                NewsFeedback.created_at >= since,
                NewsFeedback.source_id != "",
            )
            .group_by(NewsFeedback.source_type, NewsFeedback.source_id, NewsFeedback.kind)
        )
        tally: dict[tuple[str, str], dict[str, Any]] = {}
        for source_type, source_id, source_name, kind, count in sources:
            entry = tally.setdefault(
                (source_type, source_id),
                {"name": source_name or source_id, "favorite": 0, "dismiss": 0},
            )
            entry[kind] += int(count)
        suggestions = [
            NewsSourceSuggestion(
                source_type=key[0],
                source_id=key[1],
                source_name=str(v["name"]),
                dismisses=v["dismiss"],
                favorites=v["favorite"],
            )
            for key, v in sorted(tally.items(), key=lambda kv: -kv[1]["dismiss"])
            if v["dismiss"] >= SUGGEST_MIN_DISMISSES and v["dismiss"] > v["favorite"]
        ]
        return Learned(_now(), {k: v for k, v in scores.items() if v}, suggestions)

    async def relearn_and_render(self) -> str:
        """Worker 每天调用：重新计算启用的工作空间的偏好，再生成配置文件。"""
        row = await self.enabled_row()
        if row is not None:
            row.learned = (await self.learn(row.workspace_id)).as_json()
        error = await self.render_current(touch=row)
        await self.db.commit()
        return error

    # ------------------------------------------------------------ 状态

    async def status(self, access: WorkspaceAccess) -> NewsStatusOut:
        row = await self.row(access.workspace.id)
        latest = await self.db.scalar(
            select(NewsBriefing.created_at)
            .where(NewsBriefing.workspace_id == access.workspace.id)
            .order_by(NewsBriefing.created_at.desc())
            .limit(1)
        )
        enabled = bool(row and row.enabled)
        online: bool | None = None
        latest_date: str | None = None
        error: str | None = None
        if self.deployed and enabled:
            try:
                payload = await self.client().call("get_system_status", {})
                raw = payload.get("data")
                data: dict[str, Any] = raw if isinstance(raw, dict) else {}
                raw_inner = data.get("data")
                inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else {}
                online = data.get("health") == "healthy"
                value = inner.get("latest_record")
                latest_date = str(value)[:20] if value else None
            except ConnectorError as failure:
                online = False
                error = str(failure)
        return NewsStatusOut(
            deployed=self.deployed,
            enabled=enabled,
            online=online,
            latest_data_date=latest_date,
            error=error,
            latest_briefing_at=latest,
            rendered_at=row.rendered_at if row else None,
            render_error=row.render_error if row else "",
        )

    # ------------------------------------------------------------ 测试推送

    def push_channels(self, row: NewsSettings) -> list[str]:
        """经 TrendRadar send_notification 推送时用的渠道（启用了、凭证齐全的）。"""
        secrets_by_channel = self.channel_secrets(row)
        out = []
        for name, plain in row.channels.items():
            kept = secrets_by_channel.get(name, {})
            if plain.get("enabled") and all(kept.get(f) for f in _required_secrets(name)):
                out.append(_NOTIFY_CHANNELS[name])
        return out

    async def push(self, row: NewsSettings, *, title: str, message: str) -> list[NewsPushResult]:
        channels = self.push_channels(row)
        if not channels:
            raise ApiError(409, "NO_CHANNELS", "还没有启用任何推送渠道")
        try:
            payload = await self.client().notify(
                title=f"{SELF_TITLE}{title}", message=message, channels=channels
            )
        except ConnectorError as error:
            if error.kind == "TIMEOUT_UNKNOWN":
                raise ApiError(
                    504, "RESULT_UNKNOWN", "推送结果未知，请稍后看手机是否收到"
                ) from error
            raise ApiError(
                503, "EXTERNAL_UNAVAILABLE", f"TrendRadar 暂时不可用：{error}"
            ) from error
        raw_results = payload.get("results")
        results: dict[str, Any] = raw_results if isinstance(raw_results, dict) else {}
        return [
            NewsPushResult(
                channel=str(name),
                ok=bool(r.get("success")) if isinstance(r, dict) else False,
                # 失败说明可能带渠道返回的原文（含地址），只留前 120 个字
                detail=" ".join(str(r.get("detail") or "").split())[:120]
                if isinstance(r, dict)
                else "",
            )
            for name, r in results.items()
        ]

    async def test_push(self, actor: Actor, access: WorkspaceAccess) -> NewsPushTestOut:
        self._require_deployed()
        row = await self.row(access.workspace.id)
        if row is None or not row.enabled:
            raise ApiError(409, "NEWS_DISABLED", "先启用早报并保存设置")
        results = await self.push(
            row,
            title="测试推送",
            message="这是一条测试推送：收到说明推送渠道已经配置好了。",
        )
        await AuditTrail(self.db, self.settings).record(
            "news.test_push",
            workspace_ids=[access.workspace.id],
            actor_id=getattr(getattr(actor, "user", None), "id", None),
            resource_type="news_settings",
            resource_id=access.workspace.id,
            metadata={"ok": [r.channel for r in results if r.ok]},
            client_ip=getattr(actor, "client_ip", None),
        )
        await self.db.commit()
        return NewsPushTestOut(results=results)

    def _require_deployed(self) -> None:
        if not self.deployed:
            raise ApiError(503, "NEWS_NOT_DEPLOYED", "这个部署没有安装 TrendRadar（新闻早报）")

    # ------------------------------------------------------------ 接收早报（方案 A）

    async def ingest(self, token: str, body: bytes) -> str:
        """TrendRadar 通用 Webhook 推来的一批。返回处理结果（ok / duplicate / ignored）。"""
        row = await self.enabled_row()
        if row is None or not row.ingest_token_ref:
            raise ApiError(404, "NOT_FOUND", "资源不存在")
        try:
            expected = self._open(row.ingest_token_ref, row.workspace_id, "ingest")
        except (CredentialError, ApiError) as error:
            raise ApiError(404, "NOT_FOUND", "资源不存在") from error
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            raise ApiError(404, "NOT_FOUND", "资源不存在")
        if self.redis is not None:
            retry = await WindowLimiter(self.redis, 60).hit(
                f"news_ingest:{row.workspace_id}", INGEST_PER_MINUTE
            )
            if retry is not None:
                raise ApiError(
                    429, "RATE_LIMITED", "请求太频繁", headers={"Retry-After": str(retry)}
                )
        if len(body) > MAX_BATCH_BYTES:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "单批不能超过 64 KB")
        try:
            payload = json.loads(body)
        except ValueError as error:
            raise ApiError(422, "VALIDATION_FAILED", "不是 JSON") from error
        title = payload.get("title") if isinstance(payload, dict) else None
        content = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(title, str) or not isinstance(content, str) or not content.strip():
            raise ApiError(422, "VALIDATION_FAILED", "缺少 title 或 content")
        if title.startswith(SELF_TITLE):
            return "ignored"
        index, total, text = split_batch(content)
        now = _now()
        local = now.astimezone(self.zone)
        current = await self.db.scalar(
            select(NewsBriefing)
            .where(
                NewsBriefing.workspace_id == row.workspace_id,
                NewsBriefing.source == "trendradar",
                NewsBriefing.status == "receiving",
                NewsBriefing.created_at >= now - RECEIVE_WINDOW,
            )
            .order_by(NewsBriefing.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if current is not None and (index == 1 or current.total_parts != total):
            current.status = "failed"
            current.error = f"批次不完整：只收到 {current.parts}/{current.total_parts} 批"
            current = None
        if current is None:
            current = NewsBriefing(
                workspace_id=row.workspace_id,
                local_date=local.date(),
                slot=slot_for(local, row.push_times),
                source="trendradar",
                title=f"{local:%m-%d} 早报 · {' '.join(title.split())[:60] or '全天汇总'}",
                content="",
                parts=0,
                total_parts=total,
                status="receiving",
                error="",
            )
            self.db.add(current)
        if index <= current.parts:
            await self.db.commit()
            return "duplicate"
        pieces = [current.content] if current.content else []
        if index > current.parts + 1:
            pieces.append(f"（缺少第 {current.parts + 1}～{index - 1} 批）")
            current.error = "有批次缺失"
        pieces.append(text)
        content_all = "\n\n".join(pieces)
        if len(content_all) > MAX_BRIEFING_CHARS:
            content_all = content_all[:MAX_BRIEFING_CHARS]
            current.error = "正文太长，已截断"
        current.content = content_all
        current.parts = index
        if index >= total:
            current.status = "complete"
            await self.db.flush()
            await self.announce(row.workspace_id, current)
        events.mark(self.db, row.workspace_id, "news", "overview")
        await self.db.commit()
        return "ok"

    async def announce(self, workspace_id: uuid.UUID, briefing: NewsBriefing) -> None:
        await notify_owners(
            self.db,
            workspace_id,
            type_="news_briefing",
            severity="info",
            title="今日早报已送达",
            body=briefing.title,
            link=f"/news?briefing={briefing.id}",
            key=str(briefing.id),
        )

    # ------------------------------------------------------------ 早报

    async def list_briefings(
        self, access: WorkspaceAccess, cursor: str | None, limit: int
    ) -> NewsBriefingPage:
        stmt = (
            select(NewsBriefing)
            .where(NewsBriefing.workspace_id == access.workspace.id)
            .order_by(NewsBriefing.created_at.desc(), NewsBriefing.id.desc())
            .limit(limit + 1)
        )
        if cursor:
            at, row_id = _decode_cursor(cursor)
            stmt = stmt.where(
                (NewsBriefing.created_at < at)
                | ((NewsBriefing.created_at == at) & (NewsBriefing.id < row_id))
            )
        rows = list(await self.db.scalars(stmt))
        page = rows[:limit]
        return NewsBriefingPage(
            items=[
                NewsBriefingSummary(
                    id=r.id,
                    local_date=r.local_date,
                    slot=r.slot,
                    source=r.source,
                    title=r.title,
                    status=r.status,
                    excerpt=_excerpt(r.content),
                    created_at=r.created_at,
                )
                for r in page
            ],
            next_cursor=_encode_cursor(page[-1].created_at, page[-1].id)
            if len(rows) > limit
            else None,
        )

    async def get_briefing(
        self, access: WorkspaceAccess, briefing_id: uuid.UUID
    ) -> NewsBriefingOut:
        row = await self.db.get(NewsBriefing, briefing_id)
        if row is None or row.workspace_id != access.workspace.id:
            raise ApiError(404, "NOT_FOUND", "早报不存在")
        return briefing_out(row)

    async def latest_briefing(
        self, workspace_id: uuid.UUID, on: date | None = None
    ) -> NewsBriefing | None:
        stmt = select(NewsBriefing).where(NewsBriefing.workspace_id == workspace_id)
        if on is not None:
            stmt = stmt.where(NewsBriefing.local_date == on)
        # 完整的优先，其次最新的
        stmt = stmt.order_by(
            (NewsBriefing.status == "complete").desc(), NewsBriefing.created_at.desc()
        ).limit(1)
        return await self.db.scalar(stmt)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.settings.news_timezone)

    def local_today(self) -> date:
        return _now().astimezone(self.zone).date()

    # ------------------------------------------------------------ 热榜、RSS、搜索（MCP）

    async def _enabled_for(self, workspace_id: uuid.UUID) -> NewsSettings | None:
        row = await self.row(workspace_id)
        return row if row is not None and row.enabled else None

    async def _query(
        self, tool: str, arguments: dict[str, Any], *, rss: bool = False
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        """返回（条目，说明，是否不可用）。没有数据不算错误。"""
        try:
            payload = await self.client().call(tool, arguments)
        except ConnectorError as error:
            if error.kind == "NOT_FOUND":
                return [], "还没有数据（TrendRadar 每 30 分钟抓取一次）", False
            if error.kind == "VALIDATION_FAILED":
                raise ApiError(422, "VALIDATION_FAILED", str(error)) from error
            return [], "TrendRadar 暂时连不上", True
        return parse_items(payload, rss=rss), None, False

    async def _decorate(
        self,
        access: WorkspaceAccess,
        items: list[dict[str, Any]],
        topics: Sequence[NewsTopicIn],
        exclusions: Sequence[str],
    ) -> list[NewsItemOut]:
        urls = {i["url"] for i in items}
        favorites: dict[str, uuid.UUID] = {}
        dismissed: set[str] = set()
        if urls and access.user_id is not None:
            rows = await self.db.execute(
                select(NewsFeedback.url, NewsFeedback.kind, NewsFeedback.id).where(
                    NewsFeedback.workspace_id == access.workspace.id,
                    NewsFeedback.user_id == access.user_id,
                    NewsFeedback.url.in_(urls),
                )
            )
            for url, kind, row_id in rows:
                if kind == "favorite":
                    favorites[url] = row_id
                else:
                    dismissed.add(url)
        return [
            NewsItemOut(
                **i,
                topic=match_topic(i["title"], topics, exclusions),
                favorite_id=favorites.get(i["url"]),
                dismissed=i["url"] in dismissed,
            )
            for i in items
        ]

    async def _page_query(
        self, access: WorkspaceAccess, tool: str, arguments: dict[str, Any], *, rss: bool = False
    ) -> NewsItemList:
        row = await self._enabled_for(access.workspace.id)
        if not self.deployed or row is None:
            return NewsItemList(items=[], unavailable=False, message="早报没有启用")
        items, message, unavailable = await self._query(tool, arguments, rss=rss)
        return NewsItemList(
            items=await self._decorate(access, items, topics_of(row), row.exclusions),
            unavailable=unavailable,
            message=message,
        )

    async def hot(self, access: WorkspaceAccess, platform: str | None, limit: int) -> NewsItemList:
        args: dict[str, Any] = {"limit": limit, "include_url": True}
        if platform:
            args["platforms"] = [platform]
        return await self._page_query(access, "get_latest_news", args)

    async def rss(
        self, access: WorkspaceAccess, feed: str | None, days: int, limit: int
    ) -> NewsItemList:
        args: dict[str, Any] = {"days": days, "limit": limit, "include_summary": True}
        if feed:
            args["feeds"] = [feed]
        return await self._page_query(access, "get_latest_rss", args, rss=True)

    async def search(
        self, access: WorkspaceAccess, q: str, days: int, include_rss: bool, limit: int
    ) -> NewsItemList:
        row = await self._enabled_for(access.workspace.id)
        if not self.deployed or row is None:
            return NewsItemList(items=[], unavailable=False, message="早报没有启用")
        today = self.local_today()
        args = {
            "query": q,
            "date_range": {
                "start": (today - timedelta(days=days - 1)).isoformat(),
                "end": today.isoformat(),
            },
            "limit": limit,
            "include_url": True,
        }
        if not include_rss:
            items, message, unavailable = await self._query("search_news", args)
        else:
            # search_news 的 include_rss 在热榜没有命中时直接返回、不带 RSS
            # （MCP 4.1.1 实测），所以 RSS 单独查，与热榜同时查
            rss_args = {"keyword": q, "days": days, "limit": limit, "include_summary": True}
            (items, message, unavailable), (rss, rss_message, rss_down) = await asyncio.gather(
                self._query("search_news", args),
                self._query("search_rss", rss_args, rss=True),
            )
            items = items + rss
            unavailable = unavailable and rss_down
            message = None if items else (message or rss_message)
        return NewsItemList(
            items=await self._decorate(access, items, topics_of(row), row.exclusions),
            unavailable=unavailable,
            message=message,
        )

    async def today(self, access: WorkspaceAccess) -> NewsTodayOut:
        row = await self._enabled_for(access.workspace.id)
        if row is None:
            latest = await self.latest_briefing(access.workspace.id)
            return NewsTodayOut(
                briefing=briefing_out(latest) if latest else None,
                topics=[],
                unavailable=False,
                message="早报没有启用",
            )
        day = self.local_today()
        briefing = await self.latest_briefing(access.workspace.id, day)
        topics = [t for t in topics_of(row) if t.enabled]
        if not self.deployed:
            return NewsTodayOut(
                briefing=briefing_out(briefing) if briefing else None,
                topics=[],
                unavailable=True,
                message="这个部署没有安装 TrendRadar",
            )
        # 热榜与 RSS 同时查（各自一个 MCP 会话），TrendRadar 慢时页面最多等一次超时
        (hot, message, unavailable), (rss, _, rss_down) = await asyncio.gather(
            self._query("get_news_by_date", {"limit": TODAY_LIMIT, "include_url": True}),
            self._query("get_latest_rss", {"days": 1, "limit": 200}, rss=True),
        )
        decorated = await self._decorate(access, hot + rss, topics, row.exclusions)
        grouped: dict[str, list[NewsItemOut]] = {t.name: [] for t in topics}
        seen: set[str] = set()
        for item in decorated:
            if item.topic is None or item.dismissed or item.url in seen:
                continue
            bucket = grouped[item.topic]
            if len(bucket) < TODAY_PER_TOPIC:
                bucket.append(item)
                seen.add(item.url)
        return NewsTodayOut(
            briefing=briefing_out(briefing) if briefing else None,
            topics=[NewsTopicItems(name=name, items=items) for name, items in grouped.items()],
            unavailable=unavailable and rss_down,
            message=message,
        )

    # ------------------------------------------------------------ 收藏 / 不感兴趣

    async def add_feedback(
        self, actor: Actor, access: WorkspaceAccess, body: NewsFeedbackIn
    ) -> NewsFeedbackOut:
        if access.user_id is None:
            raise ApiError(403, "FORBIDDEN", "需要登录用户")
        if body.exclude_word is not None and not has_role(access.role, Role.owner):
            raise ApiError(403, "FORBIDDEN_ROLE", "只有 owner 能修改全局排除词")
        existing = await self.db.scalar(
            select(NewsFeedback).where(
                NewsFeedback.workspace_id == access.workspace.id,
                NewsFeedback.user_id == access.user_id,
                NewsFeedback.kind == body.kind,
                NewsFeedback.url == body.url,
            )
        )
        if existing is None:
            candidate = NewsFeedback(
                workspace_id=access.workspace.id,
                user_id=access.user_id,
                kind=body.kind,
                url=body.url,
                title=" ".join(body.title.split()),
                source_type=body.source_type,
                source_id=body.source_id,
                source_name=body.source_name,
                topic=body.topic,
            )
            try:
                # 单独的保存点：并发的同一条反馈只让这一步失败，不影响后面的排除词等修改
                async with self.db.begin_nested():
                    self.db.add(candidate)
                existing = candidate
            except IntegrityError:
                existing = await self.db.scalar(
                    select(NewsFeedback).where(
                        NewsFeedback.workspace_id == access.workspace.id,
                        NewsFeedback.user_id == access.user_id,
                        NewsFeedback.kind == body.kind,
                        NewsFeedback.url == body.url,
                    )
                )
                if existing is None:
                    raise
            # 收藏与不感兴趣互斥：标了一个就去掉另一个
            other = "dismiss" if body.kind == "favorite" else "favorite"
            opposite = await self.db.scalar(
                select(NewsFeedback).where(
                    NewsFeedback.workspace_id == access.workspace.id,
                    NewsFeedback.user_id == access.user_id,
                    NewsFeedback.kind == other,
                    NewsFeedback.url == body.url,
                )
            )
            if opposite is not None:
                await self.db.delete(opposite)
        if body.exclude_word is not None and body.kind == "dismiss":
            row = await self.row(access.workspace.id, lock=True)
            if row is not None and body.exclude_word not in row.exclusions:
                row.exclusions = [*row.exclusions, body.exclude_word][-100:]
                row.version += 1
                await self.db.flush()
                await self.render_current(touch=row)
                await AuditTrail(self.db, self.settings).record(
                    "news.exclusion_added",
                    workspace_ids=[access.workspace.id],
                    actor_id=access.user_id,
                    resource_type="news_settings",
                    resource_id=access.workspace.id,
                    metadata={"word": body.exclude_word},
                    client_ip=getattr(actor, "client_ip", None),
                )
        events.mark(self.db, access.workspace.id, "news")
        await self.db.commit()
        await self.db.refresh(existing)
        return _feedback_out(existing)

    async def remove_feedback(self, access: WorkspaceAccess, feedback_id: uuid.UUID) -> None:
        row = await self.db.get(NewsFeedback, feedback_id)
        if row is None or row.workspace_id != access.workspace.id or row.user_id != access.user_id:
            raise ApiError(404, "NOT_FOUND", "记录不存在")
        await self.db.delete(row)
        events.mark(self.db, access.workspace.id, "news")
        await self.db.commit()

    async def list_feedback(
        self, access: WorkspaceAccess, kind: str, cursor: str | None, limit: int
    ) -> NewsFeedbackPage:
        stmt = (
            select(NewsFeedback)
            .where(
                NewsFeedback.workspace_id == access.workspace.id,
                NewsFeedback.user_id == access.user_id,
                NewsFeedback.kind == kind,
            )
            .order_by(NewsFeedback.created_at.desc(), NewsFeedback.id.desc())
            .limit(limit + 1)
        )
        if cursor:
            at, row_id = _decode_cursor(cursor)
            stmt = stmt.where(
                (NewsFeedback.created_at < at)
                | ((NewsFeedback.created_at == at) & (NewsFeedback.id < row_id))
            )
        rows = list(await self.db.scalars(stmt))
        page = rows[:limit]
        return NewsFeedbackPage(
            items=[_feedback_out(r) for r in page],
            next_cursor=_encode_cursor(page[-1].created_at, page[-1].id)
            if len(rows) > limit
            else None,
        )


def _required_secrets(channel: str) -> tuple[str, ...]:
    """启用一个渠道至少要有的密钥字段（ntfy 的 token 可选）。"""
    return {"ntfy": ("topic",), "email": ("password",)}.get(channel, cfg.CHANNEL_SECRETS[channel])
