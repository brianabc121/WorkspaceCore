---
id: T-0040
title: Sprint 5 集成与验收：DoD 实测、A16 自动化、全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md
owner: claude
status: done
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
- [x] DoD 实测（规范第 13 节）：浏览器拿不到 Prometheus 管理端与任何 Secret（检查页面请求与响应、Caddy 路由）；第三方失败时明确显示 stale；每次触发自动化都有审计记录
- [x] 在 Compose 全栈上自动化 A16（断开 SSE 后重连，页面能重新拉取最新投影），以及通知去重、自动化白名单与幂等，并纳入 CI；Sprint 1 ~ 4 的验收继续通过
- [x] CI 里没有真实的第三方凭据：n8n / Prometheus 用锁定版本的真实容器或按 T-0033 记录格式编写的模拟服务，并写明与真实服务的差异
- [x] 浏览器 E2E 纳入 CI：运维页与首页服务状态（含 stale）、通知中心与 SSE 重连、自动化触发与运行记录、viewer 只读
- [x] `docs/acceptance/sprint-5.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）；勾选 `docs/TASKS.md` 的 S5-01 ~ S5-05

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
- 2026-10-31 05:10 UTC · claude · 人类在对话中（2026-10-31 03:55 UTC 前后）：Sprint 5 验收通过前先等着，不提前开始 Sprint 6；Sprint 6 的 AI 厂商选型也先暂停，到时再问。
- 2026-10-31 05:10 UTC · claude · 第二部分（二）（本地，未开 PR）：sprint5_browser.cjs 补上通知（SSE 推送后顶栏未读数变化、纯文本、应用内链接、首页组件、全部已读）、A16（Caddy 断开期间写入、api 重启期间 502 后恢复）、自动化（连接只写不读、登记白名单、确认后触发带 Idempotency-Key、对账后运行记录与错误摘要、结果未知不重发）、viewer 只读；seed.py 增加 notify 命令（经 EventBus 发布 SSE 事件）；sprint5_acceptance.py 结束时停用自己的 n8n 连接。用 codex PR #61 的前端在本机跑：只有“api 重启后 SSE 恢复”一步失败（PR #61 的阻塞问题，已在 PR 上提出并写进 issue #50 C-5）；本机副本加上退避重连后 15 步全部通过。下一步：PR #61 修好并合并后开 PR，写 docs/acceptance/sprint-5.md，勾选 TASKS.md S5-01 ~ S5-05。
- 2026-10-31 05:49 UTC · claude · 人类在对话中（2026-10-31 05:30 UTC 前后）：codex 没有额度，由 claude 直接修 PR #61，之后所有任务由 claude 独立完成；记录为 D-0007（accepted，人类决定、claude 记录）。claude 已接手 T-0039 并修复（同一个 PR）。
- 2026-10-31 05:49 UTC · claude · status in_progress → done：Sprint 5 验收通过：接口级 30 项、浏览器 15 步（本机全部通过，纳入 CI）；验收报告 docs/acceptance/sprint-5.md；勾选 TASKS.md S5-01 ~ S5-05。按预先批准进入 Sprint 6：AI 厂商选型被人类暂停，先做 S6-04 ~ S6-06（下一步：Sprint 6 规划 PR）。
