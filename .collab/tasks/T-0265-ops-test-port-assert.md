---
id: T-0265
title: 测试：test_services_without_monitoring 别把时间戳里的 9090 当成内部地址
owner: claude
status: done
priority: P2
branch: claude/amazing-faraday-6ey29e
paths: services/api/tests/test_operations.py
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0265 测试：test_services_without_monitoring 别把时间戳里的 9090 当成内部地址

## 目标
`tests/test_operations.py::test_services_without_monitoring` 用 `assert "9090" not in json.dumps(data)` 检查接口没有透露 Prometheus 的内部地址，
但响应里每个服务的 `checked_at` 带微秒，凑巧含“9090”时测试就失败。2026-11-16 PR #231 的 ci-api（run 43867455927 第 2 次，job 133320755792）就是这样：
`1 failed, 1818 passed, 16 skipped`，命中的是 `"checked_at": "2026-11-16T13:59:40.909042Z"`；Prometheus 本身是 `not_configured`，输出里没有任何地址。
改成检查带冒号的端口形式 `":9090"`（与同文件检查 `"vikunja:3456"` 的写法一致）：时间戳的时、分、秒只有两位且不超过 59，JSON 的数字前面是“冒号加空格”，都不会出现“:9090”；任何带 9090 端口的地址仍会被查出来。
人类 2026-11-16 在对话里要求本会话修 ci-api 的偶发失败（与 T-0264 同一件事的延续）。

## 验收标准
- [x] 断言改为 `":9090" not in json.dumps(data)`，保留“不透露内部地址”的意图；不跳过、不放宽别的断言
- [x] 本机用 CI 出错时的那段响应验证：旧断言失败、新断言通过；地址形式（如 `http://prometheus:9090`）仍能查出来
- [x] 本机跑 `tests/test_operations.py` 通过；ruff / mypy 通过；CI 的 ci-api 通过

## 背景 / 约束 / 参考
- `services/api` 归 claude（D-0003）；不在 D-0004 第 3 节的例外里。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 14:17 UTC · claude · 创建任务（owner: claude）
- 2026-11-16 14:18 UTC · claude · 开始任务
- 2026-11-16 14:19 UTC · claude · 改完：断言改为 ':9090' not in json.dumps(data)，注释写明原因。用 CI 出错那段响应的字段验证：旧断言 False（失败）、新断言 True（通过）；'http://prometheus:9090'、'localhost:9090' 仍会被查出来。services/api 里 ruff check / ruff format --check / mypy tests/test_operations.py 通过；TEST_*=127.0.0.1 REQUIRE_INTEGRATION=1 pytest tests/test_operations.py → 23 passed（第一次跑时本机 Postgres / Redis 停了，5 个连接被拒，重启后全过）。没有跳过或放宽别的断言。
- 2026-11-16 14:19 UTC · claude · status in_progress → done：断言改成查端口形式，验收标准全部勾选
