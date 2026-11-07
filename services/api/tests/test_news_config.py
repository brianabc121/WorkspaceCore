"""新闻早报的纯函数与 MCP 客户端（T-0153）：配置文件生成、批次解析、主题匹配、链接过滤、
TrendRadar MCP 的握手 / SSE / 错误归类 / 工具白名单。不需要数据库。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.domain import news_config as cfg
from app.domain.news import check_url, match_topic, safe_link, slot_for, split_batch
from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp, OutboundPolicy
from app.integrations.trendradar import TrendRadarClient
from app.schemas.news import NewsTopicIn
from tests.fake_trendradar import HOST, URL, FakeTrendRadar


def load_config(text: str) -> dict[str, object]:
    """config.yaml 是一行注释 + JSON（JSON 也是合法的 YAML）。"""
    head, body = text.split("\n", 1)
    assert head.startswith("# ")
    data: dict[str, object] = json.loads(body)
    return data


def _input(**overrides: object) -> cfg.RenderInput:
    base: dict[str, object] = {
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "push_times": ["07:30", "18:00"],
        "briefing_mode": "trendradar",
        "platforms": ["zhihu", "weibo", "not-a-platform"],
        "newsnow_api_url": "",
        "feeds": [cfg.Feed("tech", "科技", "https://tech.example.com/rss.xml")],
        "topics": [
            cfg.Topic("AI", ["AI", "大模型", "/GPT-\\d/"], ["发布"], 10, "AI 新进展"),
            cfg.Topic("芯片", ["芯片"], (), 5),
            cfg.Topic("关掉的", ["xx"], (), 5, enabled=False),
        ],
        "exclusions": ["八卦", "绯闻"],
        "filter_method": "ai",
        "ai_model": "openai/gpt-4o-mini",
        "ai_api_base": "https://llm.example.com/v1",
        "ai_api_key": "sk-test-key",
        "ai_analysis": True,
        "ai_translation": False,
        "channels": {"ntfy": {"enabled": True, "server_url": "https://ntfy.sh"}},
        "channel_secrets": {"ntfy": {"topic": "my-topic"}, "feishu": {"webhook_url": "x"}},
        "ingest_url": "http://api:8000/internal/news/ingest/tok",
    }
    base.update(overrides)
    return cfg.RenderInput(**base)  # type: ignore[arg-type]


def test_config_yaml_is_complete_and_parses() -> None:
    files = cfg.render(_input())
    assert set(files) == set(cfg.FILES)
    config: dict[str, Any] = load_config(files["config.yaml"])
    assert config["app"]["timezone"] == "Asia/Shanghai"
    assert config["schedule"] == {"enabled": True, "preset": "custom"}
    # 只保留核验过的平台，带 expected_domain
    assert [s["id"] for s in config["platforms"]["sources"]] == ["zhihu", "weibo"]
    assert config["platforms"]["sources"][0]["expected_domain"] == "zhihu.com"
    assert config["rss"]["feeds"][0]["url"] == "https://tech.example.com/rss.xml"
    assert config["filter"]["method"] == "ai"
    assert config["ai"]["api_key"] == "sk-test-key"
    # 版本检查一律关闭（不访问 GitHub）
    advanced = config["advanced"]
    assert advanced["version_check_url"] == advanced["mcp_version_check_url"] == ""
    assert advanced["configs_version_check_url"] == ""
    channels = config["notification"]["channels"]
    # 工作台的接收地址总是第一个通用 Webhook；没启用的渠道不写凭证
    assert channels["generic_webhook"]["webhook_url"] == "http://api:8000/internal/news/ingest/tok"
    assert channels["ntfy"]["topic"] == "my-topic"
    assert channels["feishu"]["webhook_url"] == ""
    assert config["storage"]["local"]["retention_days"] == 30


def test_without_model_filter_falls_back_to_keyword_and_keyless_gets_placeholder() -> None:
    config: dict[str, Any] = load_config(cfg.render_config(_input(ai_model="", filter_method="ai")))
    assert config["filter"]["method"] == "keyword"
    assert config["ai"]["api_key"] == ""
    assert config["ai_analysis"]["enabled"] is False
    keyless: dict[str, Any] = load_config(
        cfg.render_config(_input(ai_model="ollama/qwen2.5", ai_api_key=""))
    )
    assert keyless["ai"]["api_key"] == cfg.KEYLESS_PLACEHOLDER


def test_user_webhook_follows_ingest_url() -> None:
    files = cfg.render(
        _input(
            channels={"webhook": {"enabled": True}},
            channel_secrets={"webhook": {"url": "https://hook.example.com/x"}},
        )
    )
    config: dict[str, Any] = load_config(files["config.yaml"])
    url = config["notification"]["channels"]["generic_webhook"]
    assert url["webhook_url"] == (
        "http://api:8000/internal/news/ingest/tok;https://hook.example.com/x"
    )


def test_frequency_words_groups_and_global_filter() -> None:
    text = cfg.render_frequency_words(_input())
    head, groups = text.split("[WORD_GROUPS]")
    assert "[GLOBAL_FILTER]\n八卦\n绯闻" in head
    assert "[AI]\nAI\n大模型\n/GPT-\\d/\n+发布\n@10" in groups
    assert "[芯片]\n芯片\n@5" in groups
    assert "关掉的" not in groups


def test_learning_reorders_and_adjusts_items() -> None:
    learned = cfg.Learned({"芯片": 20, "AI": -9})
    topics = cfg.adjusted_topics(_input().topics, learned)
    assert [t.name for t in topics] == ["芯片", "AI"]
    assert topics[0].max_items == 15  # 5 + 最多 10
    assert topics[1].max_items == cfg.TOPIC_MIN_ITEMS  # 10 - 9 = 1，最少 3
    interests = cfg.render_interests(_input(learned=learned))
    assert "更关注：芯片" in interests
    assert "少推：AI" in interests
    assert "不想看：八卦、绯闻" in interests


def test_timeline_custom_periods() -> None:
    text = cfg.render_timeline(_input())
    assert "  default:\n    collect: true\n    analyze: false" in text
    assert (
        '    push_1:\n      name: "推送 07:30"\n      start: "07:30"\n      end: "08:00"\n'
        "      collect: true\n      analyze: true\n      push: true\n"
    ) in text
    assert '    push_2:\n      name: "推送 18:00"\n      start: "18:00"\n      end: "18:30"' in text
    assert "      periods: [push_1, push_2]" in text
    # week_map 是整数键 1~7
    assert "  week_map:\n" + "".join(f"    {d}: all_day\n" for d in range(1, 8)) in text


def test_timeline_agent_mode_and_disabled_do_not_push() -> None:
    agent = cfg.render_timeline(_input(briefing_mode="agent"))
    assert "      analyze: false\n      push: false\n" in agent
    off = cfg.render_timeline(_input(enabled=False))
    assert "  default:\n    collect: false" in off
    config: dict[str, Any] = load_config(cfg.render_config(_input(enabled=False)))
    assert config["notification"]["enabled"] is False


def test_window_crosses_midnight() -> None:
    assert cfg.window("23:30") == ("23:30", "00:00")


def test_write_files_is_atomic_and_private(tmp_path: Path) -> None:
    cfg.write_files(tmp_path / "conf", cfg.render(_input()))
    for name in cfg.FILES:
        path = tmp_path / "conf" / name
        assert path.stat().st_mode & 0o777 == 0o640
    assert not [p for p in (tmp_path / "conf").iterdir() if p.name.startswith(".")]


def test_split_batch() -> None:
    assert split_batch("**[第 2/3 批次]**\n\n正文") == (2, 3, "正文")
    assert split_batch("[第 1/2 批次]\n\n正文") == (1, 2, "正文")
    assert split_batch("没有头部") == (1, 1, "没有头部")
    assert split_batch("**[第 4/3 批次]**\n\n坏的") == (1, 1, "**[第 4/3 批次]**\n\n坏的")


def test_slot_for() -> None:
    def at(hm: str) -> datetime:
        return datetime.fromisoformat(f"2026-11-08T{hm}:00")

    assert slot_for(at("07:30"), ["07:30"]) == "07:30"
    assert slot_for(at("07:59"), ["07:30"]) == "07:30"
    assert slot_for(at("08:00"), ["07:30"]) == ""
    assert slot_for(at("18:10"), ["07:30", "18:00"]) == "18:00"


def test_match_topic() -> None:
    topics = [
        NewsTopicIn(name="AI", keywords=["AI", "/gpt-\\d/"], required=[]),
        NewsTopicIn(name="发布", keywords=["模型"], required=["发布"]),
    ]
    assert match_topic("OpenAI 发布新模型", topics, []) == "AI"
    assert match_topic("GPT-5 来了", topics, []) == "AI"
    assert match_topic("新模型发布会", topics, []) == "发布"
    assert match_topic("新模型", topics, []) is None
    assert match_topic("AI 八卦", topics, ["八卦"]) is None


def test_safe_link_and_check_url() -> None:
    assert safe_link("javascript:alert(1)") == ""
    assert safe_link("https://a.example.com/x") == "https://a.example.com/x"
    allowed = frozenset({"fake-news"})
    assert check_url("https://rss.example.com/a.xml", allowed, ["x"]) == (
        "https://rss.example.com/a.xml"
    )
    assert check_url("http://fake-news:8080/rss", allowed, ["x"]) == "http://fake-news:8080/rss"
    for bad in (
        "ftp://rss.example.com",
        "http://api:8000/x",
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://10.0.0.5/x",
        "http://169.254.169.254/latest",
        "http://user:pw@rss.example.com/",
        "http://printer.local/",
    ):
        with pytest.raises(Exception) as caught:
            check_url(bad, allowed, ["x"])
        assert getattr(caught.value, "status_code", None) == 422, bad


def _client(fake: FakeTrendRadar) -> TrendRadarClient:
    http = ConnectorHttp(
        policy=OutboundPolicy(frozenset({HOST})),
        read_retries=0,
        transport=httpx.MockTransport(fake.handler),
    )
    return TrendRadarClient(URL, http)


async def test_client_handshake_sse_and_session_close() -> None:
    fake = FakeTrendRadar()
    payload = await _client(fake).call("get_latest_news", {"limit": 2})
    assert [i["title"] for i in payload["data"]] == [
        "OpenAI 发布新一代推理模型",
        "英伟达财报超预期，AI 芯片需求强劲",
    ]
    assert fake.closed == fake.sessions and len(fake.sessions) == 1


async def test_client_classifies_errors_and_refuses_other_tools() -> None:
    fake = FakeTrendRadar()
    client = _client(fake)
    fake.missing.add("get_news_by_date")
    with pytest.raises(ConnectorError) as missing:
        await client.call("get_news_by_date", {})
    assert missing.value.kind == "NOT_FOUND"
    for tool in ("trigger_crawl", "read_article", "send_notification", "check_version"):
        with pytest.raises(ConnectorError) as refused:
            await client.call(tool, {})
        assert refused.value.reason == "tool_not_allowed"
    assert fake.tools_called() == ["get_news_by_date"]  # 不开放的工具根本没发出去
    fake.down = True
    with pytest.raises(ConnectorError) as down:
        await client.call("get_latest_news", {})
    assert down.value.kind == "UNAVAILABLE"


async def test_client_notify_sends_only_given_channels() -> None:
    fake = FakeTrendRadar()
    result = await _client(fake).notify(title="t", message="m", channels=["ntfy"])
    assert result["results"] == {"ntfy": {"success": True, "detail": ""}}
    assert fake.notifications == [{"title": "t", "message": "m", "channels": ["ntfy"]}]
    with pytest.raises(ConnectorError):
        await _client(fake).notify(title="t", message="m", channels=[])


def test_tool_error_text_is_validation() -> None:
    from app.integrations.trendradar import tool_payload

    with pytest.raises(ConnectorError) as caught:
        tool_payload(
            {"result": {"content": [{"type": "text", "text": "bad input"}], "isError": True}}
        )
    assert caught.value.kind == "VALIDATION_FAILED"
    assert tool_payload({"result": {"structuredContent": {"result": json.dumps({"a": 1})}}}) == {
        "a": 1
    }
