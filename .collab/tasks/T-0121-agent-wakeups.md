---
id: T-0121
title: 唤醒：schedule_wakeup / cancel_wakeup、管理员建唤醒、/agent-api/v1/wakeups、接入包 wakeup.mjs
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0118
created: 2026-11-04
updated: 2026-11-04
---

# T-0121 唤醒：schedule_wakeup / cancel_wakeup、管理员建唤醒、/agent-api/v1/wakeups、接入包 wakeup.mjs

## 目标
让智能体能被工作台叫醒，主动发起会话（方案第 7.2 节）。

## 验收标准
- [ ] 迁移：唤醒表；`schedule_wakeup` / `cancel_wakeup`（限额）；管理员在实例页建唤醒（含重复）
- [ ] `/agent-api/v1/wakeups`（取到期的、报结果）；接入包 `wakeup.mjs` 与 crontab 行，SSH 部署自动写入
- [ ] 后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
