# T-0224 隔离验收执行账本

所有时间为 UTC（北京时间加8小时）。私网跨节点互通、隔离、授权和两种撤销已实测通过；公网及更广运行适配仍未验收，不作为完整生产验收通过证明。

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
- 第一轮全套在审阅修复更新期间被新列定义核对阻塞，随后主动停止：3 failed、761 passed、9 skipped、68 errors / 1226.51s，**不是通过**。失败为 `test_migrations_upgrade_downgrade_on_empty_database`、`test_migration_0025_drops_note_confirmation`、`test_migration_0027_task_timeline_keeps_existing_tasks`；错误来自迁移准备阶段同一默认值规范化问题。完整历史日志压缩保存在 `api-superseded-interrupted.log.gz`。该版本后续完整运行结果见第7节；整合新主干后的最终迁移图和定向结果见第9节。
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

## 4. 真实双节点基础数据面：通过；公网仍blocked

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

## 5. 真实登记工作负载与私网网关：通过

- 仅新建Docker `t0224-control` 网络（172.31.240.0/24）、`t0224-control-pg`卷以及`t0224-pg`、`t0224-redis`、`t0224-control-api`、`t0224-control-tls`容器。PG/Redis/Caddy使用仓库已有固定digest，未清理或复用生产数据。
- 控制面为独立数据库、合成owner/实例、专用网关凭据和三天短期CA。私钥/口令仅root文件；不提交凭据、cookie、kubeconfig或含真实值的env文件。
- **只有这个全新测试控制面**的 `RUNNER_NETWORK_VERIFIED=true` 用于执行验收；生产配置和原环境的默认false未改，不将测试fixture的标记当作生产验收完成。
- TLS只绑定A的Tailscale8443，使用独立CA和正确SAN验证，数据库无宿主公开端口。运行服务器入口头仅由可信Caddy172.31.240.5写入；mwr token仍独立校验。
- 初次测试镜像发现工作区源码0600/目录0700经COPY成root所有后非root不可读，仅规范化专用测试构建上下文的代码读取权限后重建；没有修改仓库、业务目录或密钥权限。新镜像迁移head0067和测试库版本0067已实际读取确认。
- 已使用独立真实PVC/Deployment及生产 `KubernetesRunner` 的创建/UID回执/observe绑定，不使用前述临时网络Pod冒充可信DB绑定。专用测试本地PV的磁盘配额/生产存储采集仍未验收，不能以网络结果宣称该存储档位可生产调度。
- 网关镜像含官方SHA256校验的kubectl v1.35.10，最终部署使用实际内容digest；镜像只在测试主机本地构建/导入，不推公共镜像仓库。

### 实际部署修正

- 网关首次无法访问控制面；实际 Cilium monitor 捕获 Pod→A:8443 的 `Policy denied`。固定版本 chart 配置 `policyCIDRMatchMode=nodes` 并重载专用 Cilium 后，原 NetworkPolicy 的精确节点 /32 和端口生效；没有放宽为任意宿主访问。
- 原生 kubectl 未自动采用投射 ServiceAccount，实际回退 localhost:8080。增加只读 ConfigMap 显式 kubeconfig，使用已核验 HTTPS API、SA 的 CA 路径和 `tokenFile` 路径，保留最小 RBAC 和投射 token 轮换。实际配置为 `gateway-explicit-sa-config.json`、`gateway-actual-deployment.json`；不包含 token 值。离线渲染器不会自动生成该额外 ConfigMap，部署方仍需提供此配置。

### 真实来源、目标与断言

本轮 run UUID `1e4c73d9-ad80-4b64-af4f-bab67c84d249`。三个合成实例由正常 owner API 创建；专用数据库内的主机/环境fixture通过真实物理身份 claim、防重检查、Kubernetes 创建回执与当前 Pod observation 建立绑定。它们不是生产实例，也不代表已验收完整生命周期控制器/资源采集。

|角色|真实节点|当前 Pod UID|Pod IP|
|---|---|---|---|
|允许来源|A|`ebb70210-d5ae-4606-aa65-1729b17fc724`|10.244.0.222|
|拒绝来源|A|`9724e224-a015-4826-b19c-eb91bd67961c`|10.244.0.178|
|目标|B|`fbd28395-fb30-4363-a7fb-a1c3b864b451`|10.244.1.163|

三个工作负载使用固定 digest 的 Python echo 镜像、gVisor、非 root、无特权、无 SA token、无宿主网络，实际 UID/Pod/资源回执见 `fixture-resource-receipts.json`、`cluster-final-snapshot.json`。

