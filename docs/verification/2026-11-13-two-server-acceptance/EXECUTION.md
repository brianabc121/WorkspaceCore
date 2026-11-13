# T-0224 隔离验收执行账本

所有时间为 UTC（北京时间加8小时）。持续执行中，不作为完整验收通过证明。

## 授权与边界

人类在本聊天明确回复“继续 给你所有授权”，覆盖先前已提出的 B 身份维护/重启、迁移修复、专用测试集群和控制面、实际验收以及同任务 PR 记录发布。继续保护既有服务、数据和身份防重规则，不启用生产网络，不合并 PR。

## 1. B 身份维护：已执行并复核

- 16:15:52，严格核对 B=`100.121.210.36/s8006604`、原文件摘要和类型后，在 `/root/t0224-machine-id-backup-Mbl4zYp0` 保存原 `/etc/machine-id` 与 `/var/lib/dbus/machine-id`。
- 使用主机自带 `systemd-id128 new` 生成唯一值，保留原 root:root 和0444/0644权限，一次授权重启完成。两文件的新 SHA256 均为 `4eefda99b588efcf38cfab5835e1bfb57ce232f964b47dfaf7312aa9ab7b1beb`。
- B boot_id 摘要变为 `53b74482e7fd53a94ad5492412d8559a02dcf79cf91418e9cc532c68cfc15a92`；A machine-id 和 boot_id 均未变。
- 严格 SSH 指纹仍匹配原可信公钥，B 的 Tailscale 节点和 IP 未变；两机 ssh/dbus/tailscaled 正常，双向 TCP22 再次通过。原始输出：`b-identity-change.txt`、`post-maintenance-a.txt`、`post-maintenance-b.txt`。
- A 原有 Docker 网络 ID/名称及 TrendRadar 配置卷保留，未清理旧镜像、网络、卷或未挂载磁盘。

## 2. 迁移冲突：修复与审阅

Ruling: 保留历史 runner 网关0066，以0067接入会话桥；检查两条历史分支的实际结构，仅补缺失分支，不重置现有序列或删桥数据。原因：同名0066可能代表两种已经部署的状态；单纯改号会破坏旧桥部署升级。

- 第一轮新回归真实 RED：4 failed（重复0066），修复后最终初组7 passed / 24.31s。网关/桥API组合20 passed / 67.36s。
- 按代码审阅技能派独立只读审阅，发现三个 Important：只看对象名称不足以拒绝部分状态、neither-present未知0066被误补、旧桥数据回退会丢失。各新增回归，实际7 failed / 15.23s。
- 加入类型、空值/default、PK/FK/unique/check及序列属性核对；未知/部分状态拒绝；任何非空桥表在降级前拒绝。PostgreSQL对 `"workspace"` 的 regclass默认值引号作等价规范化，保持序列名称精确匹配。
- 最终审阅修复定向：14 passed / 29.86s，包含两种历史0066、两分支同时存在、桥权限字段、全部三条序列、异常结构拒绝和数据回退保护。原始输出见 `migration-review-red.txt`、`migration-review-green.txt`（最终版本）、`migration-final.txt`。
- Ruling: 0067 的旧状态核对要求在线数据库，不支持生成离线 `alembic --sql` 脚本；明确报错，不把未知旧结构假设为完整。
- Ruling: 回退到0066采用规范化的网关0066语义，非空桥表拒绝破坏性降级；仅在专用空库执行完整up/down/up。未对生产库执行迁移、stamp、删表或降级。
- 第一轮全套在审阅修复更新期间被新列定义核对阻塞，随后主动停止：3 failed、761 passed、9 skipped、68 errors / 1226.51s，**不是通过**。失败为 `test_migrations_upgrade_downgrade_on_empty_database`、`test_migration_0025_drops_note_confirmation`、`test_migration_0027_task_timeline_keeps_existing_tasks`；错误来自迁移准备阶段同一默认值规范化问题。完整历史日志压缩保存在 `api-superseded-interrupted.log.gz`。最终代码的完整后端 suite 已重新启动，结果待补。
- 最终代码 Ruff、format（562文件）、mypy（558文件）通过；正式OpenAPI检查无变更。

## 3. 专用非生产集群：已部署

