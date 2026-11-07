"""模拟的 TrendRadar MCP 4.1.1（T-0153）：Streamable HTTP 的握手与 tools/call。

返回格式按真实实例核验（docs/decisions/2026-11-07-trendradar-verification.md 第 4 节）：
工具结果是 JSON 字符串，放在 content[0].text；tools/call 的响应用 SSE（text/event-stream），
initialize 用 JSON，带 mcp-session-id。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

HOST = "trendradar-mcp"
URL = f"http://{HOST}:3333/mcp"

HOT: list[dict[str, Any]] = [
    {
        "title": "OpenAI 发布新一代推理模型",
        "platform": "zhihu",
        "platform_name": "知乎",
        "rank": 1,
        "timestamp": "2026-11-08 07:00:00",
        "url": "https://www.zhihu.com/item/1",
        "mobileUrl": "https://www.zhihu.com/m/item/1",
    },
    {
        "title": "英伟达财报超预期，AI 芯片需求强劲",
        "platform": "wallstreetcn-hot",
        "platform_name": "华尔街见闻",
        "rank": 2,
        "timestamp": "2026-11-08 07:00:00",
        "url": "https://wallstreetcn.com/item/2",
    },
    {
        "title": "明星八卦冲上热搜",
        "platform": "weibo",
        "platform_name": "微博",
        "rank": 1,
        "timestamp": "2026-11-08 07:00:00",
        "url": "https://s.weibo.com/item/1",
    },
    {
        # 不安全的链接：工作台要丢掉这一条
        "title": "AI 钓鱼链接",
        "platform": "weibo",
        "platform_name": "微博",
        "rank": 3,
        "url": "javascript:alert(1)",
    },
]
RSS: list[dict[str, Any]] = [
    {
        "title": "Claude 发布新的编程能力",
        "feed_id": "tech",
        "feed_name": "科技 RSS",
        "url": "https://tech.example.com/claude-coding",
        "published_at": "2026-11-07T23:00:00",
        "summary": "Claude 发布新的编程能力 的摘要",
    }
]


class FakeTrendRadar:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sessions: set[str] = set()
        self.closed: set[str] = set()
        self.notifications: list[dict[str, Any]] = []
        self.missing: set[str] = set()  # 这些工具返回 DATA_NOT_FOUND
        self.down = False

    def tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in self.missing:
            return {"success": False, "error": {"code": "DATA_NOT_FOUND", "message": "未找到数据"}}
        if name in ("get_latest_news", "get_news_by_date", "find_related_news"):
            items = HOT
            if args.get("platforms"):
                items = [i for i in HOT if i["platform"] in args["platforms"]]
            return {"success": True, "data": items[: args.get("limit", 50)]}
        if name == "search_news":
            query = str(args.get("query") or "").lower()
            data = [i for i in HOT if query in i["title"].lower()]
            rss = [i for i in RSS if query in i["title"].lower()] if args.get("include_rss") else []
            return {"success": True, "data": data, "rss": rss}
        if name == "get_latest_rss":
            return {"success": True, "data": RSS}
        if name == "get_trending_topics":
            return {
                "success": True,
                "topics": [{"keyword": "AI", "frequency": 5, "trend": "stable"}],
            }
        if name == "get_system_status":
            return {
                "success": True,
                "data": {"data": {"latest_record": "2026-11-08"}, "health": "healthy"},
            }
        if name == "send_notification":
            self.notifications.append(args)
            return {
                "success": True,
                "results": {ch: {"success": True, "detail": ""} for ch in args["channels"]},
            }
        return {"success": False, "error": {"code": "UNKNOWN_TOOL", "message": name}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down", request=request)
        session = request.headers.get("mcp-session-id")
        if request.method == "DELETE":
            if session:
                self.closed.add(session)
            return httpx.Response(200)
        body = json.loads(request.content)
        method = body.get("method")
        if method == "initialize":
            sid = uuid.uuid4().hex
            self.sessions.add(sid)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "trendradar-news", "version": "2.12.5"},
                    },
                },
                headers={"mcp-session-id": sid},
            )
        if session not in self.sessions:
            return httpx.Response(400, json={"error": "missing session"})
        if "id" not in body:
            return httpx.Response(202)
        if method != "tools/call":
            return httpx.Response(400)
        name = body["params"]["name"]
        args = body["params"].get("arguments") or {}
        self.calls.append((name, args))
        text = json.dumps(self.tool(name, args), ensure_ascii=False)
        message = {
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {
                "content": [{"type": "text", "text": text}],
                "structuredContent": {"result": text},
                "isError": False,
            },
        }
        sse = f"event: message\ndata: {json.dumps(message, ensure_ascii=False)}\n\n"
        return httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )

    def tools_called(self) -> list[str]:
        return [name for name, _ in self.calls]
