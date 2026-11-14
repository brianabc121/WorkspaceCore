#!/bin/bash
# 运行服务器的安装（T-0136）：以 root 在接入包目录里运行。工作台的自动部署（T-0140）也执行这个脚本。
# 1. 锁定版本的 Docker（install-docker.sh，与 OpenClaw 服务器同一份）；
# 2. gVisor release-20261019.0（install-gvisor.sh，核对 sha512）。让 Docker 认得 runsc 时先重新加载、尽量不重启；
#    非重启不可、而服务器上有运行中的 MCP 管理器时以退出码 6 停下（gvisor_needs_docker_restart），
#    确认后带环境变量 MW_ALLOW_DOCKER_RESTART=1 再运行（T-0233）；
# 3. 有 /dev/kvm 就在 runner.env 里写 MYWS_RUNNER_KVM=1；
# 4. 启动 runner-agent（compose.yaml，锁定 digest 的 python 官方镜像）。
set -euo pipefail
exec </dev/null
cd "$(dirname "$0")"
if [ "$(id -u)" != 0 ]; then
  echo "need_root" >&2
  exit 2
fi
bash ./install-docker.sh ""
bash ./install-gvisor.sh
sed -i '/^MYWS_RUNNER_KVM=/d' runner.env
if [ -c /dev/kvm ]; then
  echo "MYWS_RUNNER_KVM=1" >>runner.env
fi
chmod 600 runner.env
[ -f tls/client.key ] && chmod 600 tls/client.key
docker compose -f compose.yaml up -d --remove-orphans
echo "runner_agent_started"
