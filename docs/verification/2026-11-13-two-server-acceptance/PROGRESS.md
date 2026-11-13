# T-0224 双服务器网络验收：前置核对

本记录是前置核对，不是双服务器验收通过证明。所有时间为 UTC。

**后续实际执行以 [EXECUTION.md](EXECUTION.md) 为准：B身份已修复并重启，专用集群已部署；同实例跨节点TCP/UDP/DNS、跨实例隔离与私网授权、规则/来源能力撤销后8秒内存量断连及新连接拒绝均已实测通过。两条测试规则已撤销并获撤下ACK。六项禁止出站有对应Cilium丢弃证据。公网LB/两个外部来源仍缺，生产网络关闭。完整后端运行与数据库用户反射差异另行如实记录。下文保留前置核对的历史分段，不覆盖旧失败或将旧待确认项当作当前阻塞。**

以下为历史前置状态（2026-11-13 16:10 UTC / 北京时间 11-14 00:10）：人类调整 Tailscale grants 后，两机对端可见、Tailscale ping 和双向 TCP22 均实测通过。当时 machine-id 冲突和重复0066迁移尚未处理、尚未安装集群；这些事项已由后续执行账本中的实际维护、修复和测试取代。

## 代码与已有执行

- 2026-11-13，GitHub 连接器实时读取 PR #197：`state=closed`、`merged=true`，`merged_at=2026-11-13T15:28:36Z`。原 head 为 `4679f7ae8048b75c2d2fd5eedbdf871c82a170c3`。
- `git fetch origin` 后以 `15a4c90450af443ed3b322fd615b8832258f2b51` 为本次基线；主干包含 Claude 修复 `a07628c`，不再沿用原 PR 分支重复实施。
- 已阅读 `.collab/handoffs/2026-11-13-1432_T-0224_claude-to-codex.md`。交接前状态为 `owner=codex/status=handoff`，本轮接手为 `in_progress`。
- 已查当前可见聊天与主干任务日志，未发现其他正在执行的双服务器验收。服务器内是否有既有验收进程、集群和资源，必须在可信 SSH 登录后只读核对；目前未完成该检查。
- 人类本轮重新授权双机测试，替代历史记录中取消远端测试的本轮范围限制；仍不授权未经验证的生产切换。

## SSH 身份前置：已确认并通过

A：`100.72.60.100:22`；B：`100.121.210.36:22`。均经平台 HTTP CONNECT `proxy:8088`，没有直接连接远端。

两台目标在本轮严格 SSH 探测中均返回：

```text
Remote protocol version 2.0, remote software version Tailscale
Server host key: ssh-ed25519 SHA256:vbzwuvOu30pbIyANTCFBC/b4/At+uvpTB8kvl1QpKJE
No ED25519 host key is known ... and you have requested strict checking.
Host key verification failed.
```

初次探测因没有可信记录而拒绝登录。随后人类在当前对话明确确认“两台均为该指纹”；本轮将匹配该指纹的 ED25519 公钥固定到本地专用 known_hosts，保持 `StrictHostKeyChecking=yes`，两台认证成功。生成 known_hosts 时首次误取 ssh-keyscan 的注释 banner，SSH 严格拒绝了格式错误的记录；过滤注释、重新核对指纹后连接成功，没有关闭校验。

实际身份验证输出：

```text
TARGET 100.72.60.100
s1348279
uid=0(root) gid=0(root) groups=0(root)
TARGET 100.121.210.36
s8006604
uid=0(root) gid=0(root) groups=0(root)
```

## 后续执行顺序与保护边界

1. 取得可信指纹后固定 `known_hosts`，仍使用严格校验和平台 TCP CONNECT；先执行 `hostname`、`id`，核对 A/B 实际主机身份。
2. 只读盘点 CPU、内存、磁盘、OS、内核、路由/网段、监听端口、运行服务、Docker 容器名称/镜像/状态/端口及已有 Kubernetes/Cilium/gVisor；不导出环境变量、Secret、数据库内容或 kubeconfig 原文。检查既有验收进程及资源名称，避免重复开工。
3. 根据实际占用判断隔离方案，保护现有服务、卷、端口及数据。若没有安全的非生产双节点集群，不能把在生产宿主直接安装 K3s/Cilium 当作无风险准备动作；先形成基于盘点的具体方案。
4. 依 `infra/runtime-v2/README.md` 核对受保护节点标签、gVisor、CoreDNS、Cilium、HTTPS kubeconfig 和已核验镜像 digest。使用明确的非生产 context；不猜集群凭据或镜像。
5. 运行既有 `acceptance_two_nodes.py` 的同实例跨节点 TCP/UDP、跨实例默认拒绝及 DNS 测试；保存命令、退出码、真实报告和 run UUID。仅清理本次取得 UID 回执的测试资源。
6. 跨实例授权与在途撤销必须使用独立非生产 DB-backed allocation/generation/Pod UID 和专用规则，通过正常工作台授权流程。验证允许源、未授权源、撤销后 8 秒内断开存量流及拒绝新连接；不能用临时网络 Pod 冒充可信 DB 绑定。
7. 公网/LB 及其他启用门槛未完成时，保留相应 blocked，不把局部 PASS 当完整 PASS；生产 `RUNNER_NETWORK_VERIFIED` 必须保持关闭。

