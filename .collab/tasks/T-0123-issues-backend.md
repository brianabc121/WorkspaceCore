---
id: T-0123
title: Issues 后端：Issue / 评论 / 时间线 / 任务关联 / 已读、权限、通知与唤醒、刷屏保护、阻塞检查、六个 MCP 工具、AGENTS.md 例外五
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0121, T-0122
created: 2026-11-04
updated: 2026-11-06
---

# T-0123 Issues 后端：Issue / 评论 / 时间线 / 任务关联 / 已读、权限、通知与唤醒、刷屏保护、阻塞检查、六个 MCP 工具、AGENTS.md 例外五

## 目标
项目里的人和智能体通过 Issues 对接（方案第 8 节）。

## 验收标准
- [x] 迁移：Issue、评论、时间线、任务关联、已读
- [x] 接口与权限、模板、`#编号` 与 `@` 解析、通知与唤醒（合并）、连续评论暂停与限额、阻塞 Issue 挡住 `complete_task`、全局搜索
- [x] 六个 MCP 工具；`docs/AGENTS.md` 第 8 节例外五（人类批准方案时一并批准）
- [x] 后端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 10:01 UTC · claude · 开始任务
- 2026-11-06 10:04 UTC · claude · 后端完成：迁移 0039（issues / issue_assignees / issue_tasks / issue_comments / issue_events / issue_mentions / issue_reads，projects.issue_notices，唤醒来源 issue，全局搜索 project_issue）；app/domain/issues.py（权限、按项目编号、模板、@ 与 #编号、通知中心、5 分钟合并唤醒、连续 10 条智能体评论暂停、每小时 10 个 / 60 条、密钥拦截、已读与收件箱、PR 状态）；页面接口 app/api/v1/issues.py；六个 MCP 工具 app/domain/issue_tools.py（client_key 防重复、执行记录不存正文、项目群提示）；complete_task 被未关闭的阻塞 Issue 挡住（TASK_BLOCKED），get_task 附上关联的未关闭 Issue，release_task 提醒开交接 Issue；README 的“沟通”“讨论”接上 Issue；SSE 主题 issues。tests/test_issues.py 6 个通过，契约测试 49 个通过；web lint / typecheck / 449 个测试通过。下一步：后端全量测试，然后 T-0124 页面。
- 2026-11-06 10:50 UTC · claude · status in_progress → done：后端全量 808 passed / 15 skipped（0902c87）；tests/test_issues.py 6 个、契约 49 个通过；迁移往返、安全矩阵通过。真实 OpenClaw 上的工具验收在 T-0125。
