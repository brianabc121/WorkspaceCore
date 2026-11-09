---
id: T-0190
title: AI 审批与管理区（A1）：审批汇总、可见筛选项与到期排序
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/schemas/ai_tools.py,services/api/app/domain/ai_tools.py,services/api/app/api/v1/ai.py,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts,docs
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0190 AI 审批与管理区（A1）：审批汇总、可见筛选项与到期排序

## 目标
AI 审批的读侧补全：汇总卡、可见范围内的筛选项、到期排序与时间范围，全部服务端全量计算。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 A1，契约第 2 节）

## 验收标准
- [x] `GET /ai/tool-executions/summary`：pending / expiring(≤180 秒) / critical_pending / exception，as_of、period、instances、tools；静态路由在 `/{id}` 之前
- [x] 列表新增 period（缺省 all）与 sort（created_desc | expires_asc），条件绑定进 cursor，换条件 422；旧调用行为不变
- [x] member / viewer 只统计自己可见的调用，原列表 pending_count 同步收紧为可见范围；跨空间无泄漏
- [x] 到期边界（=now 排除、+180 包含、+181 不算）、critical 是 pending 子集、failed/unknown 才算异常、read/direct/knowledge 不进待审批
- [x] approve / reject / TTL / 上限不改；新测试与原审批 / IM 卡片 / 安全回归通过；OpenAPI 与 schema.d.ts 机械生成

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 12:52 UTC · claude · 开始任务：人类在对话中要求按 codex 的 AI 审批与管理区方案全面落实
- 2026-11-09 13:10 UTC · claude · A1 后端完成（分支 b5-a1，未推送）：新增 GET /ai/tool-executions/summary（AiExecutionSummaryOut，四个计数按 _visible + period SQL 聚合，筛选项只按可见范围 + period；只读，不调 _expire_due）；列表加 period（缺省 all）与 sort（created_desc | expires_asc，空值最后、created_at/id 升序破同分）；新游标 v2 绑定空间、用户与可见范围、筛选、时间范围与排序，换条件 422，旧格式游标只在不带新条件时可用；列表 pending_count 收紧为可见范围（权限收紧，test_ai_tools_api 的 member 断言由 1 改为 0）。period 对四个计数都按 created_at 生效（见 summary docstring）。新测试 tests/test_ai_approval_summary.py 8 个，与 test_ai_tools_api / test_approval_cards / test_oc6_security 共 40 passed；ruff / format / mypy 通过。OpenAPI 与 schema.d.ts 单独一个提交。下一步：合并三个并行分支后统一重新导出契约并跑全量测试，再做 A2 前端。
- 2026-11-09 13:19 UTC · claude · status in_progress → done：汇总 / 筛选项 / 到期排序后端完成（e318a69）；member/viewer 的 pending_count 收紧为可见范围；合并后 test_ai_approval_summary、test_ai_tools_api、test_contract 等 69 passed