## 初始前置结果（SSH 确认前）

- 本轮未改服务器服务、数据、路由、防火墙、集群或网络开关；尚不能断言远端生产开关的实际值，待只读盘点核实。
- 仓库 `.env.example` 和 Compose 默认 `RUNNER_NETWORK_VERIFIED=false`，本轮未修改。
- 本地交接复核不是远端网络验收，结果见本目录输出文件。数据库依赖首轮不可用导致集成测试报错，未记为通过；恢复已有开发容器后复跑结果另录。
- 真实同实例互通、跨实例隔离/授权及撤销断连均为 **未执行**，不勾选 T-0224 验收标准。

## 本地实际命令与输出

在 `services/api`：

```text
.venv/bin/ruff check .
All checks passed!

.venv/bin/mypy app tests
Success: no issues found in 487 source files

.venv/bin/pytest -q tests/runtime_v2/test_gateway.py tests/runtime_v2/test_node_telemetry.py
63 passed in 1.61s

TEST_DATABASE_URL=postgresql+asyncpg://workspace@127.0.0.1:5432/postgres TEST_REDIS_URL=redis://127.0.0.1:6379/15 REQUIRE_INTEGRATION=1 .venv/bin/pytest -q tests/test_runner_network_gateway_control.py
1 passed, 14 errors in 1.40s
ConnectionRefusedError: [Errno 111] Connect call failed ('127.0.0.1', 5432)
```

`docker ps -a` 确认两个既有本地开发容器 `workspace-dev-postgres`、`workspace-dev-redis` 已停止。只在当前开发执行器执行 `docker start workspace-dev-postgres workspace-dev-redis`，等待 `pg_isready` 和 Redis `PONG` 后，重复同一测试命令：

```text
1 passed, 14 warnings, 14 errors in 2.96s
alembic.script.revision.MultipleHeads: Multiple heads are present for given argument 'head'; 0066, 0066

.venv/bin/alembic heads
UserWarning: Revision 0066 is present more than once
0066 (head)
0066 (head)
```

这是真实主干集成阻塞，测试未通过。两个分支在合并后存在同名修订，需要先核对既有部署状态，再制定兼容的修复；本轮未改编号、`stamp`、跳过迁移或修改测试。完整原始错误输出随本目录保存。所有数据库操作仅涉及本地测试容器中新建且由测试夹具清理的随机数据库。

仓库根执行 `python3 scripts/collab.py check`：`check 通过（0 个警告）`。该命令不接受 `--as`；首次带该参数时参数解析失败，随后按 CLI 实际支持的格式重跑通过。

## SSH 确认后的只读盘点（15:43 UTC 起）

完整输出：`server-a-inventory.txt`、`server-b-inventory.txt`。命令见 `INVENTORY-COMMANDS.md`。

| 项目 | A | B |
|---|---|---|
| 主机名 | s1348279 | s8006604 |
| Tailscale 名称 | workspace-test-a | workspace-test-b |
| OS / 内核 | Debian 12 / 6.1.0-50 | Debian 11 / 5.10.0-19 |
| CPU | 4 | 4 |
| 总内存 / available | 7923 / 6882 MiB | 3915 / 3554 MiB |
| 根盘 / 余量 | 40 / 19 GiB | 40 / 37 GiB |
| 容器运行时 | Docker 29.9.2、containerd | 未发现 |
| 现存 Docker 容器 | 无 | 无 Docker |
| 需保护的已有资源 | 3 个 Workspace bridge 网络及 TrendRadar 配置卷；/opt/my-workspace | 系统、SSH、Tailscale |
| K3s/kubectl/Cilium/runsc/已知 kubeconfig | 未发现 | 未发现 |
| 现有验收进程 | 未发现 | 未发现 |

B 旧版 `lsblk` 不支持 `MOUNTPOINTS`，首轮原样保存该错误；随后以 `MOUNTPOINT` 只读重跑成功。没有挂载未挂载的盘、清理 Docker 网络/卷、停止服务或变更防火墙。

