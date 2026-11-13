# T-0224 网络执行与生效边界

状态：accepted（既有双池/私网设计的实现细化）；范围仅本任务。人类2026-11-13要求补齐同实例跨服务器、跨实例授权/受控公网/存量连接撤销、管理接口页面。

## 数据路径

同实例在同一受管 Kubernetes/Cilium 集群内，以受保护 workspace+instance 标签和 namespace 划分默认信任域，同域 TCP/UDP 跨节点直达。基础策略默认拒绝，另允许明确的受管 DNS。私网网关出站端口、目标入站端口按实际授权服务精确生成；不直接放通跨 namespace 工作负载。

跨实例 TCP 经私网 ClusterIP 网关。网关取原始 socket 地址并从验证 TLS 的 Kubernetes API 查询当前唯一 Ready Pod、UID、namespace 和保护标签；控制面再次校验 workspace、instance、environment、allocation、generation、Pod UID 和来源授权。不能用转发头或共享 NAT 地址代替身份。此路径依赖经实测的 Cilium 身份反欺骗、保护标签准入、跨节点加密和来源保留，不能仅凭 API 对象存在认定已具备这些能力。

公网 TCP 经 TLS 网关、明确 IPv4 来源名单与受管节点/Pod/平台网段排除。负载均衡必须保留原始来源、支持关闭普通 NodePort 的 Pod 直达；禁止把改写来源的代理或 K3s 默认 hostPort ServiceLB 当作兼容实现。跨实例/公网 UDP 明确拒绝；不提供未经验证的降级放行。

网关到控制面使用独立凭据和验证 TLS。应用容器不获取网关凭据、Kubernetes 凭据或 Docker socket。

网关控制路由属于机器间协议，不进入浏览器OpenAPI/生成类型；其路径和协议继续在部署材料中说明。浏览器公开API保持统一错误文档、会话与CSRF规则；内部网关另外验证独立令牌，浏览器会话不能代替该令牌。

## 生效、撤销与运行身份

新增入站规则会推进父 Service 修订，私网控制器完成目标网关入站策略与 Service 读取验证后，控制面才向网关提供目标。网关以 UID/resourceVersion 条件自动同步两个固定 Service 和单独 routes NetworkPolicy；读取确认以及公网地址分配后才发送监听 ACK。权限批准、策略声明、应用回报、可连接地址是不同事实。

每条 TCP 连接获取不超过60秒的租约（当前控制面返回至多20秒），每2秒重新核验。撤销/过期/权限或 UID 变化/控制面失联均禁止新增并关闭已有会话。管理页面只把实际终止回报视为“撤销已确认”；到达过期时间不冒充回报。停止环境不再返回可连接地址。

Deployment/PVC 原始 UID 持久在命令回执；Pod 自动重建时仅允许在原始 UID/分配/代次下刷新。旧代次、换部署、待完成生命周期操作、停止环境均不能借周期报告恢复。Kubernetes 资源报告不调用 Docker 的环境丢失/残留对账。

Kubernetes 的 NetworkPolicy 可选空方向列表与空 annotations 按 API 序列化语义比较；保留 policyTypes、UID、resourceVersion、身份与版本约束，不能为收敛而删除隔离要求。官方锁定来源：<https://raw.githubusercontent.com/kubernetes/api/v0.35.10/networking/v1/types.go>。

## 有界运行与资源

网关最多100个同时保有的入口（含待应用及未确认撤下的入口），创建前以跨 workspace 的 PostgreSQL 事务锁预约；它与每个 OpenClaw 默认5个环境的额度无关。过期或撤销后，需当前修订和代次的实际撤下回报才释放共享容量，避免待处理墓碑累积使网关超限。超过限制返回明确冲突，不把一条请求转成其他 workspace 的服务中断。旧配置中不可发布的来源仅撤下该绑定。端口分两条不循环序列，旧端口不重用；端口池用尽需管理员扩容。

当前 Kubernetes 工作负载档位是不可变镜像、gVisor、非root、实例私网与已验证的本地 PV；不支持的初始文件、密钥、setup、exec、快照等操作在预约前拒绝。可信资源未知不按0处理；平台网关的 node 使用已经包含于节点可用量，不冒充用户环境。旧 Docker 环境继续使用原后端，不自动迁入新网。

当全局网络验证开关已启用，且 workspace 已有 Kubernetes 容器服务器时，新建环境进入受管网络池：自动分配不会在 Kubernetes 容量不足时静默落到旧 Docker，显式指定旧 Docker 也返回明确不支持原因。这样避免同一实例的新环境混用两个不可互通的后端。已有 Docker 环境的原地重启仍沿用其原宿主；尚未启用验证开关的节点不能仅凭自己的上报进入受管池。

公网 LoadBalancer 地址尚未分配时，仅撤下公网绑定，保留已确认的私网路由；ACK 明确区分监听已启动与已撤下。共享 Kubernetes 身份查询可用且基础配置有效时，单个目标失效不会阻止其他已批准通信权限生效；目标失效本身仍撤下监听，单条连接仍逐次验证目标和授权。

## 验证与开启

默认 RUNNER_NETWORK_VERIFIED=false。单元测试、本地真实 socket/TLS、PostgreSQL 与官方 kubectl/TLS 边界测试可验证实现逻辑，不能证明 Cilium 或公网 LB 实际执行。必须在真实测试节点完成跨节点 TCP/UDP/DNS、跨实例拒绝/按规则放行、原始公网来源与允许/拒绝、在途撤销、Pod 重建与失联验收后，由部署方启用。人类已于2026-11-13取消本轮 SSH 登录和远端测试，开发交付继续完成本地可执行检查及部署验收材料；尚未进行远端变更或真实两节点验收。
