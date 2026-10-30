---
id: T-0026
title: Sprint 4 接口契约：知识入口与收藏、本地日历、跨资源搜索、GitHub 只读与首页模块（S4 契约）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app/schemas,services/api/app/api,services/api/openapi.json,services/api/tests/test_contract.py,apps/web/src/services/schema.d.ts,docs/api-contract.md,docs/decisions
depends_on: T-0025
created: 2026-10-30
updated: 2026-10-30
---

# T-0026 Sprint 4 接口契约：知识入口与收藏、本地日历、跨资源搜索、GitHub 只读与首页模块（S4 契约）

## 目标
在实现之前先确定 Sprint 4 的接口契约，让 claude 的后端（T-0027 ~ T-0029）和 codex 的前端（T-0030、T-0031）可以并行开发。
按 D-0003 契约先行；按 D-0005 同时提交重新生成的前端类型。

## 验收标准
- [ ] ADR `docs/decisions/` 下的 Sprint 4 契约——知识入口：`GET /api/v1/knowledge/resources`、`POST /api/v1/knowledge/bookmarks` 及修改 / 删除；字段与深链接校验规则依据 T-0025；收藏与项目 / 任务的关联（`resource_links`）；AFFiNE 外部地址在哪里配置
- [ ] 本地日历：`GET/POST /api/v1/calendar/events`、`PATCH/DELETE /api/v1/calendar/events/{id}`；时间范围（agenda）查询、全天事件、时区、并发修改语义；`rrule` 在 V1 的处理方式（规范 5.1 第 13 项：只读或不实现，避免半成品）
- [ ] GitHub 只读：GitHub 集成复用 `/api/v1/integrations`（provider `github`，凭证只写不回显）；`/api/v1/developer/repos`、`/developer/issues` 以及 PR、Actions 最近运行的端点；分页与过滤；限流与 Token 失效时的响应（不伪造空列表，规范第 6 节、A13、A17）
- [ ] 跨资源搜索 `GET /api/v1/search?q=`：可搜的资源类型、结果字段（来源、`source_url`、类型、更新时间）、分页、排序与中文相关性的限制说明（规范 5.3）；只返回当前用户在当前工作空间可见的资源（A11）；源对象删除后的表现（A10）
- [ ] 首页聚合新增 `github`、`calendar`、`knowledge` 等模块的状态（ok / stale / unavailable / unauthorized / not_configured），以及 CalendarAgenda、GitHubActivity、RecentDocs 组件类型的配置 schema（规范 8.2）
- [ ] `app/schemas/` 中全部请求 / 响应模型；`/api/v1` 下声明全部 Sprint 4 端点（未实现的返回 501，不写进 OpenAPI）；`make openapi` 提交 `openapi.json`；按 D-0005 提交 `schema.d.ts`；`docs/api-contract.md` 更新；契约测试覆盖新端点

## 背景 / 约束 / 参考
- 依赖：T-0025（版本核验 ADR）合并后开始
- 规范：第 4.2 节、第 5.1 节第 10 ~ 13 项、第 5.3 节（搜索设计）、第 6.1 ~ 6.4 节、第 7.2 / 7.3 节、第 8.2 节；验收场景 A10 ~ A13、A17
- 分工：D-0003、D-0005；本契约合并后 T-0027 与 T-0030 / T-0031 才开始

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: claude）
