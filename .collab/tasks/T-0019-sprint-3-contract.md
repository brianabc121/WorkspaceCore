---
id: T-0019
title: Sprint 3 接口契约：Integration、项目 / 任务、Idempotency-Key 与未知状态、Webhook 端点（S3 契约）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app/schemas,services/api/app/api,services/api/openapi.json,services/api/tests/test_contract.py,apps/web/src/services/schema.d.ts,docs/api-contract.md,docs/decisions
depends_on: T-0018
created: 2026-10-30
updated: 2026-10-30
---

# T-0019 Sprint 3 接口契约：Integration、项目 / 任务、Idempotency-Key 与未知状态、Webhook 端点（S3 契约）

## 目标
在实现之前先确定 Sprint 3 的接口契约，让 claude 的后端（T-0020 / T-0021）和 codex 的前端（T-0022）可以并行开发。
按 D-0003 契约先行；按 D-0005 同时提交重新生成的前端类型。

## 验收标准
- [ ] ADR `docs/decisions/` 下的 Sprint 3 契约：Integration（列表 / 创建 / 修改 / 测试连接 / 手动同步，admin；凭证只写不回显，状态与 `tested_version`）；项目列表；任务列表（过滤状态、项目、截止日期、优先级，分页）、详情、创建、修改、删除（删除需确认）；统一 TaskDTO / ProjectDTO（依据 T-0018 的字段映射）
- [ ] Idempotency-Key 语义（规范 4.1、6.3）：写接口必填；同键同请求返回原结果；同键不同请求 409（A07）；处理中与“结果未知”的表达方式和后续查询方式，前端不能据此盲目重试创建
- [ ] 修改的并发语义（外部系统支持时用版本 / 时间戳，不支持时明确采用的策略）；错误码（AUTH_FAILED、RATE_LIMITED、TIMEOUT_UNKNOWN、UNAVAILABLE、CONFLICT 等如何映射到 HTTP）
- [ ] Webhook 端点 `POST /api/v1/webhooks/vikunja/{integration_id}` 的签名校验、大小限制与响应；首页聚合中 `tasks` 模块从 `not_configured` 到 ok / stale / unavailable / unauthorized 的切换规则
- [ ] `app/schemas/` 中全部请求 / 响应模型；`/api/v1` 下声明全部 Sprint 3 端点（未实现的返回 501，不写进 OpenAPI）；`make openapi` 提交 `openapi.json`；按 D-0005 提交 `schema.d.ts`；`docs/api-contract.md` 更新；契约测试覆盖新端点

## 背景 / 约束 / 参考
- 依赖：T-0018（版本核验 ADR）合并后开始
- 规范：第 4.1 节（创建任务路径）、第 5.1 节第 6、7、10、15、16、18 项、第 6.1 / 6.3 / 6.4 节、第 7 节；验收场景 A06 ~ A09
- 分工：D-0003、D-0005；本契约合并后 T-0020 / T-0022 才开始

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: claude）
