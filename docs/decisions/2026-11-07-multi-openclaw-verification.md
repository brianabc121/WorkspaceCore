# 核验记录：一台服务器上的多个 OpenClaw 实例（T-0143）

日期：2026-11-07。方案：`docs/v2/MULTI_OPENCLAW_PLAN.md` 第 13 节。

## 1. 锁定的版本

| 组件 | 版本 |
|---|---|
| OpenClaw 镜像 | `openclaw/openclaw:2026.9.36@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f`（与接入包一致） |
| 本会话的 Docker | 29.9.2，cgroup v1（cgroupfs 驱动） |
| CI（GitHub `ubuntu-latest`） | Docker 28.0.4，cgroup v2（T-0148 实测后补） |

所有容器都按接入包的方式运行：`node` 用户、`cap_drop: ALL`、`no-new-privileges`。

## 2. 本会话实测（cgroup v1）

| 项 | 结果 |
|---|---|
| `boot_id` | 容器里读 `/proc/sys/kernel/random/boot_id` 与宿主机完全相同 |
| CPU | `/sys/fs/cgroup/cpuacct/cpuacct.usage` 可读（纳秒累计值）；v2 的 `cpu.stat` 不存在（v1 环境） |
| 内存 | `memory/memory.usage_in_bytes`、`memory/memory.limit_in_bytes`（设了 `--memory 512m` 时为 536870912）、`memory/memory.stat` 的 `rss` / `cache` / `inactive_file` 可读 |
| 进程数 | `pids/pids.current` 可读 |
| 网卡 | 项目网络（bridge）里 `/proc/net/dev` 只有 `lo`、`eth0`，默认路由在 `eth0`：读到的是这个实例自己的网速 |
| 负载与内存总量 | `/proc/loadavg`、`/proc/meminfo` 仍是整台机器的值（没有 lxcfs） |
| 回环隔离 | 实例 A 的容器在 `127.0.0.1:18789` 开端口；另一个项目网络里的实例 B 连 `127.0.0.1:18789` 得到 `ECONNREFUSED` |
| 共用网络命名空间 | 用 `--network container:<A>` 加入 A 的“隧道”容器能连到 A 的 `127.0.0.1:18789` |
| 各挂各的 hosts | A 用 `--add-host public.test:127.0.0.1`，解析到 `127.0.0.1`；加入 A 网络命名空间的隧道把自己的 hosts 文件只读挂到 `/etc/hosts`，解析到 `203.0.113.7`（Docker 允许在 container 网络模式下用挂载代替它生成的 hosts 文件） |

结论：

- 方案第 3.2 节的 A（每个实例自己的网络命名空间，隧道与取图代理共用命名空间、各挂自己的 hosts 文件）在锁定镜像上成立；
- 第 5.2 节的 cgroup 指标在 v1 下都能读到。

### 2.1 谁来持有网络命名空间（实施 T-0145 时补测，Docker 29.9.2 / Compose 5.6.1）

| 项 | 结果 |
|---|---|
| `docker compose restart <主容器>` | 加入它的容器（`network_mode: "service:<主容器>"`）被 Compose 一起重启，之后仍能连到主容器的 `127.0.0.1` |
| 主容器进程崩溃（宿主机上 `kill -9`，由 `restart: unless-stopped` 拉起） | **加入它的容器断网**：只剩 `lo`，连不到主容器的端口，要手动重启它们才恢复 |
| 加入的容器崩溃、被拉起 | 正常重新加入，能连到主容器 |
| `docker compose run --no-deps` 一个加入别人命名空间的服务 | 主容器没起时报 `cannot share network namespace with service net: container missing`；先 `up -d net` 后可以运行、能联网 |
| 只运行 `tini -- sleep infinity` 的锁定 OpenClaw 镜像 | 内存 880 KiB；`docker compose stop` 0.2 秒（tini 转发 SIGTERM） |
| 加入者看到的 hosts | 主容器 `extra_hosts` 写的记录，加入者里能解析到；加入者挂自己的 hosts 文件时以自己的为准 |

结论：方案原来写的“隧道加入 OpenClaw 的命名空间”在 OpenClaw 崩溃或每周重启时会让隧道断网。实施时改为
**每个实例一个只运行 `sleep` 的 `net` 服务持有命名空间**（同一个锁定镜像，不另外下载），OpenClaw、隧道、取图代理都加入它；
`net` 在 Compose 自己的项目网络上（不是共享的 `bridge`，不同项目之间 Docker 默认隔离）。SSH 部署在安装插件前先 `up -d net`。

### 2.2 上报脚本（`bin/host-report.mjs`，锁定镜像里运行）

`docker run --memory 512m --cpus 1`：容器 cgroup 读到 `cpu_usec` 353727、内存已用 18018304（`usage_in_bytes − total_inactive_file`）、`pids` 7、
内存上限 536870912；服务器指纹（`boot_id` 的 HMAC-SHA256，盐 `abc`）与宿主机上用 Python 算的完全一致。
盐按工作空间固定（`sha256("myworkspace-host-salt\0" + 工作空间 ID)`）：`boot_id` 是 122 位随机数，哈希不能反推，
不同工作空间的指纹对不上；不用会轮换的凭证密钥，换密钥后指纹不变。

## 3. 待 CI 实测（T-0148）

| 项 | 结果 |
|---|---|
| cgroup v2 的 `cpu.stat`、`memory.current`、`memory.max`、`memory.stat`、`pids.current` | （T-0148 补） |
| 真实 OpenClaw 在项目网络里：IM 长连接、MCP、密钥领取、用量上报、`wakeup.mjs` / `stop-runs.mjs` | （T-0148 补） |
| 外网实例的隧道（ghostunnel）用自己的 hosts 文件连到入口 | （T-0148 补） |
| 同一台机器上两个实例都上线、卸载一个不影响另一个 | （T-0148 补） |
| 一个空闲 OpenClaw 的内存与磁盘占用 | （T-0148 补） |
| 真实的 `net` 服务：SSH 部署后 OpenClaw、隧道是 `container:<net>`，`net` 在 `<项目>_default` 网络上；资源上限进了容器（`Memory`、`NanoCpus`） | 本 PR 的 CI（`agent_ssh_acceptance.py`）实测后补 |
| agents 验收里甲、乙在同一台机器上，按指纹归到同一台服务器；甲读得到自己容器的用量 | 本 PR 的 CI（场景 19）实测后补 |
