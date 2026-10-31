---
id: T-0054
title: OC-2 OpenClaw 实例、凭证、密钥库与接入入口：迁移、加密密钥可轮换、接入包、取密钥小程序、两个 Caddy 入口、接入网关、内部 CA（S6-01）
owner: claude
status: done
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
- [x] 迁移：`agent_instances`、`agent_credentials`、`agent_secrets`、`agent_secret_versions`、`agent_secret_grants`，以及凭证密文的 `key_version`；有迁移测试
- [x] 加密密钥可轮换：新旧版本并存、后台重新加密，覆盖现有的集成凭证
- [x] 实例登记、停用、轮换、全局总开关；接入包生成（取密钥小程序、启动包装脚本、定时器、隧道配置）
- [x] Caddy 内网与外网两个接入入口，默认拒绝；凭证和入口绑定；外网入口要求客户端证书，按指纹吊销；限流和封禁
- [x] 测试覆盖：凭证错配、入口错配、吊销立即生效、密钥授权与审计

- [x] 外网模式的本机隧道（ghostunnel 客户端或同类）加 Caddy `client_auth` 双向证书跑通，MCP、`/agent-api` 经过隧道（从 OC-0 移来；IM 的 `/im/*` 在 OC-3 放行后验证，已移到 T-0055）
- [x] OC-0 ADR 的 F5（离线启动时 doctor 的 npm 检查）、F6（不设 `commands.ownerAllowFrom`）、F8（exec 程序属主与权限）落进接入包与检查清单

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 19:03 UTC · claude · 开始任务
- 2026-10-31 19:15 UTC · claude · 第 1 部分（PR A）：迁移 0011 建 V2 表（agent_settings / agent_instances / agent_credentials / agent_secrets / agent_secret_versions / agent_secret_grants / agent_secret_accesses / agent_ca），alembic upgrade/downgrade/check 通过；加密密钥可轮换：CredentialBox 改为密钥环（CREDENTIAL_ENCRYPTION_KEY + CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS），新增 python -m app.commands.rotate_credentials 逐条重新加密集成凭证、Webhook 密钥、n8n 密钥、密钥库与 CA 私钥（不改 updated_at，可重复运行）。方案第 5 节的 key_version 列由密文引用里已有的 key_id 承担，不给旧表加列。越出 paths：docs/deployment.md 第 6.1 节（轮换步骤）。下一步：PR B——实例、凭证、密钥库服务与管理员接口、/agent-api 取密钥与心跳、实例认证
- 2026-10-31 19:36 UTC · claude · 第 2 部分（PR B）：AgentService——实例登记 / 修改 / 停用 / 启用 / 吊销凭证 / 密钥授权、总开关、密钥库（只写不读、同名 409 AGENT_SECRET_EXISTS、轮换后旧密文清除）、取密钥记录；实例认证（令牌哈希、入口绑定、外网要同一实例的客户端证书指纹、停用与总开关立即生效、外网入口失败 10 次封禁 15 分钟）；/agent-api/v1/secrets/resolve（按实例每分钟 60 次）与 /heartbeat。入口请求头只在直接对端属于 AGENT_INGRESS_TRUSTED_PROXIES 时采用（不看 X-Forwarded-For），防止同网络容器伪造。签发令牌与登记证书的方法已就绪，接入包（zip）在第 3 部分。full_smoke：冒烟账号不是 owner 时跳过只给 owner 的读接口。待办：OC-3 接 OpenIM（登记时注册用户与加好友、停用与总开关时强制下线）
- 2026-10-31 19:51 UTC · claude · 第 3 部分（并入 PR #79）：内部 CA（EC P-256，5 年，私钥 enc:v1；python -m app.commands.agent_ca export 导出证书给 Caddy）；客户端证书 90 天、仅 clientAuth、CN=oc_<id>；接入包 POST /agents/instances/{id}/bundle（zip：myworkspace.env 600、state/openclaw.json 600、两个程序 755、OpenClaw 服务器的 compose.yaml、CHECKLIST.md、README.md、外网另有 tls/client.crt / client.key）；地址来自 AGENT_INTRANET_URL / AGENT_PUBLIC_URL，未配置时 409 AGENT_INGRESS_NOT_CONFIGURED；启动包装脚本上报版本。模板与程序从 infra/openclaw 移到 services/api/app/agent_bundle/files（API 镜像只含 services/api）。F5 / F6 / F8 写进 CHECKLIST.md。pytest 431 passed。下一步：第 4 部分——Caddy 内网 / 外网接入入口、ghostunnel 隧道与双向证书端到端
- 2026-10-31 20:26 UTC · claude · 第 4 部分：接入入口 agent-gateway（Caddy 2.11.7，--profile agents，infra/caddy/agent.Caddyfile）——内网入口限定来源网段、外网入口 client_auth require_and_verify 信任内部 CA；只放行 /mcp 与 /agent-api/v1/*；先删后写 X-MyWS-* 请求头；agent-ingress 内部网络里网关固定 172.31.250.2（API 默认只信任它），另有只给网关发布端口的 agent-edge 网络。外网接入包增加 ghostunnel v1.11.4 本机隧道与 extra_hosts（Caddy 开启客户端证书后强制 SNI 与 Host 一致）。核验 ADR docs/decisions/2026-10-31-v2-oc2-agent-ingress-verification.md；tests/e2e/agent_ingress_acceptance.py 本机 13 项全部通过（含 Caddy 指纹格式与工作台一致、伪造请求头 401、吊销立即生效）。IM 经隧道移到 T-0055；验收脚本进 CI 移到 T-0059
- 2026-10-31 20:26 UTC · claude · status in_progress → done：OC-2 完成：迁移、密钥轮换、实例与密钥库、实例认证、内部 CA 与接入包、两个接入入口与本机隧道
- 2026-10-31 20:48 UTC · claude · 修复 PR #80 的 ci-infra A15：agent-ingress 网段写死为 172.31.250.0/29，make restore-verify 在同一台机器上起第二套环境时 Docker 报 Pool overlaps。改为 AGENT_INGRESS_NET（默认 172.31.250，子网、网关地址与 API 信任的地址都由它派生），restore-verify 的临时环境用 172.31.251。本机复现了冲突并验证了修复
