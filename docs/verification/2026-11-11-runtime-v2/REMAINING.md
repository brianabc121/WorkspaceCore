# T-0224 剩余覆盖核对（2026-11-12）

结论：用户已授权全面实施，并在本轮明确要求合并当前检查点；先完成全部 CI 与阻塞问题修复再合并，任务仍 in_progress，后续功能继续按下列清单实施。最大缺口是 **Kubernetes 运行与策略执行链尚未接通**，不能把当前状态描述为“代码已完成，只等集群验收”。本次只读核对设计、计划、PROGRESS/UPGRADE 和相关契约/源码；未运行测试、改代码/索引、操作生产。Task3A、Task5A、Task6B 已批准的 scoped 结论保留；下列属于整体未完成范围，不是重开其审查。

## 已实现及证据边界

| 用户要求 | 当前代码覆盖 | 尚未兑现的部分 |
|---|---|---|
| 默认 5 个、独立 critical 审批 | 跨宿主保有计数、停止/未知/残留占额；第 6 个持久去重申请；owner 二次认证、独立 Grant、撤销/期限；数量豁免不放宽资源 | Docker 数量授权可生效；存在非 Docker host 时等待控制器确认，Kubernetes ResourceQuota/Grant 应用确认尚未接通 |
| 智能体 auto/specified | 可信独立 SSH 探测、原子预约、旧请求兼容、幂等收据、指定不回退、原宿主重启；全局物理身份防重复容量 | 实际双服务器部署及可控负载选择仍需远程验证 |
| 用户/智能体容器 auto/specified | 多宿主、实际宿主路由、资源排序、镜像本地亲和、复用/恢复冲突检查、候选 MCP/UI | `runner_placement.py:82` 明确只放行 Docker；Kubernetes 节点尚不能被分配 |
| 同实例跨节点默认互通 | 固定 workspace+instance 标签、同域 NetworkPolicy、私有 Service 原语、UID/代次安全生命周期 | 没有完整运行的私网/服务发现链，不能据此声称跨节点已通 |
| 严格入站/跨实例权限 | owner 服务/来源/公网声明、修订与墓碑、过期/撤销状态、共享执行需复制；UI 不展示未确认地址 | 跨实例/公网执行器、来源身份、会话撤销均缺；数据库批准不会自动放行 |

历史本地证据：真实 PostgreSQL/Redis 定向组合 69 项；控制器含真实 kubectl/本地 TLS 边界 77 项；修复后 API 非 integration 701 项、前端 78 文件/775 项。它们均来自 PROGRESS 的合入前记录，不能外推为 main `36c6fb5e` 合入后的最终验收，更不能证明真实 SSH 部署、RBAC、存储或网络。网络 UI 已有声明/三类撤销；早期待办中的“尚未补 UI”已过时。编辑无现有接口，是整体后续项。

## 最新集成验证待处理项

合入36c6fb5e后的历史本地结果见PROGRESS，不能当新head证据。35946bdc的远端Web CI已完整成功，未提高timeout；本地日历超时过程和已撤回的懒加载实验保留作记录。当前阻塞转为合并前全CI：后端测试/迁移严格mypy已由606错误修正至520文件通过；真实并发部署死锁已修复且11项部署回归通过；独立审查发现的容器复用锁序、模板旧构建回执及回收/外键并发问题已修复；核心整组50项、最终补充15项通过，严格静态520文件通过。新的完整后端/基础设施CI尚需通过，详见PROGRESS。

## 建议下一步及路径边界

