---
id: T-0069
title: Sprint 3 浏览器验收：新建任务前等 Webhook 全部注册（Vikunja SQLite 写锁竞态）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: tests/e2e/sprint3_browser.cjs
depends_on:
created: 2026-11-01
updated: 2026-11-01
---

# T-0069 Sprint 3 浏览器验收：新建任务前等 Webhook 全部注册（Vikunja SQLite 写锁竞态）

## 目标
PR #110 的 compose 任务里 `sprint3_browser.cjs` 偶发失败：worker 在测试连接后异步给每个项目注册 Webhook，
测试同时在工作台新建任务；Vikunja 用 SQLite，两次写入撞上锁，新建任务返回 500 `database is locked`（api 返回 503），
页面上等不到新任务。测试原来只在“原生修改”之前等 Webhook，而且只等一个项目。

## 验收标准
- [x] 新建任务之前等 Vikunja 里每个未归档项目都注册好指向工作台的 Webhook（并作为一步验收输出注册的项目数）
- [x] 本机真实 Vikunja 上 `node tests/e2e/sprint3_browser.cjs` 全部通过

## 背景 / 约束 / 参考
- 失败记录：PR #110 compose job 125003853642（重跑后通过）；分析见 PR #110 的评论

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-01 23:19 UTC · claude · 创建任务（owner: claude）
- 2026-11-01 23:19 UTC · claude · 开始任务
- 2026-11-01 23:19 UTC · claude · 人类要求解决部署问题过程中发现的 CI 竞态，按 PR #110 评论里的方案修：新建任务前等全部项目的 Webhook；本机 sprint3_browser 全部通过（2 个项目）
- 2026-11-01 23:19 UTC · claude · status in_progress → done：测试改为写入前等全部 Webhook 注册；本机验收通过
