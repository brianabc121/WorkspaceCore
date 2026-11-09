---
id: T-0163
title: 首页改版（一）：任务的“待查看 / 阻塞 / 进行中”状态、首页任务进展接口、我的待办、新组件类型与默认首页布局
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/,docs/
depends_on:
created: 2026-11-08
updated: 2026-11-09
---

# T-0163 首页改版（一）：任务的“待查看 / 阻塞 / 进行中”状态、首页任务进展接口、我的待办、新组件类型与默认首页布局

## 目标
人类 2026-11-08 给出首页效果图（codex 制作的“工作总览”）并要求全面落实。效果图需要任务的四种状态
（待办、进行中、已完成待查看、阻塞）、我的待办、近期提醒和新的默认首页；现有任务只有“完成 / 未完成”。方案 `docs/v2/HOME_PLAN.md`。

## 验收标准
- [x] 迁移 0051：`tasks.review_pending` 与任务动态 `reviewed`；智能体 `complete_task` 完成时置真，确认结果 / 打回 / 人改完成状态清掉
- [x] `GET /api/v1/tasks/progress`：四种状态（阻塞来自未关闭的阻塞 Issue）的数量与一页条目、需要关注数；归档项目与已确认的不算
- [x] `GET /api/v1/tasks?assignee=me`；`POST /api/v1/tasks` 可带 `assignee_user_id`（必须是成员）
- [x] `POST /api/v1/tasks/{id}/review`（owner / admin，乐观并发，动态与审计）
- [x] 组件类型 `work_summary` / `task_progress` / `my_todos`；默认首页换成效果图的五个组件；`POST /api/v1/dashboards/{id}/reset`
- [x] 迁移 0052 只替换从没改过的默认首页（可降级）；迁移与接口测试、OpenAPI、文档

## 背景 / 约束 / 参考
方案 `docs/v2/HOME_PLAN.md`；效果图由人类在对话中给出（codex 制作）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-08 23:31 UTC · claude · 创建任务（owner: claude）
- 2026-11-08 23:32 UTC · claude · 开始任务
- 2026-11-09 00:23 UTC · claude · status in_progress → done：后端完成：review_pending 迁移 0051、默认首页迁移 0052、GET /tasks/progress、assignee=me、POST /tasks/{id}/review、POST /dashboards/{id}/reset；后端全量 946 passed（唯一失败为运行中改了新闻卡高度，重跑通过）
