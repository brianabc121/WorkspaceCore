# Runtime v2 开发预览部署材料

**T-0224 / PR #193 尚未完成。这里没有执行安装，也不提供自动切换旧 Docker 环境的动作。**

`controller.env.example` 和 `workspace-runtime-v2.service` 是独立生命周期控制器的配置模板。复制模板本身不会启用后端；`RUNTIME_V2_ENABLED=false`，工作台 `RUNNER_NETWORK_VERIFIED=false`，Kubernetes 调度仍关闭。当前支持受限 gVisor 不可变镜像档位的 create/start/stop/delete、持久恢复、可信资源采集与网络执行链；exec、初始文件/密钥、快照等其他运行操作仍明确拒绝。网络实现需通过真实节点验收后由部署方启用。

## 配置边界

- 一份控制器配置只绑定一个 workspace、RunnerHost 和 Kubernetes Node；命令不能改变绑定。主机的 mwr 凭证只用于既有接入入口，不是用户的 Kubernetes 凭证。
- `RUNTIME_V2_URL` 是现有受信接入入口的基地址。客户端自己添加 `/runner-api/v1`，不要重复配置该路径。
- Kubernetes kubeconfig 必须为绝对路径、由操作者维护并仅允许服务账号读取。只接受 HTTPS 和有效证书校验；不能使用 `insecure-skip-tls-verify: true`。验证依赖固定的 kubectl v1.35.10，系统服务的 PATH 必须能找到经官方 SHA256 校验的二进制。所需 Kubernetes RBAC 仍需在真实集群按最终控制器操作逐项验收，不能给租户配置写入权限。
- journal 与 `.lock` 必须位于同一个持久、仅服务用户可写的目录（模板的 StateDirectory）。不要放 `/tmp`、清空日志以重试，或让同一 host 运行不同 journal 的副本。缺失原始 UID 回执的未知写入不能通过同名对象“补认成功”。
- 模板 `/opt/workspace/services/api`、服务用户与 `/etc/workspace/runtime-v2` 是部署方需要创建/调整的路径，当前环境没有创建这些系统目录或账号。证书、kubeconfig、EnvironmentFile 按最小读取权限配置，不能提交真实 token。
- 正常 SIGTERM/SIGINT 会停止领取与后续写入；已发出的请求仍需保留未知状态。重启进行只读核对，不自动重复未知写入。需要永久保留当前 allocation/generation 的原始资源 UID。

## 启用前仍须完成

1. 完整可信节点/存储采集器及报告身份、采样因果契约；未知资源心跳不能参与分配。
2. 完整运行操作适配和镜像/存储归属验证，不能把仅生命周期控制器当完整 Runner 替代。
3. 至少两个真实节点的 K3s/Cilium、受保护 Node 标签、RuntimeClass、DNS/加密与同实例互通验证。
4. 跨实例/公网入口的身份、来源、租约和既有连接撤销验证；UDP 未安全支持时必须拒绝。
5. 平台、智能体服务器、元数据、宿主网络及公网回流保护，故障/未知结果和回退演练。

当前可核验的上游标签为 Kubernetes v1.35.10、K3s v1.35.10+k3s1、Cilium 1.19.10；见 research 文档中的文件摘要。这不是镜像 digest 锁文件，也不证明这些组件已在本环境兼容运行。本轮已补离线网关部署渲染器和双节点验收 harness；它们不会替代集群的正式安装与验证。

本环境没有已配置的 Kubernetes 集群或 kubeconfig；执行用户有效 capability 为 0，根文件系统可用空间不足以部署完整双节点/Cilium。没有尝试修改共享 Docker daemon、内核或真实远程服务器。当前单元边界、真实本地数据库测试与局部 TLS 检查均不能替代集群验收。

升级/回退事实：`docs/verification/2026-11-11-runtime-v2/UPGRADE.md`；实际结果与未完成项：同目录 `PROGRESS.md`。

## 网络网关部署产物（离线渲染，操作者审阅后部署）

`render_deployment.py` 只生成 Kubernetes JSON List，不连接集群，不创建 Secret，
不发送 ACK。输入必须是通过独立网关凭证、验证 HTTPS 取得的 `/config` 快照；
不要把浏览器会话、普通 host token 或手填的目标地址当作该快照。

