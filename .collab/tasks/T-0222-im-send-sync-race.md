---
id: T-0222
title: im_send 当场发送途中 Worker 也在发：deliver 回滚后读过期对象报错（agents 场景 24 偶发失败）
owner: claude
status: done
priority: P1
branch: b6-model-connections
paths: services/api/app/domain/agent_outbox.py,services/api/tests/test_agent_media.py,docs/acceptance/sprint4-browser-err-network-changed.md
depends_on:
created: 2026-11-12
updated: 2026-11-12
---

# T-0222 im_send 当场发送途中 Worker 也在发：deliver 回滚后读过期对象报错（agents 场景 24 偶发失败）

## 目标
main `c4cbddc` 的 agents 场景 24：甲经 `im_send` 发的文字和三个附件都到了 OpenIM，但工具没有返回 `sent`（`status: null`），
执行记录里也没有这次调用。人类 2026-11-12 要求查清并修好。

原因（本地复现）：`im_send` 先把几条消息写进发件箱（`queued`、马上到期）再逐条当场发。Worker 每 5 秒的
`deliver_im_outbox` 正好在这期间运行时，会把后面几条顺延（前一条还在“发送中”）或直接拿走。工具这边的 `deliver`
拿不到那一行就 `rollback()`；回滚让会话里所有对象过期，循环里下一次读 `row.status` 在异步会话里隐式加载，
报 `MissingGreenlet`。异常没有被工具层接住：MCP 返回 JSON-RPC 错误（没有 `result`），执行记录没写；
已经写进发件箱的几条随后由 Worker 发出，所以消息都到了。`_replay` 的循环有同样的问题。

做法：
- 循环前先记下要发的 ID，之后不再读可能过期的对象（`send`、`_replay`）。
- 当场发的几条先留给这次工具调用自己发：到期时间设为 30 秒后（`SYNC_RESERVE`），`deliver(reserved=True)` 不看到期时间；
  Worker 在这段时间里不碰它们。工具调用中途退出、或超过 12 秒没发完的，过了这段时间由 Worker 接着发。

## 验收标准
- [x] 回归测试在修改前的代码上复现同样的 `MissingGreenlet`（`agent_outbox.py:337`），修改后通过：
  - Worker 在第一条发出后运行：一条也拿不到，工具返回 `sent`、四条都是 `sent`、执行记录一条、OpenIM 里四条不重复；
  - 发送途中某一条被别处拿走（用取消模拟）：工具照样返回结果（`partial`）、写执行记录，其余几条照常发出。
- [x] 原有发件箱、附件、IM 工具测试通过；ruff、mypy 通过；后端全量测试与 agents 场景 24 以 PR 的 CI 为准。
- [x] 问题记录第 6 节场景 24 那一行写明原因与处理。

## 背景 / 约束 / 参考
- T-0218 给场景 24 加的诊断保留（工具原样的返回、发件箱各条状态、执行记录条数、API 警告与错误日志）。
- 失败那次的 API 日志只保留最后 200 行，覆盖不到；这次在本地照同样的时序复现，日志里就是
  `handler for 'tools/call' raised` + `MissingGreenlet`。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-12 05:29 UTC · claude · 创建任务（owner: claude）
- 2026-11-12 05:29 UTC · claude · 开始任务
- 2026-11-12 05:31 UTC · claude · 本地复现并修复：两个回归测试在旧代码上报 MissingGreenlet（agent_outbox.py:337），修改后通过；发件箱、附件、IM 工具测试 17 个通过；ruff、mypy 通过。IM / 智能体 / MCP / Worker 相关测试在跑，全量与 agents 场景 24 以 PR 的 CI 为准
- 2026-11-12 05:31 UTC · claude · status in_progress → done：根因已修；合并以 PR 的 CI（api、agents 场景 24）全部通过为前提
