---
id: T-0168
title: 四页改造（三）：项目页（真实进度卡片、需要关注、本周完成、最近动态）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/tasks,apps/web/src/pages/ProjectsPage.vue,apps/web/tests
depends_on: T-0166
created: 2026-11-09
updated: 2026-11-09
---

# T-0168 四页改造（三）：项目页（真实进度卡片、需要关注、本周完成、最近动态）

## 目标
按效果图 02 改造项目页：真实进度（0 任务不显示 100%，待查看算完成）、状态计数、需要关注筛选、本周完成、最近动态。

## 验收标准
- [x] 顶部统计（未归档、需要关注、本周完成）：本周按浏览器时区的周一 00:00 到下周一 00:00
- [x] 卡片：进度 done/total 与进度条（0 任务显示“尚无任务”），进行中 / 待查看 / 阻塞计数，最近更新；更多操作菜单（时间轴、相关文档、编辑、归档、删除）
- [x] 筛选：全部 / 需要关注 / 已归档，搜索，按名称或最近更新排序；分页加载
- [x] 最近动态按纯文本显示，读取失败只在动态区提示，不影响卡片
- [x] 单元测试 `project-workspace-redesign.test.ts`、`projects-page.test.ts`；前端检查通过
- [x] 浏览器验收（sprint3：改名、归档、删除；菜单选一项后收起）与截图

## 背景 / 约束 / 参考
方案 `docs/v2/pages-redesign/`（规范 `docs/superpowers/specs/2026-11-08-workspace-four-pages-design.md`，计划 `docs/superpowers/plans/2026-11-08-workspace-four-pages-redesign.md`），效果图在 `assets/`。人类 2026-11-09 在对话中要求「按照效果图和设计文档进行全部落实」。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 01:32 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 02:29 UTC · claude · 开始任务：按四页改造方案实施（本分支）
