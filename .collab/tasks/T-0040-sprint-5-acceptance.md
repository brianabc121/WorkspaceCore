---
id: T-0040
title: Sprint 5 集成与验收：DoD 实测、A16 自动化、全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
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
- 2026-10-31 03:38 UTC · claude · 开始任务
- 2026-10-31 03:39 UTC · claude · 第一部分（接口级验收）：tests/e2e/sprint5_acceptance.py（30 项：DoD 三条、S5-01 ~ S5-05、自动化白名单与幂等、通知去重、A16 接口级）与 tests/e2e/fake_n8n_server.py（复用后端测试的内存版 n8n，行为按 T-0037 实测 ADR）；ci-infra.yml 在 Sprint 4 之后启动 prometheus（--profile monitoring，只启动 prometheus，Grafana 不在 CI 启动）并运行 Sprint 5 验收，CI 超时放宽到 30 分钟。本机（claude 自己的 Docker）全部通过。下一步：等 codex 的 T-0038（PR #58）/ T-0039 合并后写 sprint5_browser.cjs、验收报告 docs/acceptance/sprint-5.md，勾选 TASKS.md S5-01 ~ S5-05。
- 2026-10-31 03:51 UTC · claude · 第二部分（一）：tests/e2e/sprint5_browser.cjs 先覆盖运维页（T-0038 已合并）：服务状态、6 个预设视图、切换范围与视图只提交预设 ID、首页“服务状态”“API 延迟”组件、停掉 Prometheus 后页面与组件显示“数据可能过期（截至 …）”且 Prometheus 显示不可用、恢复、390px、viewer 可读、DoD（浏览器只访问工作台地址，响应与页面里没有监控内部地址和密钥）。ci-infra.yml 在 Sprint 4 浏览器验收之后运行它，停止 Prometheus 的步骤挪到它后面。本机（claude 自己的 Docker）在配置与不配置 Grafana 两种情况下都通过。下一步：codex 的 T-0039 合并后补通知中心、SSE 重连（A16 浏览器）、自动化触发与运行记录、viewer 只读，再写验收报告、勾选 TASKS.md。
