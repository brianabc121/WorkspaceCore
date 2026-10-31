# OpenClaw 服务器接入包（OC-0 初稿）

本目录**不在工作台服务器上运行**。它是每台 OpenClaw 服务器的参考配置和工具，由人类部署到 OpenClaw 所在的服务器上。

- 设计依据：`docs/v2/TRANSFORMATION_PLAN.md`（第 4.1 ~ 4.6、6.2 节）；
- 核验结论：`docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`。

> 当前状态：
> - OC-0 只核验了下面这些文件和配置项的行为；
> - 接入包的自动生成、`/agent-api/v1/im-token` 等工作台接口在 OC-1、OC-2 实现；
> - 外网模式的隧道在 OC-2 核验。
> 在那之前不要用于生产。

## 文件

> **OC-2 起，接入包由工作台生成**：实例页的“下载接入包”（`POST /api/v1/agents/instances/{id}/bundle`）
> 返回一个 zip，里面是下面这些文件（已填好地址、凭证与 `inboundWhitelist`），外加 `compose.yaml` 与 `CHECKLIST.md`。
> 模板和程序的源文件在 `services/api/app/agent_bundle/files/`：API 镜像只包含 `services/api`，所以从本目录移到了那里。
> 部署时以接入包里的 `CHECKLIST.md` 为准；下面是 OC-0 时的说明，保留作为背景。

| 文件（在接入包里） | 用途 | 核验 |
|---|---|---|
| `bin/myworkspace-secret-resolver` | OpenClaw exec SecretRef 的取密钥程序（protocolVersion 1），向工作台的 `/agent-api/v1/secrets/resolve` 取模型密钥等 | ADR 第 2 节第 6、7 项 |
| `bin/myworkspace-openclaw-start` | 启动包装：先取 IM 令牌放进 `OPENIM_TOKEN`、上报版本（`/agent-api/v1/heartbeat`，失败不影响启动），再启动 OpenClaw 原入口 | 本机用模拟接口测过：令牌注入、心跳、退出码透传、凭证错误时重试 5 次后失败 |
| `state/openclaw.json` | OpenClaw 配置：加固基线、exec 密钥源、MCP、`openim` 渠道 | ADR 第 2、7 节 |

## 部署步骤（单台 OpenClaw 服务器）

1. **镜像**：
   - 用 `openclaw/openclaw:2026.9.36@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f`（extended-stable）；
   - 不用 `latest`。
2. **安装插件**：
   - 先核对 tarball 的 sha256 是 `3d10dca0edcba0ecdb0dc915e7cf1ddf5a8453165de13dec76bbe5ef85607643`（`npm pack @openim/openclaw-channel@0.3.3`）；
   - 然后执行：
     ```
     openclaw plugins install @openim/openclaw-channel@0.3.3 --force --accept-capabilities
     ```
     这个插件不在 ClawHub 审核范围内，所以要 `--force`。升级插件前，要重新做 ADR F9 的审查。
3. **放置文件**：
   - 把 `bin/` 下的两个程序放到 `/opt/myworkspace/bin/`；
   - **属主改成运行 OpenClaw 的用户**（Docker 镜像里是 uid 1000），权限 0755，不能是符号链接。这是 OpenClaw 对 exec 程序的要求。
4. **配置**：
   - 用 `openclaw.json.template` 生成 `~/.openclaw/openclaw.json`；
   - 把模型服务商换成实际要用的那一家，密钥 ID 和工作台密钥库里的授权保持一致。
5. **环境变量**：
   - 写进权限 600 的环境文件：`MYWS_INSTANCE_TOKEN`、`MYWS_AGENT_API`、`MYWS_MCP_URL`、`MYWS_ADMIN_IM_ID`、`OPENCLAW_GATEWAY_TOKEN`（`openssl rand -hex 32`）；
   - 外网模式还要 `MYWS_CA_FILE` 和隧道配置（OC-2）。
6. **启动**：用 `myworkspace-openclaw-start` 作为容器入口（见脚本开头的注释）。冷启动要 2 ~ 4 分钟（ADR F5）。
7. **定时任务**：
   - 每 10 分钟执行一次 `openclaw secrets reload`：工作台轮换的模型密钥会在下一次请求生效，不用重启（ADR 第 2 节第 7 项）；
   - IM 令牌只能靠重启更新（ADR F1）：每周重启一次，工作台在令牌到期前提醒。

## 检查清单（上线和每次改配置后）

- [ ] Gateway 只监听本机（`bind: loopback`），不对外暴露端口；
- [ ] `openclaw security audit --deep`、`openclaw doctor`、`openclaw health` 通过；
- [ ] `tools.exec.security = "deny"`，没有挂载 Docker socket，没有启用沙箱；
- [ ] `plugins.allow` 只有 `openclaw-channel`，没有安装其他插件和技能；
- [ ] **不设置 `commands.ownerAllowFrom`**：任何 IM 账号都不能在聊天里管理 OpenClaw（ADR F6）；
- [ ] 插件的 `openim_send_image` / `openim_send_file` / `openim_send_video` 不可用，或已列入 `tools.deny`（ADR F3）；
- [ ] `requireMention: true`；`inboundWhitelist` 只有管理员，以及允许接力的群里的其他实例；
- [ ] 环境文件权限 600；`openclaw.json` 里没有明文密钥（`openclaw secrets audit`）；
- [ ] 日志级别和保留期已设置：OpenIM SDK 会把消息内容写进日志（ADR F7）；
- [ ] `openclaw backup create --verify` 已经能在本机跑通；回滚要连同备份的状态一起恢复。
