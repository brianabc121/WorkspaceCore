# T-0224 双服务器网络验收：前置核对

本记录是前置核对，不是双服务器验收通过证明。所有时间为 UTC。

## 代码与已有执行

- 2026-11-13，GitHub 连接器实时读取 PR #197：`state=closed`、`merged=true`，`merged_at=2026-11-13T15:28:36Z`。原 head 为 `4679f7ae8048b75c2d2fd5eedbdf871c82a170c3`。
- `git fetch origin` 后以 `15a4c90450af443ed3b322fd615b8832258f2b51` 为本次基线；主干包含 Claude 修复 `a07628c`，不再沿用原 PR 分支重复实施。
- 已阅读 `.collab/handoffs/2026-11-13-1432_T-0224_claude-to-codex.md`。交接前状态为 `owner=codex/status=handoff`，本轮接手为 `in_progress`。
- 已查当前可见聊天与主干任务日志，未发现其他正在执行的双服务器验收。服务器内是否有既有验收进程、集群和资源，必须在可信 SSH 登录后只读核对；目前未完成该检查。
- 人类本轮重新授权双机测试，替代历史记录中取消远端测试的本轮范围限制；仍不授权未经验证的生产切换。

## SSH 身份前置：待确认

A：`100.72.60.100:22`；B：`100.121.210.36:22`。均经平台 HTTP CONNECT `proxy:8088`，没有直接连接远端。

两台目标在本轮严格 SSH 探测中均返回：

```text
Remote protocol version 2.0, remote software version Tailscale
Server host key: ssh-ed25519 SHA256:vbzwuvOu30pbIyANTCFBC/b4/At+uvpTB8kvl1QpKJE
No ED25519 host key is known ... and you have requested strict checking.
Host key verification failed.
```

指纹相同不能独立证明两台服务器的身份。已请求人类通过可信渠道确认指纹，未将本次网络扫描结果自行提升为信任。未设置 `StrictHostKeyChecking=no` 或 `accept-new`，未写入信任记录，未认证登录，未执行远端命令。

## 后续执行顺序与保护边界

1. 取得可信指纹后固定 `known_hosts`，仍使用严格校验和平台 TCP CONNECT；先执行 `hostname`、`id`，核对 A/B 实际主机身份。
2. 只读盘点 CPU、内存、磁盘、OS、内核、路由/网段、监听端口、运行服务、Docker 容器名称/镜像/状态/端口及已有 Kubernetes/Cilium/gVisor；不导出环境变量、Secret、数据库内容或 kubeconfig 原文。检查既有验收进程及资源名称，避免重复开工。
3. 根据实际占用判断隔离方案，保护现有服务、卷、端口及数据。若没有安全的非生产双节点集群，不能把在生产宿主直接安装 K3s/Cilium 当作无风险准备动作；先形成基于盘点的具体方案。
4. 依 `infra/runtime-v2/README.md` 核对受保护节点标签、gVisor、CoreDNS、Cilium、HTTPS kubeconfig 和已核验镜像 digest。使用明确的非生产 context；不猜集群凭据或镜像。
5. 运行既有 `acceptance_two_nodes.py` 的同实例跨节点 TCP/UDP、跨实例默认拒绝及 DNS 测试；保存命令、退出码、真实报告和 run UUID。仅清理本次取得 UID 回执的测试资源。
6. 跨实例授权与在途撤销必须使用独立非生产 DB-backed allocation/generation/Pod UID 和专用规则，通过正常工作台授权流程。验证允许源、未授权源、撤销后 8 秒内断开存量流及拒绝新连接；不能用临时网络 Pod 冒充可信 DB 绑定。
7. 公网/LB 及其他启用门槛未完成时，保留相应 blocked，不把局部 PASS 当完整 PASS；生产 `RUNNER_NETWORK_VERIFIED` 必须保持关闭。

## 当前结果

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
