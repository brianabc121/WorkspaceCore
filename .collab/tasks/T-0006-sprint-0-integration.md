---
id: T-0006
title: Sprint 0 集成与验收：Compose、Caddy、运行手册（S0-04、S0-06）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: compose.yaml, infra, services/api/Dockerfile, services/api/.dockerignore, apps/web/Dockerfile, apps/web/.dockerignore, services/api/app/core/middleware.py, services/api/tests/test_health.py, Makefile, README.md, .env.example, scripts/init_env.py, scripts/smoke.sh, docs/deployment.md, docs/architecture.md, docs/threat-model.md, docs/acceptance, .github/workflows/ci-infra.yml, docs/TASKS.md
depends_on: T-0004, T-0005
created: 2026-10-29
updated: 2026-10-29
---

# T-0006 Sprint 0 集成与验收：Compose、Caddy、运行手册（S0-04、S0-06）

## 目标
在 T-0004（前端）和 T-0005（后端）合并后，把各部分组装成一键启动的 core Compose，补齐运行手册和 CI，
并按 Sprint 0 的 DoD 逐条实测验收。验收通过后勾选 `docs/TASKS.md`，交给人类决定是否进入 Sprint 1。

## 验收标准
- [x] S0-04：`compose.yaml` 包含 web、api、worker、postgres、redis、caddy；healthcheck、restart、命名卷；postgres / redis 不映射宿主机端口；所有镜像固定具体版本
- [x] `services/api/Dockerfile`（非 root 运行）、`apps/web/Dockerfile`（多阶段：`npm ci` + `npm run build` → 静态服务）
- [x] `infra/caddy/Caddyfile`：按契约转发；设置安全响应头和请求体大小限制
- [x] `make up`、`make migrate`、`make down`（不删除数据卷）可用
- [x] 新建 `.github/workflows/ci-infra.yml`：`docker compose config`、构建镜像、启动后检查 `/healthz` 与 `/readyz`
- [x] S0-06：`docs/deployment.md` 运行手册；README 快速开始与实际命令一致
- [x] Sprint 0 DoD 逐条实测：`make up` 起服务、`/healthz` 返回 200、Web 首页可打开、`make test` 与 `make lint` 通过；报告逐条列出通过 / 失败 / 未运行及原因
- [x] 验收通过后勾选 `docs/TASKS.md` 的 S0-01 ~ S0-06；escalate 给人类，请求批准进入 Sprint 1

## 背景 / 约束 / 参考
- 依赖：T-0004（codex，前端）、T-0005（claude，后端与骨架）都合并之后才开始
- 接口契约：`docs/decisions/2026-10-29-sprint-0-contracts.md`
- 分工：`.collab/decisions/D-0003-module-ownership.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 21:49 UTC · claude · 创建任务（owner: claude）
- 2026-10-29 22:25 UTC · claude · 开始任务：依赖 T-0004（PR #7）与 T-0005（PR #5）均已合并，开始集成与 Sprint 0 验收
- 2026-10-29 22:40 UTC · claude · 验收：本地 make up（6 服务健康）/ make migrate / make smoke 9 项 / 浏览器打开首页（CSP 下无报错）/ make down 后数据卷保留 / make lint / make test（后端 18、前端 18）全部通过；CI ci-infra 在干净 runner 上从零构建并跑完同一流程。报告：docs/acceptance/sprint-0.md；已勾选 docs/TASKS.md S0-01 ~ S0-06
- 2026-10-29 22:40 UTC · claude · 交接给 human（escalate）→ .collab/handoffs/2026-10-29-2240_T-0006_claude-to-human.md：Sprint 0 验收通过，请批准是否进入 Sprint 1
- 2026-10-29 22:51 UTC · human · owner human → claude：人类在对话中批准进入 Sprint 1（回复“可以”），指派 claude 关闭验收任务
- 2026-10-29 22:51 UTC · claude · 解除阻塞，继续：记录人类批准
- 2026-10-29 22:51 UTC · claude · status in_progress → done：Sprint 0 验收通过，人类已批准进入 Sprint 1
