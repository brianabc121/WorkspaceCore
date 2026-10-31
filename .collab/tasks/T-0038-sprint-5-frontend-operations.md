---
id: T-0038
title: Sprint 5 前端（一）：运维页（服务状态、预设指标、Grafana 外链）、首页 ServiceStatus 组件（S5-01、S5-02 前端）
owner: codex
status: done
priority: P1
branch: codex/T-0038-sprint-5-operations
paths: apps/web,.github/workflows/ci-web.yml
depends_on: T-0034
created: 2026-10-31
updated: 2026-10-31
---

# T-0038 Sprint 5 前端（一）：运维页（服务状态、预设指标、Grafana 外链）、首页 ServiceStatus 组件（S5-01、S5-02 前端）

## 目标
实现运维相关前端：运维页（服务状态、预设指标、Grafana 外链）与首页 ServiceStatus 组件。只做 `apps/web`；后端由 claude 的 T-0035 实现。

## 验收标准
- [x] `/operations` 页：服务状态列表（正常 / 降级 / 不可用 + 原因与最近检查时间）；预设指标视图（时间范围切换，图表或表格）；Grafana 外链（只在配置了时显示，安全外链）
- [x] 首页 ServiceStatus 组件接真实数据，正确展示 loading / empty / error / stale / unauthorized（规范 8.3）
- [x] 失败明确显示 stale 与最近一次成功的时间，不把空数据显示成正常（Sprint 5 DoD）
- [x] 不在浏览器里拼接 PromQL、不直连 Prometheus / Grafana API；viewer 只读；移动端单列可用；亮暗主题
- [x] Vitest 覆盖状态与错误分支；`npm run lint / typecheck / test / build` 与 `npm run check:api` 通过并把输出写进 PR；不修改 `src/services/schema.d.ts`（D-0005）

## 背景 / 约束 / 参考
- 依赖：T-0034（契约）合并后开始；开发时可用契约模拟 BFF，真实联调在 T-0040
- Docker 环境不与 claude 共用（D-0006）
- 规范：第 7.4 节、第 8.2 / 8.3 节；S5-01、S5-02

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: codex）
- 2026-10-31 03:13 UTC · codex · 开始任务
- 2026-10-31 03:14 UTC · codex · 已核对最新 main 7baeb46、issue #50 C-4 与收件箱：T-0034/T-0035 已合并，无重复执行/待合并 PR。指定新分支启动，短计划 apps/web/docs/plans/T-0038.md。采用现有 Vue3、SVG 断点趋势与数据表，无新依赖；先测后写，仅 BFF，遵守 D-0006，不操作共享 Docker。
- 2026-10-31 03:34 UTC · codex · status in_progress → done：已完成运维页、service_health/api_latency 组件及共享权限拒绝隔离。Node22 npm ci；lint/typecheck/check:api/build 全部退出0；22文件224测试通过；26项运维专项通过；Chromium模拟BFF亮暗/移动/首页检查通过。一次独立审查 Important 已回归修复，Minor模板排版延后。真实部署联调归T-0040，SSE归T-0039；schema.d.ts未改。验证记录 apps/web/docs/verification/T-0038.md。待开PR由Claude审查合并，T-0039同路径等待。
