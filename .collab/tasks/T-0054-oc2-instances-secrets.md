---
id: T-0054
title: OC-2 OpenClaw 实例、凭证、密钥库与接入入口：迁移、加密密钥可轮换、接入包、取密钥小程序、两个 Caddy 入口、接入网关、内部 CA（S6-01）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app,services/api/migrations,services/api/tests,infra/caddy,infra/openclaw,compose.yaml,.env.example,scripts
depends_on: T-0053
created: 2026-10-31
updated: 2026-10-31
---

# T-0054 OC-2 OpenClaw 实例、凭证、密钥库与接入入口：迁移、加密密钥可轮换、接入包、取密钥小程序、两个 Caddy 入口、接入网关、内部 CA（S6-01）

## 目标
实现 OpenClaw 实例管理、凭证、密钥库、接入入口与接入网关（方案第 4.1 ~ 4.3、6 节）。

## 验收标准
- [ ] 迁移：`agent_instances`、`agent_credentials`、`agent_secrets`、`agent_secret_versions`、`agent_secret_grants`，以及凭证密文的 `key_version`；有迁移测试
- [ ] 加密密钥可轮换：新旧版本并存、后台重新加密，覆盖现有的集成凭证
- [ ] 实例登记、停用、轮换、全局总开关；接入包生成（取密钥小程序、启动包装脚本、定时器、隧道配置）
- [ ] Caddy 内网与外网两个接入入口，默认拒绝；凭证和入口绑定；外网入口要求客户端证书，按指纹吊销；限流和封禁
- [x] 测试覆盖：凭证错配、入口错配、吊销立即生效、密钥授权与审计

- [ ] 外网模式的本机隧道（ghostunnel 客户端或同类）加 Caddy `client_auth` 双向证书跑通，MCP、`/agent-api`、IM 三类连接都经过隧道（从 OC-0 移来）
- [ ] OC-0 ADR 的 F5（离线启动时 doctor 的 npm 检查）、F6（不设 `commands.ownerAllowFrom`）、F8（exec 程序属主与权限）落进接入包与检查清单

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 19:03 UTC · claude · 开始任务
- 2026-10-31 19:15 UTC · claude · 第 1 部分（PR A）：迁移 0011 建 V2 表（agent_settings / agent_instances / agent_credentials / agent_secrets / agent_secret_versions / agent_secret_grants / agent_secret_accesses / agent_ca），alembic upgrade/downgrade/check 通过；加密密钥可轮换：CredentialBox 改为密钥环（CREDENTIAL_ENCRYPTION_KEY + CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS），新增 python -m app.commands.rotate_credentials 逐条重新加密集成凭证、Webhook 密钥、n8n 密钥、密钥库与 CA 私钥（不改 updated_at，可重复运行）。方案第 5 节的 key_version 列由密文引用里已有的 key_id 承担，不给旧表加列。越出 paths：docs/deployment.md 第 6.1 节（轮换步骤）。下一步：PR B——实例、凭证、密钥库服务与管理员接口、/agent-api 取密钥与心跳、实例认证
- 2026-10-31 19:36 UTC · claude · 第 2 部分（PR B）：AgentService——实例登记 / 修改 / 停用 / 启用 / 吊销凭证 / 密钥授权、总开关、密钥库（只写不读、同名 409 AGENT_SECRET_EXISTS、轮换后旧密文清除）、取密钥记录；实例认证（令牌哈希、入口绑定、外网要同一实例的客户端证书指纹、停用与总开关立即生效、外网入口失败 10 次封禁 15 分钟）；/agent-api/v1/secrets/resolve（按实例每分钟 60 次）与 /heartbeat。入口请求头只在直接对端属于 AGENT_INGRESS_TRUSTED_PROXIES 时采用（不看 X-Forwarded-For），防止同网络容器伪造。签发令牌与登记证书的方法已就绪，接入包（zip）在第 3 部分。full_smoke：冒烟账号不是 owner 时跳过只给 owner 的读接口。待办：OC-3 接 OpenIM（登记时注册用户与加好友、停用与总开关时强制下线）
