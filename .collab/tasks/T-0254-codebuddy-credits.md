---
id: T-0254
title: 大模型客户端：CodeBuddy 每回合消耗的积分与本回合 token（官方回合用量事件）（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,docs
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0254 大模型客户端：CodeBuddy 每回合消耗的积分与本回合 token（官方回合用量事件）（V2）

## 目标
人类要求在 MCP 工具里看到 CodeBuddy 的积分，并提出写一个扩展钩子把内部数据导出来。
先在锁定版本（2.184.0）上核对官方程序到底能给什么，再按核对到的做。

## 验收标准
- [x] 核对扩展钩子能不能拿到额度或积分，结论写进 `docs/v2/LLM_BRIDGE_QUOTA_PLAN.md`
- [x] 回合用量里带官方程序报的“这一回合消耗的积分”；没报不写成 0
- [x] CodeBuddy 回合的 token 用这一回合自己的，不用会话累计的
- [x] 智能体的工具结果（回合的 `usage`、`llm_bridge_status` 的 `quota`）里有积分
- [x] 聊天页回答下面、额度面板、用量汇总里显示消耗的积分

## 背景 / 约束 / 参考
- 官方文档：程序包里的 `dist/web-ui/docs/en/cli/stream-json-turn-usage.md`。
- 钩子拿不到：运行时 `rateLimits` 写死为空，没有 `percentUsed`；所以没有写钩子，这一点与人类最初的提法不同。
- 剩余积分仍然拿不到。
- 遗留：真实账号上跑一个回合确认 2.184.0 实际带 `turnUsage`、积分的数对（要人类在自己的服务器上看）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 04:09 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-16 04:09 UTC · human · owner unassigned → claude：人类在对话中指派：在 MCP 工具里显示 CodeBuddy 的积分（先核验官方程序能给什么）
- 2026-11-16 04:09 UTC · claude · 开始任务
- 2026-11-16 04:18 UTC · claude · 核对结果：扩展钩子拿不到额度（运行时 rateLimits 写死为空），没有写钩子；改为读官方回合用量（_meta 的 turnUsage）：这一回合的 token 和消耗的积分。管理器、工作台接口、工具说明、页面都已改；管理器 pytest 68 passed，后端相关 98 passed，前端两个测试文件 57 passed。剩余积分仍拿不到；没有用真实账号验证。
- 2026-11-16 04:18 UTC · claude · status in_progress → done：代码完成；真实账号验证留给人类
