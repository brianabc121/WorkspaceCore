---
id: T-0172
title: 二批四页（一）：知识库总览统计接口 GET /knowledge/overview（全量统计、本周更新、最近更新）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/api/v1/knowledge_base.py,services/api/app/schemas/knowledge_base.py,services/api/app/domain/knowledge_views.py,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts,docs
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0172 二批四页（一）：知识库总览统计接口 GET /knowledge/overview（全量统计、本周更新、最近更新）

## 目标
知识库首页要显示真实的全量统计（笔记、空间、关联、本周更新），不能用图谱截断后的节点数或第一页长度冒充总量。
方案见 `docs/v2/pages-redesign-2/`（Task 1）；人类在对话中要求按效果图和开发文档全面落实。

## 验收标准
- [x] 新增 `GET /api/v1/knowledge/overview`：viewer 可读；`updated_after` / `updated_before` 必填、带时区、前者小于后者，否则 422；`recent_limit` 1–20；不可见空间 404
- [x] 笔记 / 空间为可见、未删除范围的全量（超过图谱 2000 上限仍真实）；选空间时空间数为 1（含空空间）
- [x] 关联按有向 (source, target) 去重，两端都可见且未删除才计；本周更新只计窗口内的不同笔记
- [x] 最近更新按 updated_at DESC, id DESC，不带正文；读接口不产生版本、审计或智能体读用量
- [x] `test_knowledge_overview.py` 覆盖以上情况；OpenAPI 与 schema.d.ts 机械生成；不新增迁移

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 04:04 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 04:08 UTC · claude · 开始任务
- 2026-11-09 05:33 UTC · claude · status in_progress → done：GET /knowledge/overview 全量统计；后端全量测试与知识库浏览器验收通过
