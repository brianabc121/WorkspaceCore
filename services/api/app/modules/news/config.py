"""新闻早报：按工作台的设置生成 TrendRadar 6.11.0 的配置文件
（T-0153，docs/v2/NEWS_PLAN.md 第 4 节）。

纯函数，不碰数据库：输入设置、明文密钥（调用方刚解密）与学到的偏好，输出 {文件名: 内容}。
格式按 docs/decisions/2026-11-07-trendradar-verification.md 第 3 节核验：
- config.yaml 写成 JSON（YAML 是 JSON 的超集，TrendRadar 用 yaml.safe_load 读）；
  每个键都写全，因为缺键时的内置默认值与示例不同；
- timeline.yaml 的 week_map 要整数键，JSON 写不出，所以单独输出 YAML；
  里面的值全是本模块生成的（时间、固定的键），不含用户输入；
- frequency_words.txt：[GLOBAL_FILTER] 每行一个排除词，[WORD_GROUPS] 每组空行分隔，
  `[名称]` 起头，`+词` 必含，`@N` 条数上限；
  上游的 `!词` 实际对所有组生效，所以排除只放在 [GLOBAL_FILTER]；
- ai_interests.txt：给 AI 筛选的自然语言兴趣。

版本检查地址一律为空（不访问 GitHub）；存储固定本地、保留 30 天。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 核验过的 NewsNow 平台（id → 名称、域名）；设置里只能从这里选，外加自建 NewsNow 的地址
PLATFORMS: dict[str, tuple[str, str]] = {
    "toutiao": ("今日头条", "toutiao.com"),
    "baidu": ("百度热搜", "baidu.com"),
    "wallstreetcn-hot": ("华尔街见闻", "wallstreetcn.com"),
    "thepaper": ("澎湃新闻", "thepaper.cn"),
    "bilibili-hot-search": ("bilibili 热搜", "bilibili.com"),
    "cls-hot": ("财联社热门", "cls.cn"),
    "ifeng": ("凤凰网", "ifeng.com"),
    "tieba": ("贴吧", "baidu.com"),
    "weibo": ("微博", "weibo.com"),
    "douyin": ("抖音", "douyin.com"),
    "zhihu": ("知乎", "zhihu.com"),
}
# 人类方案里举的平台
DEFAULT_PLATFORMS = [
    "zhihu",
    "weibo",
    "douyin",
    "bilibili-hot-search",
    "wallstreetcn-hot",
    "cls-hot",
]
# 渠道 → 它的密钥字段（加密保存）；其余字段是明文设置
CHANNEL_SECRETS: dict[str, tuple[str, ...]] = {
    "feishu": ("webhook_url",),
    "dingtalk": ("webhook_url",),
    "wework": ("webhook_url",),
    "telegram": ("bot_token",),
    "email": ("password",),
    "ntfy": ("topic", "token"),
    "bark": ("url",),
    "slack": ("webhook_url",),
    "webhook": ("url",),
}
CHANNELS = tuple(CHANNEL_SECRETS)
# 方案 B 时 TrendRadar 自己不推送，但工作台要经 MCP send_notification 推送，渠道仍然写进配置
FILES = ("config.yaml", "frequency_words.txt", "timeline.yaml", "ai_interests.txt")
WINDOW_MINUTES = 30  # 定时运行每 30 分钟一次（TRENDRADAR_CRON），推送时间段也是 30 分钟
KEYLESS_PLACEHOLDER = "not-needed"  # 上游要求有 Key；Ollama 这类不要 Key 的接口填占位值
TOPIC_MIN_ITEMS = 3
TOPIC_MAX_BONUS = 10


@dataclass(frozen=True)
class Topic:
    name: str
    keywords: Sequence[str]
    required: Sequence[str] = ()
    max_items: int = 10
    description: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    url: str
    enabled: bool = True
    max_age_days: int = 0


@dataclass(frozen=True)
class Learned:
    """偏好学习的结果（第 8 节）：主题分数 = 收藏 − 不感兴趣（最近 60 天）。"""

    topic_scores: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderInput:
    enabled: bool
    timezone: str
    push_times: Sequence[str]  # "HH:MM"，半小时为单位
    briefing_mode: str  # trendradar | agent
    platforms: Sequence[str]
    newsnow_api_url: str
    feeds: Sequence[Feed]
    topics: Sequence[Topic]
    exclusions: Sequence[str]
    filter_method: str  # keyword | ai
    ai_model: str
    ai_api_base: str
    ai_api_key: str
    ai_analysis: bool
    ai_translation: bool
    channels: Mapping[str, Mapping[str, Any]]  # 渠道 → 明文设置（含 enabled）
    channel_secrets: Mapping[str, Mapping[str, str]]  # 渠道 → 密钥字段
    ingest_url: str  # 工作台的接收地址（总是第一个通用 Webhook）
    learned: Learned = field(default_factory=Learned)


def window(start: str, minutes: int = WINDOW_MINUTES) -> tuple[str, str]:
    """推送时间段 [start, start+minutes)；跨过午夜时 end < start，上游按跨日处理。"""
    t = datetime.strptime(start, "%H:%M")
    return start, (t + timedelta(minutes=minutes)).strftime("%H:%M")


def adjusted_topics(topics: Sequence[Topic], learned: Learned) -> list[Topic]:
    """按偏好学习调整：分数高的主题排前面、条数上调（最多 +10），分数低的下调
    （最少 3 条；本来就设得不到 3 条的不再往下调，也不会被调多）。

    max_items 为 0 表示不限，保持不变；同分保持原来的顺序。
    """
    out: list[tuple[int, int, Topic]] = []
    for i, t in enumerate(topics):
        if not t.enabled:
            continue
        score = int(learned.topic_scores.get(t.name, 0))
        items = t.max_items
        if items > 0 and score:
            floor = min(TOPIC_MIN_ITEMS, items)
            items = max(floor, items + max(-items, min(TOPIC_MAX_BONUS, score)))
        out.append(
            (
                -score,
                i,
                Topic(t.name, t.keywords, t.required, items, t.description, t.enabled),
            )
        )
    return [t for _, _, t in sorted(out, key=lambda x: (x[0], x[1]))]


def _word(w: str) -> str:
    """关键词一行一个；换行、方括号开头、空行都会破坏上游的格式，这里去掉。"""
    w = " ".join(w.split())
    return w.lstrip("[").strip()


def render_frequency_words(inp: RenderInput) -> str:
    lines = [
        "# 由工作台生成（docs/v2/NEWS_PLAN.md 第 4 节），在“设置 → 新闻早报”里修改",
        "",
        "[GLOBAL_FILTER]",
    ]
    lines += [w for w in (_word(x) for x in inp.exclusions) if w]
    lines += ["", "[WORD_GROUPS]"]
    for t in adjusted_topics(inp.topics, inp.learned):
        words = [
            w for w in (_word(x) for x in t.keywords) if w and not w.startswith(("+", "!", "@"))
        ]
        required = [w for w in (_word(x) for x in t.required) if w]
        if not words and not required:
            continue
        group = [f"[{_word(t.name).rstrip(']')}]", *words, *(f"+{w.lstrip('+')}" for w in required)]
        if t.max_items > 0:
            group.append(f"@{t.max_items}")
        lines += ["", *group]
    return "\n".join(lines) + "\n"


def render_interests(inp: RenderInput) -> str:
    topics = adjusted_topics(inp.topics, inp.learned)
    lines = ["# 由工作台生成：AI 筛选按这里的兴趣给每条新闻打分", "", "我关注的方向："]
    for t in topics:
        desc = " ".join(t.description.split()) or "、".join(_word(k) for k in t.keywords[:8])
        lines.append(f"- {_word(t.name)}：{desc}")
    liked = [t.name for t in topics if inp.learned.topic_scores.get(t.name, 0) > 0]
    disliked = [t.name for t in topics if inp.learned.topic_scores.get(t.name, 0) < 0]
    if liked:
        lines += ["", "更关注：" + "、".join(liked)]
    if disliked:
        lines += ["", "少推：" + "、".join(disliked)]
    if inp.exclusions:
        lines += ["", "不想看：" + "、".join(w for w in (_word(x) for x in inp.exclusions) if w)]
    return "\n".join(lines) + "\n"


def _q(value: str) -> str:
    """YAML 的双引号字符串：JSON 字符串的转义都是 YAML 合法的。"""
    return json.dumps(value, ensure_ascii=False)


def render_timeline(inp: RenderInput) -> str:
    """只有 custom：平时只抓取；每个推送时间一个 30 分钟的时间段，方案 A 在这里分析并推送一次。"""
    push = inp.enabled and inp.briefing_mode == "trendradar"
    times = sorted(set(inp.push_times))
    lines = [
        "# 由工作台生成（docs/v2/NEWS_PLAN.md 第 4、5 节）",
        "presets: {}",
        "custom:",
        f"  name: {_q('工作台')}",
        "  default:",
        f"    collect: {str(inp.enabled).lower()}",
        "    analyze: false",
        f"    ai_mode: {_q('daily')}",
        "    push: false",
        f"    report_mode: {_q('daily')}",
        "    once:",
        "      analyze: true",
        "      push: true",
        "  periods:" if times else "  periods: {}",
    ]
    for i, t in enumerate(times, 1):
        start, end = window(t)
        lines += [
            f"    push_{i}:",
            f"      name: {_q('推送 ' + start)}",
            f"      start: {_q(start)}",
            f"      end: {_q(end)}",
            f"      collect: {str(inp.enabled).lower()}",
            f"      analyze: {str(push).lower()}",
            f"      push: {str(push).lower()}",
            f"      report_mode: {_q('daily')}",
            f"      ai_mode: {_q('daily')}",
        ]
    plan = ", ".join(f"push_{i}" for i in range(1, len(times) + 1))
    lines += [
        "  day_plans:",
        "    all_day:",
        f"      periods: [{plan}]",
        "  week_map:",
        *(f"    {d}: all_day" for d in range(1, 8)),
        "  overlap:",
        f"    policy: {_q('error_on_overlap')}",
    ]
    return "\n".join(lines) + "\n"


def _channels(inp: RenderInput) -> dict[str, Any]:
    on = {k: v for k, v in inp.channels.items() if v.get("enabled")}
    sec = inp.channel_secrets

    def s(channel: str, key: str) -> str:
        return sec.get(channel, {}).get(key, "") if channel in on else ""

    email = on.get("email", {})
    ntfy = on.get("ntfy", {})
    wework = on.get("wework", {})
    telegram = on.get("telegram", {})
    webhooks = [inp.ingest_url] if inp.ingest_url else []
    if s("webhook", "url"):
        webhooks.append(s("webhook", "url"))
    return {
        "feishu": {"webhook_url": s("feishu", "webhook_url")},
        "dingtalk": {"webhook_url": s("dingtalk", "webhook_url")},
        "wework": {
            "webhook_url": s("wework", "webhook_url"),
            "msg_type": wework.get("msg_type", "markdown"),
        },
        "telegram": {
            "bot_token": s("telegram", "bot_token"),
            "chat_id": str(telegram.get("chat_id", "")),
        },
        "email": {
            "from": email.get("from_addr", ""),
            "password": s("email", "password"),
            "to": email.get("to_addr", ""),
            "smtp_server": email.get("smtp_server", ""),
            "smtp_port": str(email.get("smtp_port", "") or ""),
        },
        "ntfy": {
            "server_url": ntfy.get("server_url", "https://ntfy.sh") if ntfy else "https://ntfy.sh",
            "topic": s("ntfy", "topic"),
            "token": s("ntfy", "token"),
        },
        "bark": {"url": s("bark", "url")},
        "slack": {"webhook_url": s("slack", "webhook_url")},
        # 上游用 ; 分隔多个地址；工作台的接收地址总是第一个
        "generic_webhook": {"webhook_url": ";".join(webhooks), "payload_template": ""},
    }


def render_config(inp: RenderInput) -> str:
    sources = [
        {"id": p, "name": PLATFORMS[p][0], "expected_domain": PLATFORMS[p][1]}
        for p in inp.platforms
        if p in PLATFORMS
    ]
    feeds = [
        {
            "id": f.id,
            "name": f.name,
            "url": f.url,
            "enabled": f.enabled,
            "max_age_days": f.max_age_days,
        }
        for f in inp.feeds
    ]
    ai_on = bool(inp.ai_model)
    config: dict[str, Any] = {
        "app": {"timezone": inp.timezone, "show_version_update": False},
        "schedule": {"enabled": True, "preset": "custom"},
        "platforms": {
            "enabled": inp.enabled and bool(sources),
            "api_url": inp.newsnow_api_url,
            "sources": sources,
        },
        "rss": {
            "enabled": inp.enabled and any(f.enabled for f in inp.feeds),
            "freshness_filter": {"enabled": True, "max_age_days": 3},
            "feeds": feeds,
        },
        "report": {
            "mode": "daily",
            "display_mode": "keyword",
            "sort_by_position_first": False,
            "rank_threshold": 5,
            "max_news_per_keyword": 0,
        },
        "filter": {
            "method": inp.filter_method if ai_on else "keyword",
            "priority_sort_enabled": True,
        },
        "ai_filter": {
            "batch_size": 200,
            "batch_interval": 2,
            "min_score": 0.7,
            "reclassify_threshold": 0.6,
            "prompt_file": "prompt.txt",
            "extract_prompt_file": "extract_prompt.txt",
            "update_tags_prompt_file": "update_tags_prompt.txt",
        },
        "display": {
            "region_order": ["new_items", "hotlist", "rss", "standalone", "ai_analysis"],
            "regions": {
                "hotlist": True,
                "new_items": False,
                "rss": True,
                "standalone": False,
                "ai_analysis": True,
            },
            "standalone": {"platforms": [], "rss_feeds": [], "max_items": 20},
        },
        "notification": {"enabled": inp.enabled, "channels": _channels(inp)},
        "storage": {
            "backend": "local",
            "formats": {"sqlite": True, "txt": False, "html": True},
            "local": {"data_dir": "output", "retention_days": 30},
            "remote": {
                "retention_days": 0,
                "endpoint_url": "",
                "bucket_name": "",
                "access_key_id": "",
                "secret_access_key": "",
                "region": "",
            },
            "pull": {"enabled": False, "days": 7},
        },
        "ai": {
            "model": inp.ai_model,
            "api_key": (inp.ai_api_key or KEYLESS_PLACEHOLDER) if ai_on else "",
            "api_base": inp.ai_api_base,
            "timeout": 120,
            "temperature": 1.0,
            "max_tokens": 5000,
            "num_retries": 1,
            "fallback_models": [],
            "extra_params": {},
        },
        "ai_analysis": {
            "enabled": ai_on and inp.ai_analysis,
            "language": "Chinese",
            "prompt_file": "ai_analysis_prompt.txt",
            "mode": "follow_report",
            "max_news_for_analysis": 150,
            "include_rss": True,
            "include_standalone": False,
            "include_rank_timeline": True,
        },
        "ai_translation": {
            "enabled": ai_on and inp.ai_translation,
            "language": "中文",
            "prompt_file": "ai_translation_prompt.txt",
            "batch_size": 100,
            "batch_interval": 2,
            "scope": {"hotlist": False, "rss": True, "standalone": False},
        },
        "advanced": {
            "debug": False,
            "version_check_url": "",
            "mcp_version_check_url": "",
            "configs_version_check_url": "",
            "crawler": {"request_interval": 1000, "use_proxy": False, "default_proxy": ""},
            "rss": {"request_interval": 1000, "timeout": 15, "use_proxy": False, "proxy_url": ""},
            "weight": {"rank": 0.6, "frequency": 0.3, "hotness": 0.1},
            "max_accounts_per_channel": 3,
            "batch_size": {
                "default": 4000,
                "dingtalk": 20000,
                "feishu": 30000,
                "bark": 4000,
                "slack": 4000,
            },
            "batch_send_interval": 1,
            "feishu_message_separator": "━━━━━━━━━━━━━━━━",
        },
    }
    head = (
        "# 由工作台生成（docs/v2/NEWS_PLAN.md 第 4 节）；JSON 也是合法的 YAML。"
        "在“设置 → 新闻早报”里修改\n"
    )
    return head + json.dumps(config, ensure_ascii=False, indent=2) + "\n"


def render(inp: RenderInput) -> dict[str, str]:
    return {
        "config.yaml": render_config(inp),
        "frequency_words.txt": render_frequency_words(inp),
        "timeline.yaml": render_timeline(inp),
        "ai_interests.txt": render_interests(inp),
    }


def write_files(directory: Path, files: Mapping[str, str], *, mode: int = 0o640) -> None:
    """逐个原子替换（同目录临时文件 + rename），TrendRadar 不会读到写了一半的文件。"""
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(tmp, mode)
            os.replace(tmp, directory / name)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
