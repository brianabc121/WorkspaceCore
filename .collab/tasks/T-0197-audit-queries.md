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
- 2026-11-09 13:22 UTC · claude · C1 后端完成：AuditQueryService（服务端筛选、HMAC 签名并绑定工作空间与条件的 keyset 游标、单条详情）、audit_labels（中文事件名、成员显示名、按当前权限批量补当前名称，密钥 / OpenClaw 服务器名只给 owner）、GET /audit-logs 加筛选、GET /audit-logs/{id}、GET /audit-logs/export（31 天、10000 行、超出 422 AUDIT_EXPORT_TOO_LARGE、BOM、公式注入防护、no-store、去 IP / 凭据键、记 audit.export_generated 安全摘要）。无迁移（沿用 workspace_id, created_at, id 索引）。新测试 test_audit_queries / test_audit_export 与工作空间、安全矩阵、契约回归通过；OpenAPI 与 schema.d.ts 已重新导出。下一步：C2 前端审计页。