1. 无来源通信能力时，正常创建入站规则 API 返回403 `SOURCE_CAPABILITY_REQUIRED`。
2. 通过 owner 的正常二次确认和 Critical 审批，独立网关实际领取并执行来源能力授权；之后创建规则，真实 listener ACK 生效，端口30000。没有直接把数据库状态写成 active。
3. 在既有 `acceptance_two_nodes.py` 的 **原始 `gateway_checks`** 上执行：允许来源经网关访问跨节点目标成功，拒绝来源失败；两种来源直达目标的 TCP/UDP 均失败。拒绝来源曾有仅指向该网关/端口的临时诊断 egress Policy，结束后按原 UID/resourceVersion 删除；没有开放目标或其他流量。现有 harness 判定连接失败，不额外宣称已单独区分所有拒绝发生层次。
4. 打开持续 TCP 流，经正常 owner API 撤销规则；harness 原断言确认 **8秒内存量流断开，新连接被拒绝**。18:29:45 输出 `private_gateway_allowed_denied_revocation=passed`。`elapsed_seconds=11.617` 是整个网关检查耗时，不能当作撤销延迟。
5. 用新规则端口30001再测撤销**来源通信能力**，同一原始 harness 确认存量流8秒内断开、新连接被拒绝。18:34:38 输出 `source_capability_revocation=passed`。端口单调递增，没有复用已撤销30000。
6. 撤销后目标自身 echo 仍成功、测试控制面 ready、原工作负载 UID 不变；网关 UID `c3a6fbac-5faa-4e26-98a4-1c182133c86c` 不变且重启次数仍0，排除靠目标/网关退出冒充撤销。

实际操作驱动保存在 `operator-fixture-driver.source.txt`，两个正常撤销 API 驱动为 `revoke-rule-driver.source.txt`、`revoke-capability-driver.source.txt`。这些是本次受信任操作者使用的测试夹具源码记录，不是自动生产安装入口；仅路径/环境变量引用，不包含实际凭据。运行于专用工具镜像，数据库/管理员权限仅对新测试控制面有效。

已执行的容器内命令按阶段为（共同的 root 私有 env/CA/kubeconfig/状态目录只读或专用挂载未在此展开）：

```sh
python /opt/t0224/lab_fixture.py bootstrap
python /opt/t0224/lab_fixture.py deploy
python /opt/t0224/lab_fixture.py network
python /opt/t0224/lab_fixture.py authorize
python /opt/t0224/lab_fixture.py gateway-test
python /opt/t0224/lab_fixture.py capability-test
python /opt/t0224/lab_fixture.py settle
```

原始输出分别见 `registered-workloads.txt`、`owner-authorization.txt`、`private-gateway-acceptance.txt`、`capability-revocation.txt`、`final-rule-withdrawal.txt`；机器可读结果为 `gateway-result.json`、`capability-result.json` 及两个 revoke-result。基础 harness 的 kubectl 完整 argv/退出码/stderr 记录在 `kubectl-commands.jsonl`，包括先前失败；不记录 stdin、凭据或响应中的 Secret。

## 6. 禁止出站与结束状态

- 同一真实允许来源 Pod 在撤销完成后，依次仅尝试 TCP 连接 A:22、A:6443、测试控制面 A:8443、专用数据库172.31.240.2:5432、元数据169.254.169.254:80、公网1.1.1.1:443，**六项均未连接成功**；没有发送应用数据、查询元数据或数据库内容。
- 六项分别对应源 IP10.244.0.222 的实际 Cilium `Policy denied` SYN drop；受管 DNS 查询仍返回10.245.0.1。原始结果/丢弃事件为 `prohibited-egress.txt`，结果JSON为 `prohibited-egress-result.json`。这只证明所列地址/协议的实测，不外推为全部互联网或所有协议验证。
- 正常对账后，两条入站规则均 `state=revoked`、`revocation_confirmed=true`、`accepting_new_connections=false`，来源通信能力也已撤销。目标自己的私有 echo Service 保留，但没有任何测试跨实例入站授权。最终 API 状态为 `final-network.json`。
- 原 UID 的诊断 Policy 已删除；网关 private/public Service 都仅剩不监听的65535占位端口，公网 Service `allocateLoadBalancerNodePorts=false` 且没有 LB 地址。专用控制面、三份测试工作负载/PV和集群保留供复核，不删除现有业务数据或盲目恢复全机 iptables。
- 18:52:31 再次严格 SSH 只读核对 A/B：原 hostname/可信指纹、维护后的唯一身份、双方 Tailscale `Running`/对端 online、双向 TCP22、ssh/dbus/tailscaled/专用 K3s 均正常；专用 K3s 仍未开机自启。A 原三份 Workspace Docker 网络 ID 与 TrendRadar 配置卷仍在。原始输出为 `final-host-a.txt`、`final-host-b.txt`。
- B 身份原件备份保留；三天测试 CA 到期前需由操作者续期或停用测试控制面。所有实际口令、token、私钥和管理员 kubeconfig 保持 root 私有文件，未纳入证据。
- 19:00 再次只读 `stat` 核对上述私有 env、管理员密码、网关凭据、CA/服务私钥、管理员 kubeconfig 及主机fixture token文件：所列9份均 root:root、0600、普通文件；只读取元数据，没有读取文件值。输出为 `final-private-file-metadata.txt`。

