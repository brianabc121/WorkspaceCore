---
id: T-0027
title: Sprint 4 后端（一）：AFFiNE 深链接收藏与资源关联、本地日历 CRUD / agenda（S4-01、S4-02 后端）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0026
created: 2026-10-30
updated: 2026-10-30
---

# T-0027 Sprint 4 后端（一）：AFFiNE 深链接收藏与资源关联、本地日历 CRUD / agenda（S4-01、S4-02 后端）

## 目标
按 T-0026 的契约实现知识入口与本地日历的后端：AFFiNE 深链接收藏（只保存书签元数据，不抓取正文）、收藏与项目 / 任务的关联，
以及本地日历的增删改查与 agenda 查询。这两部分只用本地数据，不依赖第三方实例。

## 验收标准
- [x] 迁移（空库 upgrade / downgrade 与 `alembic check`）：`calendar_events`、`resource_links`，以及契约确定的收藏存储方式（规范 5.1 第 10、11、13 项）
- [x] S4-01：收藏的增删改查；URL 按 T-0025 的规则校验（HTTPS、主机与配置的 AFFiNE 地址一致、拒绝脚本 URL 与 `169.254.169.254` 等危险地址；内网开发的例外必须显式配置）；只保存手工标题与授权可得的元数据，工作台不向 AFFiNE 发任何请求（Sprint 4 DoD“无未授权文档正文抓取”）
- [x] S4-01：收藏与 Vikunja 项目 / 任务的关联；被关联的资源删除后不留僵尸引用
- [x] S4-02：日历事件的增删改查与时间范围（agenda）查询；时区与全天事件、并发修改按契约处理
- [x] 所有读写限定工作空间并校验角色（viewer 只读）；写操作写入审计日志，不记录 URL 查询参数与事件正文
- [x] 首页聚合的 `knowledge`、`calendar` 模块与 RecentDocs、CalendarAgenda 组件接真实数据；`make lint`、`make test`、`make openapi` 通过

## 背景 / 约束 / 参考
- 依赖：T-0026（契约）合并后开始
- 规范：第 4.2 节、第 5.1 节第 10 ~ 13 项、第 5.3 节（`source_url` 校验）、第 7.2 节、第 9 节；验收场景 A12
- 通用约束：AGENTS.md 第 8 节第 4、6、8 条（迁移、脱敏、workspace scope）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 17:27 UTC · claude · 开始任务
- 2026-10-30 17:42 UTC · claude · 完成：迁移 0007（knowledge_sources、bookmarks、resource_links、calendar_events；空库 upgrade / downgrade / alembic check 通过）；app/domain/deeplinks.py 按核验 ADR 第 8.3 节做纯字符串校验与规范化（不访问 AFFiNE），失败时 422 带 details.reason；KnowledgeService：知识来源（每个工作空间 1 个、改地址后链接随之改、删除级联）、收藏（可见性过滤在每条查询里，别人的私有收藏 404；去重与部分唯一索引一致，并发冲突在 savepoint 里转成 409 BOOKMARK_EXISTS；只有创建者能改可见性）、关联（只接受本工作空间未删除的项目 / 任务，tombstone 后不再出现，A10）；CalendarService：定时 / 全天事件统一存 UTC、区间重叠查询（≤ 92 天）、merge-patch 后再校验、乐观并发；首页增加 calendar、knowledge 模块；审计不含标题、备注、URL、事件内容。测试：test_deeplinks.py 24 项、test_knowledge_api.py 20 项、test_calendar_api.py 8 项；全量 pytest 290 passed、4 skipped。OpenAPI 未变化。越出 paths：无（docs/data-dictionary.md、docs/api-contract.md、docs/deployment.md 的更新属于本任务的文档）。
- 2026-10-30 17:42 UTC · claude · status in_progress → done：收藏与本地日历的后端完成；下一步 T-0028（GitHub 只读 Connector）
