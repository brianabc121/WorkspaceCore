---
id: T-0082
title: 彻底移除 Vikunja：项目与任务改由工作台自己管理（单人版；转换已同步数据；升级时停掉旧 Vikunja 并保留数据卷）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,scripts,.github/workflows,infra,docs,tests,Makefile,.env.example,compose.yaml,compose.https.yaml,README.md,.gitignore
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0082 彻底移除 Vikunja：项目与任务改由工作台自己管理（单人版；转换已同步数据；升级时停掉旧 Vikunja 并保留数据卷）

## 目标
人类要求（2026-11-02）：「彻底移除掉 Vikunja，改为通过 workspace 进行项目创建、管理。只需要做单人版本即可」。
人类在对话中的选择：
- 已同步的数据：一次性转成工作台自己的数据（项目、任务 ID 不变，收藏关联、搜索照常）；
- 去掉多人协作功能：没有负责人、指派、评论、按项目共享；角色不变（viewer 只读，member 及以上可写）；
- 旧的 Vikunja：`deploy.sh upgrade` 停掉并删除容器和 vikunja 子域名站点，**保留数据卷**，打印手动删除的命令。

## 验收标准
- [x] 迁移 0020：新建 `projects` / `tasks`，把 Vikunja 投影转成工作台数据（ID 不变），收藏关联、搜索索引改指向新表；
      删除 Vikunja 集成、Webhook 收件箱、`webhook_secret_ref`、Vikunja 登录账号；可降级（降级丢失新数据，已写明）
- [x] 后端：项目增删改查（`POST/PATCH/DELETE /projects`）、任务增删改查改为本地读写（沿用 Idempotency-Key 与 version 并发控制）；
      首页、搜索、知识关联、AI 只读工具、SSE、运维页都改用本地数据；Vikunja 连接器、Webhook、投影同步、接入命令全部移除
- [x] 前端：项目页可新建 / 改名 / 归档 / 删除项目，任务页、任务抽屉、日历、首页组件改用本地数据；所有 Vikunja 文案、
      集成表单、登录账号卡片、“在 Vikunja 中打开”链接移除
- [x] `deploy.sh`：不再接入 Vikunja；`upgrade` 停掉并删除旧 Vikunja 容器与站点，保留数据卷并打印删除命令
- [x] CI：去掉真实 Vikunja 的验收，换成本地项目 / 任务的验收；后端、前端测试，契约（OpenAPI、schema.d.ts）更新
- [x] 文档：ADR `docs/decisions/2026-11-02-remove-vikunja.md`、数据字典、API 契约、部署手册
- [x] 删除 `infra/vikunja`、Makefile 的 vikunja 目标、`.env.example` 的 VIKUNJA_*；AGENTS.md 第 8 节与规范里“Vikunja 是任务主源”改为工作台自己管理（同一个 PR）

## 背景 / 约束 / 参考
- AGENTS.md 第 8 节与规范写着“Vikunja 是任务主源”，人类在本次对话里明确要求移除，以人类指令为准，规范文字在同一个 PR 里更新。
- 迁移 0020 删除了已有数据（Vikunja 集成、Webhook 收件箱），按 D-0004 第 3 节这个 PR 由人类确认后再合并。
- 原 Vikunja 相关 ADR（2026-10-29 Sprint 3 系列）保留作为历史，新 ADR 说明取代关系。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 21:04 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 21:04 UTC · claude · 开始任务
- 2026-11-02 21:52 UTC · claude · 实现完成：迁移 0020（转换已同步数据、ID 不变；收藏关联与搜索改指向新表；删除 Vikunja 集成、webhook_inbox）；项目 / 任务接口改为本地读写（项目增删改查、任务幂等在同一事务）；前端项目管理页与任务标签；deploy.sh upgrade 停用旧 Vikunja（保留数据卷）；CI 换成本地项目 / 任务验收；删除 infra/vikunja。后端 pytest、前端 lint/typecheck/test/build、迁移升降级实测通过。迁移删除已有数据，按 D-0004 第 3 节等人类确认后合并。
- 2026-11-02 22:07 UTC · claude · 人类在对话中确认（2026-11-02）：「CI 全绿就合并吧」——同意合并含删除数据迁移的 PR #123（D-0004 第 3 节）。修复 CI：sprint3_acceptance.py 搜索参数里的中文没有 URL 编码；沙箱全栈（独立项目 t82）实测 sprint2_browser、sprint3_acceptance、sprint3_browser 全部通过。