1. **可信节点/存储报告与部署底座**：`app/runtime_v2/telemetry.py`、runner report schema/API/`domain/runner_hosts.py`，配合 `infra/runtime-v2/**`。`NodeTelemetry.report()` 当前无条件拒绝；须先定可信 CPU/内存/磁盘/Pod/PVC/UID 归属、采样票据与停止因果契约，再实现采集。补锁版本/digest、非交互安装、双节点 harness；不要仅解除 Docker-only 筛选。
2. **接通私网持续对账**：`runtime_v2/{controller,runner,kubernetes}.py`、`domain/runner_network.py`、`api/runner_network_api.py`。当前 `poll_once()` 仅领命令；`apply_domain_policy`/`apply_private_service` 没有产品调用方，namespace/策略仍要求预置。补受控域创建、desired→apply→读取验证→ACK、撤销/过期墓碑、DNS、受控出口和平台禁区；ready 须包含实际策略/服务证明。数量 Grant 的 Kubernetes 应用确认也在此闭环。
3. **完整运行适配**：同 runtime 路径及 `domain/runner.py`、镜像/快照接口。现仅 create/start/stop/delete；workload 明确拒绝 setup、文件、secret/env、复制/恢复和公网出口。补 exec/files/snapshot、镜像 digest 分发、PVC/卷原节点亲和与容量归属；数据库保活/依赖须避免被普通闲置回收。完成后通过独立开关与节点证据接入候选，保留原 UID/journal/unknown 边界。
4. **身份网关与撤销**：新增独立网关实现，扩展 `runtime_v2/authorization.py`、网络模型/服务/内部 ACK。当前授权函数只是 TCP 判定，`gateway_supported=false`，高风险 ACK 被拒绝。实现不可伪造源身份、同 workspace + 源 Grant + 目标服务入站、有限租约、到期/撤销关闭在途连接；覆盖 TCP/UDP、SNAT 前来源、DNS/CNAME/代理/公网回流。UDP 未安全支持继续拒绝；IPv6 须等价验证或明确关闭。
5. 最后补正式 API/UI/MCP 所需契约、真实故障/迁移回退演练，再做合入后整体验证与覆盖审查。旧 Docker/曾共享执行环境保持原后端，可信重建/数据迁移单独演练；不因负载改变迁移既有工作负载。

## 真实环境前提

- 至少两个真实 Linux 节点，具备受控管理员权限，或提供已建集群的受信 HTTPS kubeconfig/RBAC；本地文档记录有效 capability=0、根盘不足、无集群凭据，当前环境不能直接承载完整双节点 PoC。
- 核验并锁定 K3s `v1.35.10+k3s1`、Kubernetes/kubectl `v1.35.10`、Cilium `1.19.10` 与 gVisor/内核/containerd 组合及镜像 digest。现有标签及 kubectl SHA256 只证明下载物，非安装锁文件/兼容证明。
- 节点互联、无冲突 CIDR、跨节点加密、MTU/NAT/DNS；控制/overlay 端口仅登记节点可达；受保护 Node 标签/NodeRestriction、RuntimeClass、受控 OCI 仓库、拉取凭据和有足够空间的持久存储/卷亲和。
- 每 host 绑定 workspace/host/node 的 mwr 凭据、可信 Workspace 入口；绝对路径 TLS 校验 kubeconfig、最小 RBAC、持久且单写的 journal。服务用户、`/opt/workspace/services/api` 与 `/etc/workspace/runtime-v2` 等模板路径需实际配置；禁止把 journal 放临时目录。
- 公网阶段另需可控域名/证书/入口、身份签发与轮换、可验证的出口/回流路径及允许/拒绝两类外部来源。双节点验收至少含 PostgreSQL/HTTP/UDP、跨实例拒绝/精确放行、旧连接撤销、失联/迟报/重启/删除竞态、负载改变选址和 specified 不回退。

保持 `RUNNER_NETWORK_VERIFIED=false`、默认关闭控制器；满足对应实现和实测门槛后再启用验证环境，不能凭批准记录或 API UID 把数据面标为 active。

## 已确认的既有并发风险（后续核对）

独立审查对照origin/main确认：SSH安装完成分支 `agent_ssh_install.py` 按target→deployment→instance更新，既有实例重部署按instance→server→target，存在潜在锁环。该顺序在本检查点之前已存在；本次没有宣称所有历史SSH锁序已统一。后续应补安装完成与重部署重叠的真实PG回归，按统一顺序修正。新引入且已复现的部署预检查反转、容器复用autoflush及模板构建回执问题须在本次合并前解决。
