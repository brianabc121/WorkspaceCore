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

- 方案第 3.2 节的 A（每个实例自己的网络命名空间，隧道与取图代理共用 OpenClaw 的命名空间、各挂自己的 hosts 文件）在锁定镜像上成立；
- 第 5.2 节的 cgroup 指标在 v1 下都能读到。

## 3. 待 CI 实测（T-0148）

| 项 | 结果 |
|---|---|
| cgroup v2 的 `cpu.stat`、`memory.current`、`memory.max`、`memory.stat`、`pids.current` | （T-0148 补） |
| 真实 OpenClaw 在项目网络里：IM 长连接、MCP、密钥领取、用量上报、`wakeup.mjs` / `stop-runs.mjs` | （T-0148 补） |
| 外网实例的隧道（ghostunnel）用自己的 hosts 文件连到入口 | （T-0148 补） |
| 同一台机器上两个实例都上线、卸载一个不影响另一个 | （T-0148 补） |
| 一个空闲 OpenClaw 的内存与磁盘占用 | （T-0148 补） |
