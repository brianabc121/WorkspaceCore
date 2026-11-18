---
id: T-0292
title: 大模型客户端长时间任务（二）：管理器托管的后台作业、会话里的 llm-task 命令、自动接着做的护栏（第 3、4 期）
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: services/llm-bridge/bridge/jobs.py,services/llm-bridge/bridge/session_cli.py,services/llm-bridge/bridge/activity.py,services/llm-bridge/bridge/errors.py,services/llm-bridge/bridge/app.py,services/llm-bridge/bridge/config.py,services/llm-bridge/bridge/engine.py,services/llm-bridge/bridge/store.py,services/llm-bridge/bridge/longrun.py,services/llm-bridge/bridge/tools.py,services/llm-bridge/bridge/mcp_server.py,services/llm-bridge/bridge/drivers,services/llm-bridge/tests,services/api/app/modules/llm_bridge,services/api/app/domain/audit_labels.py,services/api/openapi.json,services/api/tests/test_contract.py,services/api/tests/test_module_llm_bridge.py,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests/llm_bridge.test.ts,apps/web/tests/llm_chat.test.ts,docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md,docs/v2/LLM_BRIDGE_PLAN.md,docs/api-contract.md,docs/decisions/2026-11-13-llm-bridge-verification.md,docs/guides/mcp-manager-deploy.md,docs/AGENTS.md
depends_on: T-0291
created: 2026-11-18
updated: 2026-11-18
---

# T-0292 大模型客户端长时间任务（二）：管理器托管的后台作业、会话里的 llm-task 命令、自动接着做的护栏（第 3、4 期）

## 目标
`docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md` 第 3、4 期：跑几个小时的东西交给 MCP 管理器托管，回答结束后照样跑；结束后自动叫会话接着做。会话里的模型自己开作业、自己预约（人类 2026-11-18 决定）；连续自动开的回合有护栏，到了上限暂停、等人决定。

## 验收标准
- [x] 作业：`bash -lc`、独立进程组、先 TERM 再 KILL、主进程结束后整组停掉（不留孤儿）；日志写数据卷、有上限、读取时脱敏；环境变量只给白名单，不带管理器和回合的令牌
- [x] 只在“可以执行命令”档位的开发环境能开作业；`jobs_enabled` 总开关；同时运行的作业数默认不限（人类决定），管理员可以设上限；单个作业时长有上限
- [x] 作业结束后自动开回合（带退出码、用时、日志末尾、留的话）；停掉的默认不接着做；管理器重启时正在跑的记为“结果未知”并告诉会话；归档时停掉
- [x] 会话里的 `llm-task` 命令：回合令牌写在会话工作目录（加进 `.git/info/exclude`），只对这个会话、这一回合有效，回合结束作废，有调用频率上限；托管作业可用时给 Claude / CodeBuddy 关掉官方后台并在说明里告诉模型
- [x] 循环护栏：轮数、时长、连续失败、每个会话每天自动回合数；到了上限暂停，留提示；人和智能体可以继续或停止
- [x] 工作台：直接执行 `llm_bridge_job_start` / `llm_bridge_job_stop` / `llm_bridge_chain_control`，读工具 `llm_bridge_job_log`（例外十）；审计不含令牌；智能体开作业时通知管理员；没有 `can_write` 的实例被拒绝
- [x] 页面：“后台任务”面板（运行中、预约、已完成、日志、停止，循环暂停时“继续 / 停止”）；“运行设置”加“后台任务”一组
- [x] `docs/AGENTS.md` 第 8 节例外十，写法和例外一到九一致
- [x] 测试与检查同 T-0291；本机起真实管理器经 HTTP 跑通 `llm-task`

## 背景 / 约束 / 参考
- 人类决定（2026-11-18，原话）：“默认上限：我建议每个会话同时最多 3 个作业=这一条要改一下，不做上限限制”。
- 管理器的 SQLite 用 `CREATE TABLE IF NOT EXISTS` 加 `jobs` / `timers` / `chains` 三张表，不改旧表；工作台没有数据表变化（方案 12.3）。
- 已部署的管理器要在页面上“重新部署”才有这些功能。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-18 06:39 UTC · claude · 创建任务（owner: unassigned；编号 T-0292 由集成会话在 #235 预留）
- 2026-11-18 06:39 UTC · human · owner unassigned → claude：人类在对话中指派：按优化后的长时间任务方案全面落实代码（会话里的模型可以自己预约、自己开作业；外层智能体的新工具直接执行、不审批；同时运行的作业不设上限）
- 2026-11-18 06:39 UTC · claude · 开始任务
- 2026-11-18 06:49 UTC · claude · 做完第 3、4 期：托管作业（进程组、日志、停止、重启后结果未知）、llm-task 命令与回合令牌、循环护栏、工作台工具 job_start / job_stop / job_log / chain_control、智能体开作业时通知管理员、运行设置；docs/AGENTS.md 例外十。本机检查：管理器 ruff / format / mypy 通过，pytest 139 passed / 4 skipped；工作台 ruff / format / mypy / export_openapi --check 通过，llm_bridge、契约、审计、工具相关 151 passed（Postgres 16 + Redis）；前端 lint / typecheck / build / check:api 通过，vitest 84 个文件 928 passed；collab check 通过。本机真实管理器经 HTTP 跑通 llm-task job start / at / status，作业结束后自动开回合，回合结束后令牌作废。没在真实账号上跑（方案 12.4）。
- 2026-11-18 06:49 UTC · claude · status in_progress → done：验收标准全部勾选；和 T-0291 同一个 PR
