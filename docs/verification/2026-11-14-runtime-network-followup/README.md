# T-0224 后续核对：CI完成、连接变化与交接建议

用户侧时间为北京时间（UTC+8）；机器原始输出保留各自ISO时区。沿用既有授权，只读核对与本地模拟；本轮没有在两台服务器认证或执行命令，没有修改生产开关/注册凭据/ACL。

## 已完成的完整CI

核验提交 **7f4a59157137e8336e2823805b052abb1510bfd3**，不是后续证据文档提交的CI声明。API、Web、安全、基础设施、Runtime network image、collab六组工作流全部completed/success。实际任务/步骤和精选原始输出见 `ci-7f4a591.json`：

- API：`1774 passed, 16 skipped in 2437.46s`；额外Garage集成 `5 passed in 2.20s`。CLI迁移完整upgrade head→downgrade base→upgrade head通过，日志明确包含0066/0067/0068。
- Web：80文件、811项通过，构建通过。ANSI颜色清理后的原始汇总为 `web-ci-summary.txt`。
- 全栈compose、news、agents与三种Docker安装镜像任务均success；失败诊断步骤因没有失败而skip，不将诊断步骤的skip误报成验收被跳过。
- 安全扫描、依赖与镜像检查通过。未复制完整CI日志，以免把运行时凭据或无关内容纳入证据。

这补齐了此前[执行账本](../2026-11-13-two-server-acceptance/EXECUTION.md)中“完整CI待结束”的状态。旧本地全套的5项反射差异和对应复验仍保留原记录；当前代码7f4a591已经有CI完整后端通过证明。CI不替代公网或满负载实机验收。

## 当前SSH路径：平台VPN运行，目标TCP22超时

本轮恢复时 `/tmp` 的 known_hosts/工具脚本已不存在；工作区中的代码、缓存及证据保留。尝试通过平台TCP relay读取ED25519公钥并与用户此前确认的固定指纹比对，因没有取得公钥而停止；没有关闭StrictHostKeyChecking，也没有认证未知主机。

平台实时 `/status` 显示Tailscale configured=true、state=Running、routing_ready=true，见 `platform-vpn-state.json`。两个明确目标的有界HTTP CONNECT：

```text
proxy:8088 -> 100.72.60.100:22
proxy:8088 -> 100.121.210.36:22
HTTP/1.1 503 Service Unavailable
upstream connect error or disconnect/reset before headers. reset reason: connection timeout
```

首次直接探测约5秒后返回503；02:43 UTC（北京时间10:43）的重复并行探测约6秒返回同样错误，见 `current-tcp22-probes.json`。未读取到SSH banner或公钥，没有发送认证、执行服务器命令或改变远端。不能凭平台代理Running把当前SSH路径标为通过，也不能把上轮成功输出当作这轮重测。

用户随后提供控制台截图，确认A/B都Connected且带tag:workspace-test。当前唯一Connected的Codex设备显示为codex-environment-3，未显示tag:codex-cloud；旧三条Codex设备离线但均有该标签。当前grants和Tailscale SSH规则只接受tag:codex-cloud来源，缺标签是与超时相符的原因，仍须加标签后的实际复测确认；没有据此声称服务器离线。

## 为什么出现codex-environment-3

实际执行器PID1于**2026-11-14 10:25:57北京时间**启动，网络策略文件mtime为10:26:04。平台返回的逻辑环境ID、源配置ID/版本与上一轮相同，spec revision仍1；执行器内没有tailscale/tailscaled命令，VPN由平台代理提供，未在A/B创建Codex客户端。

这些证据支持“恢复/重启执行器后，平台代理重新登记同名Tailscale客户端，形成新设备后缀”的解释；工具没有暴露平台代理自身tailnet IP，也没有保留注册历史，所以不把具体注册触发/设备映射描述成已完全证实。细节见 `environment-resume-diagnostic.json`。截图中的真实用户邮箱未纳入记录。