先由管理员准备三个已有 Secret：独立网关凭证 `token`、控制面 CA `ca.crt`、
公网证书 `tls.crt`/`tls.key`。凭证只能通过 `secretKeyRef` 注入，文件只读挂载。
API 运行镜像必须由操作者构建、核验并提供真实 `@sha256:` digest；镜像须含
应用代码、Python 依赖及官方 SHA256 核验的 kubectl v1.35.10，支持 UID/GID 1000
和只读根文件系统。这里没有假称已构建或发布过这个镜像。

`options.json` 的结构如下；替换镜像和所有网络值，禁止使用示例地址部署：

```json
{
  "image": "REPLACE_WITH_VERIFIED_RUNTIME_IMAGE_AT_SHA256_DIGEST",
  "control_url": "https://control.example.invalid",
  "credential_secret": "runtime-gateway-auth",
  "tls_secret": "runtime-gateway-tls",
  "ca_secret": "runtime-control-ca",
  "pod_cidrs": ["10.42.0.0/16"],
  "node_cidrs": ["192.0.2.0/24"],
  "platform_cidrs": ["198.51.100.0/24"],
  "control_cidrs": ["198.51.100.10/32"],
  "api_cidrs": ["192.0.2.10/32"],
  "api_port": 6443
}
```

必须枚举**所有**受管 Pod/节点/平台 CIDR，不能漏掉工作台、智能体服务器、
集群 Service/API 实际转发后端和元数据地址。网关出站仅开放配置目标 Pod 的
受保护身份/目标 TCP 端口、受管 CoreDNS、明确控制面和 API CIDR/端口；
`api_cidrs/api_port` 应匹配 kubeconfig/ServiceAccount 实际 API 后端（包括 Cilium
Service 转换后地址）。DNS 例外仅为 kube-system 中 `k8s-app=kube-dns` 的 Pod。
IPv6 输入拒绝，Service 固定 IPv4 SingleStack；没有 IPv6 监听或任意公网出口。

```bash
# 使用操作者已有、仅本机可读的 curl 配置，里面配置独立凭证和 CA；不打印令牌。
curl --config /secure/runtime-gateway.curl-config --fail --max-time 10 \
  --request POST --header 'Content-Type: application/json' --data '{}' \
  https://control.example.invalid/api/v1/internal/runner-network/gateway/config \
  --output /secure/gateway-config.json
python infra/runtime-v2/render_deployment.py \
  --config-snapshot /secure/gateway-config.json --options /secure/options.json \
  --output /secure/reviewed-gateway.json
# 先审阅输出及当前 context；此处仅示范操作者随后显式执行的命令。
kubectl --context VERIFIED_TEST_CONTEXT --kubeconfig /secure/test-kubeconfig \
  apply --dry-run=server -f /secure/reviewed-gateway.json
```

输出含 runner-system Namespace、专用 ServiceAccount、仅 `list pods` 的身份读取
ClusterRole/Binding、单副本网关 Deployment、默认约束 ingress/egress NetworkPolicy。
私网 `runner-gateway` 为 ClusterIP；公网 `runner-gateway-public` 为 TLS LoadBalancer，
`externalTrafficPolicy: Local`、`allocateLoadBalancerNodePorts: false`。
两者只列出快照里**当前 active 的逐记录精确 TCP 端口**；没有 NodePort、hostPort、
hostNetwork 或全端口区间。无 active 绑定时保留不监听的65535占位端口，routes 策略默认拒绝；撤销墓碑不开放用户入口。
当前渲染器上限为 100 条绑定，超限显式拒绝。

公网 LB 必须支持不分配 NodePort 的 Pod 直达转发，并保留 socket 原始 IPv4 来源；
不能在前面接会改写来源的代理。Cilium/部署管理员必须控制 runner-system Namespace
及 `workspace.myworkspace.io/component=network-gateway` 标签，禁止租户 Pod 仿冒；
模板的 restricted Pod Security 标签不会替代这项 RBAC/准入保护。生产环境同时需要
Cilium 身份/反欺骗与受管域策略，不能只部署这个网关 Namespace 的 NetworkPolicy。

