#!/bin/bash
# 以 root 运行（T-0136）：按 gVisor 官方文档安装锁定版本 release-20261019.0（核对 sha512），
# 再 `runsc install` 接入 Docker 并重启 Docker。来源与核验见 docs/decisions/2026-11-06-runner-verification.md 第 3 节。
# 只支持 x86_64（arm64 的发布包还没有核对校验值）：其他架构跳过，环境只能用 runc。
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
  echo "gvisor_present ${release}"
else
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
# 重装（SSH 再部署一次、升级接入包）时 Docker 已经认得 runsc：不再重启 Docker，
# 以免停掉这台服务器上正在运行的环境（runsc 每次建容器时才调用，换了二进制不用重启）
if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"runsc"'; then
  echo "gvisor_ready ${release}"
  exit 0
fi
/usr/local/bin/runsc install
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  systemctl restart docker
else
  service docker restart >/dev/null 2>&1 || true
fi
for _ in $(seq 1 30); do
  docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q runsc && { echo "gvisor_ready ${release}"; exit 0; }
  sleep 2
done
echo "gvisor_not_registered" >&2
exit 5
