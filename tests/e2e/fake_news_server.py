"""新闻早报验收用的模拟服务（T-0155）：一个进程提供 TrendRadar 需要的全部外部依赖。

- NewsNow 热榜接口：GET /api/s?id=<平台>&latest → {"status":"success","items":[{title,url,mobileUrl}]}
  （格式按 TrendRadar 6.11.0 trendradar/crawler/fetcher.py 核验，见 docs/decisions/2026-11-07-trendradar-verification.md）
- RSS：GET /rss/<名字>.xml → RSS 2.0
- OpenAI 兼容的大模型：POST /v1/chat/completions（LiteLLM 的 openai/<模型> 走这里）。AI 分析的提示词里有
  “core_trends”，返回带这些字段的 JSON；其余返回空 JSON。
- 推送接收：POST /capture/<名字>（ntfy 的 POST /<topic> 也落到这里），GET /captured 列出收到的全部推送。

条目的标题可以在运行中换：POST /admin/items {"platform": "...", "titles": [...]}，用来验证“改关键词后条目随之变化”。
只用标准库；监听 0.0.0.0:<端口>（默认 8080）。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# 每个平台的链接都用该平台真实域名的 https 地址，TrendRadar 的 expected_domain 检查才会放行
DOMAINS = {
    "zhihu": "www.zhihu.com",
    "weibo": "s.weibo.com",
    "douyin": "www.douyin.com",
    "bilibili-hot-search": "search.bilibili.com",
    "wallstreetcn-hot": "wallstreetcn.com",
    "cls-hot": "www.cls.cn",
    "toutiao": "www.toutiao.com",
    "baidu": "www.baidu.com",
    "thepaper": "www.thepaper.cn",
    "ifeng": "news.ifeng.com",
    "tieba": "tieba.baidu.com",
}
TITLES = {
    "zhihu": ["OpenAI 发布新一代推理模型", "如何评价今年的双十一", "DeepSeek 开源新模型引发讨论", "娱乐明星八卦新闻"],
    "weibo": ["人工智能写作工具走红", "某地暴雨预警", "明星绯闻冲上热搜", "芯片出口新规发布"],
    "wallstreetcn-hot": ["美联储议息会议纪要公布", "英伟达财报超预期，AI 芯片需求强劲", "A 股三大指数收涨"],
    "cls-hot": ["央行开展逆回购操作", "人工智能板块午后拉升", "新能源车销量创新高"],
}
RSS_ITEMS = {
    "tech": [
        ("Claude 发布新的编程能力", "https://tech.example.com/claude-coding"),
        ("开源大模型排行榜更新", "https://tech.example.com/llm-leaderboard"),
        ("一篇和兴趣无关的旅行游记", "https://tech.example.com/travel"),
    ],
}
LOCK = threading.Lock()
CAPTURED: list[dict] = []
COUNTS: dict[str, int] = {}


def _items(platform: str) -> list[dict]:
    domain = DOMAINS.get(platform, "www.example.com")
    titles = TITLES.get(platform, [f"{platform} 热榜条目 {i}" for i in range(1, 4)])
    return [
        {"title": t, "url": f"https://{domain}/item/{i}", "mobileUrl": f"https://{domain}/m/item/{i}"}
        for i, t in enumerate(titles, 1)
    ]


def _rss(name: str) -> str:
    now = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
    entries = "".join(
        f"<item><title>{t}</title><link>{u}</link><guid>{u}</guid><pubDate>{now}</pubDate>"
        f"<description>{t} 的摘要</description></item>"
        for t, u in RSS_ITEMS.get(name, [])
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{name}</title><link>https://tech.example.com/</link><description>{name}</description>"
        f"{entries}</channel></rss>"
    )


def _completion(body: dict) -> dict:
    text = " ".join(str(m.get("content", "")) for m in body.get("messages", []))
    if "core_trends" in text:
        content = json.dumps(
            {
                "core_trends": "模拟 AI 摘要：AI 与大模型是今天的主线，OpenAI、DeepSeek 都有新动作。",
                "sentiment_controversy": "讨论整体积极。",
                "signals": "AI 芯片需求强劲。",
                "rss_insights": "RSS 里有编程能力与开源模型的更新。",
                "outlook_strategy": "继续关注 AI 与芯片。",
            },
            ensure_ascii=False,
        )
    else:
        content = "{}"
    with LOCK:
        COUNTS["llm"] = COUNTS.get("llm", 0) + 1
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "fake"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # 安静一些，只在出错时打印
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: object) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self._json(200, {"ok": True})
        if url.path == "/api/s":
            platform = (parse_qs(url.query).get("id") or [""])[0]
            with LOCK:
                COUNTS[f"newsnow:{platform}"] = COUNTS.get(f"newsnow:{platform}", 0) + 1
                items = _items(platform)
            return self._json(200, {"status": "success", "id": platform, "items": items})
        if url.path.startswith("/rss/") and url.path.endswith(".xml"):
            return self._send(200, _rss(url.path[5:-4]).encode(), "application/rss+xml; charset=utf-8")
        if url.path == "/captured":
            with LOCK:
                return self._json(200, {"items": CAPTURED, "counts": COUNTS})
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if url.path in ("/v1/chat/completions", "/chat/completions"):
            return self._json(200, _completion(json.loads(raw or b"{}")))
        if url.path == "/admin/items":
            body = json.loads(raw or b"{}")
            with LOCK:
                TITLES[body["platform"]] = list(body["titles"])
            return self._json(200, {"ok": True})
        # 其余 POST 都当作推送：ntfy 的 POST /<topic>、通用 Webhook 的 POST /capture/<名字>
        text = raw.decode("utf-8", "replace")
        with LOCK:
            CAPTURED.append({"path": url.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": text})
        self._json(200, {"ok": True})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()  # noqa: S104 只在验收网络里


if __name__ == "__main__":
    main()
