---
id: T-0214
title: Sprint 5 验收的通知检查只看自己的通知（与服务恢复告警的竞态）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: tests/e2e/sprint5_acceptance.py
depends_on:
created: 2026-11-10
updated: 2026-11-10
---

# T-0214 Sprint 5 验收的通知检查只看自己的通知（与服务恢复告警的竞态）

## 目标
PR #186 的 CI（e2fd990，只改了任务文件）里 `ci-infra / compose` 的 Sprint 5 验收偶发失败两项：
“S5-03 同一个通知事件只写入一次”“S5-03 标为已读后未读数与列表更新”。

原因：compose 任务里 Prometheus 在 Sprint 4 验收之后才启动，此前 worker 的服务检查（`app/domain/alerts.py`）判它不可用，
启动后的第一次检查给所有工作空间的 owner / admin 发 `service_recovered` 通知。Sprint 5 验收刚建好的工作空间
（17:47:42）正好收到这条（17:47:45），而 S5-03 要求整个工作空间的未读数为 1、标已读后为 0。恢复通知早于工作空间创建时就能通过。

## 验收标准
- [x] S5-03 只看验收自己发的那条通知（标题带随机后缀）：只有 1 条且未读；标已读后未读列表里没有它，未读数等于剩下的其他未读
- [x] 去重（同一个 key 发两次只写一条）与已读状态仍然检查；其他未读的类型写进检查详情，方便核对
- [x] PR 上 CI 全部通过：2d0bfc1 上 12 项检查全部成功（compose 的 Sprint 5 验收含改后的 S5-03）

## 背景 / 约束 / 参考
- 失败的运行：https://github.com/brianabc121/WorkspaceCore/actions/runs/42867570546（compose job 130009897684），重跑一次通过
- 说明见 PR #186 的评论

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 18:49 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 18:49 UTC · claude · 开始任务
- 2026-11-10 19:37 UTC · claude · status in_progress → done：PR #187 的 2d0bfc1 上 12 项检查全部成功，compose 的 Sprint 5 验收（改后的 S5-03）通过
