#!/usr/bin/env python3
"""新闻早报全栈验收（T-0155，docs/v2/NEWS_PLAN.md 第 12 节）：Compose 全栈 + 真实 TrendRadar 6.11.0 / MCP 4.1.1。

外部依赖都换成模拟服务 tests/e2e/fake_news_server.py（NewsNow、RSS、大模型、推送接收），跑在 api 镜像里，
在 news 网络里的别名是 fake-news（.env 里 NEWS_ALLOWED_HOSTS=fake-news，TRENDRADAR_CRON 每分钟一次）。

- 网页设置保存后生成配置文件（含接收地址、关键词、排除词）；密钥不出现在接口响应里；
- 到推送时间：TrendRadar 抓取 → 关键词筛选 → AI 分析 → 推到 ntfy、用户的 Webhook 与工作台；工作台拼成一份完整早报、通知 owner；
  早报里是与兴趣相关的条目，每条带原文链接，排除词生效；
- 今天 / 热榜 / 搜索 / 状态经 TrendRadar MCP 返回真实数据；
- 智能体工具 news_search / news_latest / news_briefing 返回真实数据，不开放的 MCP 工具调不到；
- 修改关键词后配置文件随之变化，下一次运行（新的输出目录，同一时间段）的早报条目随之变化；
- MCP 与接收地址从外面访问不到（不发布端口；Caddy 不转发 /internal/*）。

前提：make up、make migrate、make trendradar-up 已完成，.env 里 TRENDRADAR_MCP_URL=http://trendradar-mcp:3333/mcp。
用法：python3 tests/e2e/news_acceptance.py [--base-url http://127.0.0.1:8080]
"""

import argparse
import json
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from e2e_support import ROOT, Client, Report, compose_exec, default_base_url, env_value, parse_base_url
from seed import create_user

PASSWORD = "e2e news passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
AI_KEY = "sk-e2e-news-model-key"
NTFY_TOPIC = "e2e-phone"
FAKE = "ws-e2e-fake-news"
TRENDRADAR_IMAGE = (
    "wantcat/trendradar:6.11.0@sha256:3effcb5cedf5c385b50a6a32cd2599cbdbf54c9d8c52c0403406e356f88df80b"
)


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 -- 固定的 docker 命令
        list(args), cwd=ROOT, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} 失败：{result.stderr.strip()[-600:]}")
    return result.stdout.strip()


