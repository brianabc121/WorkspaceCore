---
id: T-0118
title: 发件箱与文字主动发送：im_send / im_cancel / im_outbox、预检、重试与对账、拒收记录、限流补发、AGENTS.md 例外四
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0117
created: 2026-11-04
updated: 2026-11-04
---

# T-0118 发件箱与文字主动发送：im_send / im_cancel / im_outbox、预检、重试与对账、拒收记录、限流补发、AGENTS.md 例外四

## 目标
主动发文字统一走工作台：发件箱、预检、重试、对账、拒收记录与补发（方案第 4.1、5、6 节）。

## 验收标准
- [ ] 迁移：`agent_im_outbox`、`agent_im_rejections`；可降级
- [ ] `im_send`（文字、@、定时、幂等、限流排队）、`im_cancel`、`im_outbox`；只读预检与回调共用规则
- [ ] Worker 投递、退避重试、结果未知时按 `ex` 标记对账；每小时限额
- [ ] 回调记录实例被拒收的消息；5002 限额恢复后补发
- [ ] 实例页发件记录；`docs/AGENTS.md` 第 8 节例外四（人类批准方案时一并批准）
- [ ] 后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 19:28 UTC · claude · 迁移 0034（agent_im_outbox、agent_im_rejections）；app/domain/agent_outbox.py：im_send（预检与回调同规则、幂等键、定时、限流排队、每小时 60 条）、投递（ex 带发件箱 ID、投影里先放“发送中”、退避重试、结果未知按投影对账）、im_cancel、im_outbox；before 回调拒收实例消息时后台记录，5002 的文字回复限额恢复后补发；Worker 每 5 秒投递、purge 30 / 7 天；实例页“发件记录”（接口 + OutboxPanel）；docs/AGENTS.md 例外四。tests/test_agent_outbox.py 5 个用例、tests/agents-outbox.test.ts 2 个用例通过；契约测试登记新工具。