## 7. 完整后端测试的环境差异

整合新主干之前的代码（API源码为 `a4e062d`，之后到 `f96b020` 只有文档变化）完成持久后台完整 pytest：**5 failed、1745 passed、16 skipped / 3091.84s，退出1**。测试使用本地 `workspace` 数据库用户；4项 `test_integration.py` 加1项 `test_n8n_removal_migration.py::test_removal_upgrade_and_empty_downgrade_preserve_unrelated_data` 均在 Alembic `command.check` 报相同反射差异。原完整日志为 `api-pre-main-integration.log.gz`，不能称全套通过。

只复现第一项，实际 `1 failed in 4.41s`（原始日志 `integration-workspace-user-failure.log.gz`）。默认 `search_path="$user", public` 在用户名与 schema 同为 workspace 时改变 default_schema，Alembic 对已迁移表报告 remove/add FK 与版本表差异。同一最小数据库/元数据、新建连接的对照实测见 `reflection-user-comparison.txt`：workspace 用户产生 `remove_fk/add_fk`；独立 t0224_ci 用户 default_schema=public，产生0项变更。

未修改产品、迁移环境或原断言。创建本地独立测试角色后，以 CI 相同的“用户名不等于 workspace schema”条件重跑原整文件：

```sh
TEST_DATABASE_URL=postgresql+asyncpg://t0224_ci@127.0.0.1:5432/postgres \
TEST_REDIS_URL=redis://127.0.0.1:6379/15 REQUIRE_INTEGRATION=1 \
.venv/bin/pytest -q tests/test_integration.py
```

最初只重跑第一个文件，实际 `7 passed in 23.67s`，见 `integration-ci-role.txt`。完整汇总发现第5项后，用同一独立用户原样重跑两个原文件：`8 passed in 21.42s`，见 `all-drift-tests-ci-role.txt`。未修改产品或原一致性断言。这些结果与原始全套失败分开报告，不作为整套重新通过的声明。CI使用 postgres 用户，尚未读取到本 PR 新 head 的 CI 成功证明。

## 8. 未验收门槛与下一步

- 公网 LB 尚无实现/外部地址，两个真实独立外部来源及原始 socket 来源保留未验证；原完整 harness 仍只有基础私网部分 PASS / exit2，之后私网网关组件检查的 PASS 不能改写为完整 harness 通过。
- 生产磁盘配额/可信资源采集、完整 exec/文件/密钥/快照/生命周期适配继续属于 T-0224，不能以本次网络结果结项。
- 保持任务 `in_progress / codex`、PR203草稿不合并。仓库默认 `RUNNER_NETWORK_VERIFIED=false`、运行适配开关关闭；测试控制面 true 只用于隔离验收，禁止推广到生产。

## 9. 与新主干整合：0068 单 head、历史升级与数据保护已验证

19:03 前后刷新 origin/main 得到 `6688d15`，包含 PR202 / `aa3edb8`：主干已将网关改为0067、保留桥0066。本分支此前保留网关0066/桥兼容0067的方案与新主干重叠，不能直接合并；上文第2节及既有网络/测试输出保留其当时版本，最终迁移图需要进一步更新。

等待原版本完整suite结束后，合入 `origin/main@6688d15`；仅任务日志冲突，保留双方全部追加内容。最终沿主干 **0066桥 → 0067网关 → 0068历史核对**。原始桥0066升级DDL与网关0067建表/序列DDL不变；0067升级入口先核对并补齐历史状态，0068使已经停在两种旧0067的库也接受相同核对。冻结逻辑独立于应用模型，位于 `migrations/runtime_schema_compat_0068.py`。

