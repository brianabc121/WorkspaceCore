---
id: T-0220
title: agents 场景 20 V3：说明被定时心跳取走（甲的 OpenIM 对话与心跳共用 agent:main:main），验收改为看拒收后的下一轮
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e/agents_acceptance.py,docs/acceptance/sprint4-browser-err-network-changed.md,docs/decisions/2026-11-04-agent-messaging-verification.md
depends_on:
created: 2026-11-11
updated: 2026-11-11
---

# T-0220 agents 场景 20 V3：说明被定时心跳取走（甲的 OpenIM 对话与心跳共用 agent:main:main），验收改为看拒收后的下一轮

## 目标
`agents` 场景 20 的 V3 共失败三次（main `e5e0956`、PR #190 的 `12c6636`、PR #192 的 `c2c9b66`）。
T-0218 的诊断在第三次（运行 42945534961）里定位到原因：补丁放回的 5002 说明被 OpenClaw 的定时心跳那一轮取走了，
第三问那一轮自然看不到。根因是锁定版本里一个实例在 OpenIM 的所有对话和心跳共用会话 `agent:main:main`：
openclaw-channel 0.3.3 调 `resolveAgentRoute` 不传 `peer`，OpenClaw 2026.9.36 没有 `peer` 就返回主会话键。

补丁的行为符合设计（放回同一个会话、进了下一轮），所以把验收改成看拒收之后甲的每一轮；
同时把这个事实写进核验记录与问题记录，并把“各对话共用一个会话”交给人类决定要不要改。

## 验收标准
- [x] 场景 20 的 V3 检查看拒收之后甲的每一轮模型调用（第三问或定时心跳），任意一轮带着补丁放回的 5002 说明就通过；不通过时仍输出 T-0218 的诊断。
- [x] 在锁定版本上核对并写明原因：openclaw-channel 0.3.3 `dist/inbound.js` 第 306 ~ 313 行（sha256 与补丁脚本登记的原文件一致）；
  OpenClaw 2026.9.36 的 `resolveAgentRoute` / `buildAgentSessionKey` / `buildAgentPeerSessionKey`（没有 `peer` → `agent:main:main`）。
- [x] 问题记录 `docs/acceptance/sprint4-browser-err-network-changed.md` 第 6 节改正之前“心跳已排除”的错误结论。
- [x] 核验记录 `docs/decisions/2026-11-04-agent-messaging-verification.md` 6.1 补充 V3、6.3 更正唤醒会话的说法。
- [x] “各对话共用一个会话”要不要改，另建 T-0221 交给人类决定。

## 背景 /“各对话共用一个会话”要不要改：见 T-0221（owner human）。

管理员的说明。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-11 02:59 UTC · claude · 创建任务（owner: claude）
- 2026-11-11 02:59 UTC · claude · 开始任务
- 2026-11-11 03:00 UTC · claude · 验收改为看拒收后甲的每一轮；核验记录 6.1 补充、6.3 更正；问题记录第 6 节改正结论。各对话共用 agent:main:main 是否要改，待人类决定（见背景）
- 2026-11-11 03:00 UTC · claude · status in_progress → done：验收改为看拒收后甲的每一轮；原因写进核验记录与问题记录；会话隔离与否交给 T-0221（human）。按 D-0004 在 PR 的 CI 全部通过后合并
