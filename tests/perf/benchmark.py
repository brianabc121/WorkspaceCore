#!/usr/bin/env python3
"""性能基准（S6-05，T-0044）：基础 API P95 < 300 ms、缓存命中的首页 P95 < 500 ms（规范第 12 节）。

测法（docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md 第 6 节）：
- 经 Caddy 访问（与浏览器相同的入口），已登录用户的只读接口，不含外部服务。
- 数据量：新建临时用户，写入 200 条任务投影、40 个本周日程、50 条通知（seed.py bulk 与日历接口）。
- 每个接口先预热（首页因此命中本地投影与缓存），再按并发 4 与 16 各发固定次数的请求（默认 200）。
- 报告每组的 P50 / P95 / P99 / 最大值、错误数与吞吐，写成表格；--output 另存 JSON。
- 判定：没有错误，且 P95 不超过“目标 × --tolerance”。CI 的 runner 性能不稳定，用 --tolerance 2；
  正式的对比数据以本机固定环境（--tolerance 1，默认）为准，写进验收报告。

前提：Compose 全栈已启动并迁移（make up && make migrate）。不要求空库。
用法：python3 tests/perf/benchmark.py [--base-url URL] [--requests 200] [--tolerance 1]
                                      [--output report.json]
只依赖 Python 标准库；登录与造数据复用 tests/e2e 的 e2e_support.py、seed.py。
"""

import argparse
import http.client
import json
import math
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
from e2e_support import Client, default_base_url, parse_base_url
from seed import bulk, create_user

PASSWORD = "perf benchmark passphrase"  # noqa: S105 -- 只用于本脚本临时创建的测试账号
BASIC_MS = 300
DASHBOARD_MS = 500
CONCURRENCY = (4, 16)
WARMUP = 10
TASKS, EVENTS, NOTICES = 200, 40, 50


@dataclass
class Result:
    name: str
    path: str
    target_ms: int
    concurrency: int
    requests: int
    errors: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    rps: float


def percentile(values: list[float], pct: float) -> float:
    """最近秩法：至少 pct% 的样本不大于返回值。"""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


class Worker:
    """一个线程一条长连接（与浏览器复用连接一致），只发 GET。"""

    def __init__(self, base: str, headers: dict[str, str]) -> None:
        self.parts = urlsplit(base)
        self.headers = headers
        self.connection: http.client.HTTPConnection | None = None

    def get(self, path: str) -> tuple[int, float]:
        for attempt in (1, 2):  # 服务端关闭了空闲连接时重连一次，不算进延迟
            if self.connection is None:
                cls = (
                    http.client.HTTPSConnection
                    if self.parts.scheme == "https"
                    else http.client.HTTPConnection
                )
                self.connection = cls(self.parts.netloc, timeout=15)
            started = time.perf_counter()
            try:
                self.connection.request("GET", path, headers=self.headers)
                response = self.connection.getresponse()
                response.read()
                return response.status, (time.perf_counter() - started) * 1000
            except (http.client.HTTPException, ConnectionError):
                self.connection.close()
                self.connection = None
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")


def measure(
    base: str, headers: dict[str, str], path: str, concurrency: int, count: int
) -> tuple[list[tuple[int, float]], float]:
    local = threading.local()

    def one(_: int) -> tuple[int, float]:
        if not hasattr(local, "worker"):
            local.worker = Worker(base, headers)
        try:
            status, elapsed = local.worker.get(path)
            return status, elapsed
        except (OSError, http.client.HTTPException):
            return 0, 0.0

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(one, range(concurrency * 2)))  # 每个线程先建好连接
        started = time.perf_counter()
        samples = list(pool.map(one, range(count)))
        wall = time.perf_counter() - started
    return samples, wall


