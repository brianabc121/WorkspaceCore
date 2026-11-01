---
id: T-0057
title: OC-5 MCP 工具与审批：读写高危工具、审批流程（参数哈希、过期、去重、If-Match、二次认证）、审批卡片、A14（S6-02、S6-03）
owner: claude
status: done
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
- [x] `POST /mcp`（Streamable HTTP），实例凭证加现有的 Policy 层；读、写、高危工具按方案清单实现
- [x] 迁移：`ai_tool_executions`；审批流程：参数哈希、15 分钟过期、去重、`If-Match`、高危二次认证；按存下的参数执行，幂等
- [x] 审批卡片经 `ws_notice` 发到 IM；`get_action_status`（卡片改由各工作空间的通知账号 `wn_` 发，见 OC-5 ADR 第 11 节）
- [x] A14：未经审批的删除只返回 pending_approval，不执行并留下记录，有测试

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
- 2026-11-01 09:49 UTC · claude · 第 2 批后半已合并（PR #97）。第 3 批前半（PR 待开）：approval_requested / approval_expired 通知发给 owner/admin，链接 /approvals?id=；修正 IM 通知链接 /im?c=。核实了锁定版本 OpenIM：只有通知账号（AppMangerLevel≥3）能发 1400 OA 通知（sn_ 会话），add_notification_account 遇到已存在 ID 报错——ws_notice 需改为通知账号注册，留给第 3 批后半在真实 OpenIM 上验证后实现。
- 2026-11-01 10:24 UTC · claude · 第 3 批前半已合并（PR #98）。第 3 批后半（PR 待开）：IM 审批卡片——每个工作空间一个通知账号 wn_<工作空间>（偏离契约的 ws_notice，避免同一人跨工作空间的卡片混进一个 sn_ 会话，已同步改 OC-1 契约）；worker 作业 send_approval_cards 发 OA 通知（1400），Redis 去重；投影识别 sn_ 会话与 approval_card（只认 wn_ 发的、ex 带审批 ID 的）；发送后回调 sessionType 4；通知会话不能发消息。真实 OpenIM 实例上 live 测试通过。顺带 OpenIMClient repr 不含 secret。下一步：source 推断；前端审批中心与卡片按钮（T-0056）。
- 2026-11-01 10:43 UTC · claude · status in_progress → done：全部验收标准完成：#94 记录与审批、#96 /mcp、#97 读工具、#98 审批通知、#99 IM 审批卡片、本 PR source 推断。前端审批中心与卡片按钮在 T-0056；真实 OpenClaw 的端到端核对（含 MCP 是否带会话标识）在 T-0059。
