---
id: T-0004
title: Sprint 0：工程骨架（docs/TASKS.md S0-01 ~ S0-06）
owner: codex
status: todo
priority: P1
branch:
paths: apps/web, services/api, infra, compose.yaml, Makefile, .env.example, .gitignore, README.md, docs, .github/workflows/ci.yml
depends_on:
created: 2026-10-29
updated: 2026-10-29
---

# T-0004 Sprint 0：工程骨架（docs/TASKS.md S0-01 ~ S0-06）

## 目标
按 CODEX_START.md 完成 My Workspace 的 Sprint 0 工程骨架：前端、后端、Compose、Alembic、Makefile、CI 基础结构。
本任务只做 Sprint 0；**未经人类批准，不进入 Sprint 1**。

## 验收标准
- [ ] docs/TASKS.md 的 S0-01 ~ S0-06 全部完成，并在同一个 PR 里勾选
- [ ] DoD（docs/DEVELOPMENT_SPEC.md 第 13 节 Sprint 0）：`make up` 起服务，`/healthz` 返回 200，Web 首页可打开，`make test` 与 `make lint` 通过
- [ ] 不确定的版本 / 接口写进 docs/decisions/（附来源链接），不猜测
- [ ] 运行过的命令和实际输出写进 PR；没能运行的如实说明原因
- [ ] 不破坏已有协作设施：保留 `.github/workflows/collab.yml` 与 `collab-sync.yml`（项目 CI 另建 `ci.yml`）；
      `.gitignore`、`README.md` 在现有内容上扩展，不要覆盖；不要动 `scripts/collab*.py`

## 背景 / 约束 / 参考
- 开工指令：`CODEX_START.md`（人类撰写）
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 3 节（目录结构）、第 11 节（Docker 与命令）、第 12 节（测试）、第 13 节（Sprint 0）、第 18 节（DoD）
- 项目规则：AGENTS.md 第 8 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 21:37 UTC · claude · 创建任务（owner: codex）：依据人类提交的 CODEX_START.md（caf32fc），Sprint 0 由 codex 实施
