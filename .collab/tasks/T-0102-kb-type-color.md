---
id: T-0102
title: 知识图谱按类型着色，取消“人已确认”（迁移 0025）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs
depends_on: T-0101
created: 2026-11-04
updated: 2026-11-04
---

# T-0102 知识图谱按类型着色，取消“人已确认”（迁移 0025）

## 目标
按方案第 3 节：知识图谱按类型分 6 组着色（默认），可按单个类型筛选；取消“人已确认”这个功能（接口、界面、工具结果、检索加权、数据库列）。

## 验收标准
- [x] 图谱着色选项为 类型（默认）/ 知识库 / 作者；6 组颜色在亮暗两种底色上跑过 dataviz 配色检查；图例按组列出类型；可按单个类型筛选；悬停与列表视图显示具体类型
- [x] 删除 `POST /notes/{id}/confirm`、`confirmed_*` 字段、`origin.confirmed`、图谱“只看人已确认”、笔记页的确认按钮与标记、检索里的确认加权；工具描述同步修改
- [x] 迁移 0025 删除 `knowledge_notes.confirmed_by` / `confirmed_at`，可降级（加回空列），`alembic check` 通过
- [ ] 后端 ruff / mypy / pytest，前端 lint / typecheck / test / build / check:api 通过；浏览器截图检查亮暗

## 背景 / 约束 / 参考
- 方案：`docs/v2/TIMELINE_PLAN.md`（人类 2026-11-04 批准：“之后按照推荐的来，给我全面落实”）
- 核验：`docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md`
- 效果图：https://claude.ai/artifact/CHuDA9DsafbAudwxB6A61y

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 01:56 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 02:21 UTC · claude · 开始任务
