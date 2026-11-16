# T-0224 纯 IP 私网控制通路

2026-11-15（Asia/Taipei）。此记录仅证明专用非生产控制通路，不代表完整运行操作、公网入口或容量验收完成。

用户确认 Tailscale IP、公网 IP、内网 IP 任选一条可达路径即可，域名不是先决条件。本次沿用现有 Tailscale 集群底层网络，以内部 Service IP `10.245.14.85:8443` 为控制地址。未增加公网入口、修改 Tailscale ACL 或关闭 TLS 校验。

## 实际部署

- Kubernetes 先分配 ClusterIP，再为该准确 IP 签发 IP SAN 证书；不是把 IP 写到 DNS SAN。
- 固定 Caddy `2.11.7-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5`；配置由已独立审查的 renderer 生成。
- UID/GID1000、只读 rootfs、drop ALL、无服务账号令牌；仅限定的 emptyDir 可写。
- 上游 `https://100.72.60.100:8443` 同时核验 CA 与 IP SAN；转发不注入认证。
- 初始叶证书到期：2026-11-16 **11:26:47（Asia/Taipei）**，即 `2026-11-16T03:26:47Z`；已在到期前续期，当前有效期见下方续期记录。
- 私钥、CA 私钥、令牌及 Secret 内容均不在本目录。

## 实机发现并修正的来源地址问题

最初仅允许 A/B 的 Tailscale `/32`，B 到 Service 与 Pod 均超时。Cilium 的定向 drop 记录证明实际来源是 B 的 `10.244.1.119`，原因是 Policy denied；不是 DNS 或证书失败。

随后从两台既有 CiliumNode 的 UID/地址清单核验：A 的 CiliumInternalIP 为 `10.244.0.50`，B 为 `10.244.1.119`。只补入这两个精确 `/32`，保留端口与同命名空间 controller 选择器；未允许整个 Pod 网段。配置见 [options.json](private-relay-options.json)，原始证据见 [丢包](private-relay-drop.log) 与 [节点地址](private-relay-node-addresses.log)。

## B 节点真实 TLS 请求

| 检查 | 结果 |
| --- | --- |
| `/healthz`，受信 CA、准确 IP SAN | 200 |
| `/readyz` | 200 |
| `/docs`，不在代理路由内 | 404 |
| 有效 poll 请求体、无凭据 | 401 |
| 错误 IP SAN | 证书校验拒绝 |
| 系统信任库、不信任测试 CA | 证书校验拒绝 |
| 既有隔离测试租户 Pod 连接中继 | 未连接成功 |

第一次无凭据测试使用空请求体，返回422；改用符合现有 poll 契约的请求体后才验证401，不把422当作认证结果。见 [最终 TLS 输出](private-relay-b-tls.log) 与 [租户拒绝](private-relay-tenant-deny.log)。中继 Pod UID 为 `b9951d7c-ceb5-4e71-8ea0-5d5096cf94b8`，重启数0；修正来源策略期间未重启目标。

租户探测验证现有出口策略与中继入口策略的组合边界，不单独归因于某一条策略。仍需后续真实 controller 凭据轮询、结果回调、文件及快照闭环，不能用本页健康检查替代。

## 2026-11-16 续期与复验

于 `2026-11-16T03:19:57Z` 保持原 CA 与准确 IP SAN，更新专用中继 TLS Secret，仅滚动中继。新叶证书 SHA256 为 `3D:27:B7:D2:27:67:A6:7F:DA:10:B3:B1:9C:7F:5C:3A:E6:8F:EA:4C:B0:BA:C5:C9:31:33:5E:29:29:5E:EB:2F`，叶证书到期 `2026-11-17T03:19:57Z`。

**有效链路期限仍由更早到期的 CA 限制：`2026-11-16T16:46:44Z`（台北时间11月17日00:46:44）**，不能只按新叶证书日期判断。上游测试证书也在此时附近到期，继续验收需再次核对。

B 节点在续期后重新执行受信 CA/IP SAN 请求，`healthz/readyz/docs/poll` 分别为 `200/200/404/401`；错误 IP SAN 和未受信 CA 均拒绝。见 [续期输出](private-relay-renewal.log)、[B 复验](private-relay-renewal-b-probe.log)。前文初始 Pod UID 与租户拒绝结果保留为历史证据；本次续期没有重做租户撤销/断流或完整产品操作验收。