def wait_for(predicate: Callable[[], Any], timeout: float, interval: float = 5.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def project() -> str:
    return env_value("COMPOSE_PROJECT_NAME", "my-workspace") or "my-workspace"


def start_fake() -> None:
    run("docker", "rm", "-f", FAKE, check=False)
    run(
        "docker", "run", "-d", "--name", FAKE,
        "--network", f"{project()}_news", "--network-alias", "fake-news",
        "--entrypoint", "python",
        "-v", f"{ROOT}/tests/e2e/fake_news_server.py:/srv/fake_news_server.py:ro",
        "my-workspace-api:local", "/srv/fake_news_server.py", "8080",
    )  # fmt: skip
    fake_get("/healthz")


def fake_get(path: str) -> Any:
    script = (
        "import sys, urllib.request\n"
        "print(urllib.request.urlopen('http://127.0.0.1:8080' + sys.argv[1], timeout=5).read().decode())"
    )
    for _ in range(30):
        result = subprocess.run(  # noqa: S603 -- 固定的 docker 命令
            ["docker", "exec", FAKE, "python", "-c", script, path],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        if result.returncode == 0:
            return json.loads(result.stdout)
        time.sleep(1)
    raise RuntimeError(f"模拟服务没有响应：{result.stderr[-300:]}")


def slots() -> list[str]:
    """当前与下一个半小时：TrendRadar 每分钟运行，落在推送时间段里就推送。"""
    local = datetime.now(UTC).astimezone(ZoneInfo("Asia/Shanghai"))
    start = local.replace(minute=30 if local.minute >= 30 else 0, second=0, microsecond=0)
    return [start.strftime("%H:%M"), (start + timedelta(minutes=30)).strftime("%H:%M")]


def settings_body(version: int, topics: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": version,
        "enabled": True,
        "push_times": slots(),
        "briefing_mode": "trendradar",
        "platforms": ["zhihu", "weibo", "wallstreetcn-hot", "cls-hot"],
        "newsnow_api_url": "http://fake-news:8080/api/s",
        "feeds": [{"id": "tech", "name": "科技 RSS", "url": "http://fake-news:8080/rss/tech.xml"}],
        "topics": topics,
        "exclusions": ["八卦", "绯闻"],
        "filter_method": "keyword",
        "ai_model": "openai/fake-model",
        "ai_api_base": "http://fake-news:8080/v1",
        "ai_api_key": AI_KEY,
        "ai_analysis": True,
        "channels": {
            "ntfy": {
                "enabled": True,
                "server_url": "http://fake-news:8080",
                "secrets": {"topic": NTFY_TOPIC},
            },
            "webhook": {"enabled": True, "secrets": {"url": "http://fake-news:8080/capture/user"}},
        },
    }


AI_TOPICS = [
    {"name": "AI", "keywords": ["AI", "大模型", "DeepSeek", "Claude"], "max_items": 10},
    {"name": "芯片", "keywords": ["芯片"], "max_items": 5},
]
FINANCE_TOPICS = [{"name": "财经", "keywords": ["央行", "美联储", "A 股"], "max_items": 10}]

# 在 api 容器里调用智能体的 news_* 工具（与 MCP 读工具同一个处理函数），以及一个不开放的 MCP 工具
TOOLS_SCRIPT = r"""
import asyncio, json, sys, uuid
from types import SimpleNamespace
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.config import get_settings
from app.core.resources import create_engine
from app.domain import news_tools
from app.domain.connectors import ConnectorRuntime
from app.domain.news import news_client
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker
from app.schemas.news_tools import NewsBriefingInput, NewsLatestInput, NewsSearchInput

async def main():
    settings = get_settings()
    engine = create_engine(settings)
    runtime = ConnectorRuntime(settings, CircuitBreaker())
    workspace = SimpleNamespace(id=uuid.UUID(sys.argv[1]))
    requester = SimpleNamespace(access=lambda: SimpleNamespace(workspace=workspace))
    out = {}
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        call = SimpleNamespace(db=db, settings=settings, runtime=runtime, redis=None, requester=requester,
                               access=requester.access())
        out["search"] = (await news_tools.news_search(call, NewsSearchInput(query="AI", days=1))).model_dump(mode="json")
        out["latest"] = (await news_tools.news_latest(call, NewsLatestInput(platforms=["zhihu"]))).model_dump(mode="json")
        out["briefing"] = (await news_tools.news_briefing(call, NewsBriefingInput())).model_dump(mode="json")
    try:
        await news_client(settings, runtime).call("trigger_crawl", {})
        out["refused"] = "called"
    except ConnectorError as error:
        out["refused"] = error.reason
    await engine.dispose()
    print(json.dumps(out, ensure_ascii=False))

asyncio.run(main())
"""


def briefings(client: Client, headers: dict[str, str]) -> list[dict[str, Any]]:
    page = client.request("GET", "/api/v1/news/briefings?limit=20", headers=headers)
    return page.json()["items"] if page.status == 200 else []


def complete_after(client: Client, headers: dict[str, str], seen: set[str]) -> dict[str, Any] | None:
    for item in briefings(client, headers):
        if item["status"] == "complete" and item["id"] not in seen:
            detail = client.request("GET", f"/api/v1/news/briefings/{item['id']}", headers=headers)
            return detail.json()  # type: ignore[no-any-return]
    return None


def extra_run() -> None:
    """再跑一次 TrendRadar（新的输出卷，所以这个时间段还没推送过）：验证下一次运行跟着新关键词。"""
    volume = f"ws-e2e-news-output-{uuid.uuid4().hex[:8]}"
    try:
        run(
            "docker", "run", "--rm", "--group-add", "10001", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--network", f"{project()}_news",
            "-e", "RUN_MODE=once", "-e", "LITELLM_LOCAL_MODEL_COST_MAP=True",
            "-v", f"{project()}_trendradar-config:/app/config:ro", "-v", f"{volume}:/app/output",
            TRENDRADAR_IMAGE,
        )  # fmt: skip
    finally:
        run("docker", "volume", "rm", "-f", volume, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=default_base_url())
    base = parse_base_url(parser.parse_args().base_url)
    report = Report()
    start_fake()

    email = f"news-{uuid.uuid4().hex[:8]}@example.com"
    _, workspace_id, _ = create_user(email, "早报验收", PASSWORD)
    client = Client(base)
    login = client.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    report.check("owner 登录", login.status == 200, login.summary)
    headers = {"X-Workspace-Id": workspace_id}

    status = client.request("GET", "/api/v1/news/status", headers=headers).json()
    report.check("工作台连上了 TrendRadar（已部署）", status["deployed"] is True, json.dumps(status))
    current = client.request("GET", "/api/v1/news/settings", headers=headers).json()
    saved = client.request(
        "PUT", "/api/v1/news/settings", settings_body(current["version"], AI_TOPICS), headers=headers
    )
    if not report.check("网页上保存设置并启用", saved.status == 200, saved.summary):
        return report.finish()
    body = saved.json()
    text = json.dumps(body, ensure_ascii=False)
    report.check(
        "密钥不在接口响应里（只回“已设置”）",
        AI_KEY not in text and NTFY_TOPIC not in text and body["ai_api_key_set"] is True,
    )
    report.check("配置文件写入成功", body["render_error"] == "" and body["config_rev"] >= 1, text[-300:])
    words = compose_exec("api", "cat", "/var/lib/workspace/trendradar/frequency_words.txt")
    report.check("关键词与排除词写进 frequency_words.txt", "[AI]" in words and "八卦" in words, words)
    config = compose_exec("api", "cat", "/var/lib/workspace/trendradar/config.yaml")
    report.check(
        "工作台的接收地址是第一个通用 Webhook，版本检查关闭",
        "http://api:8000/internal/news/ingest/" in config and '"version_check_url": ""' in config,
    )

    first = wait_for(lambda: complete_after(client, headers, set()), timeout=300)
    report.check("到推送时间收到一份完整早报（TrendRadar → 工作台）", first is not None)
    if first is None:
        print(json.dumps(briefings(client, headers), ensure_ascii=False))
        print(run("docker", "logs", "--tail", "80", f"{project()}-trendradar-trendradar-1", check=False))
        return report.finish()
    content = first["content"]
    report.check(
        "早报里是与兴趣相关的条目，每条带原文链接",
        "OpenAI 发布新一代推理模型" in content and "](https://www.zhihu.com/" in content,
        content[:400],
    )
    report.check("排除词生效（八卦、绯闻不在早报里）", "八卦" not in content and "绯闻" not in content)
    report.check("早报带 AI 摘要", "AI 与大模型是今天的主线" in content, content[-400:])
    notes = client.request("GET", "/api/v1/notifications", headers=headers).json()["items"]
    report.check("owner 收到“今日早报已送达”", any(n["title"] == "今日早报已送达" for n in notes))
    captured = fake_get("/captured")["items"]
    paths = [c["path"] for c in captured]
    report.check(f"推送到手机（ntfy /{NTFY_TOPIC}）", f"/{NTFY_TOPIC}" in paths, str(paths))
    report.check("推送到用户自己的 Webhook", "/capture/user" in paths, str(paths))

    today = client.request("GET", "/api/v1/news/today", headers=headers).json()
    groups = {t["name"]: [i["title"] for i in t["items"]] for t in today["topics"]}
    report.check(
        "今天：早报 + 各主题条目（经 MCP）",
        today["briefing"] is not None and bool(groups.get("AI")),
        json.dumps(groups, ensure_ascii=False),
    )
    hot = client.request("GET", "/api/v1/news/hot?platform=zhihu", headers=headers).json()
    report.check(
        "热榜（只看知乎）",
        bool(hot["items"]) and all(i["url"].startswith("https://www.zhihu.com/") for i in hot["items"]),
    )
    found = client.request("GET", "/api/v1/news/search?q=Claude", headers=headers).json()
    report.check("搜索（含 RSS）", any(i["source_type"] == "rss" for i in found["items"]))
    status = client.request("GET", "/api/v1/news/status", headers=headers).json()
    report.check("状态：TrendRadar 在线、有今天的数据", status["online"] is True and bool(status["latest_data_date"]))

    tools = json.loads(compose_exec("api", "python", "-c", TOOLS_SCRIPT, workspace_id).splitlines()[-1])
    report.check(
        "智能体工具 news_search / news_latest 返回真实数据",
        bool(tools["search"]["items"]) and bool(tools["latest"]["items"]),
        json.dumps(tools["search"], ensure_ascii=False)[:300],
    )
    report.check(
        "智能体工具 news_briefing 返回今天的早报",
        (tools["briefing"]["briefing"] or {}).get("briefing_id") == first["id"],
    )
    report.check("不开放的 MCP 工具（trigger_crawl）调不到", tools["refused"] == "tool_not_allowed")

    # 改关键词：配置文件马上变；下一次运行（同一时间段、新的输出目录）的早报跟着变
    current = client.request("GET", "/api/v1/news/settings", headers=headers).json()
    changed = client.request(
        "PUT", "/api/v1/news/settings", settings_body(current["version"], FINANCE_TOPICS), headers=headers
    )
    words = compose_exec("api", "cat", "/var/lib/workspace/trendradar/frequency_words.txt")
    report.check(
        "改关键词后配置文件随之变化",
        changed.status == 200 and "[财经]" in words and "[AI]" not in words,
        words,
    )
    extra_run()
    second = wait_for(lambda: complete_after(client, headers, {first["id"]}), timeout=120)
    second_content = (second or {}).get("content", "")
    report.check(
        "下一次运行的早报条目随之变化",
        "美联储议息会议纪要公布" in second_content and "OpenAI 发布新一代推理模型" not in second_content,
        second_content[:400],
    )

    # 从外面访问不到：MCP 没有发布端口；Caddy 不转发 /internal/*
    with socket.socket() as sock:
        sock.settimeout(2)
        reachable = sock.connect_ex(("127.0.0.1", 3333)) == 0
    report.check("TrendRadar MCP 没有发布到本机端口", not reachable)
    token = config.split("/internal/news/ingest/", 1)[1].split('"', 1)[0].split(";", 1)[0]
    outside = Client(base).request(
        "POST", f"/internal/news/ingest/{token}", {"title": "x", "content": "外面推来的"}, csrf=False
    )
    report.check(
        "接收地址经 Caddy 访问不到",
        outside.status != 200 or b'"result"' not in outside.body,
        outside.summary,
    )

    # 一个部署只有一个工作空间能启用早报：关掉，接下来的浏览器验收用它自己的新工作空间启用
    current = client.request("GET", "/api/v1/news/settings", headers=headers).json()
    off = client.request(
        "PUT",
        "/api/v1/news/settings",
        settings_body(current["version"], FINANCE_TOPICS) | {"enabled": False},
        headers=headers,
    )
    report.check("关闭早报后配置文件改成不推送", off.status == 200 and off.json()["render_error"] == "")
    run("docker", "rm", "-f", FAKE, check=False)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