- 服务名两机均为 `workspace-t0224-k3s`（A server、B agent），未设置开机自启。
- 二进制 `/opt/workspace-t0224/bin`；配置 `/etc/workspace-t0224`；状态 `/var/lib/workspace-t0224`。修改前的服务/路由/iptables快照保存在各机 `/var/lib/workspace-t0224/preflight/`。
- Kubernetes/K3s `v1.35.10+k3s1`，二进制 SHA256 `5cd49f0faca5b2ea4355d4761e7b1445b8aa05cd50e612fd9c370551db49e5e2`，官方发布摘要验证成功。
- gVisor `release-20261109.0`，使用官方固定日期 tar 包及 SHA512 验证，完整安装runsc/shim/sidecar；runsc SHA256 `c58f1dc2a3855487bf3bedc2edf074123ad433f1d98fb7f451f56790e87b5c9f`。
- Helm `v3.19.0`，官方 tar SHA256 `a7f81ce08007091b86d8bd696eb4d86b8d0f2e1b9f6c714be62f82f96a594496`；Cilium chart `1.19.10` SHA256 `b7252afca3413a33e2b987c954e52441e96979e411e1495ecc6aefe8cd258863`，chart固定镜像digest。
- PodCIDR `10.244.0.0/16`；ServiceCIDR `10.245.0.0/16`；节点 `t0224-node-a`、`t0224-node-b`，API与kubelet仅绑定Tailscale IP。CNI路径独立为 `/var/lib/workspace-t0224/cni/{bin,net.d}`。
- 禁用默认Flannel、K3s network-policy、kube-proxy、Traefik、ServiceLB、local-storage、metrics-server；Cilium使用VXLAN8472经Tailscale加密节点链路，kubernetes IPAM、kube-proxy replacement、legacy host routing。保留宿主Docker网络。
- Ruling: 本轮不启用Cilium额外healthChecking端口4240，健康端点探测不作为已测项；Cilium agent/Pod readiness保持原默认断言。实际数据面测试独立执行，未因此略过TCP/UDP/隔离断言。
- 人类已应用 B→A TCP6443、双向TCP10250和UDP8472的精确Tailscale grants。
- 已设置受保护Node标签和 `gvisor` RuntimeClass；containerd2.2.10通过专用v3 config drop-in配置runsc与CNI，未覆盖已有宿主containerd配置。

## 4. 真实双节点基础数据面：通过；整体仍blocked

命令与README一致，使用既有 `acceptance_two_nodes.py`，仅将kubectl通过严格SSH委托给A的官方K3s kubectl，TLS仍校验。输入：

```text
--allow-test-cluster --confirm-context t0224-nonproduction
--kubeconfig /etc/workspace-t0224/test-kubeconfig
--node-a t0224-node-a --node-b t0224-node-b
--image docker.io/library/python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
```

- 初次失败 run UUID `b895e439-2df9-43d5-a7a3-82f93d1d6730`，结果 `two-node-initial.json`，没有计为通过。
- 原因核实：K3s默认 `egress-selector-mode=agent` 的kubelet代理路径假设loopback，而为保护服务器将kubelet只绑定Tailscale IP。实际CA校验的直连 A/B10250均HTTP401（符合无凭据探测），127.0.0.1拒绝，API日志/exec代理EOF。
- 仅测试K3s server改用 `egress-selector-mode=disabled`，由已有精确ACL和集群TLS保护直达kubelet；未关闭证书校验或放宽断言/超时。B的日志接口恢复。
- 重跑 run UUID `8e363798-be80-45b0-bc2c-79807dc802db`，报告 `two-node-after-kubelet-fix.json`：`private_tcp_udp_dns=passed`。四个gVisor Pod分布在真实两机，完成双向同实例TCP/UDP、跨实例直接路径TCP/UDP拒绝和四个DNS查询；本次随机Namespace按UID回执清理。
- 退出码仍为2，因未提供DB-backed网关fixture和真实公网LB/外部来源。未把该部分PASS当完整网络验收。

## 5. 网关验收控制面与fixture：准备中

- 仅新建Docker `t0224-control` 网络（172.31.240.0/24）、`t0224-control-pg`卷以及`t0224-pg`、`t0224-redis`、`t0224-control-api`、`t0224-control-tls`容器。PG/Redis/Caddy使用仓库已有固定digest，未清理或复用生产数据。
- 控制面为独立数据库、合成owner/实例、专用网关凭据和三天短期CA。私钥/口令仅root文件；不提交凭据、cookie、kubeconfig或含真实值的env文件。
- **只有这个全新测试控制面**的 `RUNNER_NETWORK_VERIFIED=true` 用于执行验收；生产配置和原环境的默认false未改，不将测试fixture的标记当作生产验收完成。
- TLS只绑定A的Tailscale8443，使用独立CA和正确SAN验证，数据库无宿主公开端口。运行服务器入口头仅由可信Caddy172.31.240.5写入；mwr token仍独立校验。
- 初次测试镜像发现工作区源码0600/目录0700经COPY成root所有后非root不可读，仅规范化专用测试构建上下文的代码读取权限后重建；没有修改仓库、业务目录或密钥权限。新镜像迁移head0067和测试库版本0067已实际读取确认。
- 后续使用独立真实PVC/Deployment及生产 `KubernetesRunner` 的创建/UID回执/observe绑定，不使用前述临时网络Pod冒充可信DB绑定。专用测试本地PV的磁盘配额/生产存储采集仍未验收，不能以网络结果宣称该存储档位可生产调度。
- 网关镜像含官方SHA256校验的kubectl v1.35.10，最终部署使用实际内容digest；镜像只在测试主机本地构建/导入，不推公共镜像仓库。

下一步：完成fixture、通过正常owner授权与入站规则配置，真实网关读取socket来源/当前PodUID和DB绑定后验证允许源、拒绝源及撤销存量/新连接。公网LB与两个独立外部来源仍未提供，生产网络开关保持关闭。
