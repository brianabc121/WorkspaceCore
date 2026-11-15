---
id: T-0251
title: 大模型客户端改进第 5 期上：会话记录页（跨管理器列表、筛选、全文搜索）、出处可点、用量（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,docs
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0251 大模型客户端改进第 5 期上：会话记录页（跨管理器列表、筛选、全文搜索）、出处可点、用量（V2）

## 目标
落实 `docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md` 4.2 的 B1、B4、B5：管理员能跨管理器找到会话、知道是谁因为什么发起的、看到用量。B2、B3、B6、B7、B8 在第 5 期下。

## 验收标准
- [x] B1 “会话记录”视图：跨管理器的列表，按发起人、管理器、平台、用法、状态、时间、有没有失败筛选，翻页；在提问和回答里搜索
- [x] B4 列表和会话信息里标出智能体当时在做的任务并能点过去；链接可以定位到某一回合；接手发送的通知链接到那一回合
- [x] B5 用量：按人和智能体汇总，可展开看每天；说明 token 不是账单
- [x] 管理器、后端、前端的 lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 21:04 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 21:04 UTC · human · owner unassigned → claude：人类在对话中指派：根据改进方案全面落实代码（第 5 期上）
- 2026-11-15 21:04 UTC · claude · 开始任务
- 2026-11-15 21:04 UTC · claude · status in_progress → done：第 5 期上完成
