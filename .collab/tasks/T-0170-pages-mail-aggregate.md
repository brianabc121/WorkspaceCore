---
id: T-0170
title: 四页改造（五）：多邮箱聚合读取后端（GET /mail/messages、逐账号状态、无内容游标）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/mail_aggregate.py,services/api/app/domain/mail.py,services/api/app/schemas/mail.py,services/api/app/api/v1/mail.py,services/api/tests
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0170 四页改造（五）：多邮箱聚合读取后端（GET /mail/messages、逐账号状态、无内容游标）

## 目标
邮件页默认显示全部已连接邮箱的邮件（规范第 7 节），需要一个按账号聚合、逐账号报告状态、单个邮箱失败不影响其他邮箱的读取接口。

## 验收标准
- [x] `GET /api/v1/mail/messages`（admin）：`account_id` 可选、`q`、`label`、`cursor`、`per_account_limit`；每条带 `account_id`；`accounts[]` 逐账号状态与未读数（没有依据时为 null）；`outcome` / `partial`
- [x] 游标只存在 Redis 里且不含邮件内容；范围、查询、用户或空间变了 `422 MAIL_CURSOR_MISMATCH`，过期 `422 MAIL_CURSOR_EXPIRED`；全部邮箱模式下只接受系统标签
- [x] 并发与超时有上限（3 个邮箱、总 6 个请求、12 秒预算），网络阶段不占数据库连接；熔断按邮箱计数
- [x] 两个邮箱出现相同 message_id / thread_id 时不串号（测试）
- [x] 新用的 Gmail 接口 `users.labels.get` 已按 discovery 文档核验并写进 `docs/decisions/2026-11-06-gmail-verification.md` 第 7 节；没有新增 OAuth 范围
- [x] `test_mail_aggregate.py` 等通过；ruff / mypy / openapi 检查通过
- [ ] 后端全量测试与 CI 通过

## 背景 / 约束 / 参考
方案 `docs/v2/pages-redesign/`（规范 `docs/superpowers/specs/2026-11-08-workspace-four-pages-design.md`，计划 `docs/superpowers/plans/2026-11-08-workspace-four-pages-redesign.md`），效果图在 `assets/`。人类 2026-11-09 在对话中要求「按照效果图和设计文档进行全部落实」。

越界说明：为聚合读取改了 `app/integrations/gmail.py`（labels.get）、`tests/fake_google.py`、`services/api/openapi.json` 与核验记录，不在原 paths 里，都是本任务必需的。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 01:32 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 02:29 UTC · claude · 开始任务：按四页改造方案实施（本分支）
