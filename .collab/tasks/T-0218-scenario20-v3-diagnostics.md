---
id: T-0218
title: agents 场景 20 V3 偶发失败：补诊断（拒收后每轮模型调用、群会话记录、网关与插件日志）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: tests/e2e/agents_acceptance.py,docs/acceptance/sprint4-browser-err-network-changed.md
depends_on:
created: 2026-11-10
updated: 2026-11-10
---

# T-0218 agents 场景 20 V3 偶发失败：补诊断（拒收后每轮模型调用、群会话记录、网关与插件日志）

## 目标
`agents` 场景 20 的 V3（自动回复被 5002 拒收后，补丁放回群会话的系统事件，甲下一轮看得到）2026-11-10 偶发失败两次：
main `e5e0956` 一次，PR #190 旧头部 `12c6636` 一次（运行 42916795078）。T-0213 加的诊断只看第三问之后的模型调用和实例最后 600 行日志，
定不了事件是根本没进队列，还是被别的一轮取走了。补上能区分这几种情况的诊断，下次出现时据此定位根因。

## 验收标准
- [x] V3 不通过时，失败详情包括：
  - 拒收前后每一轮模型调用（时间、上下文里 `[工作台]` 行的条数、最后一条用户消息的末尾）；
  - 实例网关 `sessions.list` 里的会话键与更新时间；
  - 群会话 `openim:group:<群 ID>` 的 `chat.history` 里有没有补丁放回的事件；
  - 从拒收前开始的插件与网关日志（`[openim]`、心跳、系统事件相关的行）。
- [x] 诊断只在失败时收集，任何一步出错都不影响场景本身的判定。
- [x] 用到的网关方法与参数在锁定镜像 `openclaw/openclaw:2026.9.36` 上核对过（`sessions.list`、`chat.history` 的参数结构）。
- [x] 问题记录 `docs/acceptance/sprint4-browser-err-network-changed.md` 第 6 节补上这次出现与已排除的原因。

## 背景 / 约束 / 参考
- 补丁 `services/api/app/agent_bundle/files/bin/channel-patch.mjs` 只调用 `enqueueSystemEvent`；改补丁会改变打补丁后的 sha256，已打过旧补丁的文件会被拒绝（退出码 8），所以这次不改补丁，只改验收脚本。
- 已排除：第三问前约 16 秒那一轮是 OpenClaw 的定时心跳（提示末尾的 `Current time … Reference UTC` 只有心跳和会话重置会加），心跳默认跑在主会话（`heartbeat-runner-session` 的 `resolveHeartbeatSessionKey`），只取主会话队列的事件；插件运行时的 `enqueueSystemEvent` 只解析会话键，不会触发心跳（`system-events-Da7grXqF.mjs`）。
- 网关方法：`/app/docs/gateway/protocol/rpc-session-control.md`；`chat.history` 参数是封闭对象，`sessionKey` 必填、`limit` 可选（`sessions-B_b4BuMR.mjs` 的 `ChatHistoryParamsSchema`）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 23:41 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 23:41 UTC · claude · 开始任务
- 2026-11-10 23:47 UTC · claude · 验收脚本加 gateway_call / v3_diagnostics：V3 不通过时附拒收前后每轮模型调用（notes 条数、count、last_user 末尾）、网关 sessions.list 的会话键、群会话 chat.history 里的 [工作台] 条数、从拒收前开始的 [openim] / [heartbeat] / system event 日志；每项出错只记原因。用本地锁定镜像起网关实测：sessions.list 返回 {sessions: [...]}，chat.history 以 {sessionKey, limit: 40} 调用正常返回；用假 Ctx 跑通整个函数。问题记录第 6 节补这次出现与已排除的原因
- 2026-11-10 23:47 UTC · claude · status in_progress → done：诊断加进验收脚本（失败时才收集，出错只记原因）；网关调用在本地锁定镜像上实测；问题记录第 6 节已补。按 D-0004 在 PR 的 CI 全部通过后合并
