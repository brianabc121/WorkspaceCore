---
id: T-0259
title: 大模型客户端：带着摘要另开会话、手动压缩、自动压缩触发点（压缩与知识库方案第 2 期）（V2）
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/app/domain/audit_labels.py,services/api/tests/test_module_llm_bridge_memory.py,services/api/tests/test_contract.py,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,docs/AGENTS.md,docs/api-contract.md,docs/decisions/2026-11-16-llm-bridge-context-verification.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0259 大模型客户端：带着摘要另开会话、手动压缩、自动压缩触发点（压缩与知识库方案第 2 期）（V2）

## 目标
`docs/v2/LLM_BRIDGE_MEMORY_PLAN.md` 第 2 期（方案 4.2“再让它管得了”）：三个从便宜到贵的动作——带着摘要另开会话、手动压缩、调早自动压缩。
方案 2026-11-16 的补充：带着摘要另开会话复用“清空上下文”（T-0255）的换会话和分隔线，不另做一套。

## 验收标准
- [x] 带着摘要另开会话：先在老的上下文里要一份交接摘要，轮到第二个回合时清空上下文（换新的官方会话、画分隔线），把摘要作为新上下文的第一条发出；
      老的上下文没写出摘要时用管理器的记录排一份；会话还是同一个；幂等；聊天页按钮和智能体 `llm_bridge_send` 的 `continue_from`；同时整理一次会话纪要
- [x] 手动压缩：管理器的 `llm_session_compact`（Claude / CodeBuddy 发 `/compact [要保留什么]`，Codex 调 `thread/compact/start`），结果写明平台有没有真的压缩，没有就建议另开会话；
      聊天页“压缩一下”；智能体工具 `llm_bridge_session_compact`（direct，记执行记录与审计）
- [x] 自动压缩的触发点：管理器设置三项（Claude / CodeBuddy 百分比，Codex token 数），默认 0 不改；分别用核验过的环境变量和 `config.model_auto_compact_token_limit` 带给官方程序；调早后提醒的位置跟着提前
- [x] `docs/AGENTS.md` 例外九补上 `llm_bridge_session_compact`、`continue_from`、`knowledge_notes` 与读工具 `llm_bridge_file_get` / `llm_bridge_queue`（人类在方案第 9 节同意）
- [x] 管理器、工作台后端、前端测试覆盖以上各项

## 背景 / 约束 / 参考
- 方案 4.2、第 8、9 节；核验记录 `docs/decisions/2026-11-16-llm-bridge-context-verification.md` 第 4 节。
- CodeBuddy 非交互模式下的手动压缩没核实：结果以有没有收到压缩事件为准，没压成就照实说。
- 平台压缩时写的摘要（第 4 期的钩子拿到之后）可以直接当交接摘要，不再花钱问；那部分随第 4 期。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 07:50 UTC · claude · 创建任务（owner: claude）
- 2026-11-16 07:50 UTC · claude · 开始任务
- 2026-11-16 07:53 UTC · claude · 第 2 期完成（本地）。按方案 2026-11-16 的补充，带着摘要另开会话复用清空上下文：同一个会话里排“要摘要”“清空并发出摘要”两个回合，分隔线画在两者之间。本地：管理器 87 passed；工作台 ruff / mypy / openapi 通过，test_module_llm_bridge_memory（9）、test_module_llm_bridge*、test_mcp_api、test_contract 通过；前端 lint / typecheck / vitest 889 passed。等 PR #225（第 1、3 期）合并后推送开新 PR。
- 2026-11-16 07:53 UTC · claude · status in_progress → done：第 2 期完成，验收标准全部勾选
