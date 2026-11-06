# 运行服务器的核验记录（T-0135，docs/v2/RUNNER_PLAN.md）

> 规则（`docs/AGENTS.md` 第 8 节）：第三方接口必须在锁定版本上核验，不按名称猜。
> 本记录分两部分：Docker Engine 的接口与行为在本会话自己的 Docker 上实测（第 2 节）；
> gVisor 的安装方式按官方文档（第 3 节），运行行为在 CI 的运行服务器验收里实测（第 4 节，T-0141 跑完后补结果）。

## 1. 锁定的版本

| 组件 | 版本 | 来源 |
|---|---|---|
| Docker Engine（运行服务器） | 29.9.1（`docker-ce=5:29.9.1-1`、`containerd.io=2.4.2-2`、`docker-compose-plugin=5.6.0-1`） | 现有的 `services/api/app/agent_provisioning/files/install-docker.sh`（OpenClaw 服务器用的同一个，CI 的 docker-pins 在 Ubuntu 22.04 / 24.04、Debian 12 上实测） |
| Docker Engine（本次实测） | 29.9.2，API 1.56（最低 1.40） | 本会话的 `dockerd`；29.9.x 是同一个 API 版本 |
| gVisor（runsc） | release-20261019.0（已发布超过两周；之后的 20261026.0、20261102.0 暂不用） | GitHub `google/gvisor` 的 tag；官方发布桶 |
| gVisor 发布包 | `https://storage.googleapis.com/gvisor/releases/release/20261019.0/x86_64/gvisor.tar.zstd`，sha512 `bcbf431b2f9a039f6ab23bfe23986031130c0f149294978cedfd46910901deba0737d8642c848f597ed4b5c72324885db4d75ee86a1f6a90915fbe1f2f2eb915` | 同目录的 `gvisor.tar.zstd.sha512`（本次下载核对） |
| runner-agent 的基础镜像 | `python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704` | 本机已有的镜像的 RepoDigests |

## 2. Docker Engine 接口与行为（本会话实测，runc）

实测程序：`app/runner_agent/docker.py`（标准库，经 unix socket 发 HTTP）加一段验证脚本，逐项结果如下。

| 用到的接口 | 实测结果 |
|---|---|
| `GET /version` | `Version 29.9.2`、`ApiVersion 1.56` |
| `POST /networks/create`（`Internal: true`、`Labels`） | 建出的网络里直连外网：`URLError <urlopen error [Errno 101] Network is unreachable>` |
| `POST /networks/{id}/connect`（`Aliases`） | 另一个容器以别名 `egress` 连进同一个内部网络后，环境里 `http://egress:3128/` 返回 200（Docker 内置 DNS 在内部网络里可用） |
| `POST /volumes/create`（`Labels`） | 新卷的根目录是 `0:0`；用一次性助手容器（root，`--network none`）`chown -R 1000:1000` 后改为 `1000:1000` |
| `POST /containers/create` 的 `HostConfig` | `Runtime=runc`、`Memory=MemorySwap=268435456`、`NanoCpus=500000000`、`PidsLimit=64`、`CapDrop=[ALL]`、`SecurityOpt=[no-new-privileges:true]`、`Init=true`、`Mounts`（volume）、`Tmpfs`、`NetworkMode`、`LogConfig` 都按值生效（`GET /containers/{id}/json` 读回一致） |
| `POST /containers/{id}/exec` + `POST /exec/{id}/start`（`Detach:false, Tty:false`）+ `GET /exec/{id}/json` | 多路复用流（8 字节头）拆出 stdout 与 stderr；`exit 3` 读回 `ExitCode 3`；`id -u` 为 1000；`CapEff 0000000000000000` |
| exec 的超时 | 读超时设成时限：`sleep 10`、时限 2 秒 → 2.0 秒返回“结果未知”。**Docker 没有单独终止一个 exec 的接口**，所以超时后停止这个容器（数据卷保留） |
| exec 的输出上限 | 5000 字节的输出、上限 1000 → 只留前 1000 字节并标记截断 |
| `PidsLimit` | 起 100 个后台进程：`sh: 0: Cannot fork` |
| `GET /containers/{id}/archive`、`PUT /containers/{id}/archive` | **只创建、不启动**的助手容器挂同一个数据卷：读出环境写进卷里的文件（内容一致）；写进去的文件（tar 里 uid 1000）在环境里看得到，属主 1000 |
| `GET /containers/{id}/stats?stream=0&one-shot=1` | 读到 `memory_stats.usage`、`memory_stats.limit`（268435456）与 `cpu_stats` |
| `GET /system/df` | 数据卷给出 `UsageData`（`RefCount`、`Size`） |
| `GET /containers/json?all=1&size=1&filters={"label":[...]}` | 按标签列出，含 `SizeRw` |
| 不存在的镜像 | `404 No such image: …` |
| `Devices: /dev/kvm`（本机没有 KVM） | 启动时报 `error gathering device information while adding custom device "/dev/kvm": no such file or directory`：原样返回给智能体，页面提示管理员开启硬件虚拟化 |
| 删除（`DELETE` 容器 / 网络 / 卷） | 按标签删干净 |

