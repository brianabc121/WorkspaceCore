---
id: T-0129
title: Gmail MCP 工具：mail_accounts / mail_search / mail_get / mail_thread 与 mail_send（例外七）、实例的查询与发送开关
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e,infra
depends_on: T-0128
created: 2026-11-06
updated: 2026-11-06
---

# T-0129 Gmail MCP 工具：mail_accounts / mail_search / mail_get / mail_thread 与 mail_send（例外七）、实例的查询与发送开关

## 目标
智能体只经工作台的 MCP 工具查询和发送邮件，权利由管理员按实例赋予：默认只能查询，不能发送。

## 验收标准
- [x] 读工具 `mail_accounts` / `mail_search` / `mail_get` / `mail_thread`，受实例“查询邮件”开关（默认打开）与邮箱“允许智能体访问”控制，内容放在 `untrusted`
- [x] `mail_send`（例外七）：发送开关默认关闭、只有打开的实例看得到；收件人范围、密钥拦截、每小时 10 封 / 每天 50 封、client_key、署名行、执行记录、审计、通知管理员、结果未知不重发
- [x] 开关接口（owner / admin，乐观并发、审计）；契约测试与后端测试通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/MAIL_PLAN.md`；核验：`docs/decisions/2026-11-06-gmail-verification.md`
- 人类 2026-11-06 在对话中提出：“再给我为 workspace 平台新增可以收发 gmail 邮件的功能……智能体收发 / 查询邮件的权利也需要做成开关，由管理员进行赋予。默认仅可以查询邮件，不可以发送邮件”；并选定智能体发信“直接发出（新增例外七）”、接入方式“Gmail API + OAuth”。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 11:00 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 11:48 UTC · claude · 开始任务
- 2026-11-06 11:48 UTC · claude · status in_progress → done：mail_accounts/search/get/thread（查询开关默认开）与 mail_send（例外七，默认关）；收件人范围、密钥、10/时 50/天、client_key（含并发）、署名、执行记录不存正文、审计、通知；PUT /agents/instances/{id}/mail；tests/test_mail_tools.py 5 项
