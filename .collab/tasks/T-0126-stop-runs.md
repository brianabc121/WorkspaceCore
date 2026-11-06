---
id: T-0126
title: 终止正在执行的任务：MCP 软停止、接入包 stop-runs.mjs（sessions.list + sessions.abort）、终止其他智能体任务的开关、AGENTS.md 例外六
owner: claude
status: todo
priority: P2
branch:
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0121
created: 2026-11-06
updated: 2026-11-06
---

# T-0126 终止正在执行的任务：MCP 软停止、接入包 stop-runs.mjs（sessions.list + sessions.abort）、终止其他智能体任务的开关、AGENTS.md 例外六

## 目标
工作台能叫停智能体正在执行的任务，避免它深陷在一件事里；管理员可以按实例赋予智能体终止其他智能体任务的权利
（人类 2026-11-06 在对话中提出，方案 `docs/v2/AGENT_MESSAGING_PLAN.md` 第 7.3 节，D20 ~ D22 按推荐）。

## 验收标准
- [ ] 迁移：终止令表；实例的“终止其他智能体任务”开关（默认关闭，乐观并发 + 审计）
- [ ] 软停止：下令后 5 分钟内该实例的 MCP 工具调用返回 `RUN_STOPPED`（谁、为什么、怎么做）；硬停止报回后解除
- [ ] 硬停止：`/agent-api/v1/stops`（领取、报结果）；接入包 `stop-runs.mjs` 用 `sessions.list({activeOnly})` + `sessions.abort({key, clearQueued})`；crontab 行，SSH 部署自动写入；超时记为过期
- [ ] 管理员：实例页“终止正在执行的任务”与终止记录；任务抽屉对智能体执行中的任务“终止执行”（这一段记为中断）
- [ ] 智能体：开关打开后才有 `list_agent_runs` / `stop_agent_run`；不能终止自己；每小时 10 次；通知管理员；`docs/AGENTS.md` 第 8 节例外六
- [ ] 后端与前端检查通过；真实 OpenClaw 上实测 `sessions.abort`（核验记录 6.2）

## 背景 / 约束 / 参考
- 核对过的 OpenClaw 2026.9.36 文档：`/app/docs/gateway/protocol/rpc-session-control.md`（`sessions.abort`）、
  `/app/docs/gateway/protocol/rpc-bootstrap-and-events.md`（`sessions.list` 的 `activeOnly`、`hasActiveRun`）、
  `/app/docs/concepts/queue.md`（`chat.abort` 与排队）、`/app/docs/tools/slash-commands.md`（`/stop`）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 07:52 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 07:52 UTC · claude · 人类在对话中提出（2026-11-06）：“workspace平台可以通过mcp终止openclaw正在执行的某个任务，避免出现智能体深陷到一个任务里去；这个权利也需要做成一个开关，管理员可以赋予某个智能体终止其他智能体任务的权利”。已写进方案第 7.3 节（D20 ~ D22、例外六写法），排在 T-0121 之后（共用接入包每分钟的脚本与 crontab）。
