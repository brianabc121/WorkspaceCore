---
id: T-0120
title: openclaw-channel 补丁（回复被拒收时放回系统事件）与工具收口（禁用 message 等）
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,docs,tests/e2e,infra
depends_on: T-0118
created: 2026-11-04
updated: 2026-11-04
---

# T-0120 openclaw-channel 补丁（回复被拒收时放回系统事件）与工具收口（禁用 message 等）

## 目标
自动回复被拒收时让智能体知道；主动发消息只剩 `im_send` 一个出口（方案第 5.2、6.6 节）。

## 验收标准
- [ ] openclaw-channel 0.3.3 补丁：`deliver` 失败时用 `enqueueSystemEvent` 把原因放回同一个会话；构建时校验原始 tarball
- [ ] 接入包禁用 `message`、`conversations_send`、`conversations_turn`、`conversations_list`；给智能体的说明
- [ ] 实例页提示已部署的实例重新签发接入包
- [ ] 检查通过；实测项记入核验记录

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
