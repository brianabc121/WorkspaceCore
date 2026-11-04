---
id: T-0110
title: 项目成员与项目群：新建项目勾选智能体、项目页成员区、IM 项目群（建群、重试、同步成员、归档与删除）、旧项目补建群（迁移 0028）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0110 项目成员与项目群：新建项目勾选智能体、项目页成员区、IM 项目群（建群、重试、同步成员、归档与删除）、旧项目补建群（迁移 0028）

## 目标
按方案第 2.1、2.2 节：项目有智能体成员（沿用 project_agents，即项目知识库读写）；owner / admin 新建项目时可以勾选智能体，并自动建 IM 项目群（群主是创建人）；项目页能管理成员，成员变化同步到项目群；OpenIM 不可用时稍后重试；旧项目可以补建群。

## 验收标准
- [x] 迁移 0028：`im_groups.project_id`（每个项目最多一个未解散的群）、项目的建群状态；可降级，`alembic check` 通过
- [x] 新建项目可带智能体与“建项目群”（只限 owner / admin）；项目成员接口（列出、加入、移出）同步项目知识库权限与项目群成员
- [x] 项目群：群名是项目名、公告是说明前 200 字；建群幂等（固定群 ID）；OpenIM 不可用时项目照常建好，Worker 每分钟重试，也能手动重试；改名 / 改说明 / 归档同步到群；删除项目解散群
- [x] 前端：新建项目表单的“加入的智能体”和“建项目群”，项目卡片的成员与项目群状态、成员管理、建群 / 重试
- [x] 后端 ruff / mypy / pytest；前端 lint / typecheck / test / build / check:api；api-contract、data-dictionary

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 09:16 UTC · claude · 开始任务
- 2026-11-04 09:40 UTC · claude · 实现完成：迁移 0028（projects.im_group_status / owner / error，im_groups.project_id + 部分唯一索引）；app/domain/project_groups.py（成员 = project_agents，建群 / 同步 / 解散，群 ID 由项目与第几个群推出，锁行 SKIP LOCKED，失败只回滚保存点）；项目接口 instance_ids / im_group、成员 GET / PUT / DELETE、POST im-group；改名 / 说明 / 归档同步群信息，删除先解散；知识库页改参与者也标记同步；Worker 每分钟 sync_project_groups；任务执行者候选带实例说明。前端：新建项目勾选智能体与“同时建 IM 项目群”（owner / admin），项目卡片显示智能体与项目群状态，成员弹窗加入 / 移出 / 建群 / 重试 / 打开项目群。与方案的偏差：在 IM 里直接拉进项目群的智能体，下次同步时会被移出（项目群成员以项目页为准）；删除项目时解散失败只记日志，群留在 IM 里由群主处理。
