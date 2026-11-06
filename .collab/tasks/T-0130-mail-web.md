---
id: T-0130
title: Gmail 页面：邮件页、设置 → 邮件、实例卡片的邮件开关
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e,infra
depends_on: T-0128
created: 2026-11-06
updated: 2026-11-06
---

# T-0130 Gmail 页面：邮件页、设置 → 邮件、实例卡片的邮件开关

## 目标
管理员在页面上用邮件、连接 Gmail、给智能体开关邮件权限。

## 验收标准
- [x] 邮件页 `/mail`：邮箱与标签、搜索、列表、阅读（纯文字、附件清单、会话）、回复与写邮件、发件记录
- [x] 设置 → 邮件：OAuth 客户端、连接 / 重新连接 / 断开、允许智能体访问、署名行
- [x] 实例卡片：查询 / 发送开关与收件人范围；只有 owner / admin 看到邮件入口
- [x] web lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/MAIL_PLAN.md`；核验：`docs/decisions/2026-11-06-gmail-verification.md`
- 人类 2026-11-06 在对话中提出：“再给我为 workspace 平台新增可以收发 gmail 邮件的功能……智能体收发 / 查询邮件的权利也需要做成开关，由管理员进行赋予。默认仅可以查询邮件，不可以发送邮件”；并选定智能体发信“直接发出（新增例外七）”、接入方式“Gmail API + OAuth”。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 11:00 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 11:48 UTC · claude · 开始任务
- 2026-11-06 11:48 UTC · claude · status in_progress → done：邮件页 /mail、设置 → 邮件、实例卡片的邮件权限；导航只给 owner/admin；tests/mail-pages.test.ts 6 项，web 全量 459 项通过
