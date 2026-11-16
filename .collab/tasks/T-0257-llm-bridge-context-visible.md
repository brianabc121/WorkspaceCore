---
id: T-0257
title: 大模型客户端压缩与知识库第 1 期：上下文用量与压缩事件看得见（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,docs
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0257 大模型客户端压缩与知识库第 1 期：上下文用量与压缩事件看得见（V2）

## 目标
落实 `docs/v2/LLM_BRIDGE_MEMORY_PLAN.md` 4.1（第 1 期）：平台压缩上下文和上下文用了多少，现在管理器都丢掉了，人和智能体看不见。这一期只让它看得见，不改行为，不多花 token。

## 验收标准
- [x] 管理器记下上下文用量、窗口和每一次压缩（三家驱动）
- [x] 回合结果和会话信息里有 `context`；会话内容里压缩的位置有分隔
- [x] 聊天页：会话信息里的一行、分隔线、快用满时的提示；导出里有分隔
- [x] 管理器、工作台、前端各有测试

## 背景 / 约束 / 参考
- 没有用真实账号验证，见方案第 11 节。
- “压缩一下”“带着摘要另开会话”是第 2 期。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 07:56 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-16 07:56 UTC · human · owner unassigned → claude：人类在对话中指派：按 LLM_BRIDGE_MEMORY_PLAN.md 实施，第 1 期先做
- 2026-11-16 07:56 UTC · claude · 开始任务
- 2026-11-16 08:10 UTC · claude · 第 1 期做完：管理器 pytest 77 passed 3 skipped；后端 llm_bridge、契约、模块测试 100 passed；前端 llm_chat 41 passed，lint、typecheck、build 通过。没有用真实账号验证。
- 2026-11-16 08:10 UTC · claude · status in_progress → done：第 1 期代码完成；真实账号验证留给人类
- 2026-11-16 08:25 UTC · claude · CI 的 workspace（浏览器验收）在“清空上下文”一步失败：清空后发的消息没有发出去。根因是 #223 留下的时序问题——清空还在重新读会话时按回车被悄悄丢掉；改成等清空做完再发，并加了一个没有修复就失败的前端测试（llm_chat 42 passed）。失败的那一项已重跑过一次。
