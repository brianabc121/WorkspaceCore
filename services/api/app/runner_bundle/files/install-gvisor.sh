#!/bin/bash
# 以 root 运行（T-0136）：按 gVisor 官方文档安装锁定版本 release-20261019.0（核对 sha512），
# 再 `runsc install` 接入 Docker。来源与核验见 docs/decisions/2026-11-06-runner-verification.md 第 3 节。
# 只支持 x86_64（arm64 的发布包还没有核对校验值）：其他架构跳过，环境只能用 runc。
#
# 让 Docker 认得 runsc 时尽量不重启 Docker（T-0233，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 8 节）：
# 先重新加载配置；仍然没有登记、而服务器上有运行中的 MCP 管理器时不擅自重启，
# 输出 gvisor_needs_docker_restart <数量> 并以退出码 6 结束，由管理员在页面上确认后
# 带 MW_ALLOW_DOCKER_RESTART=1 再运行。没有 MCP 管理器时照旧重启。
set -euo pipefail
exec </dev/null
export DEBIAN_FRONTEND=noninteractive
release="20261019.0"
sha512_expected="bcbf431b2f9a039f6ab23bfe23986031130c0f149294978cedfd46910901deba0737d8642c848f597ed4b5c72324885db4d75ee86a1f6a90915fbe1f2f2eb915"
if [ "$(uname -m)" != x86_64 ]; then
  echo "gvisor_skipped_arch $(uname -m)"
  exit 0
fi
if command -v runsc >/dev/null 2>&1 && runsc --version 2>/dev/null | grep -q "release-${release}"; then
  runsc_bin="$(command -v runsc)"
  echo "gvisor_present ${release}"
else
  runsc_bin=/usr/local/bin/runsc
  command -v zstd >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq --no-install-recommends zstd >/dev/null; }
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL --proto '=https' --tlsv1.2 --max-time 300 \
    "https://storage.googleapis.com/gvisor/releases/release/${release}/x86_64/gvisor.tar.zstd" -o "$tmp/gvisor.tar.zstd"
  actual="$(sha512sum "$tmp/gvisor.tar.zstd" | awk '{print $1}')"
  if [ "$actual" != "$sha512_expected" ]; then
    echo "bad_sha512 ${actual}" >&2
    exit 4
  fi
  tar --zstd -xf "$tmp/gvisor.tar.zstd" -C /usr/local/bin
fi
# 重装（SSH 再部署一次、升级接入包）时 Docker 已经认得 runsc：不用再接入，也不动 Docker，
# 以免停掉这台服务器上正在运行的环境（runsc 每次建容器时才调用，换了二进制不用重启）
registered() {
  local runtimes
  runtimes="$(docker info --format '{{json .Runtimes}}' 2>/dev/null || true)"
  case "$runtimes" in
    *'"runsc"'*) return 0 ;;
  esac
  return 1
}
if registered; then
  echo "gvisor_ready ${release}"
  exit 0
fi
"$runsc_bin" install
systemd=0
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  systemd=1
fi
# 先重新加载：dockerd 收到 SIGHUP 会重新读 daemon.json 里的 runtimes，新运行时登记上、
# 已有的容器不重启（实测 Docker 29.9.2）。systemd 下 reload 发的就是 SIGHUP
if [ "$systemd" = 1 ] && systemctl reload docker >/dev/null 2>&1; then
  echo "docker_reloaded"
elif pkill -HUP -x dockerd >/dev/null 2>&1; then
  echo "docker_reloaded"
fi
for _ in $(seq 1 5); do
  if registered; then
    echo "gvisor_ready ${release}"
    exit 0
  fi
  sleep 2
done
# 重新加载后仍然没有登记：只剩重启 Docker。重启会让这台服务器上所有容器中断一下——
# 有运行中的 MCP 管理器（带 myworkspace.mcp-manager 标签，工作台另行部署的）时要管理员先确认
managers="$(docker ps -q --filter label=myworkspace.mcp-manager 2>/dev/null | grep -c . || true)"
if [ "${managers:-0}" -gt 0 ]; then
  if [ "${MW_ALLOW_DOCKER_RESTART:-}" != 1 ]; then
    echo "gvisor_needs_docker_restart ${managers}" >&2
    exit 6
  fi
  echo "docker_restart_allowed ${managers}"
fi
if [ "$systemd" = 1 ]; then
  systemctl restart docker
else
  service docker restart >/dev/null 2>&1 || true
fi
for _ in $(seq 1 30); do
  if registered; then
    echo "gvisor_ready ${release}"
    exit 0
  fi
  sleep 2
done
echo "gvisor_not_registered" >&2
exit 5
