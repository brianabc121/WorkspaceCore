#!/usr/bin/env python3
"""Sprint 5 全栈验收（T-0040）：Compose 全栈 + Prometheus（--profile monitoring）+ 模拟 n8n。

DoD 见 docs/DEVELOPMENT_SPEC.md 第 13 节，验收场景见第 15 节（接口级部分；浏览器部分在
sprint5_browser.cjs）：

- DoD“不向浏览器暴露 Prometheus 管理端 / Secret”：
  - Prometheus 没有发布端口；经 Caddy 访问 /metrics 只得到前端页面；
  - 指标接口只返回预设视图，不接受 PromQL；
  - n8n 的 API Key 与共享密钥在任何响应里都不出现。
- DoD“失败明确显示 stale”：停掉 Prometheus 后，指标返回之前的结果并标为 stale，
  运维页显示 down；模拟 n8n 不可用后，连接显示 stale。
- DoD“触发自动化有审计记录”：受理、策略拒绝的触发都有 automation.triggered，只记参数名。
- 自动化白名单与幂等：未登记的工作流 422、未声明的参数 422、同一个 Idempotency-Key 只触发一次。
- 通知去重：同一次执行失败只通知一次。
- A16（接口级）：SSE 推送失效事件；断开期间的变化按 Last-Event-ID 补发，重新拉取得到最新数据。
- S5-05：Prometheus 保留 7 天；访问日志不记录查询串。

模拟 n8n（fake_n8n_server.py）复用后端测试的内存版 n8n，运行在 api 镜像里，加入 integrations 网络，
别名 fake-n8n。前提：Compose 全栈已启动并迁移，.env 里 INTEGRATION_ALLOWED_HOSTS 含 fake-n8n，
PROMETHEUS_URL=http://prometheus:9090，并已启动 prometheus（docker compose --profile monitoring up -d prometheus）。
不要求空库：每次运行用新的工作台用户。
用法：python3 tests/e2e/sprint5_acceptance.py [--base-url http://127.0.0.1:8080]
只依赖 Python 标准库，共用工具在同目录的 e2e_support.py、seed.py。
"""

import argparse
import http.client
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
    env_value,
    parse_base_url,
)
from seed import create_user

PASSWORD = "e2e sprint5 passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
FAKE_NAME = "ws-e2e-fake-n8n"
FAKE_PORT = 18091
API_KEY = "n8n_api_e2e_" + uuid.uuid4().hex
TRIGGER_SECRET = "e2e-trigger-" + uuid.uuid4().hex
WORKFLOW = "wfE2eReport01"
PATH = "e2e-report"
RECONCILE_WAIT = 80  # Worker 每分钟第 15 秒对账：等过一轮


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 -- 固定的 docker 命令
        list(args), cwd=ROOT, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} 失败：{result.stderr.strip()[-400:]}")
    return result.stdout.strip()


def project_name() -> str:
    return env_value("COMPOSE_PROJECT_NAME") or "my-workspace"


def start_fake() -> None:
    run("docker", "rm", "-f", FAKE_NAME, check=False)
    api = run("docker", "compose", "ps", "-q", "api")
    image = run("docker", "inspect", "--format", "{{.Config.Image}}", api)
    run(
        "docker", "run", "-d", "--name", FAKE_NAME,
        "-p", f"127.0.0.1:{FAKE_PORT}:8080",
        "-e", f"FAKE_N8N_API_KEY={API_KEY}",
        "-e", f"FAKE_N8N_SECRET={TRIGGER_SECRET}",
        "-v", f"{ROOT}/services/api/tests:/fake/tests:ro",
        "-v", f"{ROOT}/tests/e2e/fake_n8n_server.py:/fake/server.py:ro",
        "--entrypoint", "python", image, "/fake/server.py",
    )
    run("docker", "network", "connect", "--alias", "fake-n8n", f"{project_name()}_integrations", FAKE_NAME)


