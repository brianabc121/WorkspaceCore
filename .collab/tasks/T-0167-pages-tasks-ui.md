---
id: T-0167
title: 四页改造（二）：任务页（状态筛选、列表、详情侧栏、标为已查看、阻塞原因）与四页共用视觉基础
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/tasks,apps/web/src/features/workspace,apps/web/src/pages/TasksPage.vue,apps/web/src/App.vue,apps/web/src/style.css,apps/web/src/app/router.ts,apps/web/tests
depends_on: T-0166
created: 2026-11-09
updated: 2026-11-09
---

# T-0167 四页改造（二）：任务页（状态筛选、列表、详情侧栏、标为已查看、阻塞原因）与四页共用视觉基础

## 目标
按效果图 01 改造任务页：状态筛选标签、搜索与筛选、表格 / 看板、右侧详情（阻塞原因、待查看说明与“标为已查看”），并建立四页共用的宽版容器与样式。

## 验收标准
- [x] 四页（任务、项目、日历、邮件）路由 `meta.wide`，`main.is-wide` 宽版容器；共用样式 `features/workspace/workspace-pages.css`
- [x] 状态标签计数来自 `GET /tasks/summary`（与列表同一筛选），URL 保存 `?state`、`?task`、`?board`
- [x] ≥1440px 右侧详情面板，更窄时用原来的抽屉；详情里阻塞原因（修改 / 解除）、待查看（完成说明、标为已查看、查看 README），打开不会自动标记
- [x] 阻塞原因、完成说明按纯文本显示；viewer 看不到写操作按钮（服务端仍校验）
- [x] 单元测试 `task-workspace-redesign.test.ts` 等；lint / typecheck / vitest / build / check:api 通过
- [x] 浏览器验收（本地全栈：home / sprint3 / timeline 通过；sprint6 需要 OpenIM，由 CI agents job 跑）与亮暗、桌面 / 手机截图

## 背景 / 约束 / 参考
方案 `docs/v2/pages-redesign/`（规范 `docs/superpowers/specs/2026-11-08-workspace-four-pages-design.md`，计划 `docs/superpowers/plans/2026-11-08-workspace-four-pages-redesign.md`），效果图在 `assets/`。人类 2026-11-09 在对话中要求「按照效果图和设计文档进行全部落实」。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 01:32 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 02:29 UTC · claude · 开始任务：按四页改造方案实施（本分支）
