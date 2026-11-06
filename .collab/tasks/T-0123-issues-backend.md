---
id: T-0123
title: Issues 后端：Issue / 评论 / 时间线 / 任务关联 / 已读、权限、通知与唤醒、刷屏保护、阻塞检查、六个 MCP 工具、AGENTS.md 例外五
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0121, T-0122
created: 2026-11-04
updated: 2026-11-04
---

# T-0123 Issues 后端：Issue / 评论 / 时间线 / 任务关联 / 已读、权限、通知与唤醒、刷屏保护、阻塞检查、六个 MCP 工具、AGENTS.md 例外五

## 目标
项目里的人和智能体通过 Issues 对接（方案第 8 节）。

## 验收标准
- [ ] 迁移：Issue、评论、时间线、任务关联、已读
- [ ] 接口与权限、模板、`#编号` 与 `@` 解析、通知与唤醒（合并）、连续评论暂停与限额、阻塞 Issue 挡住 `complete_task`、全局搜索
- [ ] 六个 MCP 工具；`docs/AGENTS.md` 第 8 节例外五（人类批准方案时一并批准）
- [ ] 后端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
