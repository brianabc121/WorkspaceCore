---
id: T-0030
title: Sprint 4 前端（一）：知识入口页与收藏、日历页与 agenda、首页 RecentDocs / CalendarAgenda 组件（S4-01、S4-02 前端）
owner: codex
status: todo
priority: P1
branch:
paths: apps/web,.github/workflows/ci-web.yml
depends_on: T-0026
created: 2026-10-30
updated: 2026-10-30
---

# T-0030 Sprint 4 前端（一）：知识入口页与收藏、日历页与 agenda、首页 RecentDocs / CalendarAgenda 组件（S4-01、S4-02 前端）

## 目标
按 T-0026 的契约实现知识入口与日历的前端：`/knowledge` 收藏页、`/calendar` 日历页，以及首页 RecentDocs、CalendarAgenda 组件。
只做 `apps/web`；后端由 claude 的 T-0027 实现。

## 验收标准
- [ ] S4-01：`/knowledge` 页：收藏列表、新增 / 编辑 / 删除（删除二次确认）、按项目筛选、“在 AFFiNE 中打开”；只展示后端返回的书签元数据，前端不请求 AFFiNE；URL 不合法时给出可理解的提示
- [ ] S4-01：收藏与项目 / 任务的关联的展示与编辑（入口位置按契约，例如任务抽屉里的“相关文档”）
- [ ] S4-02：`/calendar` 页：agenda（按天分组），以及周视图或月视图中的至少一种；新建 / 编辑 / 删除事件；全天事件与时区显示；切换时间范围
- [ ] 首页 RecentDocs、CalendarAgenda 组件接真实数据，正确展示 loading / empty / error / stale / unauthorized（规范 8.3，沿用 Sprint 2 的组件状态）
- [ ] viewer 看不到写入口（后端同样会拒绝）；移动端单列可用；亮暗主题
- [ ] Vitest 覆盖表单校验、权限、工作空间隔离与错误状态；`npm run lint / typecheck / test / build` 与 `npm run check:api` 通过并把输出写进 PR；不修改 `src/services/schema.d.ts`（D-0005）

## 背景 / 约束 / 参考
- 依赖：T-0026（契约）合并后开始；开发时可用 mock，真实联调在 T-0032
- 规范：第 7.2 节、第 8.1 ~ 8.3 节
- 不要改 `apps/web` 以外的文件（`ci-web.yml` 除外）；不要勾选 `docs/TASKS.md`；需要改契约时在任务日志写明，由 claude 修改（D-0005）
- 与 T-0031 同属 codex，先做哪个都可以，两个任务分别提 PR

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: codex）
