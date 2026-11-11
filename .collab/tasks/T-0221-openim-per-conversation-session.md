---
id: T-0221
title: 决定：智能体在 OpenIM 的各对话是否各用一个会话（现共用 agent:main:main）
owner: human
status: todo
priority: P2
branch:
paths: services/api/app/agent_bundle/files/bin/channel-patch.mjs,services/api/app/domain/agent_wakeups.py
depends_on:
created: 2026-11-11
updated: 2026-11-11
---

# T-0221 决定：智能体在 OpenIM 的各对话是否各用一个会话（现共用 agent:main:main）

## 目标
T-0220 查明：在锁定版本（openclaw-channel 0.3.3 + OpenClaw 2026.9.36）上，一个智能体实例在 OpenIM 的所有群聊、私聊，
以及 OpenClaw 的定时心跳，都在同一个会话 `agent:main:main` 里（插件调 `resolveAgentRoute` 不传 `peer`）。
请人类决定是否要改成每个对话各用一个会话。

现状：同一个智能体实例在 OpenIM 里的所有群聊、私聊，以及定时心跳，都在 `agent:main:main` 一个会话里。
- 好处：智能体在不同对话之间有连续的记忆。
- 问题：它在 A 群回复时，上下文里带着 B 群和管理员私聊的内容，可能把私聊内容说到群里；
  上下文增长更快，压缩更频繁；唤醒（`--session-key openim:group:<群>`）跑在另一个会话里，看不到群里收到的消息。

可选的改法：在 `bin/channel-patch.mjs` 的补丁里，给 `resolveAgentRoute` 加上 `peer: { kind: group ? "group" : "direct", id }`。
这样群会话是 `agent:main:openim:group:<群>`，私聊按 `dmScope: per-channel-peer` 是 `agent:main:openim:direct:<发送者>`。
- 唤醒的会话键也要相应改。
- 要在锁定版本上核对 `peer` 的字段格式。
- 补丁内容变了，打补丁后的 sha256 要更新；已经打过旧补丁的实例重新部署时会被拒绝（退出码 8），需要接入包的升级处理。
- 要补端到端验收：两个群互不看到对方的消息。

不改也可以，但要把“各对话共用上下文”写进给管理员的说明。

## 验收标准
- [ ] 人类给出决定：改（按上面的改法另开实现任务）或不改（把“各对话共用上下文”写进给管理员的说明）。

## 背景 / 约束 / 参考
- 证据与核对过程：T-0220；`docs/acceptance/sprint4-browser-err-network-changed.md` 第 6 节；
  `docs/decisions/2026-11-04-agent-messaging-verification.md` 6.1 的 2026-11-11 补充。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-11 03:00 UTC · claude · 创建任务（owner: human）
