---
id: T-0027
title: Sprint 4 后端（一）：AFFiNE 深链接收藏与资源关联、本地日历 CRUD / agenda（S4-01、S4-02 后端）
owner: claude
status: todo
priority: P1
branch:
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
- [ ] 迁移（空库 upgrade / downgrade 与 `alembic check`）：`calendar_events`、`resource_links`，以及契约确定的收藏存储方式（规范 5.1 第 10、11、13 项）
- [ ] S4-01：收藏的增删改查；URL 按 T-0025 的规则校验（HTTPS、主机与配置的 AFFiNE 地址一致、拒绝脚本 URL 与 `169.254.169.254` 等危险地址；内网开发的例外必须显式配置）；只保存手工标题与授权可得的元数据，工作台不向 AFFiNE 发任何请求（Sprint 4 DoD“无未授权文档正文抓取”）
- [ ] S4-01：收藏与 Vikunja 项目 / 任务的关联；被关联的资源删除后不留僵尸引用
- [ ] S4-02：日历事件的增删改查与时间范围（agenda）查询；时区与全天事件、并发修改按契约处理
- [ ] 所有读写限定工作空间并校验角色（viewer 只读）；写操作写入审计日志，不记录 URL 查询参数与事件正文
- [ ] 首页聚合的 `knowledge`、`calendar` 模块与 RecentDocs、CalendarAgenda 组件接真实数据；`make lint`、`make test`、`make openapi` 通过

## 背景 / 约束 / 参考
- 依赖：T-0026（契约）合并后开始
- 规范：第 4.2 节、第 5.1 节第 10 ~ 13 项、第 5.3 节（`source_url` 校验）、第 7.2 节、第 9 节；验收场景 A12
- 通用约束：AGENTS.md 第 8 节第 4、6、8 条（迁移、脱敏、workspace scope）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: claude）
