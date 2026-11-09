---
id: T-0169
title: 四页改造（四）：日历周视图（小月历、时间画布、重叠与跨日、完整分页、日程详情）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/calendar,apps/web/src/pages/CalendarPage.vue,apps/web/tests
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0169 四页改造（四）：日历周视图（小月历、时间画布、重叠与跨日、完整分页、日程详情）

## 目标
按效果图 03 改造日历页：周视图时间画布、小月历、侧栏、日程详情，完整分页读取，正确处理全天、跨日、重叠与夏令时。

## 验收标准
- [x] 周 / 月 / 列表视图；周视图时间画布（重叠分列、跨日拆段、全天行），小月历与今天按钮
- [x] 读取范围内的全部日程（`useAllPages` 跟着 next_cursor 读完，超过 100 条不漏），夏令时切换日不错位（`calendar-week-layout.test.ts`）
- [x] 点日程先看详情，再“编辑日程”；没有关联项目的接口时不放“查看关联项目”按钮
- [x] 删除旧日历样式；前端检查通过
- [x] 浏览器验收（sprint4：先看详情再编辑 / 删除，viewer 只读）与截图

## 背景 / 约束 / 参考
方案 `docs/v2/pages-redesign/`（规范 `docs/superpowers/specs/2026-11-08-workspace-four-pages-design.md`，计划 `docs/superpowers/plans/2026-11-08-workspace-four-pages-redesign.md`），效果图在 `assets/`。人类 2026-11-09 在对话中要求「按照效果图和设计文档进行全部落实」。

偏差：任务截止在周视图里只占一格（不画成时间段）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 01:32 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 02:29 UTC · claude · 开始任务：按四页改造方案实施（本分支）
