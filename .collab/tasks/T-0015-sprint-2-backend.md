---
id: T-0015
title: Sprint 2 后端：Dashboard/Widget 迁移与读写、Widget 配置校验、布局保存与版本冲突、聚合接口（S2-01、S2-03 ~ S2-05 后端）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0014
created: 2026-10-30
updated: 2026-10-30
---

# T-0015 Sprint 2 后端：Dashboard/Widget 迁移与读写、Widget 配置校验、布局保存与版本冲突、聚合接口（S2-01、S2-03 ~ S2-05 后端）

## 目标
按 T-0014 的契约实现 Sprint 2 后端：仪表盘与组件的存储和读写、组件配置校验、布局保存与版本冲突、首页聚合接口。

## 验收标准
- [x] S2-01：迁移 `dashboards`、`widgets`（规范 5.1 第 8–9 项，`workspace` schema、UUID 主键、version 字段）及 repository；空库 upgrade / downgrade 与 `alembic check`
- [x] S2-01：仪表盘与组件的读写 API；所有查询限定工作空间，非成员 404，viewer 只读、member 及以上可写（A03：viewer 修改 Widget 返回 403）
- [x] S2-03：服务端 Widget Registry：未知 widget type 拒绝写入，配置不符合该 type 的 schema 返回 422；不会因库里存在未知 type 而让整个仪表盘接口失败
- [x] S2-04：布局保存与乐观并发：版本不匹配返回 409 与当前版本；保存后重新读取布局一致、版本号递增（A04）
- [x] S2-05：`GET /api/v1/dashboard/overview` 按契约返回各模块状态；单个模块失败只把该模块标为 unavailable，不让整个接口失败；有总耗时上限
- [x] 仪表盘与组件的写操作写入审计日志（沿用 T-0011 的规则）
- [x] 测试覆盖以上各项；`make lint`、`make test`、`make openapi` 检查通过；依赖变化写入 backend-stack ADR

## 背景 / 约束 / 参考
- 依赖：T-0014（契约）合并后开始
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 5.1、6.1、6.2、6.4 节；Sprint 2 范围与 DoD 见第 13 节
- 不改 `apps/web/**`（D-0005 的 `schema.d.ts` 生成除外）；不勾选 `docs/TASKS.md`（由 T-0017 统一勾选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 03:04 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 03:17 UTC · claude · 开始任务
- 2026-10-30 03:29 UTC · claude · 完成实现：迁移 0004（dashboards / widgets，部分唯一索引保证每个工作空间恰好一个默认仪表盘，为已有工作空间补建“首页”与 4 个入门组件）；首次安装同时创建默认仪表盘；8 个端点全部实现（viewer 读 / member 写，别的工作空间资源 404）；Registry 校验在 schema 层；布局保存要求列出全部组件、乐观并发 409 VERSION_CONFLICT（带 current_version），增删组件让仪表盘版本 +1；切换默认仪表盘先按 ID 顺序锁住全部仪表盘，避免并发切换撞唯一索引；数量上限 409 LIMIT_EXCEEDED；首页聚合的模块 provider 并发执行、各自超时 + 总上限（DASHBOARD_OVERVIEW_TIMEOUT_SECONDS，默认 3 秒），失败 / 超时只标该模块 unavailable；写操作审计（不记录配置内容）。测试发现并修复：模块失败时日志字段名 module 与 LogRecord 冲突导致 500。后端 93 passed，openapi.json 未变；本地全栈经 Caddy 验证，并在全新空库上回归 Sprint 1 全栈验收（32 项通过）。越出 paths 的文件：docs 下的数据字典、api-contract、契约 ADR（补一句兜底规则）、威胁模型。
- 2026-10-30 03:29 UTC · claude · status in_progress → done：验收标准全部满足；前端 T-0016 可直接对接真实后端，联调与 DoD 验收在 T-0017
- 2026-10-30 03:36 UTC · claude · 处理 Codex 审查（PR #21）：P2 并发创建可能超出 20 个仪表盘上限——创建前对工作空间行加 FOR NO KEY UPDATE 锁再计数（切换默认仪表盘也改用这把锁，去掉逐个锁仪表盘的写法）；P2 删除与修改同一组件并发时修改返回 500——拿到仪表盘锁后重新读取组件，不存在则 404（并发重复删除也只生效一次）。三条回归测试用 monkeypatch 制造并发窗口，修复前分别得到 [201, 201]、500、(204, 204)，修复后通过。
