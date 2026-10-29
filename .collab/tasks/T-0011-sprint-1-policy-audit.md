---
id: T-0011
title: Sprint 1 后端：角色策略与防 IDOR、设置骨架与凭证加密、审计日志与会话撤销（S1-03、S1-04、S1-05）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app, services/api/migrations, services/api/tests
depends_on: T-0010
created: 2026-10-29
updated: 2026-10-29
---

# T-0011 Sprint 1 后端：角色策略与防 IDOR、设置骨架与凭证加密、审计日志与会话撤销（S1-03、S1-04、S1-05）

## 目标
在 T-0010 的身份基础上，补齐服务端权限策略、设置骨架、凭证加密存储、审计日志与会话管理。

## 验收标准
- [ ] S1-03：统一的工作空间范围与角色策略（owner / admin / member / viewer）；非成员访问返回 404，权限不足返回 403；防 IDOR 测试（两个工作空间、两个用户）
- [ ] S1-04：设置骨架——个人资料（显示名）、工作空间名称（admin 及以上可改）
- [ ] S1-04：凭证加密服务（使用 `CREDENTIAL_ENCRYPTION_KEY`），数据库只存密文或 secret 引用；单元测试覆盖加解密与密钥错误
- [ ] S1-05：`audit_logs` 迁移（只追加、metadata 脱敏、IP 只存哈希）；记录登录成功 / 失败、登出、修改密码、撤销会话、修改工作空间设置；按契约提供审计日志查询（owner / admin）
- [ ] S1-05：会话列表、撤销单个会话、修改密码后撤销其他会话；对应 API 测试

## 背景 / 约束 / 参考
- 依赖：T-0010 合并后开始
- 接口契约：`docs/decisions/2026-10-30-sprint-1-auth-contract.md`（T-0009 产出）
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 5.1、6、9 节；Sprint 1 范围与 DoD 见第 13 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 22:52 UTC · claude · 创建任务（owner: claude）
