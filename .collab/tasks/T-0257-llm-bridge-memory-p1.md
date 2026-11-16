---
id: T-0257
title: 大模型客户端：上下文用量与压缩看得见（压缩与知识库方案第 1 期）（V2）
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests/test_module_llm_bridge_chat.py,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests/llm_chat.test.ts,docs/decisions/2026-11-16-llm-bridge-context-verification.md,docs/api-contract.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0257 大模型客户端：上下文用量与压缩看得见（压缩与知识库方案第 1 期）（V2）

## 目标
人类要求按 `docs/v2/LLM_BRIDGE_MEMORY_PLAN.md`（T-0255 写的方案，PR #224）全面落实代码。这是第 1 期（方案 4.1“先让它看得见”）：
三个官方程序都自带自动压缩，但管理器把压缩事件丢掉了、上下文用了多少不知道。这一期只记录和显示，不改任何行为、不多花 token。

## 验收标准
- [x] 管理器按锁定版本核对过的字段记下每个会话的上下文用量、窗口、压缩次数、最近一次压缩的时间和触发方式（Claude 的 compact_boundary 与 modelUsage、CodeBuddy 的状态事件与 `_meta`、Codex 的 tokenUsage 与 contextCompaction）
- [x] 核验结果写进 `docs/decisions/2026-11-16-llm-bridge-context-verification.md`，标明没核实的
- [x] 智能体的工具结果（llm_send / llm_turn_get / 会话信息）里有 `context`：已用比例、压缩次数、是否快到压缩的位置
- [x] 聊天页会话信息里显示“已用 62%（124k / 200k），压缩过 2 次”；快到压缩位置时输入框上方提示一行；压缩的位置有一条分隔线，过程里有一步“压缩”
- [x] 智能体读会话时压缩的位置有一条 `context_compacted`；导出的 Markdown 里也有
- [x] 管理器、工作台后端、前端的测试覆盖以上各项

## 背景 / 约束 / 参考
- 方案 4.1、第 8 节；核验记录 `docs/decisions/2026-11-16-llm-bridge-context-verification.md`。
- “压缩一下”“带着摘要另开会话”两个按钮属于第 2 期，这一期只显示提示。
- 方案文档本身在 PR #224（未合并），它的实施状态在后续任务里更新。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 06:29 UTC · claude · 创建任务（owner: claude）
- 2026-11-16 06:29 UTC · claude · 开始任务
- 2026-11-16 06:33 UTC · claude · 按人类要求落实 docs/v2/LLM_BRIDGE_MEMORY_PLAN.md（方案在 PR #224，未合并）。第 1 期完成：在锁定版本上核对三家的上下文与压缩字段（核验记录见 docs/decisions/2026-11-16-llm-bridge-context-verification.md），管理器记录并显示上下文用量与压缩。paths 与 T-0224（codex，已暂停，D-0007）重叠：沿用 T-0247 起各任务的做法，只改 llm_bridge 模块自己的文件。本地：管理器 ruff/mypy/pytest 73 passed；工作台 ruff/mypy/openapi 检查通过，test_module_llm_bridge*.py 与 test_contract 67 passed；前端 lint/typecheck/vitest 878 passed。
- 2026-11-16 06:33 UTC · claude · status in_progress → done：第 1 期完成，验收标准全部勾选
- 2026-11-16 07:23 UTC · claude · 任务号从 T-0256 改成 T-0257：PR #224 的方案任务改号成了 T-0256，两边撞号；#224 只有文档、会先合并，按约定由后合并的一方（这里）改号。第 1 期的提交信息里写的是 T-0256，以本文件为准。