def fake_call(method: str, path: str, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{FAKE_PORT}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        raw = response.read()
    return json.loads(raw) if raw else None


def fake_ready() -> bool:
    try:
        return fake_call("GET", "/_fake/stats") is not None
    except (OSError, urllib.error.URLError):
        return False


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
    start_fake()
    if not wait_for(fake_ready, 30, 1):
        sys.exit("模拟 n8n 没有就绪")
    try:
        run_checks(base, report)
    finally:
        run("docker", "rm", "-f", FAKE_NAME, check=False)
        run("docker", "compose", "--profile", "monitoring", "start", "prometheus", check=False)
    return report.finish()


def run_checks(base: str, report: Report) -> None:
    email = f"e2e-s5-{uuid.uuid4().hex[:8]}@example.com"
    _, ws, _ = create_user(email, "Sprint 5", PASSWORD)
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

    # ---------------------------------------------------------------- n8n 连接与白名单
    fake_call("POST", "/_fake/control", {"add_workflow": {"id": WORKFLOW, "path": PATH}})
    put = owner.request(
        "PUT", "/api/v1/automations/connection",
        {"base_url": "http://fake-n8n:8080", "api_key": API_KEY, "trigger_secret": TRIGGER_SECRET},
        headers=h,
    )
    tested = owner.request("POST", "/api/v1/automations/connection/test", headers=h)
    report.check("S5-04 n8n 连接测试通过", tested.status == 200 and tested.json()["ok"], tested.summary)
    report.check(
        "DoD n8n 的 API Key 与共享密钥不出现在响应里",
        API_KEY not in put.text + tested.text and TRIGGER_SECRET not in put.text + tested.text,
    )
    params = [
        {"name": "recipient", "label": "收件人", "type": "string", "required": True, "sensitive": True},
        {"name": "days", "label": "天数", "type": "number"},
    ]
    created = owner.request(
        "POST", "/api/v1/automations",
        {"name": "验收日报", "workflow_id": WORKFLOW, "webhook_path": PATH, "params": params}, headers=h,
    )
    automation = created.json() or {}
    report.check("S5-04 登记白名单工作流", created.status == 201 and automation.get("workflow_active") is True, created.summary)
    unknown_wf = owner.request(
        "POST", "/api/v1/automations",
        {"name": "x", "workflow_id": "wfNotInN8n", "webhook_path": "other"}, headers=h,
    )
    report.check("S5-04 n8n 里没有的工作流不能登记（422）", unknown_wf.status == 422, unknown_wf.summary)
    trigger_path = f"/api/v1/automations/{automation.get('id')}/trigger"
    undeclared = owner.request(
        "POST", trigger_path, {"params": {"recipient": "a@example.com", "cc": "x"}},
        headers={**h, "Idempotency-Key": str(uuid.uuid4())},
    )
    report.check("S5-04 未声明的参数 → 422", undeclared.status == 422, undeclared.summary)

    # ---------------------------------------------------------------- 触发、幂等、审计
    key = str(uuid.uuid4())
    body = {"params": {"recipient": "boss@example.com", "days": 7}}
    fake_call("POST", "/_fake/control", {"next_status": "error"})
    first = owner.request("POST", trigger_path, body, headers={**h, "Idempotency-Key": key})
    again = owner.request("POST", trigger_path, body, headers={**h, "Idempotency-Key": key})
    stats = fake_call("GET", "/_fake/stats")
    report.check(
        "S5-04 同一个 Idempotency-Key 只触发一次",
        first.status == 202 and again.status == 202 and first.json()["id"] == again.json()["id"]
        and stats["triggers"] == 1,
        f"{first.summary} / {again.summary} / n8n 收到 {stats['triggers']} 次",
    )
    report.check(
        "S5-04 n8n 只收到声明过的参数",
        stats["bodies"] and stats["bodies"][0]["params"] == body["params"],
    )
    run_id = (first.json() or {}).get("id")

    def finished() -> Any:
        runs = owner.request("GET", f"/api/v1/automations/{automation.get('id')}/runs", headers=h).json()["items"]
        return runs if runs and runs[0]["status"] == "error" else None

    runs = wait_for(finished, RECONCILE_WAIT)
    report.check(
        "S5-04 对账后执行记录为 error，只有遮蔽后的错误摘要",
        bool(runs) and runs[0]["id"] == run_id and (runs[0]["error_summary"] or "").startswith("工作流在节点"),
        str(runs and runs[0]["error_summary"]),
    )
    time.sleep(RECONCILE_WAIT)  # 再过一轮对账：同一次失败不应该再通知
    notes = owner.request("GET", "/api/v1/notifications", headers=h).json()["items"]
    failed = [n for n in notes if n["type"] == "automation_failed"]
    report.check("通知去重：同一次执行失败只通知一次", len(failed) == 1, str(len(failed)))
    disabled = owner.request(
        "PATCH", f"/api/v1/automations/{automation.get('id')}",
        {"version": automation.get("version"), "enabled": False}, headers=h,
    )
    rejected = owner.request(
        "POST", trigger_path, body, headers={**h, "Idempotency-Key": str(uuid.uuid4())}
    )
    report.check(
        "S5-04 停用的白名单拒绝触发（409 disabled）",
        disabled.status == 200 and rejected.status == 409
        and rejected.json()["error"]["details"]["reason"] == "disabled",
        rejected.summary,
    )
    audit = owner.request("GET", "/api/v1/audit-logs?limit=100", headers=h)
    triggered = [a for a in audit.json()["items"] if a["action"] == "automation.triggered"]
    outcomes = sorted(a["metadata"]["outcome"] for a in triggered)
    report.check(
        "DoD 每次触发都有审计记录（受理与拒绝）",
        "queued" in outcomes and "rejected:disabled" in outcomes,
        str(outcomes),
    )
    everything = json.dumps([audit.json(), runs, notes], ensure_ascii=False)
    report.check(
        "DoD 审计、执行记录、通知里没有参数值与密钥",
        "boss@example.com" not in everything and API_KEY not in everything and TRIGGER_SECRET not in everything,
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
    fake_call("POST", "/_fake/control", {"down": True})

    def n8n_stale() -> Any:
        page = owner.request("GET", "/api/v1/automations", headers=h).json()
        return page if page["connection"]["status"] == "stale" else None

    page = wait_for(n8n_stale, RECONCILE_WAIT)
    report.check(
        "DoD n8n 不可用：白名单照常显示，连接标为 stale",
        bool(page) and len(page["items"]) == 1,
        str(page and page["connection"]),
    )
    fake_call("POST", "/_fake/control", {"down": False})

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
