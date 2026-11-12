# Runtime v2 开发预览部署材料

**T-0222 / PR #193 尚未完成。这里没有执行安装，也不提供自动切换旧 Docker 环境的动作。**

`controller.env.example` 和 `workspace-runtime-v2.service` 是独立生命周期控制器的配置模板。复制模板本身不会启用后端；`RUNTIME_V2_ENABLED=false`，工作台 `RUNNER_NETWORK_VERIFIED=false`，Kubernetes 调度仍关闭。当前只覆盖 create/start/stop/delete 及持久恢复；完整 exec、文件、快照、资源遥测、网络网关尚未完成。

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

当前可核验的上游标签为 Kubernetes v1.35.10、K3s v1.35.10+k3s1、Cilium 1.19.10；见 research 文档中的文件摘要。这不是镜像 digest 锁文件，也不证明这些组件已在本环境兼容运行。正式安装脚本与双节点验收 harness 仍待补齐；请勿把模板当作已完成的集群安装器。

本环境没有已配置的 Kubernetes 集群或 kubeconfig；执行用户有效 capability 为 0，根文件系统可用空间不足以部署完整双节点/Cilium。没有尝试修改共享 Docker daemon、内核或真实远程服务器。当前单元边界、真实本地数据库测试与局部 TLS 检查均不能替代集群验收。

升级/回退事实：`docs/verification/2026-11-11-runtime-v2/UPGRADE.md`；实际结果与未完成项：同目录 `PROGRESS.md`。
