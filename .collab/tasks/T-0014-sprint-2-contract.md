---
id: T-0014
title: Sprint 2 接口契约：Dashboard、Widget、布局版本、聚合接口（S2 契约）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app/schemas,services/api/app/api,services/api/openapi.json,services/api/tests/test_contract.py,apps/web/src/services/schema.d.ts,docs/api-contract.md,docs/decisions
depends_on: T-0013
created: 2026-10-30
updated: 2026-10-30
---

# T-0014 Sprint 2 接口契约：Dashboard、Widget、布局版本、聚合接口（S2 契约）

## 目标
在实现之前先确定 Sprint 2 的接口契约，让 claude 的后端（T-0015）和 codex 的前端（T-0016）可以并行开发。
按 D-0003 契约先行：后端先提交 Pydantic schema 与导出的 `services/api/openapi.json`；按 D-0005 同时提交重新生成的前端类型。

## 验收标准
- [ ] ADR `docs/decisions/2026-10-30-sprint-2-dashboard-contract.md`：Dashboard / Widget 数据模型与默认仪表盘规则；Widget Registry 协议（Sprint 2 内置的 widget type 清单、每种 type 的配置 schema、未知 type 写入时拒绝、读取时如何标记以便前端安全报错）；布局协议（12 列栅格、x/y/w/h、desktop / mobile 断点与取值范围）；乐观并发（版本号、冲突返回 409 与当前版本）；权限矩阵（viewer 读、member 写，A03）；聚合接口 DTO（规范 6.2，模块状态 ok / stale / unavailable / unauthorized，总超时）；布局导入 / 导出 JSON 格式
- [ ] `app/schemas/` 中 Sprint 2 全部请求 / 响应模型；`/api/v1` 下声明全部 Sprint 2 端点（未实现的统一返回 501，main 保持可用）
- [ ] 按契约 ADR 第 1 节清理 Sprint 1 端点在 OpenAPI 中残留的 501 声明
- [ ] `make openapi` 生成并提交 `services/api/openapi.json`；按 D-0005 运行 `npm run generate:api` 提交 `apps/web/src/services/schema.d.ts`，`npm run check:api` 与 `npm run typecheck` 通过
- [ ] `docs/api-contract.md` 更新端点表；契约测试覆盖新端点

## 背景 / 约束 / 参考
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 5.1 节第 8–9 项、第 6.1 / 6.2 节、第 8.2 节；Sprint 2 范围与 DoD 见第 13 节；验收场景 A03、A04、A05
- 分工：D-0003、D-0005；本契约合并后 T-0015 / T-0016 才开始

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 03:04 UTC · claude · 创建任务（owner: claude）
