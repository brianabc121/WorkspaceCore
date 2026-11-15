---
id: T-0242
title: 大模型客户端：显示名改为 CodeBuddy；聊天页显示额度与重置时间（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,tests/e2e,docs
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0242 大模型客户端：显示名改为 CodeBuddy；聊天页显示额度与重置时间（V2）

## 目标
人类用真实账号登录后反馈两点：

1. 登录的是 CodeBuddy，页面上还叫 WorkBuddy。把页面、工具说明、文档里的显示名统一成 CodeBuddy（平台代号 `workbuddy` 不变，接口和已有数据不受影响）。
2. 聊天页看不到额度和什么时候重置。在聊天页的“会话信息”里显示当前平台的额度：每个窗口用了多少、重置时间、暂停到什么时候、最近 5 小时经管理器用掉的 token。
   CodeBuddy 的官方程序（锁定版本 2.184.0）在回合输出里不带额度，也没有查额度的接口——页面照实说明，不显示猜的数字，也不再写“跑过一个回合后会显示”。

## 验收标准
- [x] 页面、工具说明、接口说明、部署文档里的显示名是 CodeBuddy；平台代号 `workbuddy` 不变
- [x] 聊天页“会话信息”显示当前平台的额度窗口、重置时间、暂停说明和最近 5 小时用量；回合结束后跟着更新
- [x] 管理器的额度接口多一个 `reports_quota`；CodeBuddy 为 false 时，管理页和聊天页都说明官方程序不提供额度
- [x] `docs/v2/LLM_BRIDGE_QUOTA_PLAN.md` 更正 CodeBuddy 一行
- [x] 管理器、后端、前端的 lint / typecheck / test / build / check:api 通过；浏览器验收脚本补上聊天页额度一步（由 CI 跑）

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 13:47 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 13:47 UTC · human · owner unassigned → claude：人类在对话中指派：登录的是 CodeBuddy，页面还显示 WorkBuddy；聊天页看不到 token 额度和重置时间
- 2026-11-15 13:47 UTC · claude · 开始任务
- 2026-11-15 13:47 UTC · claude · 核对锁定版本 CodeBuddy 2.184.0：-p --output-format stream-json 的输出里没有 rate_limit_event，result 只有 usage；SessionRateLimit 只在程序内部扩展钩子里能读，本机服务没有查额度的接口。所以加 reports_quota=false，页面照实说明。越界说明：改了 docs/v2/LLM_BRIDGE_QUOTA_PLAN.md（更正事实），没有改 docs/AGENTS.md。
- 2026-11-15 13:47 UTC · claude · status in_progress → done：显示名改为 CodeBuddy；聊天页会话信息显示额度；CodeBuddy 不给额度照实说明
