---
id: T-0034
title: Sprint 5 接口契约：服务状态、预设指标、通知中心、SSE 事件流、自动化白名单触发与运行记录、首页模块（S5 契约）
owner: claude
status: todo
priority: P1
branch:
paths: docs/decisions,docs/api-contract.md,services/api/app/schemas,services/api/app/api,services/api/openapi.json,apps/web/src/services/schema.d.ts,services/api/tests/test_contract.py
depends_on: T-0033
created: 2026-10-31
updated: 2026-10-31
---

# T-0034 Sprint 5 接口契约：服务状态、预设指标、通知中心、SSE 事件流、自动化白名单触发与运行记录、首页模块（S5 契约）

## 目标
在实现之前先确定 Sprint 5 的接口契约，让 claude 的后端（T-0035 ~ T-0037）和 codex 的前端（T-0038、T-0039）可以并行开发。
按 D-0003 契约先行；按 D-0005 同时提交重新生成的前端类型。

## 验收标准
- [ ] ADR `docs/decisions/` 下的 Sprint 5 契约——运维：`GET /api/v1/operations/services`（各服务状态、最近检查时间、降级原因）、`GET /api/v1/operations/metrics`（预设视图 ID 与时间范围，返回已聚合的数据点，不透传 PromQL）、Grafana 外链字段；viewer 可读（规范 6.1）
- [ ] 通知：`GET/PATCH /api/v1/notifications`（列表、未读数、批量已读）；字段按规范 5.1 第 14 项；与首页 `summary.unread_notifications` 一致；只看自己的通知
- [ ] SSE：`GET /api/v1/events/stream` 的事件格式（只发轻量失效通知，客户端收到后重新拉取，规范第 4 节）、`id` / `Last-Event-ID` 与重连语义、心跳、鉴权与工作空间范围；不把 SSE 当唯一可靠队列（规范第 10 节、A16）
- [ ] 自动化：`GET /api/v1/automations`（白名单工作流、最近执行）、`POST /api/v1/automations/{id}/trigger`（member + policy；参数 schema；幂等键；未知完成状态的处理，规范第 8 节项目规则 7、9）；执行记录只存错误摘要，不存第三方完整 payload（规范 7.5）
- [ ] 首页聚合新增 `operations`、`notifications`、`automations` 等模块状态，以及 ServiceStatus 等组件类型的配置 schema（规范 8.2）；已有接口只增不改
- [ ] `app/schemas/` 中全部请求 / 响应模型；`/api/v1` 下声明全部 Sprint 5 端点（未实现的返回 501，不写进 OpenAPI）；`make openapi` 提交 `openapi.json`；按 D-0005 提交 `schema.d.ts`；`docs/api-contract.md` 更新；契约测试覆盖新端点

## 背景 / 约束 / 参考
- 依赖：T-0033（版本核验 ADR）合并后开始
- 规范：第 4 节、第 5.1 节第 14 项、第 6.1 ~ 6.4 节、第 7.4 / 7.5 节、第 8.2 / 8.3 节、第 10 节；验收场景 A16
- 分工：D-0003、D-0005；本契约合并后 T-0035 ~ T-0039 才开始

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: claude）
