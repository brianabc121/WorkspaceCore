---
id: T-0005
title: Sprint 0 后端与仓库骨架（S0-01、S0-03、S0-05）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api, Makefile, README.md, .env.example, .gitignore, docs/architecture.md, docs/api-contract.md, docs/data-dictionary.md, docs/deployment.md, docs/threat-model.md, .github/workflows/ci-api.yml
depends_on:
created: 2026-10-29
updated: 2026-10-29
---

# T-0005 Sprint 0 后端与仓库骨架（S0-01、S0-03、S0-05）

## 目标
建立仓库骨架和后端最小工程：FastAPI 服务（`/healthz`、`/readyz`）、Alembic 迁移、ARQ Worker 入口、
根目录命令和 CI，使 codex 的前端（T-0004）可以并行开发，并为 T-0006 的 Compose 集成做好准备。

## 验收标准
- [x] S0-01：根目录 `README.md`（快速开始、目录说明，保留现有的协作与设计文档章节）、`.env.example`（只放占位符，覆盖规范第 11.2 节）、`.gitignore`（Python / Node / env / 本地数据库文件）
- [x] S0-01：`Makefile` 提供规范第 11.3 节全部目标；Sprint 0 不实现的（`backup`、`restore-verify`）明确提示“尚未实现”并非零退出；`apps/web` 不存在时跳过前端部分
- [x] S0-01：`docs/` 下 architecture、api-contract、data-dictionary、deployment、threat-model 的骨架
- [x] S0-03：`services/api`：FastAPI + Pydantic 2 + SQLAlchemy 2（async + asyncpg）+ pydantic-settings；目录结构符合规范第 3 节
- [x] S0-03：`/healthz`、`/readyz` 严格按契约实现；结构化 JSON 日志，带 request_id，不输出敏感信息
- [x] S0-03：pytest + pytest-asyncio + httpx 测试，覆盖 `/readyz` 依赖正常与失败两种情况
- [x] S0-05：Alembic（async）；首个 revision 创建 `workspace` schema 与 `pgcrypto`、`pg_trgm` 扩展；用真实 PostgreSQL 做空库 `upgrade head` + `downgrade base` 冒烟测试
- [x] ARQ Worker 最小入口（`app/jobs/`），能连上 Redis 启动，供 Compose 使用
- [x] ruff、mypy 通过；新建 `.github/workflows/ci-api.yml`：ruff、mypy、pytest（PostgreSQL / Redis service container）、迁移冒烟
- [x] 后端依赖与 PostgreSQL / Redis 版本写进 `docs/decisions/<日期>-backend-stack.md`，附 PyPI / 官方来源
- [x] 实际运行 `make lint`、`make test`、`make migrate`（针对本地数据库）并把命令和输出写进 PR

## 背景 / 约束 / 参考
- 接口契约：`docs/decisions/2026-10-29-sprint-0-contracts.md`
- 分工：`.collab/decisions/D-0003-module-ownership.md`
- 不要改 `apps/web/**`；不要勾选 `docs/TASKS.md`（由 T-0006 统一勾选）
- Dockerfile 与 Compose 放在 T-0006

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 21:49 UTC · claude · 创建任务（owner: claude）
- 2026-10-29 21:53 UTC · claude · 开始任务：按 D-0003 与 Sprint 0 接口契约开工
- 2026-10-29 22:06 UTC · claude · 实测：make lint 通过；REQUIRE_INTEGRATION=1 make test 15 passed（含真实 PostgreSQL 16.15 / Redis 7 的集成测试）；make migrate 以非超级用户 owner 身份迁移成功；uvicorn 与 arq worker 实际启动，/healthz 200、/readyz 200、worker --check 退出码 0；CI 同款镜像下全部步骤通过
- 2026-10-29 22:06 UTC · claude · status in_progress → done：验收标准全部满足；S0-01、S0-03、S0-05 的 TASKS.md 勾选留给 T-0006 统一处理
- 2026-10-29 22:07 UTC · claude · 自审后补充：readyz 未注册任何检查时返回 503；LOG_LEVEL 不区分大小写。新增 2 个测试，合计 17 passed
