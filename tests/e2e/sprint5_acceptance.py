#!/usr/bin/env python3
"""Sprint 5 全栈验收（T-0040）：Compose 全栈 + Prometheus（--profile monitoring）。

保留运维、通知和 SSE 的接口级验收，浏览器部分在 sprint5_browser.cjs：
- Prometheus 不发布端口；指标只返回预设视图、不接受 PromQL、不暴露内部地址。
- Prometheus 不可用时返回缓存并标为 stale，运维页显示 down，恢复后返回正常数据。
- 通知按事件标识去重，标为已读后未读数更新。
- A16：SSE 推送失效事件；断开后按 Last-Event-ID 补发，重新拉取得到最新数据。
- S5-05：Prometheus 保留 7 天；访问日志不记录查询串。

前提：Compose 全栈已启动并迁移，.env 里 PROMETHEUS_URL=http://prometheus:9090，
并已启动 prometheus（docker compose --profile monitoring up -d prometheus）。
不要求空库：每次运行用新的工作台用户。只依赖 Python 标准库。
用法：python3 tests/e2e/sprint5_acceptance.py [--base-url http://127.0.0.1:8080]
"""

import argparse
import http.client
import json
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from e2e_support import (
    ROOT,
    Client,
    Report,
    compose_exec,
    default_base_url,
    parse_base_url,
)
from seed import create_user, notify

PASSWORD = "e2e sprint5 passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 -- 固定的 docker 命令
        list(args), cwd=ROOT, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} 失败：{result.stderr.strip()[-400:]}")
    return result.stdout.strip()


def wait_for(predicate: Callable[[], Any], timeout: float, interval: float = 2.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def login(base: str, email: str) -> Client:
    client = Client(base)
    response = client.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if response.status != 200:
        sys.exit(f"登录失败：{response.summary}")
    return client


class Stream:
    """经 Caddy 打开的 SSE 连接，逐个读事件（忽略心跳注释）。"""

    def __init__(self, base: str, client: Client, workspace_id: str, last_id: str | None = None) -> None:
        parts = urlsplit(base)
        self.conn = http.client.HTTPConnection(parts.hostname or "127.0.0.1", parts.port or 80, timeout=20)
        cookie = "; ".join(f"{c.name}={c.value}" for c in client.jar)
        headers = {"Cookie": cookie, "Accept": "text/event-stream", "Accept-Encoding": "gzip, zstd"}
        if last_id:
            headers["Last-Event-ID"] = last_id
        self.conn.request("GET", f"/api/v1/events/stream?workspace_id={workspace_id}", headers=headers)
        self.response = self.conn.getresponse()

    def next_event(self, timeout: float) -> dict[str, Any] | None:
        assert self.conn.sock is not None
        self.conn.sock.settimeout(timeout)
        event: dict[str, Any] = {}
        try:
            while True:
                line = self.response.fp.readline().decode().rstrip("\n")
                if line.startswith("id:"):
                    event["id"] = line[3:].strip()
                elif line.startswith("data:"):
                    event["data"] = json.loads(line[5:])
                elif line == "" and "data" in event:
                    return event
        except (TimeoutError, socket.timeout):
            return None

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------- 验收


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=default_base_url())
    args = parser.parse_args()
    base = parse_base_url(args.base_url)
    report = Report()
    print(f"Sprint 5 全栈验收：{base}")
    try:
        run_checks(base, report)
    finally:
        run("docker", "compose", "--profile", "monitoring", "start", "prometheus", check=False)
    return report.finish()


