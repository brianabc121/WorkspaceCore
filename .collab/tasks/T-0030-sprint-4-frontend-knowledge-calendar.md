---
id: T-0030
title: Sprint 4 前端（一）：知识入口页与收藏、日历页与 agenda、首页 RecentDocs / CalendarAgenda 组件（S4-01、S4-02 前端）
owner: codex
status: done
priority: P1
branch: codex/T-0030-sprint-4-knowledge-calendar
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
- [x] S4-01：`/knowledge` 页：收藏列表、新增 / 编辑 / 删除（删除二次确认）、按项目筛选、“在 AFFiNE 中打开”；只展示后端返回的书签元数据，前端不请求 AFFiNE；URL 不合法时给出可理解的提示
- [x] S4-01：收藏与项目 / 任务的关联的展示与编辑（入口位置按契约，例如任务抽屉里的“相关文档”）
- [x] S4-02：`/calendar` 页：agenda（按天分组），以及周视图或月视图中的至少一种；新建 / 编辑 / 删除事件；全天事件与时区显示；切换时间范围
- [x] 首页 RecentDocs、CalendarAgenda 组件接真实数据，正确展示 loading / empty / error / stale / unauthorized（规范 8.3，沿用 Sprint 2 的组件状态）
- [x] viewer 看不到写入口（后端同样会拒绝）；移动端单列可用；亮暗主题
- [x] Vitest 覆盖表单校验、权限、工作空间隔离与错误状态；`npm run lint / typecheck / test / build` 与 `npm run check:api` 通过并把输出写进 PR；不修改 `src/services/schema.d.ts`（D-0005）

## 背景 / 约束 / 参考
- 依赖：T-0026（契约）合并后开始；开发时可用 mock，真实联调在 T-0032
- 规范：第 7.2 节、第 8.1 ~ 8.3 节
- 不要改 `apps/web` 以外的文件（`ci-web.yml` 除外）；不要勾选 `docs/TASKS.md`；需要改契约时在任务日志写明，由 claude 修改（D-0005）
- 与 T-0031 同属 codex，先做哪个都可以，两个任务分别提 PR

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: codex）
- 2026-10-30 17:32 UTC · codex · 开始任务
- 2026-10-30 17:33 UTC · codex · 已核对最新 main 6c0e66f，T-0026 已合并；无重复执行或待合并 Codex PR。实现计划见 apps/web/docs/plans/T-0030.md；沿用现有组件，agenda + 周视图，不新增依赖，先测后写。
- 2026-10-30 17:55 UTC · codex · 已实现知识来源与收藏 CRUD、项目/任务关联及入口、日历 agenda/周视图与时区、RecentDocs/CalendarAgenda。既有与新增测试全量 160 项通过（新增项目入口后知识测试 12 项通过）；lint/typecheck 已通过；本地模拟 BFF 的 Playwright 浏览器测试通过，已生成桌面/移动与亮暗截图，无 AFFiNE 请求。正在最终验证与独立审查，真实后端联调留 T-0027/T-0032。
- 2026-10-30 19:39 UTC · codex · status in_progress → done：知识收藏/来源/关联、日历 agenda/周视图和首页双卡片已完成；独立审查三项 Important 已补回归修复。Node22 下 lint/typecheck、167 项测试、build、check:api、模拟 BFF 浏览器检查通过；collab check 0 警告。真实输出与审查裁定见 apps/web/docs/verification/T-0030.md；真实部署浏览器验收由 Claude 的 T-0032 补齐。提交 PR 后等待 Claude 审查合并，T-0031 因路径重叠暂不启动。
- 2026-10-30 19:40 UTC · codex · 已推送并创建 PR #45：https://github.com/brianabc121/WorkspaceCore/pull/45。分支 codex/T-0030-sprint-4-knowledge-calendar；功能提交 7b5038c，已合入最新 main f3effe0；本地全部验收通过，等待 Claude 审查 / CI / 合并。请勿重复实现或重复开 PR；T-0031 等本 PR 合并后再开始。
