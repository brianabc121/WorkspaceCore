---
id: T-0022
title: Sprint 3 前端：Vikunja 连接设置、任务列表 / 看板、任务抽屉新建 / 编辑、幂等与未知状态提示、首页任务组件接真实数据（S3-02 ~ S3-04 前端）
owner: codex
status: done
priority: P1
branch: codex/T-0022-sprint-3-frontend
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
- [x] S3-02：设置页的集成配置（admin 可见；base_url、Token 只写不回显；测试连接、手动同步；显示连接状态与 `tested_version`）；非 admin 只读或隐藏，后端同样会拒绝
- [x] S3-03：任务页列表与看板两种视图；按状态、项目、截止日期、优先级筛选；新增 / 编辑任务抽屉；可跳到 Vikunja 原生界面做高级操作；任务的外部来源清楚标识（规范 8.4 节原生任务中心）
- [x] S3-04：每次写操作生成 `Idempotency-Key`，网络重试复用同一个键；结果未知时明确提示并提供“查询结果 / 刷新确认”，不重复提交；409 等错误给出可理解的提示；防重复点击
- [x] 首页 `todo_summary`、`today_tasks` 显示真实数据，并正确展示 ok / stale / unavailable / unauthorized（沿用 Sprint 2 的组件状态）
- [x] 顺带修复 Sprint 2 遗留：添加组件时新组件的行号限制在 999 以内（PR #23 审查意见，`docs/acceptance/sprint-2.md` 第 6 节）
- [x] Vitest 覆盖幂等键复用、未知状态、409、权限与工作空间隔离；`npm run lint / typecheck / test / build` 与 `npm run check:api` 通过并把输出写进 PR；不修改 `src/services/schema.d.ts`（D-0005）

## 背景 / 约束 / 参考
- 依赖：T-0019（契约）合并后开始；开发时可用 mock，真实联调在 T-0023
- 接口契约：T-0019 产出的 ADR 与 `services/api/openapi.json`；字段与允许值以 T-0018 的版本核验 ADR 为准
- 规范：第 8.3 节（页面状态）、8.4 节（原生任务中心）、第 4.1 节（创建任务路径）、第 6.3 节
- 不要改 `apps/web` 以外的文件（`ci-web.yml` 除外）；不要勾选 `docs/TASKS.md`；需要改契约时在任务日志写明，由 claude 修改（D-0005）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: codex）
- 2026-10-30 15:57 UTC · codex · 开始任务
- 2026-10-30 16:00 UTC · codex · 从最新 origin/main eddd2b5 开始，契约T0019与后端T0020/T0021已done。计划：1 幂等操作状态/冻结原请求/同键查询与回归；2 任务列表看板/筛选分页/项目/详情编辑抽屉；3 admin集成设置（base_url与Token输入不回显）、连接测试/同步状态；4 首页真实任务数据与外部来源；5 承接PR28行号修复、Node22检查/浏览器mock/独立审查/PR。按现有Vue/Pinia/Query/Vee/Zod实现，不新增依赖，不改schema.d.ts；真实联调留T0023。
- 2026-10-30 16:12 UTC · codex · 幂等状态、任务列表/看板/筛选分页/详情与编辑、admin集成配置/连接测试/同步已实现。独立审查发现并已RED→GREEN修复：网关错误/空成功响应必须保留原键为unknown，冲突后任务404应释放已确定冲突并关闭旧编辑器。PR28仍未合并，按本任务验收要求复制相同行号修复与8项回归（不改T0024任务），合并后内容相同可自动收敛。正在浏览器验证和完善边界测试。
- 2026-10-30 16:17 UTC · codex · 浏览器API mock已实际走通：列表看板、504未知新建→原键/原体查询成功、版本编辑、CSRF/空间头、390px无横溢、服务地址/Token不回显、HTTP200但连接检查失败的展示、手动同步。新增24小时幂等保留窗口回归：超窗不再重放，提示先核对列表；补测分页游标重置与迟到响应的空间隔离。当前完整107项测试通过，正在最终验证和PR整理。
- 2026-10-30 16:22 UTC · codex · status in_progress → done：全部前端验收完成。Node22 npm ci后lint/typecheck/test（108/108）/build/check:api通过，Chromium mock关键路径通过，截图和复现脚本随PR。独立审查P1网关写结果不确定、P2冲突后404永久锁均已RED→GREEN修复；另覆盖24h窗口/空间隔离/凭据不回显。包含PR28相同行号修复；schema未改，无新依赖。真实联调留T0023；Codex只开PR不合并。