def run_checks(base: str, report: Report) -> None:
    email = f"e2e-s5-{uuid.uuid4().hex[:8]}@example.com"
    user_id, ws, _ = create_user(email, "Sprint 5", PASSWORD)
    owner = login(base, email)
    h = {"X-Workspace-Id": ws}

    # ---------------------------------------------------------------- DoD：不暴露 Prometheus / Secret
    port = run("docker", "compose", "--profile", "monitoring", "port", "prometheus", "9090", check=False)
    # 没有发布端口时 docker compose port 输出 ":0"
    report.check("DoD Prometheus 没有发布端口（只在内部网络）", port in {"", ":0"}, port or "无")
    metrics_page = Client(base).request("GET", "/metrics")
    report.check(
        "DoD 经 Caddy 访问 /metrics 得不到 api 的指标",
        "workspace_http_requests" not in metrics_page.text,
        str(metrics_page.status),
    )
    services = owner.request("GET", "/api/v1/operations/services", headers=h)
    items = {i["id"]: i for i in (services.json() or {}).get("items", [])}
    report.check(
        "S5-02 服务状态：核心服务与 Prometheus 正常",
        all(items.get(k, {}).get("status") == "ok" for k in ("api", "postgres", "redis", "prometheus")),
        str({k: v.get("status") for k, v in items.items()}),
    )
    worker = wait_for(
        lambda: owner.request("GET", "/api/v1/operations/services", headers=h).json()["items"][1]["status"] == "ok",
        70,
    )
    report.check("S5-02 Worker 心跳正常", bool(worker))
    report.check("DoD 服务状态里没有内部地址", "9090" not in services.text and "prometheus:" not in services.text)
    owner.request("GET", "/api/v1/dashboard/overview", headers=h)

    def metrics_ready() -> Any:
        data = owner.request("GET", "/api/v1/operations/metrics?range=1h", headers=h).json()
        return data if data["sync"]["status"] == "ok" and data["views"] else None

    metrics = wait_for(metrics_ready, 60)
    report.check(
        "S5-01 预设指标：6 个视图，来自 Prometheus",
        bool(metrics) and [v["id"] for v in metrics["views"]] == [
            "request_rate", "latency_p50", "latency_p95", "latency_p99", "error_rate", "services_up"
        ],
        str(metrics and metrics["sync"]),
    )
    injected = owner.request("GET", "/api/v1/operations/metrics?views=up%7Bjob%3D%22x%22%7D", headers=h)
    report.check("DoD 指标接口不接受 PromQL（视图 ID 以外的值 → 422）", injected.status == 422, injected.summary)
    report.check("DoD 指标响应里没有 PromQL 与内部地址", bool(metrics) and "histogram_quantile" not in json.dumps(metrics) and "9090" not in json.dumps(metrics))

    # ---------------------------------------------------------------- S5-05
    flags = compose_exec("prometheus", "wget", "-qO-", "http://127.0.0.1:9090/api/v1/status/flags")
    data = json.loads(flags)["data"]
    report.check(
        "S5-05 Prometheus 保留 7 天，管理与生命周期接口关闭",
        data.get("storage.tsdb.retention.time") in {"7d", "1w"}
        and data.get("web.enable-admin-api") == "false"
        and data.get("web.enable-lifecycle") == "false",
        f"{data.get('storage.tsdb.retention.time')}",
    )
    marker = "e2esecret" + uuid.uuid4().hex[:8]
    owner.request("GET", f"/api/v1/notifications?cursor={marker}", headers=h)
    logs = run("docker", "compose", "logs", "--no-color", "--since", "2m", "api")
    report.check("S5-05 访问日志不记录查询串", marker not in logs)

    # ---------------------------------------------------------------- 通知：同一事件去重、已读状态
    notice_key = f"sprint5-{uuid.uuid4().hex}"
    for _ in range(2):
        notify(ws, user_id, notice_key, "验收通知")
    notes = owner.request("GET", "/api/v1/notifications", headers=h).json()
    notices = [item for item in notes["items"] if item["title"] == "验收通知"]
    report.check("S5-03 同一个通知事件只写入一次", len(notices) == 1 and notes["unread_count"] == 1)
    if notices:
        marked = owner.request(
            "PATCH", "/api/v1/notifications", {"ids": [notices[0]["id"]]}, headers=h,
        )
        unread = owner.request("GET", "/api/v1/notifications?unread_only=true", headers=h).json()
        report.check(
            "S5-03 标为已读后未读数与列表更新",
            marked.status == 200 and unread["unread_count"] == 0 and not unread["items"],
            marked.summary,
        )

    # ---------------------------------------------------------------- DoD：失败明确显示 stale
    run("docker", "compose", "--profile", "monitoring", "stop", "prometheus")
    stale = owner.request("GET", "/api/v1/operations/metrics?range=1h", headers=h).json()
    report.check(
        "DoD Prometheus 不可用：指标返回之前的结果并标为 stale",
        stale["sync"]["status"] == "stale" and stale["views"] and stale["sync"]["as_of"] is not None,
        str(stale["sync"]),
    )
    down = wait_for(
        lambda: {i["id"]: i for i in owner.request("GET", "/api/v1/operations/services", headers=h).json()["items"]}["prometheus"]["status"] == "down",
        30,
    )
    report.check("DoD 运维页显示 Prometheus 不可用", bool(down))
    run("docker", "compose", "--profile", "monitoring", "start", "prometheus")
    recovered = wait_for(metrics_ready, 60)
    report.check("DoD Prometheus 恢复后指标不再过期", bool(recovered))

    # ---------------------------------------------------------------- A16（接口级）
    stream = Stream(base, owner, ws)
    report.check("A16 SSE 连接经 Caddy 建立", stream.response.status == 200 and stream.response.getheader("Content-Encoding") is None, str(stream.response.status))
    event_body = {
        "title": "验收评审", "timezone": "Asia/Shanghai",
        "start_at": "2026-11-02T10:00:00+08:00", "end_at": "2026-11-02T11:00:00+08:00",
    }
    started = time.monotonic()
    owner.request("POST", "/api/v1/calendar/events", event_body, headers=h)
    pushed = stream.next_event(10)
    report.check(
        "A16 写入后很快收到失效事件（calendar）",
        bool(pushed) and "calendar" in pushed["data"]["topics"],
        f"{(time.monotonic() - started) * 1000:.0f} ms",
    )
    stream.close()
    last_id = pushed["id"] if pushed else "0-0"
    owner.request("POST", "/api/v1/calendar/events", {**event_body, "title": "断线期间新增"}, headers=h)
    resumed = Stream(base, owner, ws, last_id)
    replay = resumed.next_event(10)
    resumed.close()
    report.check(
        "A16 断开后带 Last-Event-ID 重连：补发断开期间的事件",
        bool(replay) and replay["data"]["reset"] is False and "calendar" in replay["data"]["topics"],
        str(replay and replay["data"]["topics"]),
    )
    events = owner.request(
        "GET", "/api/v1/calendar/events?start=2026-11-01T00:00:00%2B08:00&end=2026-11-08T00:00:00%2B08:00",
        headers=h,
    ).json()
    report.check(
        "A16 重新拉取得到最新数据",
        "断线期间新增" in [e["title"] for e in events["items"]],
    )
    reset_stream = Stream(base, owner, ws, "not-an-id")
    reset = reset_stream.next_event(10)
    reset_stream.close()
    report.check("A16 无法续传时发 reset（要求重新拉取全部数据）", bool(reset) and reset["data"]["reset"] is True)


if __name__ == "__main__":
    sys.exit(main())
