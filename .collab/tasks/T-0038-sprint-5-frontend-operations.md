---
id: T-0038
title: Sprint 5 前端（一）：运维页（服务状态、预设指标、Grafana 外链）、首页 ServiceStatus 组件（S5-01、S5-02 前端）
owner: codex
status: todo
priority: P1
branch:
paths: apps/web,.github/workflows/ci-web.yml
depends_on: T-0034
created: 2026-10-31
updated: 2026-10-31
---

# T-0038 Sprint 5 前端（一）：运维页（服务状态、预设指标、Grafana 外链）、首页 ServiceStatus 组件（S5-01、S5-02 前端）

## 目标
实现运维相关前端：运维页（服务状态、预设指标、Grafana 外链）与首页 ServiceStatus 组件。只做 `apps/web`；后端由 claude 的 T-0035 实现。

## 验收标准
- [ ] `/operations` 页：服务状态列表（正常 / 降级 / 不可用 + 原因与最近检查时间）；预设指标视图（时间范围切换，图表或表格）；Grafana 外链（只在配置了时显示，安全外链）
- [ ] 首页 ServiceStatus 组件接真实数据，正确展示 loading / empty / error / stale / unauthorized（规范 8.3）
- [ ] 失败明确显示 stale 与最近一次成功的时间，不把空数据显示成正常（Sprint 5 DoD）
- [ ] 不在浏览器里拼接 PromQL、不直连 Prometheus / Grafana API；viewer 只读；移动端单列可用；亮暗主题
- [ ] Vitest 覆盖状态与错误分支；`npm run lint / typecheck / test / build` 与 `npm run check:api` 通过并把输出写进 PR；不修改 `src/services/schema.d.ts`（D-0005）

## 背景 / 约束 / 参考
- 依赖：T-0034（契约）合并后开始；开发时可用契约模拟 BFF，真实联调在 T-0040
- Docker 环境不与 claude 共用（D-0006）
- 规范：第 7.4 节、第 8.2 / 8.3 节；S5-01、S5-02

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: codex）
