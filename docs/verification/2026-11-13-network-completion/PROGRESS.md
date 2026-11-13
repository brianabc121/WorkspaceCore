# T-0224 网络执行链后续进度

基线 main e3c3766，工作分支 codex/T-0224-network-completion。任务继续 in_progress，未执行生产迁移或启用新网络。

本轮人类明确要求完成：同 OpenClaw 跨服务器私网、跨实例授权/受控公网/存量连接撤销、网络接口和管理页面。沿用已批准 K3s/Cilium 设计。

## 当前代码

- 私网 namespace/同域策略/受限 DNS/精确网关策略持续对账，保护 namespace、allocation、generation、UID/resourceVersion 和撤销墓碑。
- 真实 asyncio TCP 网关，私网用 socket 来源 + 当前 Kubernetes Pod UID/保护标签鉴别；公网 TLS + IPv4 来源限制，受管 Pod/节点/平台来源拒绝。直接跨实例路径仍拒绝。跨域/公网 UDP 明确不支持；同域 UDP 支持。
- 每连接短租约，最多60秒、每2秒重验；撤销、失联、身份/代次替换关闭存量连接。TCP握手和 TLS 握手共享有界连接上限。
- 独立网关凭据与 ready/config/lease/ack；已批准通信 Grant 等实际网关 ready 才生效。服务修改/入站配置变更均版本校验，高风险写二次认证；服务策略回报前不发布网关目标。
- 固定 Service/NetworkPolicy 的精确端口自动同步与读取验证，RBAC 仅允许这几个固定对象 get/patch；无 NodePort/hostPort。公网 LoadBalancer 保留源地址，未分配地址不确认就绪。
- Kubernetes 生命周期初始 Deployment/PVC UID 回执，持续重读原 UID 下的 Pod 绑定，容器自动重启后可刷新当前 Pod；停止/未知/更换部署/旧代次不能借报告恢复。普通资源报告只刷新容量，不使用 Docker inventory 改 Kubernetes 生命周期。
- 可信 Node/本地PV 采集及条件调度；当前仅 gVisor、不可变镜像、非root、实例私网生命周期档位，其他未支持操作在预约前拒绝，旧 Docker 行为保持。
- 页面可编辑服务/有效期/来源CIDR，来源实例选择、二次认证、版本冲突、待应用/撤销确认、只展示已确认地址，错误不回显密码。
- 增量迁移0066提供独立端口序列与单调策略修订。旧端口不复用，撤销墓碑确认后不再占网关快照。

## 已实际取得的局部结果

- 新控制面与运行层组合：211 passed, 1 skipped（29.91s），后续扩大组合220 passed, 1 skipped（46.55s）。这是当时提交前源码的局部证据；后续端口自动同步与目标权限修复仍在复验。
- TCP网关42项，私网/生命周期组合93项（含1个当时未指定官方kubectl的跳过），节点/容量60项，前端定向43项通过。原始报告在当前环境忽略目录 `.superpowers/sdd/network-completion/`。
- 完整 strict mypy app/migrations/tests/scripts 已对539文件通过；后续新增项继续纳入。
- npm ci 使用Node22完成457包安装；前端完整 lint/typecheck/test/build正在执行。不是最终全绿声明。
- TDD 实际发现并修复：私网执行链不存在、更新契约不存在、网关路由不存在、普通库存误判K8s运行环境丢失、旧代次回报未拒绝、周期绑定入口不存在、目标运行权限关闭后旧租约仍放行、网关尚未安装目标策略却能取得连接目标等。

## 真实节点验收仍待完成

人类提供了一台额外测试服务器并授权测试。首次 SSH 连接在握手前返回 ConnectionRefusedError / errno111，尚未认证、未发送密码、未修改远端。不在仓库记录服务器地址或凭据。已询问其现有SSH可达性/端口，需进一步区分目标服务与云端出站限制。

当前环境无现成 Kubernetes/Cilium 集群或两台已连通的受管测试节点；本地有效 capability=0，根盘剩余空间有限。Docker daemon可查询（28.4.0，0运行容器），尚未建立真实跨物理服务器测试网络。提供了带显式测试环境确认的 `acceptance_two_nodes.py`；缺少真实场景时必须返回阻塞，不可把本地socket/数据库/Pod清单测试当作跨服务器已通。

## 待执行

