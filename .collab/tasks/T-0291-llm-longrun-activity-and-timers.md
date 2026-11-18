---
id: T-0291
title: 大模型客户端长时间任务（一）：回合进行中看得见、预约回来（第 1、2 期）
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: services/llm-bridge/bridge/activity.py,services/llm-bridge/bridge/errors.py,services/llm-bridge/bridge/app.py,services/llm-bridge/bridge/config.py,services/llm-bridge/bridge/engine.py,services/llm-bridge/bridge/store.py,services/llm-bridge/bridge/longrun.py,services/llm-bridge/bridge/tools.py,services/llm-bridge/bridge/mcp_server.py,services/llm-bridge/bridge/drivers,services/llm-bridge/tests,services/api/app/modules/llm_bridge,services/api/app/domain/audit_labels.py,services/api/openapi.json,services/api/tests/test_contract.py,services/api/tests/test_module_llm_bridge.py,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests/llm_bridge.test.ts,apps/web/tests/llm_chat.test.ts,docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md,docs/v2/LLM_BRIDGE_PLAN.md,docs/api-contract.md,docs/decisions/2026-11-13-llm-bridge-verification.md,docs/guides/mcp-manager-deploy.md,docs/AGENTS.md
depends_on:
created: 2026-11-18
updated: 2026-11-18
---

# T-0291 大模型客户端长时间任务（一）：回合进行中看得见、预约回来（第 1、2 期）

## 目标
人类 2026-11-18 在对话里问：开发会话遇到要等很久的事（例如开一个要跑很久的 CI），会话本身没有输出变化、后台在跑，工具能不能看到；有些任务要反复调用、隔几个小时回来看结果再决定下一轮。
方案见 `docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md`，人类决定见其第 10 节。本任务做第 1、2 期：

1. 回合进行中看得见：最后一次有动静的时间、正在跑的命令或工具、Codex 输出的末尾、官方程序报出的后台任务（`activity`）。
2. 开发环境可以单独设单回合时限。
3. 预约回来：到点给同一个会话发一条写明“不是人发的”的消息、开新的一回合；人、外层智能体、会话里的模型都能预约。

## 验收标准
- [x] 管理器：`activity` 出现在 `llm_turn_get`、页面的后台任务里；Codex 接 `item/started` / `outputDelta` / `item/completed`，Claude / CodeBuddy 接 `task_*` 事件；回合结束时还有没完成的后台任务，留一条提示
- [x] 开发环境 `turn_timeout_seconds`（30 秒到 6 小时，空 = 用管理器的设置）
- [x] 预约：`timers` 表、调度协程只睡到最近的到点时间、停机期间到点的补发一次并写明晚了多久；最早 1 分钟、最远和个数有上限；归档时取消
- [x] 自动回合：发起方 `system`，消息开头固定标记；多条合并进同一个排着的自动回合
- [x] 工作台：读工具 `llm_bridge_tasks`，直接执行 `llm_bridge_timer_create` / `llm_bridge_timer_cancel`（例外十），审计、密钥扫描；`notify` 时预约引出的自动回合结束后叫醒实例；页面接口与审计
- [x] 页面：对话页“后台任务”面板显示进度与预约，可以预约、取消；开发环境弹窗加“单回合时限”
- [x] 测试与检查：管理器 ruff / mypy / pytest，工作台 ruff / format / mypy / export_openapi --check / 相关测试，前端 lint / typecheck / vitest / build / check:api

## 背景 / 约束 / 参考
- 编号在 #235 预留（T-0291、T-0292）。和 T-0292 在同一个 PR 里，很多文件两个任务都改。
- 人类决定（2026-11-18，原话）：“需要你决定：1. 会话里的模型可以自己预约，自己开作业 2. 外层智能体的这些新工具可以按照，直接执行、不审批”。按此写入 `docs/AGENTS.md` 第 8 节例外十。
- 官方程序的事件字段只在锁定版本的程序文件和生成的协议上核对过，没跑真实回合（`docs/decisions/2026-11-13-llm-bridge-verification.md` 4.1）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-18 06:39 UTC · claude · 创建任务（owner: unassigned；编号 T-0291 由集成会话在 #235 预留）
- 2026-11-18 06:39 UTC · human · owner unassigned → claude：人类在对话中指派：按优化后的长时间任务方案全面落实代码（会话里的模型可以自己预约、自己开作业；外层智能体的新工具直接执行、不审批；同时运行的作业不设上限）
- 2026-11-18 06:39 UTC · claude · 开始任务
- 2026-11-18 06:49 UTC · claude · 做完第 1、2 期：回合进度 activity、Codex 与 Claude / CodeBuddy 的进度事件、开发环境单回合时限、预约与调度、工作台工具 llm_bridge_tasks / timer_create / timer_cancel、页面后台任务面板。本机检查：管理器 ruff / format / mypy 通过，pytest 139 passed / 4 skipped；工作台 ruff / format / mypy / export_openapi --check 通过，llm_bridge、契约、审计、工具相关 151 passed（Postgres 16 + Redis）；前端 lint / typecheck / build / check:api 通过，vitest 84 个文件 928 passed；collab check 通过。本机真实管理器经 HTTP 跑通 llm-task job start / at / status，作业结束后自动开回合，回合结束后令牌作废。没在真实账号上跑（方案 12.4）。
- 2026-11-18 06:49 UTC · claude · status in_progress → done：验收标准全部勾选；和 T-0292 同一个 PR
- 2026-11-18 08:03 UTC · claude · 随 T-0292 一起处理集成审查（PR #255）：第 8、9 条涉及自动消息与开发环境，见方案 12.5。
