---
id: T-0011
title: Sprint 1 后端：角色策略与防 IDOR、设置骨架与凭证加密、审计日志与会话撤销（S1-03、S1-04、S1-05）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app, services/api/migrations, services/api/tests
depends_on: T-0010
created: 2026-10-29
updated: 2026-10-29
---

# T-0011 Sprint 1 后端：角色策略与防 IDOR、设置骨架与凭证加密、审计日志与会话撤销（S1-03、S1-04、S1-05）

## 目标
在 T-0010 的身份基础上，补齐服务端权限策略、设置骨架、凭证加密存储、审计日志与会话管理。

## 验收标准
- [x] S1-03：统一的工作空间范围与角色策略（owner / admin / member / viewer）；非成员访问返回 404，权限不足返回 403；防 IDOR 测试（两个工作空间、两个用户）
- [x] S1-04：设置骨架——个人资料（显示名）、工作空间名称（admin 及以上可改）
- [x] S1-04：凭证加密服务（使用 `CREDENTIAL_ENCRYPTION_KEY`），数据库只存密文或 secret 引用；单元测试覆盖加解密与密钥错误
- [x] S1-05：`audit_logs` 迁移（只追加、metadata 脱敏、IP 只存哈希）；记录登录成功 / 失败、登出、修改密码、撤销会话、修改工作空间设置；按契约提供审计日志查询（owner / admin）
- [x] S1-05：会话列表、撤销单个会话、修改密码后撤销其他会话；对应 API 测试

## 背景 / 约束 / 参考
- 依赖：T-0010 合并后开始
- 接口契约：`docs/decisions/2026-10-30-sprint-1-auth-contract.md`（T-0009 产出）
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 5.1、6、9 节；Sprint 1 范围与 DoD 见第 13 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 22:52 UTC · claude · 创建任务（owner: claude）
- 2026-10-29 23:33 UTC · claude · 开始任务
- 2026-10-29 23:49 UTC · claude · 完成实现：统一 Policy（app/domain/policy.py，非成员 404、角色不够 403）；GET/PATCH /workspaces/{id}（viewer+/admin+）、PATCH /me；会话列表与撤销（只能撤销自己的，撤销当前会话同时清 Cookie）；修改密码（按用户限流，撤销全部会话后签发新会话）；迁移 0003 audit_logs（触发器拒绝 UPDATE，metadata 脱敏，IP 存 HMAC 哈希，账户事件按用户所在工作空间分发，游标分页）；凭证加密服务 app/domain/credentials.py（AES-256-GCM 绑定用途 + env:WS_SECRET_* 引用，cryptography 50.0.2）。openapi.json 未改动。越出 paths 的文件（均为 claude 负责、无人占用）：compose.yaml（向 api 传入 CREDENTIAL_ENCRYPTION_KEY）、.env.example 注释、docs 下的契约 ADR（新增第 11、12 节，第 10 节前端约定编号不变）、backend-stack ADR、数据字典、威胁模型、api-contract、deployment；共享测试夹具移到 tests/conftest.py 与 tests/helpers.py。
- 2026-10-29 23:49 UTC · claude · status in_progress → done：验收标准全部满足：make lint、pytest 69 passed（含 PostgreSQL/Redis 集成测试）、前端 18 passed、openapi --check、make smoke 通过，经 Caddy 全栈验证
- 2026-10-29 23:55 UTC · claude · 处理 Codex 审查：P1 并发登录与改密竞态——登录在签发会话前对用户行 SELECT … FOR UPDATE 并确认密码哈希未变，改密也先锁用户行，二者串行（新增两条回归测试，修复前分别得到 200 与 [204, 204]）；P2 审计游标先校验结构与类型再转换（修复前非字符串元素返回 500）。