1. 完成新增绑定/自动端口发布的完整检查、数据库迁移冒烟、全API及全前端检查。
2. 补独立全分支审阅；前几名实现代理触发了工具报告的使用额度限制，父代理继续接手剩余实现，不能把未完成的 followup 当作已审查。
3. 取得测试服务器SSH可达性与真实两节点访问，运行正反向网络、原始来源、公网TLS、撤销在途、重启/失联验收；在此之前保留 RUNNER_NETWORK_VERIFIED=false。
4. 记录真实输出后提交/推送新PR供Claude审阅。保持任务 in_progress，未完成不勾选原全部验收。

## 独立审阅后的修复

独立审阅实际提出5项Important：真实Kubernetes省略空字段造成隔离策略永不收敛；单个workspace可使共享网关全体关闭；平台网关Pod被当作用户环境导致可信采集失败；过期时间被误当成连接关闭回报；停止环境仍显示可连接地址。父代理补真实API序列化、平台Pod/畸形身份、来源/共享容量准入、过期及停止输出回归，实际RED组合5 failed/1 passed，修复后相关组合14 passed/45.68s；旧无效绑定不会影响其他路由另有回归，纳入最终全套。

完整前端：Node22 npm ci457包；lint与typecheck退出0；79文件794测试通过/449.32s；build退出0/26.81s，原有大bundle与第三方注释警告保留。最新后端strictmypy541文件和Ruff通过。最终 bare pytest 已启动（先前预修复全套中断，不算完整通过），结果随后追加。

## 云端恢复后的 SSH 诊断与验证状态

2026-11-13 再次读取当前环境状态，provider=cloud，observations_current=true；执行器有 CODEX_ENVIRONMENT_ID，未设置 CODESPACES / CODESPACE_NAME。这是 Codex Cloud 执行器。继承平台代理的 GitHub HTTPS 请求实际返回 HTTP/2 200；测试服务器 SSH 22 和官方 ssh.github.com:443 的直接 TCP 连接均在认证前返回 ConnectionRefusedError / errno111。没有发送密码或修改远端。

启动策略显示 vpn_configured=false，TCP grants 为空；配置中的 TCP CONNECT 入口也返回连接被拒绝。运行时网络说明明确区分 HTTP(S) 代理与只支持 Tailscale 传输的原始 TCP。开启 HTTP 网络访问不能证明 SSH 可达。当前应先取得受支持的 VPN/TCP 路径或改用可直连 SSH 的执行环境；现有证据不能定位远端是否另外存在按来源限制。GitHub 官方 Codespaces 安全说明允许出站互联网连接，不能把此次故障归因为 Codespaces 全局禁止 SSH 22。

恢复后 /tmp 中的测试服务、Node22/前端依赖、kubectl 和全套 pytest 的日志/结果均已消失；之前启动的全后端进程也不存在。不得将它计为已通过，最终全套结果仍待取得。工作区源码和 Python 虚拟环境保留，其他工作区未动。重新 fetch 后 origin/main=43b5bfa7（仅新增 T-0225 新闻默认启用变更），当前网络工作分支仍基于 e3c3766，没有自动覆盖或合并本地改动。

本次恢复后实际执行：网关和动态端口发布测试 45 passed in 1.95s；Ruff app/migrations/tests/scripts 全部通过；collab check 通过（0 个警告）。已格式化新增回归测试。公网 LoadBalancer 尚未分配地址时，只撤下公网绑定，保留私网发布；撤下的活记录不能错误确认成已生效。控制面新增 active ACK 字段后的 OpenAPI/数据库集成及最终完整检查仍需复验。真实两台物理服务器验收继续阻塞，不启用 RUNNER_NETWORK_VERIFIED，不标记整个任务完成。

本次定向 mypy 首次发现新增测试的字典推导类型问题，补显式类型后重跑：Success: no issues found in 5 source files。OpenAPI `--check` 实际提示过期（新增 active ACK 字段），尚未重新导出并生成前端类型，不能算通过。下次先恢复测试服务/Node22 依赖，再完成该同步、控制面集成回归、迁移与全套验证后交 PR。

## 继续开发（人类已取消 SSH 测试）

人类明确要求放弃登录测试服务器、继续完成开发。本轮不再探测 SSH，也不要求先配置 VPN。已把工作分支快进同步到最新 origin/main 43b5bfa7，保留全部本地开发改动；原 T-0149 工作区未动。

