---
id: T-0053
title: OC-1 V2 接口契约：实例、密钥、IM、审批、/agent-api、MCP 工具、内部回调（S6-01 契约）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/decisions,docs/api-contract.md,services/api/app/schemas,services/api/app/api,services/api/openapi.json,apps/web/src/services/schema.d.ts,services/api/tests/test_contract.py
depends_on: T-0052
created: 2026-10-31
updated: 2026-10-31
---

# T-0053 OC-1 V2 接口契约：实例、密钥、IM、审批、/agent-api、MCP 工具、内部回调（S6-01 契约）

## 目标
给 OC-2 ~ OC-5 定接口契约（方案第 4、5 节），前后端和测试按契约并行。

## 验收标准
- [x] 契约 ADR：管理员 API（实例、密钥、IM、审批）、`/agent-api`、MCP 工具的参数和返回、内部回调，以及错误码
- [x] Pydantic schema 与接口桩、`openapi.json`、`apps/web/src/services/schema.d.ts`、`docs/api-contract.md` 已更新
- [x] 契约测试通过；`make lint`、`make test` 通过

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 17:45 UTC · claude · 开始任务
- 2026-10-31 17:57 UTC · claude · 契约 ADR docs/decisions/2026-10-31-v2-oc1-contract.md；schema（agents/im/ai_tools/openim_callbacks）与 48 个管理员端点、3 个实例端点的 501 桩；网关与 OpenIM 回调路由不进 OpenAPI；MCP 工具清单 TOOLS（15 读 6 写 3 高危）。越出 paths 的文件（都不在其他进行中任务的范围内）：services/api/app/main.py（挂载实例接口路由）、apps/web/src/features/events/useWorkspaceEvents.ts（SSE 新 topic 的映射，否则前端 typecheck 失败）、infra/openclaw/bin/myworkspace-openclaw-start（im-token 响应改用 ws_addr/api_addr）。make lint-api、pytest（含集成，412 passed）、web lint/typecheck/test/build 通过
- 2026-10-31 17:57 UTC · claude · status in_progress → done：OC-1 契约完成：ADR、schema、接口桩、OpenAPI、schema.d.ts、api-contract.md、契约测试