Ruling: 0068回退不删除对象，规范化0067应保留桥/网关两部分；桥数据保护移到实际删桥的0066回退入口。该入口对两桥表取得事务级 ACCESS EXCLUSIVE 锁后才检查空表，锁持续到后续DROP/事务结束。没有数据时仍可完整up/down/up；有数据时拒绝回退、整个PostgreSQL事务回滚。未经核对的网关-only旧0066也不能被当成桥0066直接回退。

- 新回归在主干规范编号、但尚无兼容逻辑时实际 `18 failed / 4 passed / 47.35s`，见 `canonical-initial-red.log.gz`。首轮实现组合 `51 passed / 137.98s`，见 `canonical-initial-green.txt`。
- 再次按代码审查技能进行独立只读复审，发现3项Important：INTEGER宽度校验误接受SMALLINT/BIGINT；同名正确表达式但NOT VALID的CHECK/FK未拒绝；空表检查和DROP之间并发插入可能丢失。
- 各加真实PG回归，实际 `9 failed / 22 deselected / 19.42s`，见 `canonical-review-red.log.gz`。并发用例在真实空表SELECT完成后，从第二连接插入并提交，旧实现确实提交后被DROP；采用数据库500ms lock_timeout界定受锁阻塞，没有放宽产品超时或更改原断言。
- 修复严格整数宽度、查询 `pg_constraint.convalidated`，并为删桥空表检查加事务持锁。独立复审确认3项已解决、没有新阻塞。
- 最终组合 **60 passed / 160.99s**，见 `canonical-final-green.txt`。命令在仓库根目录执行，环境为上述独立用户/Redis/REQUIRE_INTEGRATION，参数如下：

```sh
services/api/.venv/bin/pytest -q \
  services/api/tests/test_migration_revision_collision.py \
  services/api/tests/test_runner_gateway_migration.py \
  services/api/tests/test_integration.py \
  services/api/tests/test_n8n_removal_migration.py \
  services/api/tests/test_runner_network_gateway_control.py \
  services/api/tests/test_module_llm_bridge.py
```

- 最终 Ruff/format565文件、strict mypy559文件、OpenAPI检查通过，`alembic heads` **仅0068**。输出 `canonical-final-quality.txt`。migrations增加仅docstring的 `__init__.py`，解决正常strict mypy发现同一helper被当作两个模块的问题；没有新增ignore或放宽检查。
- 本轮新主干后的完整suite没有重新运行；最终60项直接覆盖全部改动迁移、旧失败一致性断言及网关/桥API，原完整1745/5/16属于旧代码基线，不外推为最终整套或CI成功。

### 真实测试库 0067→0068

19:27:54，对先前真实网络验收使用的**独立测试库**执行升级；未修改生产数据库。将上述已测试迁移目录只读挂入一次性专用工具容器，使用root私有env，不复制或打印凭据。包 SHA256 `195adb6681830489df5b64dc522c2ba753f54c65e70afb45249d2cffcba5ee0e`；实际驱动源码为 `canonical-upgrade-driver.source.txt`。

```sh
docker run --rm --user 0 --network t0224-control \
  --env-file /etc/workspace-t0224/control.env --workdir /app \
  --mount type=bind,src=/opt/workspace-t0224/fixture-tools/canonical-0068/migrations,dst=/app/migrations,readonly \
  --mount type=bind,src=/opt/workspace-t0224/fixture-tools/canonical_upgrade.py,dst=/opt/t0224-canonical-upgrade.py,readonly \
  --entrypoint /opt/venv/bin/python workspace-t0224-gateway:lab-20261114 \
  /opt/t0224-canonical-upgrade.py
```

退出0，实际版本0067→0068；3个合成实例、2个主机、3个环境、2条入站规则以及两张空桥表的**行数与整表摘要前后完全相同**。入站/公网端口序列的值与is_called均相同；策略序列只要求不减小，因为运行中的网关可继续正常轮询。完整只读前后结果为 `canonical-live-upgrade.json`。

测试控制面仍使用之前的r3镜像，仅迁移工具挂载新源；升级后CA验证的 `/readyz` 返回数据库/Redis均ok，见 `control-ready-after-canonical.txt`。此前网络PASS对应的镜像、代码版本和实际输出保持原记录，不改写成整合后所有产品功能均已重跑。生产开关继续关闭。

19:29:45 只读再次取得真实 Pod/Service：三个原工作负载 UID 不变、gVisor、全部Ready且重启0；网关原UID不变且重启0，private/public入口仍仅65535占位、LB无地址，fixture phase=settled。输出为 `post-canonical-current-resources.json`。所有非Markdown证据的摘要索引为 `SHA256SUMS`。
