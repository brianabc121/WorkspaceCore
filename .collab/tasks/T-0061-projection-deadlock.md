---
id: T-0061
title: API 删除任务与定时对账写投影时死锁，导致 Sprint 3 浏览器验收偶发失败
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/repositories,services/api/tests
depends_on:
created: 2026-11-01
updated: 2026-11-01
---

# T-0061 API 删除任务与定时对账写投影时死锁，导致 Sprint 3 浏览器验收偶发失败

## 目标
PR #94 的 `ci-infra / compose` 第一次运行时，Sprint 3 浏览器验收失败：`DELETE /api/v1/tasks/{id}` 返回 500
（PostgreSQL `DeadlockDetectedError`），对话框没关，后面的点击超时。原因是 API 写任务投影与定时对账
写同一个集成的投影时加锁顺序相反：对账每次都重存项目，项目的搜索触发器会改写这个项目下所有任务的
搜索文档（先锁 `search_documents` 再锁任务行）；API 删除任务先锁任务行，再由触发器删它的搜索文档。
与 T-0057 的改动无关（重跑一次通过），但用户在对账那一刻删除或修改任务会拿到 500，需要修。

## 验收标准
- [x] 同一个集成的投影写入事务（API 写任务、Webhook 处理、定时对账、全量同步）依次进行，不再互相死锁
- [x] 回归测试：两个事务按 CI 里的顺序交错（对账先重存项目、API 删除任务、对账再存任务），修复前报死锁，修复后都成功
- [x] 任务、同步、Webhook、搜索、GitHub 相关测试与完整后端测试通过

## 背景 / 约束 / 参考
- CI 日志：https://github.com/brianabc121/WorkspaceCore/actions/runs/41287310978/job/124791126405（第 18 步与 postgres 日志）
- 触发器：`migrations/versions/*0008*`（`search_index_external`，项目改名时更新任务的 context）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-01 08:32 UTC · claude · 创建任务（owner: claude）
- 2026-11-01 08:32 UTC · claude · 开始任务
- 2026-11-01 08:42 UTC · claude · status in_progress → done：projection 仓储写函数先取集成的事务级 advisory lock（不自动 flush）；回归测试修复前稳定复现死锁、修复后通过；完整后端 530 passed