A 的 `/opt/my-workspace/.env` 不存在，该目录无 `.git`，指定深度未发现 Compose 文件。两个节点均未发现运行中的 Workspace 容器，故无法断言其他位置的生产配置值。本仓库默认网络开关仍为 false，本轮没有启用任何开关。

### 主机身份冲突

两机 `/etc/machine-id` 文件 SHA256 均为：

```text
73658ad349ec675300d22bdffe711f442745186fed8890811a422861e70da9de
```

独立 DMI product_uuid 摘要不同：A `fc7297ac8fd8a2b588aa5d3787b10645125b5c283537d4328fd5ae3b41f5a136`；B `e85ca38ef83514d789ae7ddc914be5e8427b92c74c894aabe78e06ab7980a8f9`。boot_id 摘要亦不同，结合不同 hostname、IP、OS 和内核确认是不同运行主机，但克隆 machine-id 会撞上本项目物理主机防重。

源码 `domain/physical_host_probe.py`、`runner_agent/agent.py` 和 `runtime_v2/telemetry.py` 均基于 `sha256('machine-id:' + machine_id)`；`domain/physical_hosts.py` 不允许同一物理身份占用两个 runner 登记。不能用虚构 ID 或修改防重约束冒充双机可信登记。未修改任一 `/etc/machine-id` 或 `/var/lib/dbus/machine-id`。

### A/B 底层互通未通过

从已严格认证的 A、B 分别使用 Python `socket.create_connection((peer,22),5)`，仅尝试读取 SSH banner：

```text
SOURCE 100.72.60.100 PEER 100.121.210.36
peer=100.121.210.36 tcp22=failed error=TimeoutError:timed out
SOURCE 100.121.210.36 PEER 100.72.60.100
peer=100.72.60.100 tcp22=failed error=timeout:timed out
```

随后只读筛选 `tailscale status --json` 中目标对端，双方 `BackendState=Running`、`Self.Online=true`，但对端均为 `Peer: []`。对指定对端执行 `tailscale ping --c=1 --timeout=5s --until-direct=false`，双方均输出：

```text
no matching peer
```

`iptables -S` 显示两机 INPUT/OUTPUT 为 ACCEPT，INPUT 挂接 ts-input，ts-input 接受 tailscale0；A 的 FORWARD 为 DROP 并挂接现有 Docker/ts-forward 链，B 为 ACCEPT。没有修改规则。此证据定位到两节点缺少对端 Tailscale netmap/可达性；同 tailnet 配置、节点共享范围或 ACL/grants 需管理员核查，不能仅凭这些输出断言具体 ACL 根因。平台 sidecar 能连两端不能替代 A/B 之间可达。

## 首次盘点后的验收门槛与下一步（16:10 复测前）

1. 先由 tailnet 管理员核实 A、B 所在 tailnet 和共享/ACL/grants，使双方获得对端信息及所需受限节点流量。先复跑上述双向底层探测，不能用公开 IP、额外隧道或放开所有 ACL 绕过。
2. 解决两个主机的重复 machine-id；这是主机身份变更，需要维护方案（保留原文件、核对 dbus、考虑重启和既有注册影响），不在只读盘点阶段擅自执行。
3. 修复当前主干重复 0066 修订并通过真实空库迁移和网关集成测试。必须先确认部署历史；不对生产 stamp/删表/修改修订号来隐藏冲突。
4. 上述门槛满足后，再准备独立非生产 K3s/Cilium/gVisor 环境：A 控制节点、B 工作节点；使用专用数据目录、专用服务名和固定版本/digest，不覆盖 A 的 Docker 配置/网络/卷，不使用默认 K3s ServiceLB/hostPort。先核对 CNI/API/Pod/Service 网段和实际节点端口，再安装。当前没有这些组件的安装副作用。
5. 按 README 的已有 harness 分别验收同实例双向 TCP/UDP、跨实例直接路径拒绝、DB-backed 授权网关与在途撤销；所有日志和失败都保留。仅网络 Pod 部分通过也不代表完整网络 PASS。

**结论：只读盘点与平台 SSH 身份验证已完成；底层 A/B 可达、可信双机身份、当前主干迁移以及集群前提均未满足，尚未创建任何远端隔离测试资源，真实网络验收未执行。未执行生产网络启用操作，仓库默认开关保持关闭；其他位置的生产配置值未核实。**

## 发布状态

