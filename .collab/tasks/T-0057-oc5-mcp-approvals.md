---
id: T-0057
title: OC-5 MCP 工具与审批：读写高危工具、审批流程（参数哈希、过期、去重、If-Match、二次认证）、审批卡片、A14（S6-02、S6-03）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0054
created: 2026-10-31
updated: 2026-10-31
---

# T-0057 OC-5 MCP 工具与审批：读写高危工具、审批流程（参数哈希、过期、去重、If-Match、二次认证）、审批卡片、A14（S6-02、S6-03）

## 目标
实现 MCP 工具服务器与工具审批（方案第 4.6 节）。

## 验收标准
- [ ] `POST /mcp`（Streamable HTTP），实例凭证加现有的 Policy 层；读、写、高危工具按方案清单实现
- [ ] 迁移：`ai_tool_executions`；审批流程：参数哈希、15 分钟过期、去重、`If-Match`、高危二次认证；按存下的参数执行，幂等
- [ ] 审批卡片经 `ws_notice` 发到 IM；`get_action_status`
- [ ] A14：未经审批的删除只返回 pending_approval，不执行并留下记录，有测试

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
