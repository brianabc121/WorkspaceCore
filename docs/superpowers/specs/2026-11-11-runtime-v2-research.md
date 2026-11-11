# 官方资料调研与代码依据

研究性质：查阅官方资料并结合本仓库设计；未执行新运行层、没有使用真实生产节点/密钥验证。产品技术组合必须在P0核验版本/内核/网络后才能进入实施。

## 官方资料与采用方式

| 来源 | 核验到的原则 | 本方案采用/限制 |
|---|---|---|
| [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/) | 源出站和目标入站分别约束；需支持策略的网络插件 | 同域默认允许、跨域默认拒绝；跨域目标入站保留控制 |
| [Kubernetes ResourceQuota](https://kubernetes.io/docs/concepts/policy/resource-quotas/) | 对象数量与资源预算可分别控制 | 数量豁免不等于资源无限；停用环境的业务名额仍由Workspace记账 |
| [Kubernetes资源调度](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/) | 调度按资源requests判容量，不依据瞬时用量自然平衡 | Workspace另做实时压力筛选和预约，K8s最终检查requests |
| [Kubernetes节点选择](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/) | required亲和是硬约束，nodeName绕过调度器 | specified及选定节点使用必需亲和，保留原生容量检查 |
| [Docker overlay](https://docs.docker.com/engine/network/drivers/overlay/) | 多宿主网络；独立容器仍需Swarm与attachable；可选加密 | 可作Docker替代路线，不等于已有租户ACL/审批 |
| [Docker防火墙](https://docs.docker.com/engine/network/packet-filtering-firewalls/) | Docker端口发布与宿主防火墙有特定处理顺序，UFW不能简单兜底 | 严格入口必须验证实际流量路径 |
| [Docker iptables](https://docs.docker.com/engine/network/firewall-iptables/) | 部分直接路由流量在raw PREROUTING先被丢弃，DOCKER-USER不是万能放行点 | 不写未经验证的一条iptables规则宣称跨主机已完成 |
| [Cilium网络策略](https://docs.cilium.io/en/stable/security/policy/) | 支持工作负载网络策略 | 策略按不可伪造的实例/环境身份编译 |
| [Cilium显式deny](https://docs.cilium.io/en/stable/security/policy/deny/) | 显式deny优先 | 硬禁区不被广域授权覆盖；不把受管Pod网误列为硬禁区 |
| [Cilium WireGuard](https://docs.cilium.io/en/stable/security/network/encryption-wireguard/) | 支持跨节点工作负载透明加密，存在配置/流量范围边界 | 加密需明确启用和实测，不等于全部节点流量自动加密 |
| [WireGuard](https://www.wireguard.com/) | 公钥和AllowedIPs关联节点隧道，Key分发属外层职责 | 节点私网加密与容器业务权限分层 |
| [Cilium on K3s](https://docs.cilium.io/en/stable/installation/k3s/) | 有官方接入指引，需协调原CNI/网络策略组件 | 不把默认Flannel与Cilium策略引擎叠着装 |
| [K3s替代运行时](https://docs.k3s.io/advanced) | 可配置替代运行时 | 本项目runsc/KVM能力逐节点核验 |
| [gVisor containerd](https://gvisor.dev/docs/user_guide/containerd/quick_start/) | 通过containerd与RuntimeClass接入 | Docker上跑通不代表K3s组合已实测 |

以上是官方产品实践，不引用或虚构某位专家已经审阅过Workspace。本文的架构选择是作者基于这些资料的推导。

## 源码基线

origin/main = a1053c77573ebd87a4e82566b40ac302f1db86ac。
读取使用git show固定提交，不使用旧codex/T-0149分支的未提交文件。
2026-11-11修订基线为PR #192；此前清单中15个源码文件的SHA256在新基线上均未变化，本次另加入6个智能体部署/指标/UI来源。source-manifest.json可复核。

| 路径 | 当前事实/改造原因 |
|---|---|
| services/api/app/domain/agent_deployments.py | create/_execute必须server_id/server_version，已有幂等收据与宿主锁；没有默认自动选址 |
| services/api/app/domain/agent_servers.py | deployable_target检查初始化/密码/任务；capacity_warning低内存/磁盘仅提醒，未作资源预约 |
| services/api/app/domain/host_metrics.py | HostService.report以AgentInstance接收宿主指标；新空宿主不能依赖此路径完成首个部署选址 |
| services/api/app/models/agent.py | AgentServer/实例宿主关联及资源字段；新增Placement和预约模型须与现有版本一致 |
| services/api/app/schemas/agents.py | AgentDeploymentCreateIn.server_id/server_version必填，cpus/mem_limit_mb默认null |
| apps/web/src/features/agents/AgentDeployDrawer.vue | 目前表单必须选择服务器；上限留空为不限制，容量展示明确不等于实际占用 |
| services/api/app/domain/runner_specs.py | 默认envs3、persistent2；每环境独立网络名 |
| services/api/app/models/runner.py | runner_hosts.workspace_id唯一；环境host_id不可空 |
| services/api/app/domain/runner_hosts.py | create拒绝第二台；认证与指令领取已按host身份 |
| services/api/app/domain/runner.py | 按单机online_host、active配额、shared can_use和exec |
| services/api/app/domain/runner_tools.py | RunnerError分支rollback，不能把新审批写进同一失败事务 |
| services/api/app/domain/ai_tools.py | pending审批、critical二次认证、参数哈希、登记人执行语义、执行后清除arguments |
| services/api/app/schemas/runner.py | 无通用服务/端口/网络域字段，需要新增而非编造已存在接口 |
| services/api/app/runner_agent/containers.py | Docker参数受控，默认无特权/hostNetwork；gVisor DNS有已知特殊限制 |
| services/api/app/runner_agent/agent.py | 每环境internal网络、固定出口代理、按env串行生命周期 |
| services/api/app/runner_agent/proxy.py | all只接受公网IP，受保护地址拒绝 |
| services/api/app/domain/runner_snapshots.py | 数据卷快照/恢复需保留，跨节点不是自动共享盘 |
| docs/AGENTS.md | 现行规则禁止私网访问，新受管容器私网例外须设计确认并更新ADR |
| docs/v2/RUNNER_PLAN.md | 含“附属服务”设想但当前接口未落地；每环境独立隔离 |

## 实施前不可省略的核验

- 锁定K3s/containerd/Cilium/gVisor版本和digest，在目标Linux内核实测。
- 真正跨两台节点的数据库、HTTP、UDP、DNS与租户隔离。
- 授权撤销的现有连接行为；API保存成功不能代替节点策略生效。
- 本地卷/CSI能力、镜像仓库可达、节点资源账本与未知状态幂等。
- 共享环境的直接exec是否会提供他人的网络身份，迁移须有明确处理。

## 本次新增设计核验边界

独立宿主探测、双池PlacementService、资源预约、实时负载评分、auto/specified契约均是建议新增；未安装报告器、未改真实服务器、未验证实际调度。Kubernetes官方文档核实于2026-11-11。
