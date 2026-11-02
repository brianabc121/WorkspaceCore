---
id: T-0075
title: 一键部署兼容性：Debian 最小安装没有 curl 的说明；Docker 官方脚本在 Ubuntu 20.04 等停更系统上失败时只装核心组件；提示系统已停止更新
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: scripts/deploy.sh,docs/deploy-guide.md,README.md
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0075 一键部署兼容性：Debian 最小安装没有 curl 的说明；Docker 官方脚本在 Ubuntu 20.04 等停更系统上失败时只装核心组件；提示系统已停止更新

## 目标
人类在不同服务器上运行一键部署时遇到两个问题：
1. Ubuntu 20.04（focal）：`get.docker.com` 默认装最新版，从 28.2 起加装 `docker-model-plugin`；focal 的 Docker 仓库最高只到 28.1.1、没有这个包，
   整条安装失败（`E: Unable to locate package docker-model-plugin`）。
2. Debian 12 最小安装：系统没有 curl，`curl … | bash` 这条一键命令本身就跑不起来（`curl: command not found`）。

让脚本在停更系统上也能装好 Docker、提醒换系统；文档写明没有 curl 时先装 curl。

## 验收标准
- [x] 官方脚本失败、且是 apt 系并已写好 `/etc/apt/sources.list.d/docker.list` 时，改为只安装 docker-ce、docker-ce-cli、containerd.io、docker-compose-plugin、docker-buildx-plugin；其他情况照旧报错退出
- [x] 部署时检查系统版本：Ubuntu 22.04 以下、Debian 12 以下提示已停止常规安全更新、建议换系统，但不阻止部署
- [x] 部署教程、README 与脚本开头写明 `curl: command not found` 时先 `apt-get update && apt-get install -y curl ca-certificates`
- [x] 测试：ubuntu:20.04 / 24.04 最小镜像按文档装 curl 后运行 `deploy.sh install`；停更提示在 Ubuntu 20.04、Debian 11 出现，Ubuntu 22.04 / 24.04、Debian 12 不出现；shellcheck

## 背景 / 约束 / 参考
- Docker 安装脚本：<https://github.com/docker/docker-install/blob/master/install.sh>（`get.docker.com` 的源码；apt 系先写 `docker.list` 再装包，`version_gte 28.2` 时加 `docker-model-plugin`）
- Docker 仓库（2026-11-02 核验）：focal 最高 docker-ce 5:28.1.1、无 docker-model-plugin；bullseye 5:29.8.0；bookworm、jammy 5:29.9.2，都有 docker-model-plugin

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 07:13 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 07:13 UTC · claude · 开始任务
- 2026-11-02 07:13 UTC · claude · 人类在 Ubuntu 20.04（focal）上运行一键部署：get.docker.com 加装 docker-model-plugin，focal 的 Docker 仓库最高 28.1.1、没有这个包，安装失败（E: Unable to locate package docker-model-plugin）
- 2026-11-02 08:00 UTC · claude · 人类在 Debian 12 最小安装上运行一键命令报 curl: command not found（系统没有 curl）：部署教程、README、脚本开头写明先 apt-get update && apt-get install -y curl ca-certificates；任务范围扩大到 README.md。修正 check_os 的 &&/|| 优先级（之前 Ubuntu 20.04 的提示不会出现）；Debian 11 仓库仍有 29.8.0，提示改为“会停止发布新版本（Ubuntu 20.04 停在 28.1.1）”
- 2026-11-02 08:00 UTC · claude · 测试：ubuntu:20.04 最小镜像无 curl → 按文档装 curl → deploy.sh install --yes：Python 3.12.16 安装、停更提示、官方脚本因 docker-model-plugin 失败 → 回退装 docker-ce 28.1.1 / Compose 2.35.1 / Buildx 0.23.0 → 在 DNS 检查处按预期停下；ubuntu:24.04 同样流程，官方脚本直接成功（29.9.2，含 docker-model-plugin），没有停更提示与回退；check_os 在 Ubuntu 20.04、Debian 11 提示，Ubuntu 22.04/24.04、Debian 12 不提示；shellcheck v0.10.0 -S warning 通过；pytest test_deploy_script/test_init_env 14 passed。沙箱拦截 deb.debian.org 与 get.docker.com，Debian 镜像的 apt 步骤没能实跑（get.docker.com 用 GitHub 上同一脚本代替）
- 2026-11-02 08:00 UTC · claude · status in_progress → done：Debian 最小安装没有 curl 的说明；停更系统上 Docker 回退为核心组件；停更提示