- 实际恢复 Node22.23.4，使用锁文件 npm ci 安装457包；PostgreSQL16.15来自PGDG官方带SHA256核验的软件包，Redis7.4.12从官方源码本地编译，仅监听回环地址。官方 kubectl v1.35.10 SHA256核验通过，供本地真实TLS边界测试使用。
- 已重新导出 OpenAPI 并通过 openapi-typescript7.13.0生成类型；Node22完整 lint 与 typecheck 命令退出0。
- 新增目标故障与共享就绪隔离回归：RED `test_unready_target_does_not_block_gateway_permission_readiness` 1 failed；修复后包含该回归的45项通过，但同次组合中 `test_kubectl_subprocess_is_bounded_and_does_not_cache_identity[fresh-identity]` 在并行编译时触发0.2秒超时，尚待安静环境复跑，不冒充整组通过。
- 容量回收回归 RED 2 failed / 1 passed（revoked、expired记录尚未回报即被过早释放）；修复后容量与inactive ACK组合 `4 passed, 10 deselected in 121.34s`。已确认撤下的记录才能释放共享入口容量。
- 隔离数据库实际命令行 `alembic upgrade head`、`alembic downgrade base`、`alembic upgrade head` 均 exit0。修正后的0066迁移已覆盖完整回退链，没有对生产执行迁移。
- 最新全范围 Ruff/format检查通过（547文件）；先前 strict mypy 对542文件通过，最后两项修复仍纳入最终检查。

首次前端全套与编译/类型检查并行时，`model-connections.test.ts` 的以下6项超时，已停止该次运行：`test_connections_list_filters_total_and_summary`、`test_official_relay_and_multi_model_form`、`test_saved_key_is_never_revealed_or_submitted_blank`、`test_statuses_do_not_collapse_to_empty`、`test_draft_and_compat_credentials`、`test_workspace_switch_and_stale_responses`。后续完整 test/build/check:api 改为串行，未增加超时或削弱断言；结果仍在执行，随后补录。

下一步：先推送草稿PR保存可审阅代码并运行远端全套CI，同时完成本地串行前端和官方kubectl/网络集成检查；检查未全通过前不转为可合并交付。任务仍in_progress，真实集群验收未做，网络启用开关保持默认关闭。

## PR #197 与代码收尾验证

已提交并推送网络源码e80ccc72，创建草稿PR #197：<https://github.com/brianabc121/WorkspaceCore/pull/197>，由Claude审查。实际验证与本地失败/中断记录集中到本目录`VALIDATION.md`，不使用此前丢失的临时全套日志做通过证据。

- 本地最终网络组合：`236 passed in 179.41s`，无跳过，含真实PG/Redis/socket/TLS及官方kubectl边界。此前fresh-identity超时用例在该组合中通过，没有修改超时。
- e80ccc72完整前端CI：79文件794项全部通过，check:api/lint/typecheck/build均通过；构建10.14s。本地重复全套曾有未修改页面用例失败并中断，详见VALIDATION，不声称本地完整通过。
- 继续检查实际部署发现Compose未传新增网络参数；先写真实Compose渲染+Settings读取回归，实际RED2 failed，补齐API/Worker共享配置后GREEN2 passed/0.55s。
- 新增独立网关镜像构建CI；本轮不发布镜像或部署服务器。后端完整CI与最新提交的最终质量结果见PR检查/正文。

人类取消的是本轮登录测试服务器，不是授权开启未经验证的生产网络。当前网络代码与部署材料继续交付，真实双节点/Cilium/LB验收留作生产启用门槛；更广的exec/文件/快照适配未伪装为已实现，T-0224继续in_progress。

## 完整CI契约失败修复

首轮完整API CI返回失败。日志下载连接器遇到Transport closed，原始日志存储下载也未成功；检查注释只给出exit1。读取本地保留的失败缓存后，直接运行并实际复现统一422文档与CSRF矩阵两项失败，不把缓存当本轮结果。

根因是内部机器网关的4个路由误进入浏览器OpenAPI。加强“不公开内部接口”的契约断言，实际RED1 failed；路由设置include_in_schema=False后，重新生成正式OpenAPI/TS。真实最终组合：两个契约 + 完整安全矩阵 + 网关独立鉴权，8 passed in473.35s；没有修改CSRF矩阵、放宽角色、跳过用例或增加超时。check:api/typecheck、Ruff548/strictmypy544/OpenAPI全部通过。广泛的本地诊断批次只获得部分进度后停止，不计为完整通过；完整API结论等待最新修复提交的CI。
