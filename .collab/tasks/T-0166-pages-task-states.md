---
id: T-0166
title: 四页改造（一）：任务状态闭环（完成轮次、个人已查看、明确阻塞）与项目统计契约；首页改用同一状态
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web/src/services/schema.d.ts,apps/web/src/widgets/home,apps/web/src/features/tasks/TaskClaimPanel.vue,docs
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0166 四页改造（一）：任务状态闭环（完成轮次、个人已查看、明确阻塞）与项目统计契约；首页改用同一状态

## 目标
任务页、项目卡片、首页需要同一套五种互斥状态（待办 / 进行中 / 已完成待查看 / 阻塞 / 已完成），且“查看”按人隔离、“阻塞”必须是人写明的原因
（规范第 4.2、4.3 节）。首页改版（T-0163）的 `review_pending`、“有阻塞 Issue 就算阻塞”、“开始过就算进行中”不满足这些要求。

## 验收标准
- [x] 迁移 0053：`completion_seq`、`blocked_at/blocked_reason/blocked_by`、`task_result_reads`（复合外键防跨空间）、动态 `blocked/unblocked`；upgrade / downgrade / 再 upgrade 有测试
- [x] `app/domain/task_state.py` 集中判定（SQL 与 Python 同一规则）；读一页任务的查询次数不随条数增加（测试比较 5 条与 50 条）
- [x] `GET /tasks` 的 `work_state` / `assignee_user_id` / `assignee_instance_id`，`GET /tasks/summary`，`PUT /tasks/{id}/result-view`（409 COMPLETION_CHANGED），`PATCH /tasks/{id}/block`（Idempotency-Key、version、409 TASK_DONE）
- [x] 所有完成入口（人勾完成、智能体 `complete_task`、审批通过的完成）加轮次、结束执行段、清阻塞；人自己完成记自己已查看；打回不加轮次
- [x] 阻塞的任务不能被领取或开始执行（TASK_BLOCKED）；两个用户的待查看互不影响；迁移前完成的任务不变成待查看
- [x] `ProjectOut.task_states`、`last_activity_at`，`GET /projects` 的 `attention_only` / `archived_only` / `sort=updated`，`GET /projects/summary`、`GET /projects/activity`
- [x] 首页 `/tasks/progress` 与工作概况改用同一状态；删除 `POST /tasks/{id}/review`
- [x] openapi.json 与 schema.d.ts 重新生成；文档：api-contract、data-dictionary、threat-model、HOME_PLAN、决策 `docs/decisions/2026-11-09-task-work-states.md`
- [ ] 后端全量测试与 CI 通过

## 背景 / 约束 / 参考
方案 `docs/v2/pages-redesign/`（规范 `docs/superpowers/specs/2026-11-08-workspace-four-pages-design.md`，计划 `docs/superpowers/plans/2026-11-08-workspace-four-pages-redesign.md`），效果图在 `assets/`。人类 2026-11-09 在对话中要求「按照效果图和设计文档进行全部落实」。

偏差：迁移 0053 删除了 T-0163 刚加的 `review_pending`（方案写“迁移只能加列”），信息无损转成 `completion_seq = 1`，downgrade 回填；理由写在决策记录里。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 01:32 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 02:29 UTC · claude · 开始任务：按四页改造方案实施（本分支）
