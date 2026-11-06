---
id: T-0126
title: 终止正在执行的任务：MCP 软停止、接入包 stop-runs.mjs（sessions.list + sessions.abort）、终止其他智能体任务的开关、AGENTS.md 例外六
owner: claude
status: in_progress
priority: P2
branch: claude/cool-wright-99qbbg
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
- 2026-11-06 08:15 UTC · claude · 开始任务
- 2026-11-06 08:34 UTC · claude · 后端（迁移 0037、agent_stops 领域、MCP 软停止与 list_agent_runs / stop_agent_run、管理员 PUT stop-others / POST stop / GET stops、/agent-api/v1/stops 领取与报回、每日清理）、接入包 bin/stop-runs.mjs 与 crontab 行、前端（实例卡片的权限开关与“终止正在执行的任务”、任务抽屉与时间轴的“终止执行”）、测试（tests/test_agent_stops.py 5 个、test_stop_runs_script.py 5 个、web 4 个）、e2e 场景 22、核验记录 6.2 已完成。锁定镜像里核对了 gateway call、activeOnly、sessions.abort 参数（封闭对象含 agentId，global / unknown 行带 agentId）。等 CI agents 任务跑场景 22 后补结果、标 done。
- 2026-11-06 09:13 UTC · claude · CI agents（3e9b6d5）场景 22 失败：乙（外网实例）的模型经 Tool Search 调 myworkspace__search 时 OpenClaw 报 Unknown tool id，调用没到工作台，所以等不到 RUN_STOPPED（叫停本身成功：stop_agent_run 返回 stopping）。另外发现 MCP 的每分钟限流在软停止之前，刷调用的那一轮会先看到 RATE_LIMITED。修复：软停止放到限流前面（新增测试）；场景 22 改成甲叫停乙在 MCP 层核对 + 乙的 stop-runs.mjs 报回，硬停止改由管理员叫停反复调用 knowledge_search 的甲（甲经模型调工作台工具在场景 15、16、20 已验证）。乙经模型调工作台工具的情况留给 T-0125 核对。
