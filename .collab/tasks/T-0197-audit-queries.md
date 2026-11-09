---
id: T-0197
title: AI 审批与管理区（C1）：审计服务端筛选、详情与安全 CSV 导出
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/audit_query.py,services/api/app/domain/audit_labels.py,services/api/app/repositories/audit.py,services/api/app/schemas/audit.py,services/api/app/api/v1/audit.py,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts,docs
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0197 AI 审批与管理区（C1）：审计服务端筛选、详情与安全 CSV 导出

## 目标
审计日志的服务端查询：时间 / 操作者 / 事件 / 资源 / 关键词筛选、单条详情、受控 CSV 导出。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 C1，契约第 5 节）

## 验收标准
- [ ] `GET /audit-logs` 加筛选（分页前生效、cursor 绑定条件）；`GET /audit-logs/{id}`；`GET /audit-logs/export`（静态路由在前）
- [ ] actor_label / action_label / resource_current_name 只在当前权限内补充；admin 不能借审计读 owner 专属密钥名
- [ ] 导出：31 天、最多 10000 行（10001 → 422 AUDIT_EXPORT_TOO_LARGE）、公式注入防护、BOM、no-store、不含 IP / ip_hash / 凭据；记 audit.export_generated
- [ ] 只追加语义不变、不加删日志；新测试与安全 / 审计回归通过；OpenAPI 与 schema.d.ts 机械生成

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 12:52 UTC · claude · 开始任务：人类在对话中要求按 codex 的 AI 审批与管理区方案全面落实
