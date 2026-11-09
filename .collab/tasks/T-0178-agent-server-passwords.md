---
id: T-0178
title: 智能体服务器（一）：加密登录密码、连接检测契约与加法迁移（取消 OpenClaw 服务器的 SSH 密钥登录）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/models/agent.py,services/api/app/schemas/agents.py,services/api/app/api/v1/agents.py,services/api/app/domain/agent_servers.py,services/api/app/domain/agent_server_credentials.py,services/api/app/domain/credential_rotation.py,services/api/migrations/versions,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts,docs
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0178 智能体服务器（一）：加密登录密码、连接检测契约与加法迁移（取消 OpenClaw 服务器的 SSH 密钥登录）

## 目标
OpenClaw 服务器改为用 IP、端口、账号、密码添加；密码加密保存、只写不读。补连接检测接口与加法迁移，为取消 SSH 密钥登录打基础。
方案见 `docs/v2/agent-servers-redesign/`（设计规范、接口与迁移约定、六阶段计划、两张效果图）；人类在对话中要求“根据效果图和开发文档全面落实”。（计划 Task 1）

## 验收标准
- [ ] `agent_ssh_targets` 加法迁移：`login_password_ref`、`credential_revision`、`last_connection_at`、`last_connection_reason`、`legacy_key_cleanup_state`（有旧公钥 / 私钥的回填 pending）；`agent_deployments.runtime_installed`（旧记录 null）。临时库 upgrade / downgrade / upgrade 通过
- [ ] 密码用 CredentialBox 加密（context `agent_server_password:<target_id>`），接入密钥轮换；API、日志、422 回显、审计、SSE、幂等缓存都不出现明文或密文引用；前后空格保留
- [ ] `POST /agents/servers` 需要 host / port / username / login_password，名称可选；确认主机身份前不把密码发给远端
- [ ] 新增 `PUT /agents/servers/{id}/credentials`、`POST /agents/servers/{id}/test-connection`；更新密码不清初始化记录、不停实例，有同机任务时 409；检测不安装、不写公钥
- [ ] `ServerSshOut` 增加 auth_method / password_set / credential_revision / connection_status / last_connection_at / legacy_key_cleanup_state
- [ ] 新 ADR 记录“OpenClaw 服务器改用保存的登录密码”取代旧的专用密钥方案（旧 ADR 保留）

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 06:30 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 06:31 UTC · claude · 开始任务
