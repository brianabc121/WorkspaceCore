---
id: T-0039
title: Sprint 5 前端（二）：通知中心与 SSE 订阅（重连、缓存失效）、自动化页（白名单触发、运行记录）（S5-03、S5-04 前端）
owner: codex
status: done
priority: P1
branch: codex/T-0039-sprint-5-notifications-automations
paths: apps/web,.github/workflows/ci-web.yml
depends_on: T-0034
created: 2026-10-31
updated: 2026-10-31
---

# T-0039 Sprint 5 前端（二）：通知中心与 SSE 订阅（重连、缓存失效）、自动化页（白名单触发、运行记录）（S5-03、S5-04 前端）

## 目标
实现通知中心、SSE 订阅与自动化页的前端。只做 `apps/web`；后端由 claude 的 T-0036、T-0037 实现。

## 验收标准
- [x] 顶栏通知入口：未读数、通知列表、单条 / 全部标为已读；与首页 `unread_notifications` 一致
- [x] SSE 订阅：登录后连接 `/api/v1/events/stream`，按事件失效对应的 Vue Query 缓存并重新拉取；断线后自动重连（带 `Last-Event-ID`），重连后重新拉取最新数据（A16）；切换工作空间时断开旧连接
- [x] `/automations` 页：白名单工作流列表、最近执行状态与错误摘要；触发前确认、按参数 schema 填写、带 `Idempotency-Key`；结果未知时引导核对而不是重复触发；viewer 只读
- [x] 所有状态正确展示 loading / empty / error / stale / unauthorized（规范 8.3）；移动端单列可用；亮暗主题
- [x] Vitest 覆盖 SSE 重连与缓存失效、通知已读、触发的幂等与未知结果；`npm run lint / typecheck / test / build` 与 `npm run check:api` 通过并把输出写进 PR；不修改 `src/services/schema.d.ts`（D-0005）

## 背景 / 约束 / 参考
- 依赖：T-0034（契约）合并后开始；开发时可用契约模拟 BFF，真实联调在 T-0040
- Docker 环境不与 claude 共用（D-0006）
- 规范：第 4 节、第 7.5 节、第 8.3 节、第 10 节；验收场景 A16；S5-03、S5-04

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: codex）
- 2026-10-31 03:57 UTC · codex · 开始任务
- 2026-10-31 03:59 UTC · codex · 已fetch最新main479ef2b，确认PR58已合并、无重复执行；issue50 C4要求现在接手。短计划 apps/web/docs/plans/T-0039.md，原地独立分支、Node22 npm ci；按通知→SSE→自动化→浏览器/独立审查执行，沿用契约，遵守D0005/6。
- 2026-10-31 04:13 UTC · codex · 通知/SSE/自动化功能已实现；32项专项测试通过，模拟BFF+EventSource Chromium通知/重连/触发未知/亮暗移动端检查通过。浏览器发现弹窗焦点丢失Escape失效，新增失败回归后修复；参数select增加明确可访问名称。独立整分支审查进行中，最终全套验证与PR待完成，不标done。
- 2026-10-31 04:22 UTC · codex · status in_progress → done：通知入口/首页组件、SSE作用域刷新、自动化白名单/连接管理/运行记录/参数确认和幂等恢复已完成。Node22 npm ci；lint/typecheck/check:api/build/browser均退出0；25文件261测试通过（专项37）。一次独立审查5项回归RED→GREEN后全套复跑。schema无改动，collab check0警告；build有527.16kB非阻塞警告。真实部署联调由T0040，验证 apps/web/docs/verification/T-0039.md。将推PR等Claude审查合并。
