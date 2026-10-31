---
id: T-0053
title: OC-1 V2 接口契约：实例、密钥、IM、审批、/agent-api、MCP 工具、内部回调（S6-01 契约）
owner: claude
status: todo
priority: P1
branch:
paths: docs/decisions,docs/api-contract.md,services/api/app/schemas,services/api/app/api,services/api/openapi.json,apps/web/src/services/schema.d.ts,services/api/tests/test_contract.py
depends_on: T-0052
created: 2026-10-31
updated: 2026-10-31
---

# T-0053 OC-1 V2 接口契约：实例、密钥、IM、审批、/agent-api、MCP 工具、内部回调（S6-01 契约）

## 目标
给 OC-2 ~ OC-5 定接口契约（方案第 4、5 节），前后端和测试按契约并行。

## 验收标准
- [ ] 契约 ADR：管理员 API（实例、密钥、IM、审批）、`/agent-api`、MCP 工具的参数和返回、内部回调，以及错误码
- [ ] Pydantic schema 与接口桩、`openapi.json`、`apps/web/src/services/schema.d.ts`、`docs/api-contract.md` 已更新
- [ ] 契约测试通过；`make lint`、`make test` 通过

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
