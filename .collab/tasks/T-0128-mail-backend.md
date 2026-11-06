---
id: T-0128
title: Gmail 后端：OAuth 接入、邮箱账号、Gmail 客户端（假 Google 测试）、页面接口与发件记录
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e,infra
depends_on: T-0127
created: 2026-11-06
updated: 2026-11-06
---

# T-0128 Gmail 后端：OAuth 接入、邮箱账号、Gmail 客户端（假 Google 测试）、页面接口与发件记录

## 目标
工作台能连接 Gmail（OAuth），管理员在工作台查收、搜索、阅读、回复、发送邮件。

## 验收标准
- [x] 迁移：OAuth 客户端设置、邮箱账号（加密的 refresh token、授权范围、状态）、发件记录；实例的查询 / 发送开关与收件人范围
- [x] Google OAuth（state、PKCE S256、offline、按实际授权范围启用功能、刷新、`invalid_grant` 后标为需要重新连接、撤销）与 Gmail 客户端（超时、错误归类、读请求有限重试、发信不重试、结果未知）
- [x] 页面接口：设置、连接 / 回调 / 断开、标签、搜索、读信、会话、附件、发信、发件记录；只有 owner / admin；审计
- [x] 假 Google 的后端测试；后端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/MAIL_PLAN.md`；核验：`docs/decisions/2026-11-06-gmail-verification.md`
- 人类 2026-11-06 在对话中提出：“再给我为 workspace 平台新增可以收发 gmail 邮件的功能……智能体收发 / 查询邮件的权利也需要做成开关，由管理员进行赋予。默认仅可以查询邮件，不可以发送邮件”；并选定智能体发信“直接发出（新增例外七）”、接入方式“Gmail API + OAuth”。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 11:00 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 11:48 UTC · claude · 开始任务
- 2026-11-06 11:48 UTC · claude · status in_progress → done：迁移 0040、Google OAuth（state/PKCE/offline/部分授权/刷新/invalid_grant/撤销）、Gmail 客户端、/api/v1/mail 页面接口；tests/test_mail.py 8 项 + test_mail_format.py 5 项（假 Google）
