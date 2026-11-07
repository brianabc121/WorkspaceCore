"""新闻早报（T-0153，docs/v2/NEWS_PLAN.md）。真实 PostgreSQL + Redis，模拟 TrendRadar MCP
（tests/fake_trendradar.py）与 OpenIM。覆盖：

- 设置：默认值、owner 保存、密钥加密且不回传、生成配置文件（含接收地址）、乐观并发、权限、
  地址校验、一个部署只能一个工作空间启用；
- 接收早报（方案 A）：token、分批拼接、重复批次、缺批、工作台自己推的忽略、通知 owner、大小限制；
- 页面：今天（按主题分组、过滤不感兴趣）、热榜 / RSS / 搜索（链接过滤）、TrendRadar 连不上；
- 收藏 / 不感兴趣：互斥、幂等、只能删自己的、排除词只有 owner 能加并写进配置；偏好学习；
- 智能体工具 news_*：只列给启用了早报的工作空间、返回真实数据、早报正文、不开放的工具调不到；
- 方案 B：到点建任务和唤醒，智能体写 README 并完成后入库与推送，超时记失败；
- 密钥轮换包括新闻的三个字段。
"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.domain import news_agent
from app.domain.credential_rotation import rotate
from app.domain.credentials import CredentialBox
from tests.fake_trendradar import HOST, URL, FakeTrendRadar
from tests.helpers import OWNER, add_member, add_user, add_workspace, run_sql, tool_error
from tests.test_agent_projects_api import tool_names
from tests.test_im_accounts_callbacks import ImEnv, im_env
from tests.test_task_claims_api import agent, call, ok

pytestmark = pytest.mark.integration

AI_KEY = "sk-news-secret-123"
TOPIC_SECRET = "secret-topic-abc"  # noqa: S105 - 测试用的假值
BODY: dict[str, Any] = {
    "enabled": True,
    "push_times": ["07:30"],
    "briefing_mode": "trendradar",
    "platforms": ["zhihu", "weibo", "wallstreetcn-hot"],
    "feeds": [{"id": "tech", "name": "科技", "url": "https://tech.example.com/rss.xml"}],
    "topics": [
        {"name": "AI", "keywords": ["AI", "大模型"], "max_items": 10},
        {"name": "芯片", "keywords": ["芯片"], "max_items": 5},
    ],
    "exclusions": ["八卦"],
    "filter_method": "keyword",
    "ai_model": "openai/gpt-4o-mini",
    "ai_api_base": "https://llm.example.com/v1",
    "ai_api_key": AI_KEY,
    "channels": {
        "ntfy": {
            "enabled": True,
            "server_url": "https://ntfy.sh",
            "secrets": {"topic": TOPIC_SECRET},
        }
    },
}


@pytest.fixture
def trendradar() -> FakeTrendRadar:
    return FakeTrendRadar()


@pytest.fixture
async def env(
    make_client: Any, migrated_database_url: str, tmp_path: Path, trendradar: FakeTrendRadar
) -> AsyncIterator[ImEnv]:
    async with im_env(
        make_client,
        migrated_database_url,
        trendradar_mcp_url=URL,
        news_config_dir=str(tmp_path / "trendradar"),
    ) as env:
        openim = env.app.state.connectors.transport

        async def route(request: httpx.Request) -> httpx.Response:
            if request.url.host == HOST:
                return trendradar.handler(request)
            response: httpx.Response = await openim.handle_async_request(request)
            return response

        env.app.state.connectors.transport = httpx.MockTransport(route)
        yield env


def conf(env: ImEnv, name: str = "config.yaml") -> Any:
    """config.yaml 是一行注释 + JSON；其他文件返回原文。"""
    text = (conf_dir(env) / name).read_text()
    return json.loads(text.split("\n", 1)[1]) if name == "config.yaml" else text


def conf_dir(env: ImEnv) -> Path:
    return Path(env.app.state.settings.news_config_dir)


async def settings(env: ImEnv, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = await env.client.get("/api/v1/news/settings", headers=headers or env.headers())
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def save(env: ImEnv, expect: int = 200, **overrides: Any) -> dict[str, Any]:
    current = await settings(env)
    body = {**BODY, "version": current["version"], **overrides}
    response = await env.client.put(
        "/api/v1/news/settings", json=body, headers=env.headers(write=True)
    )
    assert response.status_code == expect, response.text
    data: dict[str, Any] = response.json()
    return data


async def ingest_token(env: ImEnv) -> str:
    config = conf(env)
    url: str = config["notification"]["channels"]["generic_webhook"]["webhook_url"].split(";")[0]
    assert url.startswith("http://api:8000/internal/news/ingest/")
    return url.rsplit("/", 1)[1]


async def push(env: ImEnv, token: str, title: str, content: str) -> httpx.Response:
    return await env.client.post(
        f"/internal/news/ingest/{token}",
        content=json.dumps({"title": title, "content": content}),
        headers={"Content-Type": "application/json"},
    )


async def login(env: ImEnv, email: str, password: str) -> None:
    response = await env.client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------- 设置


async def test_settings_save_encrypts_secrets_and_renders_files(env: ImEnv) -> None:
    initial = await settings(env)
    assert initial["version"] == 0 and initial["enabled"] is False
    assert initial["deployed"] is True and initial["taken_by_other"] is False
    assert initial["topics"][0]["name"] == "AI 与大模型"
    assert {c["id"] for c in initial["platform_choices"]} >= {"zhihu", "weibo", "cls-hot"}

    saved = await save(env)
    text = json.dumps(saved, ensure_ascii=False)
    assert AI_KEY not in text and TOPIC_SECRET not in text
    assert saved["ai_api_key_set"] is True
    assert saved["channels"]["ntfy"]["secrets_set"] == ["topic"]
    assert saved["channels"]["ntfy"]["enabled"] is True
    assert saved["version"] == 1 and saved["config_rev"] == 1 and saved["render_error"] == ""

    refs = await run_sql(
        env.database_url,
        "SELECT ai_key_ref || channel_secrets_ref || ingest_token_ref FROM workspace.news_settings",
    )
    assert AI_KEY not in refs and TOPIC_SECRET not in refs and "enc:v1:" in refs

    files = conf_dir(env)
    for name in ("config.yaml", "frequency_words.txt", "timeline.yaml", "ai_interests.txt"):
        assert (files / name).stat().st_mode & 0o777 == 0o640
    config = conf(env)
    assert config["ai"]["api_key"] == AI_KEY
    assert config["notification"]["channels"]["ntfy"]["topic"] == TOPIC_SECRET
    assert len(await ingest_token(env)) >= 32
    words = (files / "frequency_words.txt").read_text()
    assert "[AI]\nAI\n大模型\n@10" in words and "[GLOBAL_FILTER]\n八卦" in words

    audit = await run_sql(
        env.database_url,
        "SELECT metadata_redacted_json::text FROM workspace.audit_logs "
        "WHERE action = 'news.settings_updated'",
    )
    assert AI_KEY not in audit and TOPIC_SECRET not in audit

    # 不给 Key 表示不改；空字符串表示清除
    kept = await save(env, ai_api_key=None)
    assert kept["ai_api_key_set"] is True
    assert conf(env)["ai"]["api_key"] == AI_KEY
    cleared = await save(env, ai_api_key="", ai_model="ollama/qwen2.5")
    assert cleared["ai_api_key_set"] is False
    assert conf(env)["ai"]["api_key"] == "not-needed"

    # 改关键词，配置文件跟着变
    await save(env, topics=[{"name": "新能源", "keywords": ["新能源", "电池"], "max_items": 8}])
    words = (files / "frequency_words.txt").read_text()
    assert "[新能源]\n新能源\n电池\n@8" in words and "[AI]" not in words

    stale = await env.client.put(
        "/api/v1/news/settings",
        json={**BODY, "version": 1},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"

    # 关闭早报：生成不抓取、不推送的配置
    await save(env, enabled=False)
    assert "  default:\n    collect: false" in conf(env, "timeline.yaml")
    assert conf(env)["notification"]["enabled"] is False


async def test_settings_validation(env: ImEnv) -> None:
    for overrides in (
        {"feeds": [{"id": "x", "name": "x", "url": "http://10.0.0.1/rss"}]},
        {"feeds": [{"id": "x", "name": "x", "url": "http://api:8000/rss"}]},
        {"newsnow_api_url": "http://127.0.0.1:4444"},
        {"ai_api_base": "http://169.254.169.254/latest"},
        {"channels": {"feishu": {"enabled": True}}},
        {"channels": {"webhook": {"enabled": True, "secrets": {"url": "http://localhost/x"}}}},
        {"channels": {"ntfy": {"enabled": True, "secrets": {"password": "x"}}}},
        {"briefing_mode": "agent"},
        {"push_times": ["07:15"]},
        {"timezone": "Asia/Tokyo"},  # 时区由部署设置，网页上改不了
        {"topics": [{"name": "[坏]", "keywords": ["x"]}]},
        {"ai_model": "gpt-4o"},
    ):
        current = await settings(env)
        response = await env.client.put(
            "/api/v1/news/settings",
            json={**BODY, "version": current["version"], **overrides},
            headers=env.headers(write=True),
        )
        assert response.status_code == 422, (overrides, response.text)


async def test_settings_permissions(env: ImEnv) -> None:
    workspace = uuid.UUID(env.workspace_id)
    admin = await add_user(env.database_url, "admin@example.com", "admin password 1234")
    member = await add_user(env.database_url, "member@example.com", "member password 1234")
    await add_member(env.database_url, workspace, admin, "admin")
    await add_member(env.database_url, workspace, member, "member")
    await save(env)

    await login(env, "admin@example.com", "admin password 1234")
    assert (await settings(env))["version"] == 1  # admin 能看
    denied = await env.client.put(
        "/api/v1/news/settings", json={**BODY, "version": 1}, headers=env.headers(write=True)
    )
    assert denied.status_code == 403
    test_push = await env.client.post(
        "/api/v1/news/settings/test-push", headers=env.headers(write=True)
    )
    assert test_push.status_code == 403

    await login(env, "member@example.com", "member password 1234")
    hidden = await env.client.get("/api/v1/news/settings", headers=env.headers())
    assert hidden.status_code == 403
    today = await env.client.get("/api/v1/news/today", headers=env.headers())
    assert today.status_code == 200  # 成员能看早报


async def test_only_one_workspace_can_enable(env: ImEnv) -> None:
    await save(env)
    other = await add_workspace(env.database_url, env.owner_id, "第二个空间")
    headers = {**env.headers(write=True), "X-Workspace-Id": str(other)}
    current = await settings(env, {"X-Workspace-Id": str(other)})
    assert current["taken_by_other"] is True
    response = await env.client.put(
        "/api/v1/news/settings", json={**BODY, "version": 0}, headers=headers
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "NEWS_TAKEN"
    # 不启用可以保存，且不覆盖正在用的配置文件
    saved = await env.client.put(
        "/api/v1/news/settings",
        json={**BODY, "version": 0, "enabled": False, "ai_api_key": "sk-other"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    config = conf(env)
    assert config["ai"]["api_key"] == AI_KEY


async def test_test_push_goes_through_trendradar(env: ImEnv, trendradar: FakeTrendRadar) -> None:
    disabled = await env.client.post(
        "/api/v1/news/settings/test-push", headers=env.headers(write=True)
    )
    assert disabled.status_code == 409
    await save(env)
    response = await env.client.post(
        "/api/v1/news/settings/test-push", headers=env.headers(write=True)
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"] == [{"channel": "ntfy", "ok": True, "detail": ""}]
    sent = trendradar.notifications[-1]
    assert sent["channels"] == ["ntfy"] and sent["title"].startswith("【工作台】")


# ---------------------------------------------------------------- 接收早报


async def test_ingest_assembles_batches_and_notifies_owner(env: ImEnv) -> None:
    await save(env)
    token = await ingest_token(env)
    assert (await push(env, "x" * 43, "全天汇总", "正文")).status_code == 404
    first = await push(
        env, token, "全天汇总", "**[第 1/3 批次]**\n\n## 热榜\n- [A](https://a.example.com)"
    )
    assert first.status_code == 200 and first.json() == {"result": "ok"}
    again = await push(env, token, "全天汇总", "**[第 1/3 批次]**\n\n重复")
    assert again.status_code == 200
    # 第 1 批重发会重新开始一份：上一份记为不完整
    assert (await push(env, token, "全天汇总", "**[第 2/3 批次]**\n\n第二批")).json() == {
        "result": "ok"
    }
    duplicate = await push(env, token, "全天汇总", "**[第 2/3 批次]**\n\n第二批")
    assert duplicate.json() == {"result": "duplicate"}
    assert (await push(env, token, "全天汇总", "**[第 3/3 批次]**\n\n## AI 分析\n总结")).json() == {
        "result": "ok"
    }
    ignored = await push(env, token, "【工作台】测试推送", "这是工作台自己推的")
    assert ignored.json() == {"result": "ignored"}

    page = (await env.client.get("/api/v1/news/briefings", headers=env.headers())).json()
    statuses = [b["status"] for b in page["items"]]
    assert statuses == ["complete", "failed"]
    latest = await env.client.get(
        f"/api/v1/news/briefings/{page['items'][0]['id']}", headers=env.headers()
    )
    body = latest.json()
    assert body["parts"] == 3 and body["total_parts"] == 3 and body["source"] == "trendradar"
    assert body["content"] == "重复\n\n第二批\n\n## AI 分析\n总结"
    assert "全天汇总" in body["title"]
    notes = await env.client.get("/api/v1/notifications", headers=env.headers())
    assert [n["title"] for n in notes.json()["items"]].count("今日早报已送达") == 1

    too_big = await push(env, token, "全天汇总", "x" * 70_000)
    assert too_big.status_code == 413


async def test_ingest_single_batch_gap_and_disabled(env: ImEnv) -> None:
    await save(env)
    token = await ingest_token(env)
    assert (await push(env, token, "当前榜单", "只有一批")).json() == {"result": "ok"}
    await push(env, token, "全天汇总", "**[第 1/3 批次]**\n\n一")
    await push(env, token, "全天汇总", "**[第 3/3 批次]**\n\n三")
    rows = await run_sql(
        env.database_url,
        "SELECT string_agg(status || ':' || error, ',' ORDER BY created_at) "
        "FROM workspace.news_briefings",
    )
    assert rows == "complete:,complete:有批次缺失"
    await save(env, enabled=False)
    assert (await push(env, token, "全天汇总", "关了")).status_code == 404


# ---------------------------------------------------------------- 页面


async def test_today_hot_rss_search(env: ImEnv, trendradar: FakeTrendRadar) -> None:
    disabled = (await env.client.get("/api/v1/news/today", headers=env.headers())).json()
    assert disabled["message"] == "早报没有启用"
    await save(env)
    today = (await env.client.get("/api/v1/news/today", headers=env.headers())).json()
    groups = {t["name"]: [i["title"] for i in t["items"]] for t in today["topics"]}
    # 先匹配到的主题优先，同一条只出现一次；排除词与不安全的链接都去掉
    assert groups == {
        "AI": ["OpenAI 发布新一代推理模型", "英伟达财报超预期，AI 芯片需求强劲"],
        "芯片": [],
    }
    assert "AI 钓鱼链接" not in json.dumps(today, ensure_ascii=False)  # javascript: 链接丢掉
    assert "明星八卦冲上热搜" not in json.dumps(today, ensure_ascii=False)

    hot = (await env.client.get("/api/v1/news/hot?platform=weibo", headers=env.headers())).json()
    assert [i["title"] for i in hot["items"]] == ["明星八卦冲上热搜"]
    assert trendradar.calls[-1] == (
        "get_latest_news",
        {"limit": 100, "include_url": True, "platforms": ["weibo"]},
    )
    rss = (await env.client.get("/api/v1/news/rss", headers=env.headers())).json()
    assert rss["items"][0]["source_type"] == "rss" and rss["items"][0]["summary"]
    found = (await env.client.get("/api/v1/news/search?q=Claude", headers=env.headers())).json()
    assert [i["title"] for i in found["items"]] == ["Claude 发布新的编程能力"]
    assert [name for name, _ in trendradar.calls[-2:]] == ["search_news", "search_rss"]

    trendradar.missing.add("get_latest_news")
    empty = (await env.client.get("/api/v1/news/hot", headers=env.headers())).json()
    assert empty["items"] == [] and empty["unavailable"] is False and empty["message"]
    trendradar.down = True
    down = (await env.client.get("/api/v1/news/hot", headers=env.headers())).json()
    assert down["unavailable"] is True
    status = (await env.client.get("/api/v1/news/status", headers=env.headers())).json()
    assert status["online"] is False and status["enabled"] is True
    trendradar.down = False
    status = (await env.client.get("/api/v1/news/status", headers=env.headers())).json()
    assert status["online"] is True and status["latest_data_date"] == "2026-11-08"
    services = (await env.client.get("/api/v1/operations/services", headers=env.headers())).json()
    item = next(i for i in services["items"] if i["id"] == "trendradar")
    assert (item["kind"], item["provider"]) == ("integration", "trendradar")
    assert item["status"] in ("ok", "down")  # 状态有 15 秒缓存：刚才断开时可能已经缓存了 down


# ---------------------------------------------------------------- 收藏、不感兴趣、偏好学习


def _feedback(kind: str, url: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "url": url,
        "title": "标题",
        "source_type": "hotlist",
        "source_id": "weibo",
        "source_name": "微博",
        "topic": "AI",
        **extra,
    }


async def test_feedback_and_learning(env: ImEnv) -> None:
    await save(env)
    url = "https://www.zhihu.com/item/1"
    fav = await env.client.post(
        "/api/v1/news/feedback", json=_feedback("favorite", url), headers=env.headers(write=True)
    )
    assert fav.status_code == 201, fav.text
    again = await env.client.post(
        "/api/v1/news/feedback", json=_feedback("favorite", url), headers=env.headers(write=True)
    )
    assert again.json()["id"] == fav.json()["id"]
    hot = (await env.client.get("/api/v1/news/hot", headers=env.headers())).json()
    marked = next(i for i in hot["items"] if i["url"] == url)
    assert marked["favorite_id"] == fav.json()["id"] and marked["topic"] == "AI"
    # 不感兴趣会去掉收藏
    await env.client.post(
        "/api/v1/news/feedback", json=_feedback("dismiss", url), headers=env.headers(write=True)
    )
    favorites = (await env.client.get("/api/v1/news/feedback", headers=env.headers())).json()
    assert favorites["items"] == []
    today = (await env.client.get("/api/v1/news/today", headers=env.headers())).json()
    assert url not in json.dumps(today)

    # 一个来源被不感兴趣 5 次以上：提示可以关掉；主题分数为负：条数下调
    for i in range(5):
        await env.client.post(
            "/api/v1/news/feedback",
            json=_feedback("dismiss", f"https://s.weibo.com/item/x{i}"),
            headers=env.headers(write=True),
        )
    for i in range(3):
        await env.client.post(
            "/api/v1/news/feedback",
            json=_feedback("favorite", f"https://wallstreetcn.com/item/c{i}", topic="芯片"),
            headers=env.headers(write=True),
        )
    saved = await save(env)
    learned = {t["name"]: t for t in saved["learned"]["topics"]}
    assert learned["AI"]["score"] == -6 and learned["AI"]["adjusted_max_items"] == 4
    assert learned["芯片"]["score"] == 3 and learned["芯片"]["adjusted_max_items"] == 8
    assert saved["learned"]["source_suggestions"][0]["source_id"] == "weibo"
    words = (conf_dir(env) / "frequency_words.txt").read_text()
    assert words.index("[芯片]") < words.index("[AI]")
    assert "[AI]\nAI\n大模型\n@4" in words

    # 只能删自己的
    first = (
        await env.client.get("/api/v1/news/feedback?kind=dismiss", headers=env.headers())
    ).json()["items"][0]
    other = await add_user(env.database_url, "m@example.com", "member password 1234")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), other, "member")
    await login(env, "m@example.com", "member password 1234")
    refused = await env.client.delete(
        f"/api/v1/news/feedback/{first['id']}", headers=env.headers(write=True)
    )
    assert refused.status_code == 404
    exclude = await env.client.post(
        "/api/v1/news/feedback",
        json=_feedback("dismiss", "https://x.example.com/1", exclude_word="明星"),
        headers=env.headers(write=True),
    )
    assert exclude.status_code == 403
    await login(env, OWNER["email"], OWNER["password"])
    excluded = await env.client.post(
        "/api/v1/news/feedback",
        json=_feedback("dismiss", "https://x.example.com/1", exclude_word="明星"),
        headers=env.headers(write=True),
    )
    assert excluded.status_code == 201
    assert "明星" in (await settings(env))["exclusions"]
    assert "\n明星\n" in (conf_dir(env) / "frequency_words.txt").read_text()
    removed = await env.client.delete(
        f"/api/v1/news/feedback/{first['id']}", headers=env.headers(write=True)
    )
    assert removed.status_code == 204


# ---------------------------------------------------------------- 智能体工具


async def test_agent_news_tools(env: ImEnv, trendradar: FakeTrendRadar) -> None:
    _, token = await agent(env, "甲")
    names = await tool_names(env, token)
    assert not {n for n in names if n.startswith("news_")}  # 没启用：看不到
    refused = tool_error(await call(env, token, "news_latest", {}))
    assert refused["code"] == "NOT_FOUND"

    await save(env)
    names = await tool_names(env, token)
    assert {
        "news_latest",
        "news_search",
        "news_by_date",
        "news_trending",
        "news_rss",
        "news_related",
        "news_briefing",
    } <= names
    assert not {"trigger_crawl", "send_notification", "read_article"} & names

    latest = ok(await call(env, token, "news_latest", {"platforms": ["zhihu"], "limit": 5}))
    assert latest["items"][0]["untrusted"]["url"] == "https://www.zhihu.com/item/1"
    found = ok(await call(env, token, "news_search", {"query": "AI", "days": 7}))
    titles = [i["untrusted"]["title"] for i in found["items"]]
    assert "英伟达财报超预期，AI 芯片需求强劲" in titles and "AI 钓鱼链接" not in titles
    called = dict(trendradar.calls[-2:])
    assert set(called["search_news"]["date_range"]) == {"start", "end"}
    assert called["search_rss"]["keyword"] == "AI"  # RSS 单独查（search_news 不命中时不带 RSS）
    trending = ok(await call(env, token, "news_trending", {}))
    assert trending["topics"][0]["untrusted"]["keyword"] == "AI"
    rss = ok(await call(env, token, "news_rss", {"days": 3}))
    assert rss["items"][0]["source_type"] == "rss"
    related = ok(await call(env, token, "news_related", {"title": "AI 芯片"}))
    assert related["items"]
    by_date = ok(
        await call(env, token, "news_by_date", {"date_from": "2026-11-01", "date_to": "2026-11-07"})
    )
    assert by_date["items"]
    bad = tool_error(
        await call(env, token, "news_by_date", {"date_from": "2026-10-01", "date_to": "2026-11-07"})
    )
    assert bad["code"] == "VALIDATION_FAILED"
    none = ok(await call(env, token, "news_briefing", {}))
    assert none["briefing"] is None

    await push(
        env, await ingest_token(env), "全天汇总", "## 今日早报\n- [A](https://a.example.com)"
    )
    briefing = ok(await call(env, token, "news_briefing", {}))
    assert briefing["briefing"]["untrusted"]["content"].startswith("## 今日早报")
    trendradar.down = True
    down = tool_error(await call(env, token, "news_latest", {}))
    assert down["code"] == "EXTERNAL_UNAVAILABLE"
    recorded = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.ai_tool_executions WHERE tool_name LIKE 'news_%'",
    )
    assert recorded >= 10


# ---------------------------------------------------------------- 方案 B


def _slot_now() -> str:
    local = datetime.now(UTC).astimezone(ZoneInfo("Asia/Shanghai"))
    return f"{local.hour:02d}:{'30' if local.minute >= 30 else '00'}"


async def _tick(env: ImEnv) -> dict[str, int]:
    app = env.app
    async with app.state.sessionmaker() as db:
        return await news_agent.tick(db, app.state.settings, app.state.connectors, app.state.redis)


async def test_agent_mode_writes_briefing(env: ImEnv, trendradar: FakeTrendRadar) -> None:
    created, token = await agent(env, "写早报的")
    await save(
        env, briefing_mode="agent", agent_instance_id=created["id"], push_times=[_slot_now()]
    )
    # TrendRadar 自己不推（只抓取）
    assert "      analyze: false\n      push: false\n" in conf(env, "timeline.yaml")

    stats = await _tick(env)
    assert stats["scheduled"] == 1
    assert (await _tick(env))["scheduled"] == 0  # 同一个时间段只建一次
    task_id = await run_sql(
        env.database_url,
        "SELECT task_id::text FROM workspace.news_briefings WHERE status = 'pending'",
    )
    task = await run_sql(
        env.database_url,
        "SELECT p.title || '|' || (t.assignee_instance_id = $1)::text || '|' || t.description "
        "FROM workspace.tasks t JOIN workspace.projects p ON p.id = t.project_id WHERE t.id = $2",
        uuid.UUID(created["id"]),
        uuid.UUID(task_id),
    )
    assert task.startswith("每日早报|true|") and "readme_update" in task
    wakeup = await run_sql(
        env.database_url,
        "SELECT source || ':' || status FROM workspace.agent_wakeups WHERE instance_id = $1",
        uuid.UUID(created["id"]),
    )
    assert wakeup == "news:pending"

    # 智能体：写 README 的「早报」部分，再完成任务（都是不审批的工具）
    content = (
        "今天 AI 是主线。\n\n- [OpenAI 发布新一代推理模型](https://www.zhihu.com/item/1)：新模型"
    )
    ok(
        await call(
            env,
            token,
            "readme_update",
            {"task_id": task_id, "section": "早报", "text": content, "mode": "replace"},
        )
    )
    ok(await call(env, token, "complete_task", {"task_id": task_id}))
    assert (await _tick(env))["collected"] == 1
    briefing = (await env.client.get("/api/v1/news/today", headers=env.headers())).json()[
        "briefing"
    ]
    assert briefing["status"] == "complete" and briefing["source"] == "agent"
    assert briefing["content"] == content
    sent = trendradar.notifications[-1]
    assert sent["channels"] == ["ntfy"] and sent["message"] == content
    assert sent["title"].startswith("【工作台】")

    # 现在生成一份；同时只能有一份在写
    generated = await env.client.post(
        "/api/v1/news/briefings/generate", headers=env.headers(write=True)
    )
    assert generated.status_code == 201, generated.text
    busy = await env.client.post("/api/v1/news/briefings/generate", headers=env.headers(write=True))
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "BRIEFING_PENDING"
    # 超时记为失败并通知 owner
    await run_sql(
        env.database_url,
        "UPDATE workspace.news_briefings SET created_at = now() - interval '3 hours' "
        "WHERE status = 'pending'",
    )
    assert (await _tick(env))["collected"] == 1
    failed = await run_sql(
        env.database_url,
        "SELECT error FROM workspace.news_briefings WHERE status = 'failed'",
    )
    assert "90 分钟内没有写完" in failed
    notes = await env.client.get("/api/v1/notifications", headers=env.headers())
    assert "今日早报没有生成" in [n["title"] for n in notes.json()["items"]]


async def test_tick_rewrites_missing_config(env: ImEnv) -> None:
    await save(env)
    for path in conf_dir(env).iterdir():
        path.unlink()
    assert (await _tick(env)).get("rendered") == 1
    assert (conf_dir(env) / "config.yaml").exists()


# ---------------------------------------------------------------- 密钥轮换


async def test_rotation_covers_news_secrets(env: ImEnv) -> None:
    await save(env)
    app = env.app
    old = app.state.settings.credential_encryption_key.get_secret_value()
    new_key = "ab" * 32
    box = CredentialBox(bytes.fromhex(new_key), [bytes.fromhex(old)])
    async with app.state.sessionmaker() as db:
        report = await rotate(db, box)
    fields = {k: v.rotated for k, v in report.fields.items() if k.startswith("news_settings")}
    assert fields == {
        "news_settings.ai_key_ref": 1,
        "news_settings.channel_secrets_ref": 1,
        "news_settings.ingest_token_ref": 1,
    }
    assert report.remaining_old == 0
    ref = await run_sql(env.database_url, "SELECT ai_key_ref FROM workspace.news_settings")
    workspace_id = uuid.UUID(env.workspace_id)
    assert (
        box.resolve(ref, context=f"news_settings:{workspace_id}:ai_key").get_secret_value()
        == AI_KEY
    )
