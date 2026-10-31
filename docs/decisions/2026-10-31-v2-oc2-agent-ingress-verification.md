# V2 OC-2 核验：OpenClaw 接入入口（Caddy 双向证书）与本机隧道（ghostunnel）

- 状态：accepted
- 日期：2026-10-31
- 作者：claude（协作任务 T-0054，OC-2 第 4 部分）
- 依据：`docs/v2/TRANSFORMATION_PLAN.md` 第 4.2、6.1、6.2 节；`docs/decisions/2026-10-31-v2-oc1-contract.md` 第 1、3、8 节
- 验收脚本：`tests/e2e/agent_ingress_acceptance.py`（本会话自己的 Compose 全栈 + `agent-gateway` + 测试证书，13 项全部通过）

## 1. 锁定的版本

| 组件 | 镜像 | digest | 说明 |
|---|---|---|---|
| Caddy 2.11.7（接入入口） | `caddy:2.11.7-alpine` | `sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5` | 与管理员入口同一版本；`caddy version` 输出 `v2.11.7` |
| ghostunnel v1.11.4（本机隧道） | `ghostunnel/ghostunnel:v1.11.4` | `sha256:e5248233863aadd36383a11c92360f2981141881ef05e38360a6ada8630d36b1` | 2026-10-14 发布。Docker Hub 上的最新版是 v1.11.5（2026-10-21），发布还不到两周，所以没有选它。这次会话访问不了 GitHub，没能读 release notes；升级前补读 |

## 2. 核验结论

| # | 核验项 | 结论 | 怎么验证的 |
|---|---|---|---|
| V1 | Caddy 2.11.7 的 `client_auth { mode require_and_verify; trust_pool file <pem> }` 语法 | 可用 | `caddy validate`：`Valid configuration` |
| V2 | 开启客户端证书后 Caddy **强制 SNI 与 Host 一致** | 是。启动日志：`enabling strict SNI-Host enforcement because TLS client auth is configured` | `caddy validate` 的输出 |
| V3 | `{http.request.tls.client.fingerprint}` 的格式 | 与工作台的 `fingerprint_of`（证书 DER 的 SHA-256，小写十六进制）一致 | 经隧道调用后，API 按这个请求头找到了客户端证书凭证（`last_used_at` 被更新）；吊销后立即 401 |
| V4 | 不带客户端证书、或带别的 CA 签发的证书连外网入口 | TLS 握手失败 | 验收脚本第 2 部分 |
| V5 | ghostunnel v1.11.4 client 模式的参数 | `--listen`（默认只允许本机地址）、`--target`、`--cert`、`--key`、`--cacert`（不给时用系统信任库） | `ghostunnel client --help` |
| V6 | 本机隧道转发明文 HTTP，Host 头取的是本机地址 | 和 V2 冲突，所以 OpenClaw 那边要把外网入口的**域名解析到 127.0.0.1**：接入包的 `compose.yaml` 写 `extra_hosts`，地址写 `http://<域名>:18443`；程序用 `MYWS_LOCAL_TUNNEL_HOST` 允许这一个域名走明文（只在本机回环上） | `--network host --add-host` 在 host 网络下同样生效（`/etc/hosts` 有记录）；经隧道取密钥 200 |
| V7 | 发布端口 | `internal: true` 的网络不能发布端口。所以接入入口另加一个只有它自己的 `agent-edge` 网络用来发布端口；访问 API 只经 `agent-ingress`（内部网络，网关固定 172.31.250.2，动态地址只分配 .4 ~ .7） | 改之前连接被拒绝，改之后能连上 |
| V8 | 同一网络里的其他容器伪造 `X-MyWS-Ingress` 直接连 API | 401：API 只认直接对端是 172.31.250.2 的请求头 | 验收脚本第 4 部分 |
| V9 | 客户端在请求里伪造入口与指纹请求头 | 无效：网关先删掉再写入；内网入口上的外网令牌仍然 401 | 验收脚本第 1 部分 |

## 3. 部署决定

- **接入入口是单独的 Caddy**（`agent-gateway`，`--profile agents`），配置 `infra/caddy/agent.Caddyfile`：
  - 内网入口 `https://$AGENT_INTRANET_HOST:8443`：只接受 `AGENT_INTRANET_ALLOWED_CIDRS` 的来源；
  - 外网入口 `https://$AGENT_PUBLIC_HOST:9443`：双向证书，信任 `infra/caddy/agent-ca/agent-ca.pem`（`make agent-ca` 导出）；
  - 两个入口都只放行 `/mcp`、`/agent-api/v1/*`，其余 404；
  - 发布端口由 `AGENT_INTRANET_BIND/PORT`、`AGENT_PUBLIC_BIND/PORT` 控制，默认只绑定本机。
  - `agent-ingress` 的网段由 `AGENT_INGRESS_NET` 控制（默认 `172.31.250`，子网 `.0/29`，网关固定 `.2`）。同一台机器上的第二套环境必须换网段，否则 Docker 报 `Pool overlaps`：`make restore-verify` 的临时环境用 `172.31.251`（CI 的 A15 备份恢复验收发现）。
- **证书来自文件**（`AGENT_TLS_DIR`）：内网用内部 CA 或 Tailscale 证书，外网用公网证书。
  - 不用 ACME：接入入口不一定在 80 / 443 端口上，而且可能和管理员入口在同一台机器。
  - 外网入口用私有 CA 时，接入包的 `tunnel` 服务要加 `--cacert`；内网入口用私有 CA 时，OpenClaw 那边设置 `NODE_EXTRA_CA_CERTS`（`docs/deployment.md` 第 16 节）。
- **接入包（外网模式）**：增加 `tunnel` 服务（ghostunnel client，用户 1000、host 网络、只读挂载 `tls/`），OpenClaw 服务加 `extra_hosts` 与 `depends_on`。
- **IM 的三类路径**（`/im/ws`、`/im/api`、`/im/obj`）在 OC-3 接入 OpenIM 后再放行，届时在同一套隧道上验证。

## 4. 没有核验的项目

| 项目 | 留给 |
|---|---|
| WebSocket（`/im/ws`）经隧道与 Caddy 转发 | OC-3（T-0055） |
| 真实 OpenClaw 按接入包启动并经隧道调用 MCP 与取密钥 | OC-5 / OC-7 的多实例端到端 |
| 验收脚本进入 ci-infra | OC-7（T-0059） |
| ghostunnel v1.11.5 的变化 | 下次升级时 |
