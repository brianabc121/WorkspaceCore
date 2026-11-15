---
id: T-0248
title: 大模型客户端改进第 2 期：底座（统一的回合状态、出处、会话索引）（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/app/domain/ai_tools.py,services/api/migrations,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts,docs
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0248 大模型客户端改进第 2 期：底座（统一的回合状态、出处、会话索引）（V2）

## 目标
落实 `docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md` 第 3 节的 3.1、3.3、3.4：三条链路共用的底座。3.2（事件流细化与推送通道）在第 3 期。

## 验收标准
- [x] 3.1 回合对外多 `timing`、`waiting`、`next_turn_id`、`failure.retryable` / `retry_after_seconds`、`usage`、`outputs`
- [x] 3.3 出处：工作台随调用带给管理器（智能体：实例和它没结束的任务执行；人：是谁），存在会话和回合上；执行记录里带会话和回合的编号
- [x] 3.4 工作台的会话索引表、每分钟的同步任务、读索引的接口；不含对话内容；有迁移
- [x] 管理器与后端的 lint / typecheck / test 通过；`check:api` 无差异

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 20:01 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 20:01 UTC · human · owner unassigned → claude：人类在对话中指派：根据改进方案全面落实代码（第 2 期）
- 2026-11-15 20:01 UTC · claude · 开始任务
- 2026-11-15 20:01 UTC · claude · 越界说明：改了 services/api/app/domain/ai_tools.py（record_knowledge 加一个可选的 display 参数，让执行记录能带上执行得到的编号），没有改别的工具的行为。迁移 0072 新增 llm_bridge_sessions 表和 llm_bridge_bridges.index_rev 列。
- 2026-11-15 20:01 UTC · claude · status in_progress → done：第 2 期底座完成
