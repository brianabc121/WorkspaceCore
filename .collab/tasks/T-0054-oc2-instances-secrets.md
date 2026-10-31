---
id: T-0054
title: OC-2 OpenClaw 实例、凭证、密钥库与接入入口：迁移、加密密钥可轮换、接入包、取密钥小程序、两个 Caddy 入口、接入网关、内部 CA（S6-01）
owner: claude
status: todo
priority: P1
branch:
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
- [ ] 测试覆盖：凭证错配、入口错配、吊销立即生效、密钥授权与审计

- [ ] 外网模式的本机隧道（ghostunnel 客户端或同类）加 Caddy `client_auth` 双向证书跑通，MCP、`/agent-api`、IM 三类连接都经过隧道（从 OC-0 移来）
- [ ] OC-0 ADR 的 F5（离线启动时 doctor 的 npm 检查）、F6（不设 `commands.ownerAllowFrom`）、F8（exec 程序属主与权限）落进接入包与检查清单

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
