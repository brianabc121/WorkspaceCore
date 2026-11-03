---
id: T-0098
title: 知识库后端：三类知识库、笔记与版本、链接、回收站、接口；智能体 knowledge_* 工具与保护（K-1 + K-4）
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web/src/services/schema.d.ts,apps/web/src/features/ai,apps/web/src/features/search,docs
depends_on: T-0097
created: 2026-11-03
updated: 2026-11-03
---

# T-0098 知识库后端：三类知识库、笔记与版本、链接、回收站、接口；智能体 knowledge_* 工具与保护（K-1 + K-4）

## 目标
按 `docs/v2/KNOWLEDGE_PLAN.md` 实现知识库后端：
- K-1：三类知识库（公共 / 项目 / 智能体）、`project_agents`、笔记与版本、链接与反向链接、回收站、检索、图谱接口；
- K-4：智能体的 `knowledge_*` MCP 工具（读直接执行，写不审核），以及 3.8 的保护：权限校验、版本与撤销、回收站、密钥拦截、限流与自动暂停、来源标记、冲突提醒、变更记录。

## 验收标准
- [ ] 迁移 0024（新表与检查约束、搜索触发器、补建知识库）及升降级测试
- [ ] 人用的接口：知识库、参与的智能体、笔记、版本与撤销、回收站、图谱、变更记录；按角色校验，跨工作空间隔离
- [ ] MCP 工具 knowledge_search / get / list / brief / write / delete：
  - 风险等级 knowledge，不进审批；
  - 幂等，写入带 base_version；
  - 工具描述不超过 1200 字符
- [ ] 保护：密钥拦截（原文不入库、不进日志）、每小时写 20 次 / 删 5 篇、超过删除上限自动暂停并通知、单篇 20 KB、简报只有人能改
- [ ] 人修改或撤销智能体写的内容时，写回它的《收到的纠正》
- [ ] ruff / mypy / pytest 通过；OpenAPI 与 `apps/web` 的 `schema.d.ts` 同步；web lint / typecheck / test 通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/KNOWLEDGE_PLAN.md`（已批准）；核验：`docs/decisions/2026-11-03-knowledge-k0-verification.md`
- 效果图：https://claude.ai/artifact/Lib1PcRket1qH18Bo5YwBv

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 20:45 UTC · claude · 创建任务（owner: claude）
