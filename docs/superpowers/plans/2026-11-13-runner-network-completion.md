# T-0224 网络执行链补齐

基线：main e3c3766。人类于2026-11-13明确要求完成原设计中剩余的同实例跨服务器网络、授权跨实例/公网连接与撤销断连、管理接口及页面。延续2026-11-11已确认设计，不改协作协议，不自动迁移旧Docker环境。

## 约束与实施决定

- 同一受管Kubernetes/Cilium集群，workspace+instance为私网信任域；直接跨域流量始终拒绝。同域TCP/UDP不经业务网关；DNS只允许受管CoreDNS。
- 跨域和公网TCP经平台网关，批准记录、来源身份、目标服务、代次和有限租约每次连接及续租都校验。UDP跨域/公网明确返回不支持，不开不受控入口。
- 私网网关从socket对端IPv4和Kubernetes当前Pod UID/保护标签取得身份，不信用户请求头；控制面重新验证environment/allocation/generation/pod UID。Cilium阻止身份伪造；私网入口仅ClusterIP，不经会改写来源的公网代理。
- 公网网关必须TLS、保留原始socket来源，拒绝所有受管Pod/节点/平台CIDR和IPv6；仅明确IPv4来源名单可入站。LoadBalancer externalTrafficPolicy=Local。未知来源和SNAT后身份丢失拒绝。
- 网关到控制面使用独立SecretStr凭据和验证TLS，不能用浏览器会话或普通host凭据。TCP连接最多60秒租约，每2秒重新授权；撤销、过期、服务删除/停止、身份变化、控制面失联均关闭已建立连接。
- 地址只在实际策略/网关绑定回报确认后显示可连接，撤销立即停止新连接，页面区分等待断连确认。
- 未配置真实双节点时仍完成可测试源码、部署材料与验收脚本，如实记录集群验收阻塞；不能把本地socket/数据库测试当跨节点已验证。

## Task 1 — 控制面与网关契约（主代理）

文件：domain/runner_network.py、schemas/runner_network.py、models/runner_network.py、api/v1/runner_network.py、新domain/runner_network_gateway.py、新api/runner_network_gateway.py、config/main、0066迁移及测试。

- [x] 测试先行：权限隔离、同服务编辑、版本冲突、代次变化、来源Grant过期、端口唯一、已撤销拒绝新连接、网关凭据隔离。
- [x] 为规则/公网入口分配固定网关端口；GET控制面快照、POST lease、POST ack。控制面不接受任意目标地址，目标来自当前受信运行绑定。
- [x] 管理接口补编辑/查询和可执行状态；网络输出含确认地址、PodIP、撤销确认。

网关专用前缀 `/api/v1/internal/runner-network/gateway`，Authorization Bearer独立令牌；此机器间协议不进入浏览器OpenAPI：

- `POST /config {}` → `{revision:1, bindings:[{record_id,kind:'ingress'|'published',port,revision,generation,active,source_cidrs}], denied_cidrs:[...]}`；包含撤销墓碑。
- `POST /lease {record_id,kind,peer_ip,source?:{workspace_id,instance_id,environment_id,allocation_id,generation,pod_uid}}` → `{allowed,lease_seconds,revision,generation,target?:{workspace_id,instance_id,environment_id,allocation_id,generation,pod_uid,pod_ip,node_name,port}}`；拒绝不回目标。
- `POST /ack {record_id,kind,revision,generation,success,active}` → `{accepted}`；active区分监听启动与撤下。

## Task 2 — 私网持续对账（独立运行层）

文件：runtime_v2/network.py、runner.py、kubernetes.py、controller.py、__main__.py及runtime_v2测试。消费现有host `/network/desired`、`/network/ack`。desired新增`domains`，每项`workspace_id,instance_id,environment_id,allocation_id,generation,node_name`。

- [x] RED→GREEN：受控namespace、同域跨节点策略、受限DNS、策略更新旧版本拒绝；私有Service创建/更新/删除实际读取后ACK；无UID不报成功；单服务失败不阻断撤销。
- [x] 所有环境即使未登记Service也有domain；create前安装默认拒绝/同域策略；主循环每次持续对账，允许观测结果未知后安全恢复。
- [x] 网关身份仅runner-system命名空间受控标签`workspace.myworkspace.io/component=network-gateway`，不向任意Pod开放目标端口；工作负载只允许访问网关已配置服务范围，平台禁区仍拒绝。

## Task 3 — TCP租约网关（独立模块）

文件：runtime_v2/gateway.py、gateway_main.py及独立socket测试。消费Task1 HTTP契约；Kubectl只读当前Pod身份，验证target UID/IP/保护标签。

- [x] RED→GREEN：真实socket双向转发、拒绝来源、过期/撤销断开已有连接、控制面故障即断、代次替换拒绝、负载有界/连接上限/时间限制、TLS公网入口、伪造转发头不生效。
- [x] 私网来源必须精确匹配唯一Running/Ready Pod，socket IP与保护标签/namespace和UID一致，不能缓存过旧身份；公布端口与规则一一对应。
- [x] 配置回报成功前监听/身份依赖必须就绪；绑定撤销关闭监听及会话再ACK；令牌/证书不进日志。

## Task 4 — 管理页面（独立前端）

文件：apps/web/src/features/runner/ContainerNetworkPanel.vue、api.ts及页面测试；生成类型由主代理统一生成。

- [x] 私有地址/实际节点/同域说明、来源实例选择、服务及授权编辑、过期时间、CIDR、网关TCP说明、版本与撤销确认；失效数据不显示可连接地址。
- [x] Owner写操作二次认证、保留失败前输入、切workspace防串数据；等待/失败/已生效明确区分。

## Task 5 — 部署与验收（主代理集成）

- [ ] 官方锁定版本/schema核验；部署namespace/RBAC/Cilium/gateway TLS与受限入口模板、双节点正反向验收及回退说明。
- [ ] 真实数据库和真实socket覆盖；Node22 npm ci/lint/typecheck/test/check:api/build，ruff/mypy/pytest/OpenAPI/迁移/collab。
- [ ] 独立最终审查，逐项原需求覆盖。未真实验收不标done。代码和任务进度一起提交/推送PR交Claude审查，不合并。