网关每次读取认证快照后，会以 UID/resourceVersion 前置条件自动同步固定 Service 的精确端口和独立 routes NetworkPolicy，再读取验证；成功后才允许监听 ACK。
Role 仅有 runner-system 中两个固定 Service 和一个固定 NetworkPolicy 的 get/patch，没有任意 create 权限。没有绑定时使用不监听的65535占位端口和默认拒绝策略；它不是用户入口。控制面/API/DNS例外保留在独立 control 策略。
config SHA256 注解记录所审阅的快照。监听 ACK 不能替代 Service/LB/Cilium 数据面
验收，自动同步读取确认解决 Service 端口发布先后；实际LB来源保留与 Cilium 数据面仍须真实节点验收，不能仅凭 ACK 宣称已验证。

锁定的 Kubernetes v1.35.10 官方 OpenAPI 定义已核验 Service 的
`allocateLoadBalancerNodePorts`、`externalTrafficPolicy`、`ipFamilies`、
`ipFamilyPolicy`、`loadBalancerSourceRanges` 与 ports 字段：
<https://raw.githubusercontent.com/kubernetes/kubernetes/v1.35.10/api/openapi-spec/swagger.json>。
本地文件 SHA256 见本轮 deployment-report；字段存在不证明所选 LB 实现已兼容。

## 两节点非生产验收 harness

`acceptance_two_nodes.py` 只有同时提供 `--allow-test-cluster` 和完全匹配
`kubectl config current-context` 的 `--confirm-context` 才会创建对象；它不会安装
集群/Cilium、改现有 Namespace 或自动开启生产注册。须先完成节点受保护标签、
gVisor、Cilium、CoreDNS 配置，并提供含 Python 标准库的已核验镜像 digest。
使用 API 环境的 Python 执行，以便导入生产 `KubernetesRuntime` 策略构造器。

```bash
services/api/.venv/bin/python infra/runtime-v2/acceptance_two_nodes.py \
  --allow-test-cluster --confirm-context EXACT_NONPRODUCTION_CONTEXT \
  --kubeconfig /secure/test-kubeconfig --node-a runner-node-a --node-b runner-node-b \
  --image 'REPLACE_WITH_VERIFIED_RUNTIME_IMAGE_AT_SHA256_DIGEST' \
  --report /secure/two-node-result.json
```

它创建两个随机 UUID 实例 Namespace：A1/A2 位于 node-a，B1/B2 位于 node-b；
A1/B1 同实例，A2/B2 同实例。测试 Pod 无 PVC、无宿主挂载、无 ServiceAccount token，
按生产 Pod/域策略构造，使用 gVisor 和非 root。实际执行双向跨节点同实例 TCP/UDP echo、
两个实例之间 TCP/UDP 拒绝、四个 Pod 的受管 DNS 查询。它只删除本次 `create`
回执中取得的 Namespace 名称和 UID，删除前再次核对本次 UUID 标签，使用 UID
precondition；绝不按宽泛 selector 清理。若 create 已提交但回执丢失，未知 Namespace
会保留给管理员核查，不能通过同名资源补认自动删除。失败报告包含本次 run UUID。

可加 `--gateway-plan /secure/private-gateway-plan.json` 验收既有 DB 绑定的测试环境：

```json
{
  "source_namespace": "oc-REPLACE_WITH_REGISTERED_INSTANCE_UUID_HEX",
  "source_pod": "REPLACE_WITH_CURRENT_ALLOWED_POD",
  "denied_namespace": "oc-REPLACE_WITH_OTHER_REGISTERED_INSTANCE_UUID_HEX",
  "denied_pod": "REPLACE_WITH_CURRENT_DENIED_POD",
  "gateway_host": "runner-gateway.runner-system.svc.cluster.local",
  "port": 30001,
  "revoke_argv": ["/secure/revoke-test-rule", "/secure/authenticated-client-config"]
}
```