两个影响设计的发现：
1. **不能给环境容器设 `WorkingDir=/workspace`**：镜像里没有这个目录时，Docker 会以 root 创建它，并把这个属主复制到空数据卷上
   （实测：设了 `-w /workspace` 后卷的根目录变回 `0:0`，uid 1000 写不进去；不设时保持 `1000:1000`）。所以容器本身不设工作目录，每次 exec 才设。
2. **环境里的 `NO_PROXY` 要包含 `egress`**：否则访问出口代理本身也会走代理。

## 3. gVisor 的安装方式（官方文档原文）

来源：`google/gvisor` 仓库的 `g3doc/user_guide/install.md` 与 `g3doc/user_guide/quick_start/docker.md`（gvisor.dev 本会话访问不了，读的是仓库里的同一份文档）。

- “Prior to 2026-07, gVisor releases were limited to 2 binaries (`runsc` and `containerd-shim-runsc-v1`) … This is changing to a multi-file approach”；
  新的发布包 `gvisor.tar.zstd` 含 `runsc`、`containerd-shim-runsc-v1` 与 `gvisor-bin/`，“`runsc` looks for `gvisor-bin/` next to its own binary, so keep them together”。
- 安装（文档原文）：`wget ${URL}/gvisor.tar.zstd ${URL}/gvisor.tar.zstd.sha512`、`sha512sum -c gvisor.tar.zstd.sha512`、
  `sudo tar --zstd -xf gvisor.tar.zstd -C /usr/local/bin`；没有 zstd 时可以用 `gvisor.tar.bz2`。
- 指定版本：`https://storage.googleapis.com/gvisor/releases/release/${yyyymmdd}/${ARCH}`；点版本 `…/release/${yyyymmdd}.${rc}/${ARCH}`。
- 接入 Docker：`sudo /usr/local/bin/runsc install`、`sudo systemctl reload docker`，之后 `docker run --runtime=runsc …`；
  “`runsc install` … will install a docker runtime”；“You must restart the Docker daemon after installing the runtime”。
- 要求：“gVisor supports x86_64 and ARM64, and requires Linux 5.6+”。

运行服务器的自动部署按上面的方式装 release-20261019.0，核对 sha512 后再解压。

## 4. 待 CI 实测（T-0141）

本会话下载并运行 gVisor 的二进制被权限策略拦下，gVisor 下的行为改在 CI 的运行服务器验收里实测（GitHub 的 runner 上按第 3 节安装）。
要实测的项：`--runtime=runsc` 的环境容器能启动；exec 的输出与退出码；内部网络里经出口代理访问外网、直连不通；
数据卷经助手容器读写；资源限制；`runsc` 与 `runc` 两种运行时都能用。结果补在下面。

| 项 | 结果 |
|---|---|
| （T-0141 跑完后补） | |
