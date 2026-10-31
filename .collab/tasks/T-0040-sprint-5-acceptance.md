---
id: T-0040
title: Sprint 5 集成与验收：DoD 实测、A16 自动化、全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md
owner: claude
status: todo
priority: P1
branch:
paths: docs/acceptance,docs/TASKS.md,tests/e2e,.github/workflows/ci-infra.yml,scripts/smoke.sh
depends_on: T-0035, T-0036, T-0037, T-0038, T-0039
created: 2026-10-31
updated: 2026-10-31
---

# T-0040 Sprint 5 集成与验收：DoD 实测、A16 自动化、全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md

## 目标
T-0035 ~ T-0039 合并后做前后端联调，并按 Sprint 5 的 DoD 实测验收。通过后勾选 `docs/TASKS.md` 的 S5-01 ~ S5-05，
按人类的预先批准进入 Sprint 6。

## 验收标准
- [ ] DoD 实测（规范第 13 节）：浏览器拿不到 Prometheus 管理端与任何 Secret（检查页面请求与响应、Caddy 路由）；第三方失败时明确显示 stale；每次触发自动化都有审计记录
- [ ] 在 Compose 全栈上自动化 A16（断开 SSE 后重连，页面能重新拉取最新投影），以及通知去重、自动化白名单与幂等，并纳入 CI；Sprint 1 ~ 4 的验收继续通过
- [ ] CI 里没有真实的第三方凭据：n8n / Prometheus 用锁定版本的真实容器或按 T-0033 记录格式编写的模拟服务，并写明与真实服务的差异
- [ ] 浏览器 E2E 纳入 CI：运维页与首页服务状态（含 stale）、通知中心与 SSE 重连、自动化触发与运行记录、viewer 只读
- [ ] `docs/acceptance/sprint-5.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）；勾选 `docs/TASKS.md` 的 S5-01 ~ S5-05

## 背景 / 约束 / 参考
- 依赖：T-0035 ~ T-0039 都合并之后完成；接口级验收可以先于前端完成（同 T-0032 的做法）
- 人类已于 2026-10-30 预先批准“验收通过就自动进入下一个 Sprint”（见 T-0032 的进度日志）
- 规范：第 13 节（Sprint 5 DoD）、第 15 节（A16）、第 18 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: claude）
