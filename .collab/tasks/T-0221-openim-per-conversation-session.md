---
id: T-0221
title: 智能体在 OpenIM 的各对话各用一个会话（补丁给 resolveAgentRoute 传 peer）
owner: claude
status: done
priority: P2
branch: b6-model-connections
paths: services/api/app/agent_bundle/files/bin/channel-patch.mjs,services/api/app/agent_bundle/files/bin/myworkspace-openclaw-start,services/api/app/agent_bundle/__init__.py,services/api/app/agent_bundle/files/CHECKLIST.md,services/api/app/agent_provisioning/files/install-plugin.sh,services/api/app/domain/agent_wakeups.py,services/api/app/schemas/agents.py,services/api/openapi.json,apps/web/src/services/schema.d.ts,services/api/tests/test_channel_patch_script.py,services/api/tests/test_agent_wakeups.py,services/api/tests/test_agents_api.py,tests/e2e/agents_acceptance.py,docs/decisions/2026-11-04-agent-messaging-verification.md,docs/acceptance/sprint4-browser-err-network-changed.md,docs/threat-model.md,docs/api-contract.md,docs/versions.md,docs/deploy-guide.md
depends_on:
created: 2026-11-11
updated: 2026-11-12
---

# T-0221 智能体在 OpenIM 的各对话各用一个会话（补丁给 resolveAgentRoute 传 peer）

## 目标
T-0220 查明：在锁定版本（openclaw-channel 0.3.3 + OpenClaw 2026.9.36）上，一个智能体实例在 OpenIM 的所有群聊、私聊，
以及 OpenClaw 的定时心跳，都在同一个会话 `agent:main:main` 里（插件调 `resolveAgentRoute` 不传 `peer`）。
它在 A 群回复时，上下文里带着 B 群和管理员私聊的内容；唤醒（`--session-key openim:group:<群>`）也跑在另一个会话里。

人类 2026-11-12 决定：改成每个对话各用一个会话。做法：

- 接入包的插件补丁（`bin/channel-patch.mjs`）加第二处，给 `resolveAgentRoute` 传
  `peer: { kind: group ? "group" : "direct", id }`：群在 `agent:main:openim:group:<群 ID>`，私聊按 `dmScope: per-channel-peer`
  在 `agent:main:openim:direct:<发送者>`（都转小写）；心跳留在 `agent:main:main`。
- 打补丁后的 sha256 更新；已经打过旧补丁（只有第一处）的插件，重新部署时补上第二处（`plugin_patch_upgraded`），结果与从原文件打的相同。
- 两处都打了的插件上报 `0.3.3+myworkspace.2`；只有旧补丁的 `+myworkspace.1` 在实例页提示重新签发接入包。
- 发给管理员的唤醒会话键改为 `openim:direct:<管理员的 IM ID>`，群的唤醒键不变（OpenClaw 补成 `agent:main:` 开头，与群消息同一个会话）。

## 验收标准
- [x] 在锁定镜像里核对 `resolveAgentRoute` 的 `peer` 格式和得到的会话键（没有 peer、群、私聊三种），以及唤醒键的补全规则；写进核验记录 6.1、6.3。
- [x] 补丁：原文件 → 两处都打；旧补丁 → 补上第二处（结果一致）；已打过只核对；改过的补丁文件退出码 8；别的版本退出码 7。在真实文件上核对过，单元测试覆盖。
- [x] 插件版本 `+myworkspace.2`，`bundle_outdated` 对 `.1` 为真、对 `.2` 为假；OpenAPI 与前端类型重新生成。
- [x] 唤醒会话键：私聊 `openim:direct:<id>`，群 `openim:group:<id>`，都转小写；单元测试与接口测试更新。
- [x] agents 场景 20 核对：新群和管理员私聊各有自己的会话，第一问那一轮不带别的对话的消息；插件版本 `.2`。
- [x] 文档：威胁模型、接口约定、版本、部署指南（旧部署怎么升级）、接入包 CHECKLIST、问题记录第 6 节。

## 背景 / 约束 / 参考
- 证据与核对过程：T-0220；`docs/acceptance/sprint4-browser-err-network-changed.md` 第 6 节；
  `docs/decisions/2026-11-04-agent-messaging-verification.md` 6.1 的 2026-11-11、2026-11-12 补充与 6.3。
- 不放宽智能体的工具限制；补丁只加 `peer` 这一行，不改插件别的逻辑。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-11 03:00 UTC · claude · 创建任务（owner: human）
- 2026-11-12 01:24 UTC · human · owner human → claude：人类在对话中指派：改成每个对话各用一个会话（补丁里给 resolveAgentRoute 传 peer，唤醒会话键跟着改）
- 2026-11-12 01:24 UTC · claude · 开始任务
- 2026-11-12 04:23 UTC · claude · 实现完成：补丁第二处给 resolveAgentRoute 传 peer（群 agent:main:openim:group:<群>，私聊 agent:main:openim:direct:<对方>，心跳留在 main）；打补丁后 sha256 更新，旧补丁重新部署时补上第二处（plugin_patch_upgraded，结果与从原文件打的一致）；插件版本 0.3.3+myworkspace.2；唤醒私聊键改 openim:direct:<id>；场景 20 加会话检查。真实 inbound.js 上核对 applied / already / upgraded / 8 / 7；后端 ruff、mypy、相关测试，前端 lint / typecheck / test / build / check:api 通过；端到端以 PR 的 agents 任务为准
- 2026-11-12 04:23 UTC · claude · status in_progress → done：各对话各用一个会话已实现；合并以 PR 的 CI（含 agents 场景 20、21）全部通过为前提
