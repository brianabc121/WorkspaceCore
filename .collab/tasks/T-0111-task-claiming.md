---
id: T-0111
title: 任务接取：自由接取、claim_task / release_task、complete_task 直接执行、打回、智能体可见字段、calendar_agenda due_tasks、AGENTS.md 例外二（迁移 0029）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0111 任务接取：自由接取、claim_task / release_task、complete_task 直接执行、打回、智能体可见字段、calendar_agenda due_tasks、AGENTS.md 例外二（迁移 0029）

## 目标
按方案第 3、4 节：任务可以指派，也可以设为自由接取；空闲的智能体自主领取、放弃；智能体完成任务直接执行，管理员可以打回；智能体能看到执行者等字段并按“分给我的 / 我能领取的”筛选；calendar_agenda 带上有截止时间的任务。

## 验收标准
- [x] 迁移 0029：任务的接取方式与范围、打回记录（任务动态）；可降级
- [x] 设为自由接取（条件、范围、通知符合条件的智能体）；`claim_task`（先到先得、空闲、限额、错误码、自动加入项目）、`release_task`、`complete_task` 直接执行；打回（原因、新段、IM 告知）
- [x] `list_tasks` 的 `assigned_to_me` / `claimable`，任务输出的执行者、接取方式、计划开始、执行状态、最近打回原因；`calendar_agenda.due_tasks`
- [x] `docs/AGENTS.md` 第 8 节例外二（人类批准方案时一并批准）
- [x] 前端：自由接取设置与标记、打回；后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 10:12 UTC · claude · 开始任务
- 2026-11-04 10:12 UTC · claude · 实现完成：迁移 0029（tasks.claim_mode / claim_scope / claim_opened_by、task_events、ai_tool_executions.risk_level 加 direct）；task_claims.py（能领取 / 空闲 / 范围判断、通知文字、管理员账号单聊、项目群 ws_notice 通知、announce）；agent_tasks.py（claim_task / release_task / complete_task 直接执行：限额 10/h、实例咨询锁 + 任务行锁先到先得、空闲才能领、自动加入项目、执行记录 direct、审计、通知管理员）；PATCH 任务的 claim_mode / claim_scope（只限 owner / admin，设为 open 后通知空闲的智能体）；POST /tasks/{id}/reopen（新段、IM 告知、执行者停用时放回未分配）、GET /tasks/{id}/events；list_tasks 的 assigned_to_me / claimable 与执行者、接取方式、running 字段；get_task 的最近打回原因；calendar_agenda 的 due_tasks；交给它执行时项目群里也发通知（D6）。AGENTS.md 第 8 节例外二。前端：执行者里的“可自由接取”与范围、列表的“可自由接取”标记、任务动态与打回、执行记录的“直接执行”标签。agents 场景 16 改为直接完成，新增场景 17。与方案的偏差：时间轴上打回的时刻表现为新的一段的起点，没有单独的标记；'可自由接取'的通知只发给在线的智能体（离线的上线后可以用 list_tasks claimable 查）。
- 2026-11-04 10:12 UTC · claude · status in_progress → done：验收标准全部完成：新增后端测试 9 个通过；相关后端套件（契约、任务、时间轴、MCP、AI 工具、迁移、项目群、知识库工具）135 个通过；ruff / mypy 通过；前端 lint / typecheck / 420 个测试 / build 通过。agents 场景 16、17 交给 CI。下一步 T-0112。
