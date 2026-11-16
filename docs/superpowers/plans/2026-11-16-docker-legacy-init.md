# 旧系统 Docker 初始化兼容修复计划

> 按 superpowers:executing-plans 在本会话内实施，以失败回归测试、最小修改和实际验证交付。

**Goal:** Debian 11 与 Ubuntu 20.04 可自动初始化 Docker，解决用户报告的 Debian 11 失败。

**Architecture:** 保持固定版本安装，按发行版选择版本组合；Python 检查与 Shell 安装白名单同步扩展，已有可用 Docker 继续沿用。

**Tech Stack:** Python 3.12、pytest、Bash、Docker 官方 apt 仓库、GitHub Actions。

**Spec:** 用户报告与 `docs/decisions/2026-11-16-docker-legacy-init.md`。

## Global Constraints

- 使用现有 /workspace/WorkspaceCore 隔离工作区与专属分支，不新建 worktree。
- 不连接或更改用户生产服务器，不修改数据库结构，不改 SSH 密码和主机身份校验。
- Debian 11: Docker 29.8.0、containerd 2.3.4-2、Compose 5.5.1；Ubuntu 20.04: Docker 28.1.1、containerd 1.7.27-1、Compose 2.35.1。
- Ubuntu 22.04 / 24.04、Debian 12 保留原有版本；新系统不自动选 latest。
- 官方签名公钥指纹校验与 apt-mark hold 保留。
- Debian 11 的官方 bullseye-security 旧入口迁移至官方归档，保留协议、签名配置和原文件备份，不改自定义镜像与其他套件。

## Review Focus

- root 且没有 sudo 的旧系统仍可初始化。
- amd64 / arm64 的版本包均存在。
- 只缺 Compose 时不重装 Docker。
- 已装 Docker 与 Compose 的其他系统仍可沿用；没有 Docker 的未知系统仍失败。
- CI 根据不同系统分别验证真实安装版本，不能只修改白名单。

### Task 1: 修复旧系统初始化并交付

**Files:**
- 修改 `services/api/app/domain/agent_ssh_install.py` 与 `agent_server_ssh.py` 的支持范围和提示。
- 修改 `services/api/app/agent_provisioning/files/install-docker.sh` 的按系统固定版本选择。
- 新建 `services/api/tests/test_docker_legacy_install.py`，覆盖页面初始化、连接检查、只缺 Compose 与已有 Docker。
- 修改 `.github/workflows/ci-infra.yml` 的 docker-pins 矩阵与按版本验证。
- 更新部署指南、版本核验 ADR 与 T-0266 任务日志。

**Interfaces:** `HostInfo.supported`、`AgentServerInstaller._docker` 与 `install-docker.sh <user>` 保持原签名。

- [x] 写回归测试：Debian 11 / Ubuntu 20.04、x86_64 / aarch64、root 无 sudo 初始化成功；未知系统失败；已有 Docker 不重装。
- [x] 运行测试，确认新支持系统在修复前以 unsupported_os 失败。
- [x] 安装脚本按系统选择固定版本，扩展两处系统检查和提示。
- [x] 合并前 CI 复现 Debian 11 安全源归档 404；补充 `.list` / deb822 的迁移回归与传统源、其他系统不受影响的测试，确认新测试在旧代码失败后再验证修复。
- [x] 运行回归与相关测试、ruff/mypy、真实干净容器安装；记录可运行与受环境限制部分。
- [x] 更新 CI 矩阵、部署说明与 ADR，执行协作校验，审查 diff，提交专属分支并创建 PR。
