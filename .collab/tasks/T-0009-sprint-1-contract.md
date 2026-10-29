---
id: T-0009
title: Sprint 1 接口契约：认证、会话、工作空间、设置、审计（S1 契约）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/schemas, services/api/app/api, services/api/openapi.json, services/api/scripts, services/api/tests/test_contract.py, Makefile, .github/workflows/ci-api.yml, docs/api-contract.md
depends_on:
created: 2026-10-29
updated: 2026-10-29
---

# T-0009 Sprint 1 接口契约：认证、会话、工作空间、设置、审计（S1 契约）

## 目标
在实现之前先确定 Sprint 1 的接口契约，让 codex 的前端（T-0012）和 claude 的后端（T-0010、T-0011）可以并行开发。
按 D-0003：后端先提交 Pydantic schema 与导出的 `services/api/openapi.json`，前端据此生成 TypeScript 类型。

## 验收标准
- [x] ADR `docs/decisions/2026-10-30-sprint-1-auth-contract.md`：Session Cookie 名称与属性、CSRF 方案（含 Origin 校验）、工作空间上下文的传递方式、角色矩阵、错误码、首次安装的保护方式、登录限流、会话生命周期
- [x] `app/schemas/` 中 Sprint 1 全部请求 / 响应模型；`/api/v1` 下声明全部 Sprint 1 端点（未实现的统一返回 501 `NOT_IMPLEMENTED`，main 保持可用）
- [x] `make openapi` 生成并提交 `services/api/openapi.json`；ci-api 检查它与代码一致
- [x] `docs/api-contract.md` 更新端点表
- [x] 测试：OpenAPI 导出一致；未实现端点返回统一错误格式的 501

## 背景 / 约束 / 参考
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 5.1、6、9 节；Sprint 1 范围与 DoD 见第 13 节
- 分工：D-0003；本契约合并后 T-0010 / T-0011 / T-0012 才开始

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 22:52 UTC · claude · 创建任务（owner: claude）
- 2026-10-29 22:53 UTC · claude · 开始任务：人类批准进入 Sprint 1；按 D-0003 先提交接口契约
- 2026-10-29 22:59 UTC · claude · 契约完成：13 个端点已声明（未实现返回 501），openapi.json 由 make openapi 生成且 ci-api 检查一致；ADR docs/decisions/2026-10-30-sprint-1-auth-contract.md；ruff / mypy / pytest 通过；本 PR 同时包含 Sprint 1 规划（T-0009~T-0013）与 D-0001/D-0002 批准记录
- 2026-10-29 22:59 UTC · claude · status in_progress → done：验收标准全部满足；T-0010 与 T-0012 可在合并后开始
