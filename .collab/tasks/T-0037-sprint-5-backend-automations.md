---
id: T-0037
title: Sprint 5 后端（三）：n8n 白名单触发（工作流 allowlist、参数校验、策略与审计、执行记录与错误摘要、密钥隔离）、可选 n8n Compose（S5-04）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app,services/api/migrations,services/api/tests,infra/n8n
depends_on: T-0034
created: 2026-10-31
updated: 2026-10-31
---

# T-0037 Sprint 5 后端（三）：n8n 白名单触发（工作流 allowlist、参数校验、策略与审计、执行记录与错误摘要、密钥隔离）、可选 n8n Compose（S5-04）

## 目标
实现 n8n 白名单触发的后端：工作台只管理允许的工作流、受控触发并记录执行状态，n8n 的管理凭据不下发到浏览器（规范 7.5、Sprint 5 DoD）。

## 验收标准
- [x] n8n 集成复用 `/api/v1/integrations`（凭据只写不回显、SSRF 允许列表）；工作流白名单由管理员维护，只有白名单内的工作流能被触发
- [x] 触发：member + 策略校验（规范第 8 节项目规则 9）；参数按白名单里声明的 schema 校验；`Idempotency-Key` 幂等；超时与未知完成状态按 Sprint 3 的做法处理，不自动重复触发
- [x] 执行记录：状态、开始 / 结束时间、错误摘要；不保存第三方完整敏感 payload；定期从 n8n 对账执行结果
- [x] 每次触发写审计记录（Sprint 5 DoD：触发自动化有审计记录），审计里不含参数中的敏感值
- [x] 可选 `infra/n8n/` Compose（版本锁定、不使用 trust 认证或浮动 latest）；部署文档写明白名单配置与密钥隔离
- [x] 数据表变化提供 Alembic 迁移与测试；ruff / mypy / pytest 通过

## 背景 / 约束 / 参考
- 依赖：T-0034 合并后开始
- 规范：第 7.5 节、第 9 节、第 10 节、第 8 节项目规则 7 / 9；S5-04

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 03:08 UTC · claude · 开始任务
- 2026-10-31 03:28 UTC · claude · 实现完成。与验收标准第 1 条的偏差：按已合并的 T-0034 契约（第 1、6.1 节），n8n 连接放在 /api/v1/automations/connection 而不是 /api/v1/integrations（不扩大前端按枚举索引的 provider）；凭据只写不回显、SSRF 允许列表与 Vikunja 相同。实现：迁移 0010（automation_connections / automations（软删除）/ automation_runs，执行记录不存参数）；app/integrations/n8n.py；app/domain/automations.py（连接、白名单、受控触发：Idempotency-Key + 策略校验 + 先写 unknown 记录再调用 n8n，明确拒绝不留记录，结果未知保持 unknown 不重发；对账：按触发请求 traceparent 的 trace-id 在执行详情里精确对应，失败时只读 redactExecutionData=true 后的 lastNodeExecuted 与 redactedError.type；15 分钟找不到记为无法确认；结果通知触发者；Key 失效通知管理员）；Worker 每分钟 reconcile_automations；运维页 n8n 状态与首页 automations 模块；infra/n8n/compose.yaml 与 make n8n-up / n8n-down / n8n-logs。实测：新 ADR docs/decisions/2026-10-31-n8n-trigger-and-execution-verification.md（traceparent 原样出现在执行详情的 tracingContext；遮蔽后只剩节点名与错误类型；完成初始设置后读不到实例版本，测试连接给出警告）；make n8n-up + 工作台全栈走通连接、登记、触发、重放、对账、错误摘要、通知、审计，响应 / 通知 / 审计里没有密钥与参数值。pytest 385 passed, 7 skipped。越界修改（claude 负责的基础设施）：compose.yaml（INTEGRATION_ALLOWED_HOSTS 默认加 n8n）、.env.example（N8N_* 变量）、Makefile（n8n-up/down/logs）、docs/deployment.md 第 12.4 节、docs/api-contract.md、docs/decisions/ 新 ADR。
- 2026-10-31 03:28 UTC · claude · status in_progress → done：n8n 白名单触发后端完成