初始前置文档提交 `8a43d84` 已推送到 `codex/T-0224-two-server-acceptance`。创建草稿 PR 的连接器动作被自动审批拒绝，理由是测试文档/日志发布到 GitHub 的授权和目标未获确认；未创建 PR，也未绕过拒绝。确认 SSH 后产生的服务器盘点和后续记录仅保存在本地，不推送，等待明确发布授权。

人类随后明确回复“允许”，授权将上述记录发布到 WorkspaceCore 草稿 PR。本轮按该授权推送记录并创建草稿；草稿不是验收通过证明，不合并、不修改服务器或生产网络开关。

草稿 PR 已创建：<https://github.com/brianabc121/WorkspaceCore/pull/203>。后续同任务复测证据在该草稿更新，不另开重复 PR。

## 调整 Tailscale grants 后的实际复测

人类按本聊天提供的配置增加 A→B、B→A 的 `tcp:22` grants，并明确告知已应用。原有 Codex Cloud→测试节点 grants 和 SSH root 规则保留。本轮仍经平台 `proxy:8088`、固定可信公钥和严格 SSH 校验进入两机；只读检查对端状态、指定对端 ping、TCP22 banner，没有赋予两机相互 root 登录权限。

命令沿用 `INVENTORY-COMMANDS.md`；原始输出分别为 `acl-retest-a.txt`、`acl-retest-b.txt`。两次 SSH 命令退出码均为0。

```text
A（2026-11-13T16:10:30Z）：
Peer: workspace-test-b, Online=true
pong from workspace-test-b (100.121.210.36) via 156.227.24.7:41641 in 2ms
peer=100.121.210.36 tcp22=connected banner=SSH-2.0-Tailscale elapsed_ms=52

B（2026-11-13T16:10:33Z）：
Peer: workspace-test-a, Online=true
pong from workspace-test-a (100.72.60.100) via 156.227.24.250:41641 in 1ms
peer=100.72.60.100 tcp22=connected banner=SSH-2.0-Tailscale elapsed_ms=27
```

判定：先前“对端不可见/双向TCP22超时”阻塞已解除。ping 显示直连传输，TCP 客户端仍以对端 Tailscale IP 建连。当前仅证明该底层基线，不能推断 K3s/Cilium 所需端口、UDP 工作负载、跨 OpenClaw 策略、授权或撤销已经通过；这些测试尚未执行。

### B 主机身份维护的只读前置与待批准方案

2026-11-13T16:11:46Z，确认 B=`s8006604`，`/etc/machine-id`（0444）和 `/var/lib/dbus/machine-id`（0644）均为普通文件，内容摘要相同，所在 ext4 根文件系统可写。`systemd-id128`、`install`、`cp`、`sha256sum`、`systemctl` 可用；Tailscale 和 dbus 均运行，Tailscale 状态文件存在且0600，仅检查元数据，没有读取凭据。没有发现新的应用服务。输出见 `b-identity-preflight.txt`。

下一步需要对主机身份维护取得单独确认。可审核范围如下，尚未执行：

1. 仅操作 B=`100.121.210.36/s8006604`；再次核对可信 SSH 身份、主机名和原文件摘要，无并发变更才继续。
2. 在 B 的 root 私有备份目录分别保存两个原始文件及权限；记录备份路径，不删除原备份。
3. 通过 B 自带的 `systemd-id128 new` 生成唯一32位标识，校验与原值不同；将同一新值写入 `/etc/machine-id` 和 `/var/lib/dbus/machine-id`，保留各自原有所有者及0444/0644权限。
4. 重启 B 一次使系统及 dbus 统一使用新身份；不改 A、Tailscale 状态、SSH 密钥、业务目录或物理身份防重规则。重启会短暂中断 B 的 SSH/Tailscale 连接，若无法恢复需要管理控制台介入。
5. 严格核对 B 的 SSH 指纹、原 Tailscale 节点/IP、两个 machine-id 的一致性以及与 A 的差异，复测双向 TCP22；同时确认服务恢复。失败时保留证据和原备份，恢复身份文件及再次重启应按当时实际访问情况处理。

截至本轮只读复测，未获执行该维护/重启的明确确认，未修改任一主机身份或重启服务器。主干仍是 `15a4c90`，迁移冲突的旧输出是该基线结果，不作为本轮新测试通过证据。

### 本轮发布状态补充

复测与 B 只读维护前置输出已在提交 `03d1ea4` 推送到 PR #203 分支。随后更新 PR 正文的连接器动作被自动审批拒绝，理由为先前授权未明确覆盖新增服务器/网络/身份证据。未通过其他方法重试正文更新，PR 正文暂仍保留上轮网络失败的描述，应以本报告的时间分段和原始复测输出区分历史与最新结果。本段先仅保存在本地，等待进一步发布确认。
