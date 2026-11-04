---
id: T-0111
title: 任务接取：自由接取、claim_task / release_task、complete_task 直接执行、打回、智能体可见字段、calendar_agenda due_tasks、AGENTS.md 例外二（迁移 0029）
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0111 任务接取：自由接取、claim_task / release_task、complete_task 直接执行、打回、智能体可见字段、calendar_agenda due_tasks、AGENTS.md 例外二（迁移 0029）

## 目标
按方案第 3、4 节：任务可以指派，也可以设为自由接取；空闲的智能体自主领取、放弃；智能体完成任务直接执行，管理员可以打回；智能体能看到执行者等字段并按“分给我的 / 我能领取的”筛选；calendar_agenda 带上有截止时间的任务。

## 验收标准
- [ ] 迁移 0029：任务的接取方式与范围、打回记录（任务动态）；可降级
- [ ] 设为自由接取（条件、范围、通知符合条件的智能体）；`claim_task`（先到先得、空闲、限额、错误码、自动加入项目）、`release_task`、`complete_task` 直接执行；打回（原因、新段、IM 告知）
- [ ] `list_tasks` 的 `assigned_to_me` / `claimable`，任务输出的执行者、接取方式、计划开始、执行状态、最近打回原因；`calendar_agenda.due_tasks`
- [ ] `docs/AGENTS.md` 第 8 节例外二（人类批准方案时一并批准）
- [ ] 前端：自由接取设置与标记、打回；后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
