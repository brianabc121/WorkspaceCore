---
id: T-0127
title: Gmail 邮件方案：核验 Gmail API 与 Google OAuth、MAIL_PLAN.md、AGENTS.md 例外七
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs
depends_on:
created: 2026-11-06
updated: 2026-11-06
---

# T-0127 Gmail 邮件方案：核验 Gmail API 与 Google OAuth、MAIL_PLAN.md、AGENTS.md 例外七

## 目标
在动手前核验 Gmail API 与 Google OAuth 的官方文档，写出方案与决定，并把人类选定的“智能体直接发信”写进 docs/AGENTS.md 第 8 节例外七。

## 验收标准
- [x] `docs/decisions/2026-11-06-gmail-verification.md`：discovery 文档版本与 sha256、OAuth 参数、令牌与撤销、配额与错误、发信格式
- [x] `docs/v2/MAIL_PLAN.md`：接入、权限、数据、工具、开关与保护、页面、安全、验收、决定 M1 ~ M12、任务拆分
- [x] `docs/AGENTS.md` 第 8 节例外七（人类在对话中选定）

## 背景 / 约束 / 参考
- 方案：`docs/v2/MAIL_PLAN.md`；核验：`docs/decisions/2026-11-06-gmail-verification.md`
- 人类 2026-11-06 在对话中提出：“再给我为 workspace 平台新增可以收发 gmail 邮件的功能……智能体收发 / 查询邮件的权利也需要做成开关，由管理员进行赋予。默认仅可以查询邮件，不可以发送邮件”；并选定智能体发信“直接发出（新增例外七）”、接入方式“Gmail API + OAuth”。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 11:00 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 11:00 UTC · claude · 人类在对话中指派（2026-11-06）：新增 Gmail 收发功能；智能体经 MCP 调用；查询默认开、发送默认关，由管理员按实例赋予。人类选定：发信直接发出（例外七）、Gmail API + OAuth。T-0128 ~ T-0131 同属此需求。
- 2026-11-06 11:48 UTC · claude · 开始任务
- 2026-11-06 11:48 UTC · claude · status in_progress → done：方案 MAIL_PLAN.md、核验记录 2026-11-06-gmail-verification.md、AGENTS.md 例外七；M5 实现时改为不缓存（邮件内容不进 Redis）
