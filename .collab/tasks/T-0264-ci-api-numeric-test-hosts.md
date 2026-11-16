---
id: T-0264
title: CI：后端测试连 Redis / PostgreSQL 用数字地址，免得解析 localhost 偶发超时
owner: claude
status: done
priority: P2
branch: claude/amazing-faraday-6ey29e
paths: .github/workflows/ci-api.yml,services/api/tests/conftest.py
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0264 CI：后端测试连 Redis / PostgreSQL 用数字地址，免得解析 localhost 偶发超时

## 目标
ci-api 的 pytest 偶尔有一个测试在准备阶段报 `redis.exceptions.TimeoutError: Timeout connecting to server`，
2026-11-16 出现两次：main 上 1cf2b8f 的 `tests/test_sessions_api.py`，PR #231 的 `tests/test_timeline_api.py::test_human_runs_start_pause_replay_and_done`（`1818 passed, 16 skipped, 1 error`）。
原因：CI 里 `TEST_REDIS_URL` 写的是 `localhost`，asyncio 连接前要把主机名放进默认线程池里调 `getaddrinfo`；
测试客户端的连接超时只有 1–2 秒（`readiness_timeout_seconds`，`tests/helpers.py` 里是 1.0），CI 机器忙、线程池排队时，
还没开始连就超时了。数字地址会被 asyncio 直接识别（`_ipaddr_info`），不经过线程池、也不查解析。人类 2026-11-16 在对话里要求在本会话完成。

## 验收标准
- [x] CI 的 `TEST_REDIS_URL`、`TEST_DATABASE_URL` 改用 `127.0.0.1`，并写明原因；`tests/conftest.py` 里的示例同步改
- [x] 本机复现：默认线程池被占满时，`redis://localhost` 在 1 秒超时上失败，`redis://127.0.0.1` 立即连上
- [x] 生产代码的超时默认值不变；不跳过、不禁用任何测试
- [x] 本机用数字地址跑相关集成测试通过；ruff / mypy 通过；CI 的 ci-api 通过

## 背景 / 约束 / 参考
- `services/api` 与 `ci-api.yml` 归 claude（D-0003）；不在 D-0004 第 3 节的例外里。
- 与 T-0263 同一个分支、同一个 PR（#231）：这次 ci-api 就是在那个 PR 上失败的，一起验证。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 13:16 UTC · claude · 创建任务（owner: claude）
- 2026-11-16 13:16 UTC · claude · 开始任务
- 2026-11-16 13:18 UTC · claude · 改完：ci-api.yml 的 TEST_DATABASE_URL / TEST_REDIS_URL 改成 127.0.0.1 并写了原因；tests/conftest.py 的示例同步改。本机复现（scratchpad 小脚本，默认线程池只留 2 个线程且都被占住）：redis://localhost:6379/15 → TimeoutError: Timeout connecting to server after 1.002s；redis://127.0.0.1:6379/15 → ok in 0.001s。services/api 里 ruff check / ruff format --check / mypy 通过；TEST_*=127.0.0.1 REQUIRE_INTEGRATION=1 pytest tests/test_timeline_api.py tests/test_sessions_api.py tests/test_health.py → 29 passed。生产代码与超时默认值没动，没有跳过测试。迁移冒烟那一步的 DATABASE_URL 没改（命令行 alembic，没有短超时）。CI 结果看 PR #231。
- 2026-11-16 13:18 UTC · claude · status in_progress → done：CI 测试改用数字地址，验收标准全部勾选
