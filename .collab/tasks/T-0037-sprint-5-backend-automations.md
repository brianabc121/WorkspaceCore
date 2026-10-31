---
id: T-0037
title: Sprint 5 后端（三）：n8n 白名单触发（工作流 allowlist、参数校验、策略与审计、执行记录与错误摘要、密钥隔离）、可选 n8n Compose（S5-04）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app,services/api/migrations,services/api/tests,infra/n8n
depends_on: T-0034
created: 2026-10-31
updated: 2026-10-31
---

# T-0037 Sprint 5 后端（三）：n8n 白名单触发（工作流 allowlist、参数校验、策略与审计、执行记录与错误摘要、密钥隔离）、可选 n8n Compose（S5-04）

## 目标
实现 n8n 白名单触发的后端：工作台只管理允许的工作流、受控触发并记录执行状态，n8n 的管理凭据不下发到浏览器（规范 7.5、Sprint 5 DoD）。

## 验收标准
- [ ] n8n 集成复用 `/api/v1/integrations`（凭据只写不回显、SSRF 允许列表）；工作流白名单由管理员维护，只有白名单内的工作流能被触发
- [ ] 触发：member + 策略校验（规范第 8 节项目规则 9）；参数按白名单里声明的 schema 校验；`Idempotency-Key` 幂等；超时与未知完成状态按 Sprint 3 的做法处理，不自动重复触发
- [ ] 执行记录：状态、开始 / 结束时间、错误摘要；不保存第三方完整敏感 payload；定期从 n8n 对账执行结果
- [ ] 每次触发写审计记录（Sprint 5 DoD：触发自动化有审计记录），审计里不含参数中的敏感值
- [ ] 可选 `infra/n8n/` Compose（版本锁定、不使用 trust 认证或浮动 latest）；部署文档写明白名单配置与密钥隔离
- [ ] 数据表变化提供 Alembic 迁移与测试；ruff / mypy / pytest 通过

## 背景 / 约束 / 参考
- 依赖：T-0034 合并后开始
- 规范：第 7.5 节、第 9 节、第 10 节、第 8 节项目规则 7 / 9；S5-04

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: claude）