标签属于设备身份；旧设备被手动赋予标签，不表示下一个同名注册设备会继承。tagOwners只定义谁能赋标签，不会按主机名自动加标签。当前无可用Tailscale管理接口/已连接管理员浏览器可替用户操作。恢复办法是给已核实的当前Codex节点赋tag:codex-cloud后严格复测；持久方案应确保平台使用的注册身份带所需标签。没有扩大ACL或把普通成员加入root SSH来源。

## 交接建议6/8/9/10的核实

本轮不修改产品逻辑。下列是对当前源码与定向执行的核实，容量结果仍未完成。

|建议|核实|结论|
|---|---|---|
|6：租约续期负载|私网每次续期重新核对来源Pod、控制面租约和目标Pod，默认续期等待2秒，整体授权操作有1.5秒默认超时；全局连接默认上限256|忽略请求耗时，256个持续私网连接理论可达到约128次lease/s、256次kubectl查询/s，另有轮询成本。该计算不是实测容量；两台4核节点的满负载资格仍缺，生产保持关闭|
|8：快照版本和重复patch|desired_policy每次nextval；策略内容相同但新revision时会写入共享Kubernetes版本围栏。相同revision重复应用则不写|跨节点旧快照必须被拒，不能只按spec相同就忽略版本写入。当前是明确的控制面写入成本，不是可直接删除的冗余校验。若优化，须先保留跨控制器的共享围栏语义；本轮没有放宽它|
|9：编辑清apply_error|显式编辑增加desired_revision、置approved_pending_apply；旧applied_revision不能确认新配置。非Kubernetes的全局runtime_status仍为unsupported，页面持续展示不支持提示|没有发现被提前报告active的路径。新版本重置旧版本错误是重试语义；没有把列表级unsupported原因抹掉。此结论来自源码核对，不冒充单独的非Kubernetes实机测试|
|10：轮询是否两秒完成|网关先做路由观测，再按绑定顺序做就绪/ACK，完成后才等待poll_interval；不是承诺两秒完成全量。路由发布器上限100个active绑定；max_bindings默认1024还包含其他记录|大量绑定会延长周期，仍需实机测量；已有流使用独立续期任务与授权超时，不以整轮完成作为唯一撤销机制。不能把1000条配置上限解释为1000条active入口容量|

`policy-fence-trace.json`用真实KubernetesRunner配合仓库既有内存API适配器追踪：策略spec不变、revision1→2→3产生2次更新；重复revision3产生0次更新；旧revision2被拒。它不代表真实apiserver性能或Cilium数据面测试。

定向原测试执行：

```sh
.venv/bin/pytest -q \
  tests/runtime_v2/test_private_network.py::test_advancing_snapshot_revisions_do_not_starve_gateway_service_ack \
  tests/runtime_v2/test_private_network.py::test_second_node_policy_revision_fences_delayed_first_node_ingress \
  tests/runtime_v2/test_gateway.py::test_lease_expiry_deadline_wins_over_stalled_renewal \
  tests/runtime_v2/test_gateway.py::test_revoke_closes_listener_and_session_before_ack
```

实际 `4 passed in 0.16s`，见 `fencing-and-revocation.txt`。这四项是本地安全回归，没有重跑或改写历史两节点实测结论。

## 继续条件

先恢复已授权的Codex→A/B TCP22路径，严格核对原指纹/身份后继续实机容量检查；公网部分仍需真正支持直达Pod、保留原始socket来源的LB及两个独立外部来源。不能靠给Service手填LB状态或externalIPs替代：当前发布器明确拒绝externalIPs/NodePort，LB无真实地址时会关闭published绑定。

生产RUNNER_NETWORK_VERIFIED仍保持false；没有远端新授权、重启或数据清理。本轮未能复核服务器最新状态，原隔离集群/控制面/撤销状态的最后真实读取时间仍以此前账本为准。T-0224保持in_progress，PR203保持草稿不合并。
