---
id: T-0122
title: 项目与任务的 README：readme 笔记、创建时生成与补建、自动部分合并更新、readme_get / readme_update、页面
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0116
created: 2026-11-04
updated: 2026-11-06
---

# T-0122 项目与任务的 README：readme 笔记、创建时生成与补建、自动部分合并更新、readme_get / readme_update、页面

## 目标
项目与任务创建后自动生成 README 导览，自动部分随数据更新（方案第 9 节）。

## 验收标准
- [x] 迁移：笔记类型 `readme` 与 `task_id` 列；已有项目、任务补建
- [x] 创建时按模板生成；自动部分标记、合并更新、只在内容变化时写版本
- [x] `readme_get` / `readme_update`；`list_projects` / `get_task` / `claim_task` 等结果附带
- [x] 项目页 README 标签（默认打开）、任务抽屉 README 标签
- [x] 后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 08:46 UTC · claude · 开始任务
- 2026-11-06 09:14 UTC · claude · 实现完成，等全量后端测试：迁移 0038（类型 readme、knowledge_notes.task_id、两个唯一索引）；app/domain/readme.py（模板生成、自动部分合并更新、作者是工作台、任务换项目跟着走、改名同步、任务删除进回收站、Worker 每 20 秒刷新 + 每 30 分钟对账补建）；readme_marks.py（after_flush 收集要刷新的项目 / 任务，提交后进 Redis，1 分钟限一次）；readme_text.py（标记、合并、按部分编辑）；知识库保护（不能新建 / 改类型 / 移动 / 删除 README）；图谱默认不显示任务 README；GET /api/v1/projects/{id}/readme、/tasks/{id}/readme；MCP readme_get / readme_update（例外五，docs/AGENTS.md 第 8 节按 2026-11-04 批准的写法加上）；list_projects / get_task 附 README、claim_task 带全文、release / complete 提醒更新“进展与交接”；IM 模板与工具说明；页面：/projects/:id（默认 README）、项目卡片“打开”、任务抽屉“详情 / README”、编辑器锁定类型与知识库并说明自动部分。测试：test_readme.py 5 个、test_readme_text.py 10 个、web readme-pages 4 个。
- 2026-11-06 09:33 UTC · claude · status in_progress → done：全量后端 802 passed / 15 skipped；web 48 文件 449 个用例、lint、typecheck、build 通过。README 的真实 OpenClaw 验收（人和智能体改过之后自动部分仍然正确）在 T-0125。
