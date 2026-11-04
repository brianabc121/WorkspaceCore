---
id: T-0122
title: 项目与任务的 README：readme 笔记、创建时生成与补建、自动部分合并更新、readme_get / readme_update、页面
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0116
created: 2026-11-04
updated: 2026-11-04
---

# T-0122 项目与任务的 README：readme 笔记、创建时生成与补建、自动部分合并更新、readme_get / readme_update、页面

## 目标
项目与任务创建后自动生成 README 导览，自动部分随数据更新（方案第 9 节）。

## 验收标准
- [ ] 迁移：笔记类型 `readme` 与 `task_id` 列；已有项目、任务补建
- [ ] 创建时按模板生成；自动部分标记、合并更新、只在内容变化时写版本
- [ ] `readme_get` / `readme_update`；`list_projects` / `get_task` / `claim_task` 等结果附带
- [ ] 项目页 README 标签（默认打开）、任务抽屉 README 标签
- [ ] 后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