这些必须是当前已登记 allocation/generation/Pod UID 的测试环境，目标运行本 harness
相同的 TCP echo 协议；临时网络 Pod 不能冒充 DB 受信绑定。harness 只对这些既有 Pod
执行受限网络探测，显式 argv 调用管理员提供的撤销测试规则工具（无 shell、30 秒超时、
输出丢弃，密钥只放文件），不会猜测用户会话/二次认证。它验证允许源连通、未授权源拒绝、
已打开流在撤销后 8 秒内关闭，并拒绝新连接。规则撤销是本测试的明确副作用，须使用
专用非生产规则，不会自动重建或回滚授权。

公网单独从**两个不同的真实外部 IPv4 来源**运行（不需要访问集群）：

```bash
services/api/.venv/bin/python infra/runtime-v2/acceptance_two_nodes.py \
  --public-plan /secure/public-plan.json --external-role allowed \
  --report /secure/public-allowed-origin.json
# 在不属于 source_cidrs 的另一台外部主机，使用相同配置 fingerprint：
services/api/.venv/bin/python infra/runtime-v2/acceptance_two_nodes.py \
  --public-plan /secure/public-plan.json --external-role denied \
  --report /secure/public-denied-origin.json
```

公网 plan：`host`（LB DNS/IPv4）、`server_name`（证书验证主机名）、`port`（当前已批准
20000–29999 精确端口）、`ca_file`（只读 CA 路径）、`source_cidrs`（真实批准的 IPv4
列表）、`external_source_expected`（该外部主机经管理员核验的实际出口 IPv4）、
`reviewed_config_sha256`（相同认证快照的 fingerprint）；允许源还可提供明确
`revoke_argv`，测试 TLS 既有连接撤销。TLS 必须验证证书与主机名，连接强制 IPv4。
输出分别记录一台主机的 connected/refused、socket 本地地址、证书 fingerprint 和
来源为管理员断言，不能把 NAT 前本地地址当公网 socket 身份。管理员必须与 LB/网关
真实 peer 审计核对原始来源，并合并两个独立来源产物。LB 可能在 TLS 前拒绝，报告会
保留“可能拒绝/故障”的区别，必须对照同时段允许源 baseline 及控制面健康来判断。
先采集两个来源 baseline，再从允许源执行撤销；不能用撤销后的拒绝证明 CIDR 隔离。

未提供私网 gateway plan、公网外部产物、真实 LB 来源审计时，两节点命令会报告对应
`blocked` 并返回 2，绝不把部分成功当完整网络 PASS。公网单来源命令返回 0 只表示
该次探测已完成，产物中的 `combined_acceptance` 始终等待独立来源及审计；两台主机、
控制面失联、过期、代次替换、平台/节点/元数据/UDP 公网拒绝等剩余验收仍要逐项记录。
当前开发环境没有真实 kubeconfig/双节点/LB，本节命令均未执行集群副作用。

## 网关镜像与节点前提

`Dockerfile.gateway` 从已构建的 Workspace API 不可变镜像扩展，加入官方 SHA256 锁定的 kubectl v1.35.10（当前amd64）。构建参数必须是管理员核验的 image@sha256 引用，渲染器也强制实际部署镜像有digest；不要填浮动tag。当前本地环境没有构建该镜像的磁盘容量；`Runtime network image` CI独立构建测试镜像，验证非root用户、kubectl版本和未配置时拒绝启动，实际结果见PR检查。

Workspace使用Compose部署时，在自己的`.env`中设置`RUNNER_NETWORK_GATEWAY_TOKEN`、`RUNNER_NETWORK_PRIVATE_HOST`、`RUNNER_NETWORK_PUBLIC_HOST`、`RUNNER_NETWORK_POD_CIDRS`及`RUNNER_NETWORK_DENIED_CIDRS`。独立令牌须与网关Secret的token一致，Pod网段和节点/平台禁止网段须对应实际集群。Compose共享环境把这些值同时传给API和Worker；配置更新后需按部署流程重新创建相关服务。未完成部署验收前保持`RUNNER_NETWORK_VERIFIED=false`；这些参数的默认值不会自动启用网络。

