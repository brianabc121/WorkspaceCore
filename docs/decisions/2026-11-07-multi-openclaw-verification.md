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

## 3. CI 实测（T-0148，PR brianabc121/WorkspaceCore#169 的 agents 任务）

GitHub Actions 的 `ubuntu-24.04` runner：**cgroup v2**（SSH 部署验收打印 `/sys/fs/cgroup` 的文件系统类型为 `cgroup2fs`，commit 80d287b），
Docker 由 `install-docker.sh` 按锁定版本安装，真实 sshd、真实 OpenClaw 2026.9.36。

| 项 | 结果 |
|---|---|
| 自己容器的 cgroup：`cpu.stat`、`memory.current`、`memory.max`、`memory.stat`、`pids.current` | 通过。两个实例各读各的：甲 `mem_bytes` 979935232、`mem_limit` 3221225472（= 设置的 3072 MiB）、`pids` 52、`cpu_cores` 0.237；乙 `mem_bytes` 1048616960、`mem_limit` 空（没设上限）、`pids` 78、`cpu_cores` 1.402（第二次上报才有 CPU，两次之差）。commit 96a8d1c |
| 真实 OpenClaw 在项目网络里：IM 长连接、MCP、密钥领取、用量上报、`wakeup.mjs` / `stop-runs.mjs` | 通过。agents 场景（两个真实实例，`patch_compose` 用占位容器持有命名空间）全部通过；SSH 部署的两个实例都“连上了工作台”（心跳、取密钥），照 crontab 那几行跑用量与服务器状态上报成功。commit e9ce4b3、96a8d1c |
| 外网实例的隧道（ghostunnel）用自己的 hosts 文件连到入口 | 通过。自动接入验收：`tls/hosts` 把入口主机名映射到 `AGENT_PUBLIC_ADDRESS`，在实例自己的命名空间里经隧道取到密钥、心跳 200；宿主上连不到隧道端口。commit e9ce4b3 |
| 同一台机器上两个实例都上线、卸载一个不影响另一个 | 通过。准备一次（五步、一次密码）后部署甲、乙都不用密码；`authorized_keys` 只有一行；各六行带自己标记的定时任务；甲的重启、重新拉起、重新部署不动乙的容器（容器 ID 不变）；卸载甲（删除目录）后乙的容器、目录、定时任务都在；乙还在时不能移除服务器；卸载乙后上一级目录一起删掉，吊销、移除成功。commit 96a8d1c |
| 一个空闲 OpenClaw 的内存与磁盘占用 | 内存约 0.93 ~ 1.0 GiB（上面两个实例，刚上线、只跑了一次假模型对话）；数据目录大小每 10 分钟算一次，验收时间内还没有值 |
| 真实的 `net` 服务：SSH 部署后 OpenClaw、隧道是 `container:<net>`，`net` 在 `<项目>_default` 网络上；资源上限进了容器（`Memory`、`NanoCpus`） | 通过。两个实例的 `net` 各在自己的 `myworkspace-openclaw-<短号>_default` 上、不是宿主网络，OpenClaw 与隧道都是 `container:<自己的 net>`，两个 `net` 不同；甲 `Memory` 3221225472、`NanoCpus` 1500000000，乙都是 0。commit e9ce4b3、96a8d1c |
| agents 验收里甲、乙在同一台机器上，按指纹归到同一台服务器；甲读得到自己容器的用量 | 通过（场景 19）：两个实例归到同一台自动识别的服务器；甲 `available` true、`mem_bytes` 1634299904、`pids` 59。commit e9ce4b3 |
| 重新部署后的每晚复盘任务（OpenClaw 网关冷启动） | 96a8d1c 上重新部署甲后 50 秒就更新好（“已有”）；80d287b 上同一步 4 分半（24 次 × 10 秒）网关还没就绪，记为跳过（实例已在线，不算部署失败）。机器上有两个实例加全栈（4 核）时冷启动会超过原来的 4 分钟，重试窗口改为 10 分钟（`REFLECTION_TRIES` 60）；验收在这一步没成时打印任务日志与 OpenClaw 日志 |
| SSH 部署的实例与整机数据归到那台 SSH 服务器 | 96a8d1c 上**发现问题**：部署过程中定时任务先上报，按指纹自动建了一台“自动识别的服务器”；部署完实例归到 SSH 服务器后，SSH 服务器（还没有指纹）接不过来，整机数据不记。已修：手动指定 / 部署的那台还没有指纹时接过指纹，自动建的那台并过来、删掉（`host_metrics._absorb`），部署结束与手动指定时原来那台自动识别的空了就删（`drop_if_empty_auto`）；单元测试 `test_ssh_server_takes_over_fingerprint_from_auto_server`。f14ac3d 起两实例 SSH 部署验收全部通过（整机数据记到那台 SSH 服务器） |
| 服务器页浏览器验收（`agent_ssh_browser.cjs`：添加服务器、核对指纹、准备、部署到服务器、运维、负载、服务器详情、卸载、吊销、移除，含 390px 宽度） | 跑出两个页面问题并修掉：还没确认时整串 SHA256 指纹在 390px 下撑出横向滚动（b7b3441，指纹可以换行）；移除服务器后刷新时详情 404、组件已卸载，回不到列表（8709340，删除成功先回列表再刷新，补单元测试）。63a8b01 上移除之前的各步全部通过；以本 PR 最后一次 agents 任务为准 |
