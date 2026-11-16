# 旧系统初始化使用各自可用的固定 Docker 版本（T-0263）

2026-11-16 用户报告 Debian 11 bullseye、x86_64、root、未安装 Docker 时，点击初始化服务器直接失败。
Python 的 `SUPPORTED_OS` 与 Shell 的发行版检查都只允许 Ubuntu 22.04 / 24.04、Debian 12。
Debian 11 的 Docker 官方仓库也没有脚本原本统一安装的 29.9.1 / 2.4.2 / 5.6.0 组合，因此只扩展白名单不够。

本次保留固定版本、官方签名公钥指纹验证、hold 和沿用已有 Docker 的策略，补充两组旧系统版本：

| 系统 | docker-ce / docker-ce-cli | containerd.io | docker-compose-plugin |
| --- | --- | --- | --- |
| Debian 11 bullseye | `5:29.8.0-1~debian.11~bullseye` | `2.3.4-2~debian.11~bullseye` | `5.5.1-1~debian.11~bullseye` |
| Ubuntu 20.04 focal | `5:28.1.1-1~ubuntu.20.04~focal` | `1.7.27-1`（没有发行版后缀） | `2.35.1-1~ubuntu.20.04~focal` |

原有 Ubuntu 22.04 / 24.04、Debian 12 的锁定版本不变。不改用浮动最新版，不保证任意更旧系统；未列入范围的系统仍可先手动安装 Docker 与 Compose。

2026-11-16 逐条下载并检查以下官方 Packages 索引，确认四个软件包的完整版本号在 amd64 / arm64 都存在，Debian 11 的 containerd 依赖 libseccomp2 >= 2.5.0：

- [Debian 11 amd64](https://download.docker.com/linux/debian/dists/bullseye/stable/binary-amd64/Packages.gz)
- [Debian 11 arm64](https://download.docker.com/linux/debian/dists/bullseye/stable/binary-arm64/Packages.gz)
- [Ubuntu 20.04 amd64](https://download.docker.com/linux/ubuntu/dists/focal/stable/binary-amd64/Packages.gz)
- [Ubuntu 20.04 arm64](https://download.docker.com/linux/ubuntu/dists/focal/stable/binary-arm64/Packages.gz)

实际验证：Ubuntu 20.04 的干净 amd64 容器装上表中四包并全部 hold；本环境为 Cloud 代理挂载宿主 CA，TLS 校验保持开启。容器没有 init / Docker daemon，随后按预期以 `docker_not_running` / 退出码 5 结束，CI 的精确版本校验对实际输出通过。

Debian 11 的干净 amd64 容器在系统 apt 软件源安装 gnupg 等依赖时收到 404，未完成 Docker 实装；arm64 本地仅核验官方索引。新增 GitHub Actions 安装矩阵覆盖 Debian 11 / Ubuntu 20.04 的 amd64 实际安装；索引核验与进程内 SSH 回归不等同于真实服务器部署。
