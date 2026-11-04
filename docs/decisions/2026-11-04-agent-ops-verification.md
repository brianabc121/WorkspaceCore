# 核验：智能体能力补齐与 OpenClaw 服务器运维（T-0109）

- 状态：accepted（核验记录；方案本身见 `docs/v2/AGENT_OPS_PLAN.md`，待人类批准）
- 日期：2026-11-04
- 范围：`docs/v2/AGENT_OPS_PLAN.md` 第 5 节服务器指标、第 6 节运维动作所依赖的第三方行为

## 1. 在 OpenClaw 容器里读宿主机指标

**结论**：用接入包的运行方式读 `/proc` 与文件系统统计，得到的是宿主机的数值。具体做法如下：

- 镜像 `openclaw/openclaw:2026.9.36`（digest 见 `docs/versions.md`）；
- 用户 `node`（uid 1000）、`network_mode: host`、`cap_drop: [ALL]`、`no-new-privileges`；
- 接入包的 `state/` 挂到 `/home/node/.openclaw`。

外网模式的 Compose 也是 `network_mode: host`，只多了 `extra_hosts` 和隧道服务（`app/agent_bundle/__init__.py` 的 `@PUBLIC_OPENCLAW@` 替换），结论相同。

**方法**：在本会话的 Docker（29.9.2，cgroup v1）上用同样的参数运行镜像自带的 Node（v24.22.1），读下表各项，并与宿主机上 `nproc`、`/proc/loadavg`、`/proc/meminfo`、`df -B1`、`/proc/net/route`、`/proc/uptime` 的输出对比。

| 指标 | 容器里的来源 | 实测（容器 / 宿主机） |
|---|---|---|
| CPU 核心数、型号 | `os.cpus()` | 4 / `nproc` 4；型号相同 |
| 1 / 5 / 15 分钟负载 | `os.loadavg()`（即 `/proc/loadavg`） | 2.67 0.86 0.32 / 相同 |
| CPU 累计时间 | `/proc/stat` 第一行 | 可读；使用率要用两次之差计算 |
| 内存 | `/proc/meminfo` 的 `MemTotal`、`MemAvailable` | 16477348 kB、15894632 kB / 相同（读取时间相差约 1 秒，可用值略有变动） |
| Swap | `/proc/meminfo` 的 `SwapTotal`、`SwapFree` | 0 kB / 相同 |
| 接入包目录所在分区 | `fs.statfsSync("/home/node/.openclaw")` | 总量 270553174016 字节、可用 4542562304 字节 / `df -B1` 相同 |
| Docker 数据目录所在分区 | `fs.statfsSync("/")`（容器根目录在 Docker 数据目录上） | 同上（本环境两者在同一分区；`DockerRootDir` 是 `/var/lib/docker`） |
| 默认路由网卡 | `/proc/net/route` 里目标为 `00000000` 的一行 | `eth0` / 相同 |
| 网卡收发累计字节 | `/proc/net/dev` 中该网卡的第 1、9 列 | 可读；速率要用两次之差计算 |
| 开机时长 | `/proc/uptime` | 与宿主机一致 |
| 容器内存上限 | `/sys/fs/cgroup/memory.max`（cgroup v2） | 本环境是 cgroup v1，文件不存在；脚本要同时尝试 v1 的 `/sys/fs/cgroup/memory/memory.limit_in_bytes` |

**注意**：

- 宿主机装了 lxcfs，或者用 rootless Docker 时，`/proc` 可能是容器自己的视图（方案第 11 节）。
- 本环境的接入包目录和 Docker 数据目录在同一分区；真实服务器上可能分开，所以方案里两个都报。

## 2. MCP 工具错误的格式

锁定的 OpenClaw 带的 `@modelcontextprotocol/sdk` 1.32.1，客户端只要结果里有 `structuredContent`，就按工具的 outputSchema 校验，错误结果也不例外（`dist/esm/client/index.js` 第 486 ~ 498 行）。T-0108 已改为错误结果只带文字内容，新增的工具照此执行。

## 3. 运维动作用到的 Docker Compose 子命令

方案第 6.1 节用到这些子命令：`docker compose ps`、`logs --tail`、`restart`、`up -d`，以及 `docker stats --no-stream`、`docker image prune -f`。它们都是 Docker Compose v2 / Docker CLI 的标准子命令。

- 本会话 Docker Compose 的版本：`docker compose version` 输出 `Docker Compose version v5.6.1`。
- 运维动作的输出格式不按记忆解析。实施时（T-0113 / T-0114）在 agents CI 的 SSH 部署验收里：
  - 用 `scripts/install-docker.sh` 装好的版本和真实的部署账号，逐个执行这些命令；
  - 状态页需要结构化输出的地方，用 `--format json` 并核对字段名；
  - 字段名对不上就只显示原文。

## 4. 现有代码里可以复用的部分

- **SSH 通道**：`app/integrations/ssh.py` 的 `connect` / `run`，带固定主机指纹、超时与错误归类；专用密钥在密钥库里加密保存（T-0089）。
- **日历**：日历页已经显示有截止时间的任务（`apps/web/src/features/calendar/CalendarWorkspace.vue`）；智能体的 `calendar_agenda` 只返回日程（`CalendarAgendaOutput.items`）。
- **IM 群**：`im_groups` 表里没有项目字段，建群与拉人在 `app/domain/im_groups.py`。
- **任务输出**：给智能体的任务（`TaskItem`、`TaskDetail`）没有执行者和计划开始字段；`UpdateTaskInput` 不能改执行者。
