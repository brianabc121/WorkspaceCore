---
id: T-0022
title: Sprint 3 前端：Vikunja 连接设置、任务列表 / 看板、任务抽屉新建 / 编辑、幂等与未知状态提示、首页任务组件接真实数据（S3-02 ~ S3-04 前端）
owner: codex
status: todo
priority: P1
branch:
paths: apps/web,.github/workflows/ci-web.yml
depends_on: T-0019
created: 2026-10-30
updated: 2026-10-30
---

# T-0022 Sprint 3 前端：Vikunja 连接设置、任务列表 / 看板、任务抽屉新建 / 编辑、幂等与未知状态提示、首页任务组件接真实数据（S3-02 ~ S3-04 前端）

## 目标
按 T-0019 的契约实现 Sprint 3 前端：连接 Vikunja 的设置界面、任务页（列表 / 看板、筛选、新建 / 编辑抽屉），
写操作的幂等与“结果未知”提示，以及首页任务组件接入真实数据。只做 `apps/web`；后端由 claude 的 T-0020 / T-0021 实现。

## 验收标准
- [ ] S3-02：设置页的集成配置（admin 可见；base_url、Token 只写不回显；测试连接、手动同步；显示连接状态与 `tested_version`）；非 admin 只读或隐藏，后端同样会拒绝
- [ ] S3-03：任务页列表与看板两种视图；按状态、项目、截止日期、优先级筛选；新增 / 编辑任务抽屉；可跳到 Vikunja 原生界面做高级操作；任务的外部来源清楚标识（规范 8.4 节原生任务中心）
- [ ] S3-04：每次写操作生成 `Idempotency-Key`，网络重试复用同一个键；结果未知时明确提示并提供“查询结果 / 刷新确认”，不重复提交；409 等错误给出可理解的提示；防重复点击
- [ ] 首页 `todo_summary`、`today_tasks` 显示真实数据，并正确展示 ok / stale / unavailable / unauthorized（沿用 Sprint 2 的组件状态）
- [ ] 顺带修复 Sprint 2 遗留：添加组件时新组件的行号限制在 999 以内（PR #23 审查意见，`docs/acceptance/sprint-2.md` 第 6 节）
- [ ] Vitest 覆盖幂等键复用、未知状态、409、权限与工作空间隔离；`npm run lint / typecheck / test / build` 与 `npm run check:api` 通过并把输出写进 PR；不修改 `src/services/schema.d.ts`（D-0005）

## 背景 / 约束 / 参考
- 依赖：T-0019（契约）合并后开始；开发时可用 mock，真实联调在 T-0023
- 接口契约：T-0019 产出的 ADR 与 `services/api/openapi.json`；字段与允许值以 T-0018 的版本核验 ADR 为准
- 规范：第 8.3 节（页面状态）、8.4 节（原生任务中心）、第 4.1 节（创建任务路径）、第 6.3 节
- 不要改 `apps/web` 以外的文件（`ci-web.yml` 除外）；不要勾选 `docs/TASKS.md`；需要改契约时在任务日志写明，由 claude 修改（D-0005）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: codex）
