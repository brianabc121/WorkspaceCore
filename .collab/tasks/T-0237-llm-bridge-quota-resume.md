---
id: T-0237
title: MCP 管理器优化：查看各平台额度与重置时间，额度用完中断后自动续跑
owner: claude
status: in_progress
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/tests,apps/web/src/services/schema.d.ts,tests/e2e,docs
depends_on: T-0236
created: 2026-11-14
updated: 2026-11-14
---

# T-0237 MCP 管理器优化：查看各平台额度与重置时间，额度用完中断后自动续跑

## 目标
人类 2026-11-14 要求（方案 `docs/v2/LLM_BRIDGE_QUOTA_PLAN.md`，先写方案再落实）：

1. 会话任务因为平台的额度（例如 5 小时额度）用完被临时打断时，MCP 管理器在额度重置后自动发“继续意外中断的任务”，把任务接着跑起来。
2. 在工作台上能看到每个大模型平台的额度用了多少、什么时候重置。

## 验收标准
- [x] 管理器记录各平台的额度窗口（已用百分比、重置时间、状态）；Codex 可以随时查，Claude / WorkBuddy 从回合输出里读
- [x] 额度用完的回合记成 `interrupted`（不算失败），平台暂停到重置时间，期间新消息排队不去撞限额
- [x] 到重置时间后自动往每个被打断的会话发一句话（默认“继续意外中断的任务”），排在会话队列最前面；有次数上限；管理器重启后继续
- [x] 设置里能开关、改次数、改那句话；聊天页能“现在就试”“不要自动继续”
- [x] 管理页显示每个平台的额度与重置时间、暂停到什么时候、有几个会话在等
- [x] 管理器、工作台接口、前端的测试与浏览器验收通过
- [ ] 真实账号验收（Claude / WorkBuddy 额度事件的真实字段、三家“额度用完”的真实表现）——本环境没有账号，留给人类或后续任务

## 背景 / 约束 / 参考
- 平台只给已用百分比和重置时间，不给剩余 token 数；页面如实显示百分比（剩余 = 100 − 已用）。
- 依赖 T-0236 第一步（按平台限流、消息队列，PR #212）。
- 不使用本云端会话自己的 Claude 登录做测试；用仿真驱动和仿真命令行。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-14 14:29 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-14 15:48 UTC · human · owner unassigned → claude：人类在对话中指派：MCP 管理器查看平台额度与重置时间；额度用完中断后到重置时间自动发“继续意外中断的任务”（先写方案再落实）
- 2026-11-14 15:48 UTC · claude · 开始任务
- 2026-11-14 15:48 UTC · claude · 管理器、工作台接口、前端都已实现并通过测试；浏览器验收（llm_chat_browser.cjs，仿真命令行）通过，并抓到修掉一处：额度事件的 utilization 是 0–1 的比例。真实账号验收没做（没有账号）。下一步：PR #212 合并后把本分支 rebase 到 main、开 PR、等 CI。
