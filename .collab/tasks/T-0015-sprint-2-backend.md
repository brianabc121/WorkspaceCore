---
id: T-0015
title: Sprint 2 后端：Dashboard/Widget 迁移与读写、Widget 配置校验、布局保存与版本冲突、聚合接口（S2-01、S2-03 ~ S2-05 后端）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0014
created: 2026-10-30
updated: 2026-10-30
---

# T-0015 Sprint 2 后端：Dashboard/Widget 迁移与读写、Widget 配置校验、布局保存与版本冲突、聚合接口（S2-01、S2-03 ~ S2-05 后端）

## 目标
按 T-0014 的契约实现 Sprint 2 后端：仪表盘与组件的存储和读写、组件配置校验、布局保存与版本冲突、首页聚合接口。

## 验收标准
- [ ] S2-01：迁移 `dashboards`、`widgets`（规范 5.1 第 8–9 项，`workspace` schema、UUID 主键、version 字段）及 repository；空库 upgrade / downgrade 与 `alembic check`
- [ ] S2-01：仪表盘与组件的读写 API；所有查询限定工作空间，非成员 404，viewer 只读、member 及以上可写（A03：viewer 修改 Widget 返回 403）
- [ ] S2-03：服务端 Widget Registry：未知 widget type 拒绝写入，配置不符合该 type 的 schema 返回 422；不会因库里存在未知 type 而让整个仪表盘接口失败
- [ ] S2-04：布局保存与乐观并发：版本不匹配返回 409 与当前版本；保存后重新读取布局一致、版本号递增（A04）
- [ ] S2-05：`GET /api/v1/dashboard/overview` 按契约返回各模块状态；单个模块失败只把该模块标为 unavailable，不让整个接口失败；有总耗时上限
- [ ] 仪表盘与组件的写操作写入审计日志（沿用 T-0011 的规则）
- [ ] 测试覆盖以上各项；`make lint`、`make test`、`make openapi` 检查通过；依赖变化写入 backend-stack ADR

## 背景 / 约束 / 参考
- 依赖：T-0014（契约）合并后开始
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 5.1、6.1、6.2、6.4 节；Sprint 2 范围与 DoD 见第 13 节
- 不改 `apps/web/**`（D-0005 的 `schema.d.ts` 生成除外）；不勾选 `docs/TASKS.md`（由 T-0017 统一勾选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 03:04 UTC · claude · 创建任务（owner: claude）
