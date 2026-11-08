---
id: T-0159
title: 插拔式模块（二）：模块宿主——清单、注册表、启用表、/modules 接口、应用中心
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,tests/e2e
depends_on:
created: 2026-11-08
updated: 2026-11-08
---

# T-0159 插拔式模块（二）：模块宿主——清单、注册表、启用表、/modules 接口、应用中心

## 目标
落实方案的 P1：工作台从模块注册表读清单，统一接好接口、智能体工具、定时任务、脱敏声明，
按工作空间启用 / 停用；网页上新增“应用中心”。现有功能的行为不变。

## 验收标准
- [x] 迁移 0049 新建 `workspace_modules`（工作空间 + 模块 + 是否启用），有测试
- [x] `GET /api/v1/modules`（成员可读）、`PATCH /api/v1/modules/{key}`（只有 owner；审计；依赖检查）
- [x] 模块接口自动加启用检查：停用后返回 404 `MODULE_DISABLED`
- [x] 停用后该模块的智能体工具不再列出、不能调用；定时任务跳过该工作空间
- [x] 模块一致性检查测试：key 格式、接口前缀、工具名前缀、事件主题、小组件类型、脱敏路径、启用检查
- [x] 前端：模块注册表、启用状态 store、侧栏按启用过滤、未启用页、应用中心页（owner 可改，admin 只读）
- [x] 后端 lint / mypy / 全量测试、OpenAPI 检查；前端 lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
方案：https://claude.ai/artifact/AqLi7VYg8UaQVmpWxfibmj（落库后见 `docs/v2/MODULES_PLAN.md`）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-08 12:57 UTC · claude · 创建任务（owner: claude）
- 2026-11-08 12:58 UTC · claude · 开始任务
- 2026-11-08 14:04 UTC · claude · 越界说明：除 services/api、apps/web、tests/e2e 外，还改了 compose.yaml、.env.example、infra/caddy/routes.caddy（EMBED_ORIGINS 与 CSP frame-src，T-0161 嵌入页需要）、.github/workflows/ci-infra.yml（compose job 设 EMBED_ORIGINS 并跑 modules_browser.cjs）、Makefile 与 scripts/new_module.py（T-0158 脚手架）。没有碰别人进行中的任务（inbox 第 6 节为空）。