公网LB必须实际支持直接Pod路由、保留原socket来源且关闭普通NodePort。不要使用会通过hostPort暴露端口的默认K3s ServiceLB，也不要在前面加会SNAT来源的反向代理。节点/Pod/平台CIDR必须完整进入禁止来源名单，IPv6不支持时在受管域和入口都关闭。真实公网原始来源与允许/拒绝/存量撤销验收未通过前，保留功能开关关闭。

当前测试进度见 `docs/verification/2026-11-13-network-completion/PROGRESS.md`。人类已取消本轮 SSH 登录和远端测试，本次仅交付代码、本地检查和验收材料；未认证或改动远端，真实两节点验收留给部署阶段。

## 后续专用双节点实测补充（2026-11-14 北京时间）

人类随后重新授权两服务器的隔离验收；上述取消远端测试是当时的历史状态。
最新原始输出、失败诊断、UID回执及实际部署见
[`2026-11-13-two-server-acceptance/EXECUTION.md`](../../docs/verification/2026-11-13-two-server-acceptance/EXECUTION.md)。
专用 K3s/Cilium/gVisor 集群已经实际通过基础 TCP/UDP/DNS、跨实例隔离、私网授权网关及
规则/来源能力撤销后的断连检查；公网 LB、两个独立外部来源、生产存储配额和完整运行适配仍是独立门槛，不能据此开启生产开关。

本轮实际环境还验证了下列部署前提：

- API、kubelet 只绑定 Tailscale 地址时，K3s 使用 `egress-selector-mode=disabled`，
  由明确节点 ACL 和集群 TLS 保护直接 kubelet 连接。本轮默认 `agent` 代理到 loopback
  与私有绑定冲突，实际日志/exec 返回 EOF；直接 CA 校验与配置调整后恢复。不要通过关闭 TLS 修复。
- Cilium `policyCIDRMatchMode=nodes` 使网关 NetworkPolicy 中精确的控制面/API 节点 CIDR
  能匹配节点身份。本轮实际 monitor 捕获了指定控制端口的 `Policy denied`；配置并重载
  专用 agent 后恢复。仍只允许所列 CIDR/端口，不能替换为不受限的宿主访问。
- 本轮网关原生 kubectl 自动配置实际回退到 localhost:8080。已使用只读 ConfigMap
  提供显式 kubeconfig，并设置 `RUNTIME_V2_GATEWAY_KUBECONFIG` 指向该文件。
  kubeconfig 只包含已核验 HTTPS API 地址、投射 SA 的 CA 路径及 `tokenFile` 路径，
  不含 token 值；保留原最小 ServiceAccount/RBAC 和凭据轮换。实际结构见验收记录中的
  `gateway-explicit-sa-config.json` 与 `gateway-actual-deployment.json`；这个额外 ConfigMap
  和只读挂载由本轮操作者审阅配置，当前离线渲染器不会自动生成它。
- 私网网关与测试控制面使用独立凭据和短期 CA。只有全新 `APP_ENV=test` 控制面的
  `RUNNER_NETWORK_VERIFIED=true` 用于验收，生产配置没有启用。测试环境的节点标记不能
  作为生产网络、磁盘配额或生命周期全链路已验证的声明。
- 受管运行环境不应访问节点/平台/元数据/公网。本轮六个禁止目标都以仅 TCP 握手的
  负向探测和对应 Cilium `Policy denied` 事件核对，未读取元数据或数据库内容。

测试记录中的两条专用入站规则和来源通信能力已撤销；撤下 ACK 已确认，网关 Service
仅剩不监听的65535占位端口。专用集群/控制面保留用于复核，身份备份与原业务卷保留。

数据库迁移已整合后续主干的编号修复：最终链为0066会话桥→0067网关→0068历史核对。
0066/0067的旧部署通过在线实际结构核对补缺，保留既有桥权限、记录与端口序列；
未知/部分结构及未验证约束会拒绝升级，不支持离线SQL导出。不要用手工stamp跳过核对。
0068回退不删表；实际删除桥表的0066回退入口持锁检查空表，有桥数据会拒绝破坏性回退。
本轮已在专用真实0067库完成0068升级，记录摘要和分配序列保留；这不代表生产已迁移。
