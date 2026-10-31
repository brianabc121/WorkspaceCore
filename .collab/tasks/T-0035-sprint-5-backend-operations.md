---
id: T-0035
title: Sprint 5 后端（一）：服务状态与健康告警、预设 Prometheus 指标视图（白名单查询）、可选监控 Compose 与 7 天保留（S5-01、S5-02、S5-05）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app,services/api/migrations,services/api/tests,infra/monitoring
depends_on: T-0034
created: 2026-10-31
updated: 2026-10-31
---

# T-0035 Sprint 5 后端（一）：服务状态与健康告警、预设 Prometheus 指标视图（白名单查询）、可选监控 Compose 与 7 天保留（S5-01、S5-02、S5-05）

## 目标
实现运维视图的后端：服务状态与降级、预设 Prometheus 指标（只允许白名单查询），以及可选的监控 Compose，满足 Sprint 5 DoD“不向浏览器暴露 Prometheus 管理端 / Secret；失败明确显示 stale”。

## 验收标准
- [ ] 服务状态：汇总 api、worker、PostgreSQL、Redis 与已配置的第三方（Vikunja、GitHub、n8n、Prometheus）的健康；第三方故障时首页与运维页显示 stale / unavailable，其余模块照常可用（A05 的延续）
- [ ] 预设指标：Prometheus Connector 只发送 T-0033 记录的固定查询（按视图 ID 选择），带 timeout、错误归类、重试上限；Prometheus 不可达时返回 stale 并带最近一次结果的时间，不伪造 0
- [ ] 工作台自身暴露指标（请求次数、延迟分位、5xx），`/metrics` 只在内部网络，Caddy 不对外转发；指标标签不含 Token、用户输入的路径参数等敏感值（S5-05）
- [ ] 可选 `infra/monitoring/` Compose（版本锁定），Prometheus 保留 7 天；不对外发布管理端口；部署文档写明启用方式
- [ ] 数据表变化提供 Alembic 迁移与测试；ruff / mypy / pytest 通过

## 背景 / 约束 / 参考
- 依赖：T-0034 合并后开始
- 规范：第 7.4 节、第 8.3 节、第 11 节；Sprint 5 DoD；S5-01、S5-02、S5-05

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: claude）
