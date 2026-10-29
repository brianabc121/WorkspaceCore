---
id: T-0010
title: Sprint 1 后端：用户/会话/工作空间迁移、首次安装、Argon2id、Session Cookie、CSRF、登录限流（S1-01、S1-02）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app, services/api/migrations, services/api/tests, services/api/pyproject.toml, services/api/uv.lock, .env.example, compose.yaml, scripts/init_env.py
depends_on: T-0009
created: 2026-10-29
updated: 2026-10-29
---

# T-0010 Sprint 1 后端：用户/会话/工作空间迁移、首次安装、Argon2id、Session Cookie、CSRF、登录限流（S1-01、S1-02）

## 目标
实现 Sprint 1 的身份基础：用户、会话、工作空间、成员的数据模型与迁移，首次安装管理员、密码登录、Session Cookie、CSRF 防护和登录限流。

## 验收标准
- [ ] S1-01：迁移 `users`、`auth_sessions`、`external_identities`、`workspaces`、`workspace_members`（规范 5.1 字段，`workspace` schema，UUID 主键）及 repository；空库 upgrade / downgrade 测试
- [ ] S1-02：首次安装接口只能成功一次，并按契约校验安装凭据；创建 owner 用户、默认工作空间与成员关系
- [ ] S1-02：Argon2id 哈希（参数写入 ADR）；密码长度上下限；错误信息不泄露账户是否存在
- [ ] S1-02：登录 / 登出 / `GET /me` / `GET /workspaces`；Session Token 只存哈希；Cookie HttpOnly、SameSite=Lax、生产环境 Secure
- [ ] S1-02：改变状态的 Cookie 认证接口校验 CSRF Token 与 Origin / Referer；登录按 IP + 账户双重限流并有失败延迟（Redis）
- [ ] 测试：A01（空库首次安装）、A02（未登录访问私有接口 401）、A18（登出后旧会话失效）、CSRF 缺失或错误被拒、限流返回 429
- [ ] 依赖与版本写入 backend-stack ADR（附来源）

## 背景 / 约束 / 参考
- 接口契约：`docs/decisions/2026-10-30-sprint-1-auth-contract.md`（T-0009 产出）
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 5.1、6、9 节；Sprint 1 范围与 DoD 见第 13 节
- 不改 `apps/web/**`；不勾选 `docs/TASKS.md`（由 T-0013 统一勾选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 22:52 UTC · claude · 创建任务（owner: claude）
