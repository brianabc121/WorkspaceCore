---
id: T-0057
title: OC-5 MCP 工具与审批：读写高危工具、审批流程（参数哈希、过期、去重、If-Match、二次认证）、审批卡片、A14（S6-02、S6-03）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0054
created: 2026-10-31
updated: 2026-11-01
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
- 2026-11-01 07:25 UTC · claude · 开始任务
- 2026-11-01 08:03 UTC · claude · 第 1 批（PR 待开）：迁移 0017 ai_tool_executions；审批流程（参数规范化与 HMAC 哈希去重、每实例最多 20 条、15 分钟过期 + worker 每分钟清理、If-Match、高危操作密码二次认证并按用户限流、按存下的参数以登记人身份执行、记录 ID 作幂等键、版本保护、结果未知不重试）；敏感工作流参数加密保存、处理完清空完整参数；/api/v1/ai/tool-executions 列表/详情/批准/拒绝；IM 角标待审批数；A14 测试。决定写在 docs/decisions/2026-11-01-v2-oc5-tool-approvals.md（越出 paths 只为写 ADR 和 docs/api-contract.md 状态列）。下一步：第 2 批 POST /mcp（MCP SDK、实例凭证、读工具、限流、get_action_status），第 3 批审批卡片。
- 2026-11-01 09:09 UTC · claude · 第 1 批已合并（PR #94）。第 2 批（PR 待开）：POST /mcp（MCP SDK 2.3.0 底层 Server + 无状态 JSON 会话管理器，只接受 POST）、实例认证与 /agent-api 相同、按实例每分钟 120 次调用、写/高危工具接审批、get_action_status（读记录）；官方 MCP 客户端互通测试。新依赖 mcp==2.3.0（MIT），pip-audit 无漏洞。下一步：其余 14 个读工具（untrusted + freshness），然后第 3 批审批卡片。
- 2026-11-01 09:29 UTC · claude · 第 2 批前半已合并（PR #96）。第 2 批后半（PR 待开）：其余 14 个读工具接上领域服务（第三方/用户文字进 untrusted，外部数据带 freshness，未配置集成返回 not_configured）；搜索与收藏列表改为按用户 ID 过滤私有收藏（页面与 MCP 共用）；dashboard_overview 直接计算概况数字。下一步：第 3 批审批卡片（ws_notice + approval_requested 通知）与 source 推断。