def prepare(base: str) -> tuple[dict[str, str], list[tuple[str, str, int]]]:
    """新建用户与数据，登录，返回请求头与要测的接口。"""
    run = uuid.uuid4().hex[:8]
    email = f"perf-{run}@perf.example.com"
    user_id, workspace_id, _ = create_user(email, "性能基准", PASSWORD)
    bulk(workspace_id, user_id, TASKS, NOTICES)
    client = Client(base)
    login = client.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if login.status != 200:
        sys.exit(f"登录失败：{login.summary}")
    ws = {"X-Workspace-Id": workspace_id}
    monday = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    monday -= timedelta(days=monday.weekday())
    for i in range(EVENTS):
        start = monday + timedelta(days=i % 7, hours=8 + i % 10)
        created = client.request(
            "POST",
            "/api/v1/calendar/events",
            {
                "title": f"性能基准日程 {i}",
                "timezone": "Asia/Shanghai",
                "start_at": start.isoformat(),
                "end_at": (start + timedelta(minutes=45)).isoformat(),
            },
            headers=ws,
        )
        if created.status != 201:
            sys.exit(f"写入日程失败：{created.summary}")
    sunday = monday + timedelta(days=7)
    week = f"start={quote(monday.isoformat())}&end={quote(sunday.isoformat())}"
    cookies = "; ".join(f"{c.name}={c.value}" for c in client.jar)
    headers = {"Accept": "application/json", "Cookie": cookies, **ws}
    endpoints = [
        ("当前用户", "/api/v1/me", BASIC_MS),
        ("工作空间列表", "/api/v1/workspaces", BASIC_MS),
        ("任务列表", "/api/v1/tasks", BASIC_MS),
        ("本周日程", f"/api/v1/calendar/events?{week}", BASIC_MS),
        ("通知", "/api/v1/notifications", BASIC_MS),
        ("首页聚合（缓存命中）", "/api/v1/dashboard/overview", DASHBOARD_MS),
    ]
    return headers, endpoints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=default_base_url())
    parser.add_argument("--requests", type=int, default=200, help="每组的请求次数")
    parser.add_argument("--tolerance", type=float, default=1.0, help="P95 允许为目标的几倍")
    parser.add_argument("--output", type=Path, help="另存 JSON 报告")
    args = parser.parse_args()
    base = parse_base_url(args.base_url)
    print(f"性能基准：{base}，每组 {args.requests} 次，并发 {'/'.join(map(str, CONCURRENCY))}")
    headers, endpoints = prepare(base)
    print(f"数据：{TASKS} 条任务投影、{EVENTS} 个本周日程、{NOTICES} 条通知\n")

    results: list[Result] = []
    for name, path, target in endpoints:
        warm, _ = measure(base, headers, path, 1, WARMUP)
        if any(status != 200 for status, _ in warm):
            print(f"{name}：预热请求失败（{sorted({s for s, _ in warm})}）")
        for concurrency in CONCURRENCY:
            samples, wall = measure(base, headers, path, concurrency, args.requests)
            ok = [elapsed for status, elapsed in samples if status == 200]
            values = ok or [0.0]
            results.append(
                Result(
                    name=name,
                    path=path.split("?")[0],
                    target_ms=target,
                    concurrency=concurrency,
                    requests=len(samples),
                    errors=len(samples) - len(ok),
                    p50_ms=round(percentile(values, 50), 1),
                    p95_ms=round(percentile(values, 95), 1),
                    p99_ms=round(percentile(values, 99), 1),
                    max_ms=round(max(values), 1),
                    rps=round(len(samples) / wall, 1),
                )
            )

    print("| 接口 | 并发 | P50 | P95 | P99 | 最大 | 错误 | 吞吐（次/秒） | 目标 P95 | 结果 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    failed = 0
    for r in results:
        passed = r.errors == 0 and r.p95_ms <= r.target_ms * args.tolerance
        failed += 0 if passed else 1
        print(
            f"| {r.name} `{r.path}` | {r.concurrency} | {r.p50_ms} | {r.p95_ms} | {r.p99_ms} "
            f"| {r.max_ms} | {r.errors} | {r.rps} | < {r.target_ms} ms "
            f"| {'通过' if passed else '未达标'} |"
        )
    if args.output:
        report = {
            "base_url": base,
            "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "tolerance": args.tolerance,
            "data": {"tasks": TASKS, "events": EVENTS, "notifications": NOTICES},
            "results": [asdict(r) for r in results],
        }
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    limit = f"目标 × {args.tolerance:g}" if args.tolerance != 1 else "目标"
    verdict = "全部达标" if not failed else f"{failed} 组未达标"
    print(f"\n{verdict}（判定：没有错误且 P95 不超过{limit}）")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
