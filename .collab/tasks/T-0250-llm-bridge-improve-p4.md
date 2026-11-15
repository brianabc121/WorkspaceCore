---
id: T-0250
title: 大模型客户端改进第 4 期：智能体调用（结束后唤醒、一步发送、结果精简、错误分类、读产出文件）（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/app/domain,services/api/app/models/im.py,services/api/app/schemas,services/api/migrations,services/api/tests,services/api/openapi.json,apps/web/src/features/agents/WakeupPanel.vue,apps/web/src/services/schema.d.ts,docs
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0250 大模型客户端改进第 4 期：智能体调用（结束后唤醒、一步发送、结果精简、错误分类、读产出文件）（V2）

## 目标
落实 `docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md` 4.1：智能体调用这条链路的 A1 ~ A6。

## 验收标准
- [x] A1 发送时可选“结束后叫我”：回合结束后经现有的唤醒机制叫醒实例，提示词不含模型的内容；有迁移
- [x] A2 结果默认精简：回答截断并能接着取；过程要了才给；读会话默认最新一页并带总数
- [x] A3 错误带 retryable / retry_after_seconds；没登录、账号不对、结果未知各有错误码
- [x] A4（部分）按编号读回合产出的文字文件；放进运行环境 / 知识库、发送时带文件没有做
- [x] A5 发送时不给会话就先新建一个
- [x] A6 状态查询只给用得上的
- [x] 管理器、后端的 lint / typecheck / test 通过；`check:api` 无差异

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 20:46 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 20:46 UTC · human · owner unassigned → claude：人类在对话中指派：根据改进方案全面落实代码（第 4 期）
- 2026-11-15 20:46 UTC · claude · 开始任务
- 2026-11-15 20:46 UTC · claude · 越界说明：ToolCallError 和 MCP 的 error_result 加了可选的 details（app/domain/ai_tools.py、mcp_tools.py），别的工具不受影响；唤醒来源加 llm_bridge（app/models/im.py、两处 schema、WakeupPanel.vue、迁移 0073）。新增只读工具 llm_bridge_file_get，docs/AGENTS.md 例外九的读工具名单里没有它，没有改 AGENTS.md，留给人类定。
- 2026-11-15 20:46 UTC · claude · status in_progress → done：第 4 期完成（A4 只做了读文字文件）
