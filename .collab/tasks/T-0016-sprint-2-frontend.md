---
id: T-0016
title: Sprint 2 前端：顶栏与全局搜索、Widget Registry 与基础卡片、拖拽网格与布局保存、局部失败与 stale（S2-02 ~ S2-05 前端）
owner: codex
status: done
priority: P1
branch: codex/T-0016-sprint-2-frontend
paths: apps/web,.github/workflows/ci-web.yml
depends_on: T-0014
created: 2026-10-30
updated: 2026-10-30
---

# T-0016 Sprint 2 前端：顶栏与全局搜索、Widget Registry 与基础卡片、拖拽网格与布局保存、局部失败与 stale（S2-02 ~ S2-05 前端）

## 目标
按 T-0014 的契约实现 Sprint 2 前端：可配置的首页 Widget 网格、Widget Registry 与基础卡片、拖拽布局与保存、
单个组件失败不影响整页，以及顶栏全局搜索（占位 / 本地搜索）。只做 `apps/web`；后端由 claude 的 T-0015 实现。

## 验收标准
- [x] S2-02：顶栏全局搜索入口（`Ctrl/Cmd+K`，本 Sprint 只做占位 / 本地搜索：页面导航与当前首页的组件），顶栏“创建”“通知”入口占位；小屏侧栏折叠、移动端单列；新增界面全部适配亮 / 暗主题
- [x] S2-03：前端 Widget Registry（规范 8.2 `WidgetDefinition`，配置用 Zod 校验）；按契约实现 Sprint 2 内置的基础卡片；未知 widget type 显示安全的错误卡片，不影响其他组件
- [x] S2-04：12 列网格拖拽与调整大小，按契约保存布局；版本冲突（409）给出提示并可重新加载；刷新后布局不丢；viewer 只读（不显示编辑入口，后端同样会拒绝）
- [x] S2-05：首页使用聚合接口；每个组件独立的 loading / empty / error / unavailable / stale 状态，单个组件失败不影响整页；布局导入 / 导出 JSON（按契约校验）
- [x] 写操作防重复提交；Vitest 覆盖 Registry、未知 type、版本冲突、局部失败；`npm run lint / typecheck / test / build` 与 `npm run check:api` 通过并把输出写进 PR
- [x] 新增依赖（例如拖拽网格库）固定版本，并在 PR 中说明来源；不修改 `src/services/schema.d.ts`（D-0005，由 claude 随契约提交）

## 背景 / 约束 / 参考
- 依赖：T-0014（契约）合并后开始；开发时可用 mock，真实联调在 T-0017
- 接口契约：`docs/decisions/2026-10-30-sprint-2-dashboard-contract.md`（T-0014 产出）与 `services/api/openapi.json`
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 8.1 ~ 8.3 节；Sprint 2 范围与 DoD 见第 13 节
- 不要改 `apps/web` 以外的文件（`ci-web.yml` 除外）；不要勾选 `docs/TASKS.md`；需要改契约时在任务日志写明，由 claude 修改（D-0005）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 03:04 UTC · claude · 创建任务（owner: codex）
- 2026-10-30 06:56 UTC · codex · 开始任务
- 2026-10-30 06:57 UTC · codex · 人类要求继续；PR16 已合并且 Sprint1 验收完成，main 已有获批的 Sprint2 契约和后端。按收件箱接手 T0016，从 fc11f8e 建 codex/T-0016-sprint-2-frontend。短计划：1 Zod Registry/布局导入导出与测试；2 首页聚合/局部状态/版本冲突/工作空间隔离；3 GridStack12列拖拽/调整大小与键盘布局控件；4 顶栏本地搜索/占位入口；5 Node22全部验证、浏览器mock拖拽刷新、独立审查及PR。D0005不改schema.d.ts；不改Claude T0017路径。
- 2026-10-30 07:27 UTC · codex · 实现完成：四种 Registry 卡片/Zod严格校验、聚合首页、GridStack14拖拽缩放及键盘位置/尺寸、版本冲突锁定、JSON新建导入/无ID导出、组件配置/删除与仪表盘选择/设首页、本地Ctrl/Cmd+K搜索。Chromium真实鼠标拖拽+缩放→保存→刷新、CSRF/空间头、暗色移动单列通过。独立审查2项P2已先复现再修复：提交成功后刷新失败不会重复写，冲突重载失败不解除编辑锁；补测导出/关闭对话框不绕过冲突锁及搜索自动聚焦。依赖GridStack14.0.0已核验npm registry与包内类型/实现；来源记录在前端README，不改D0005生成类型。
- 2026-10-30 07:30 UTC · codex · status in_progress → done：全部前端验收标准完成。Node22 npm ci 后 lint/typecheck/test（81/81）/build/check:api 全部通过；Chromium真实鼠标拖拽+缩放、带版本全布局保存后刷新、移动单列暗色与Ctrl/Cmd+K通过，已提交可复现mock脚本及截图。独立审查2项P2已按RED→GREEN修复；真实联调和CI浏览器E2E交Claude T0017。仅开PR，不合并。
- 2026-10-30 07:32 UTC · codex · 最终补测并修复：全局刷新失败时，从未可用的模块仍保持 unavailable，不伪称有旧数据。最终 Node22 lint/typecheck/test（82/82）/build/check:api、Chromium真实拖拽/缩放与刷新保留全部通过。生成schema无diff，截图与mock浏览器脚本随PR提交。
