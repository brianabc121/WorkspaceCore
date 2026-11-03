#!/bin/bash
# 以 root 运行（T-0089）：从 Docker 官方软件源安装锁定版本（核对签名公钥指纹），并把登录账号加入 docker 组。
# 版本与指纹在 2026-11-03 对照官方软件源的 Packages 索引核验，见
# docs/decisions/2026-11-03-openclaw-ssh-provisioning.md 第 5.3 节。参数：要加入 docker 组的账号（root 时留空）。
set -euo pipefail
exec </dev/null
export DEBIAN_FRONTEND=noninteractive
user="${1:-}"
fpr_expected="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
# shellcheck disable=SC1091
. /etc/os-release
suffix="${ID:-}.${VERSION_ID:-}~${VERSION_CODENAME:-}"
packages=()
if ! command -v docker >/dev/null 2>&1; then
  packages+=("docker-ce=5:29.9.1-1~${suffix}" "docker-ce-cli=5:29.9.1-1~${suffix}" "containerd.io=2.4.2-2~${suffix}")
fi
if ! docker compose version >/dev/null 2>&1; then
  packages+=("docker-compose-plugin=5.6.0-1~${suffix}")
fi
if [ "${#packages[@]}" -gt 0 ]; then
  # 只在要装软件包时检查发行版：已经有 Docker 的服务器只需要启动服务、加入 docker 组
  case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:22.04 | ubuntu:24.04 | debian:12) ;;
    *) echo "unsupported_os ${ID:-} ${VERSION_ID:-}" >&2; exit 3 ;;
  esac
  arch="$(dpkg --print-architecture)"
  case "$arch" in
    amd64 | arm64) ;;
    *) echo "unsupported_arch ${arch}" >&2; exit 3 ;;
  esac
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  tmp="$(mktemp)"
  curl -fsSL --proto '=https' --tlsv1.2 --max-time 60 "https://download.docker.com/linux/${ID}/gpg" -o "$tmp"
  fpr="$(gpg --show-keys --with-colons "$tmp" 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')"
  if [ "$fpr" != "$fpr_expected" ]; then
    rm -f "$tmp"
    echo "bad_key_fingerprint ${fpr}" >&2
    exit 4
  fi
  install -m 0644 "$tmp" /etc/apt/keyrings/docker.asc
  rm -f "$tmp"
  echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    >/etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends "${packages[@]}"
  apt-mark hold docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null 2>&1 || true
fi
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  systemctl enable --now docker >/dev/null
else
  service docker start >/dev/null 2>&1 || true
fi
if [ -n "$user" ] && [ "$user" != root ]; then
  getent group docker >/dev/null || groupadd docker
  usermod -aG docker "$user"
fi
# 没有 systemd 时 service 立即返回：最多等 60 秒，直到守护进程能响应
for _ in $(seq 1 30); do
  if docker version --format '{{.Server.Version}}' 2>/dev/null; then exit 0; fi
  sleep 2
done
echo "docker_not_running" >&2
exit 5
