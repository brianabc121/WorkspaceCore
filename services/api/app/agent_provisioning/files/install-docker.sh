#!/bin/bash
# 以 root 运行（T-0089）：从 Docker 官方软件源安装锁定版本（核对签名公钥指纹），并把登录账号加入 docker 组。
# 版本与指纹在 2026-11-03 对照官方软件源的 Packages 索引核验，见
# docs/decisions/2026-11-03-openclaw-ssh-provisioning.md 第 5.3 节；旧系统版本见
# docs/decisions/2026-11-16-docker-legacy-init.md。参数：要加入 docker 组的账号（root 时留空）。
set -euo pipefail
exec </dev/null
export DEBIAN_FRONTEND=noninteractive
user="${1:-}"
fpr_expected="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
# shellcheck disable=SC1091
. /etc/os-release
suffix="${ID:-}.${VERSION_ID:-}~${VERSION_CODENAME:-}"
docker_version="5:29.9.1-1~${suffix}"
containerd_version="2.4.2-2~${suffix}"
compose_version="5.6.0-1~${suffix}"
# 旧系统仓库没有新系统的锁定版本；分别选择核验过的版本，不能只放开发行版检查。
case "${ID:-}:${VERSION_ID:-}" in
  debian:11)
    docker_version="5:29.8.0-1~${suffix}"
    containerd_version="2.3.4-2~${suffix}"
    compose_version="5.5.1-1~${suffix}"
    ;;
  ubuntu:20.04)
    docker_version="5:28.1.1-1~${suffix}"
    containerd_version="1.7.27-1"
    compose_version="2.35.1-1~${suffix}"
    ;;
esac
packages=()
if ! command -v docker >/dev/null 2>&1; then
  packages+=("docker-ce=${docker_version}" "docker-ce-cli=${docker_version}" "containerd.io=${containerd_version}")
fi
if ! docker compose version >/dev/null 2>&1; then
  packages+=("docker-compose-plugin=${compose_version}")
fi
if [ "${#packages[@]}" -gt 0 ]; then
  # 只在要装软件包时检查发行版：已经有 Docker 的服务器只需要启动服务、加入 docker 组
  case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:20.04 | ubuntu:22.04 | ubuntu:24.04 | debian:11 | debian:12) ;;
    *) echo "unsupported_os ${ID:-} ${VERSION_ID:-}" >&2; exit 3 ;;
  esac
  arch="$(dpkg --print-architecture)"
  case "$arch" in
    amd64 | arm64) ;;
    *) echo "unsupported_arch ${arch}" >&2; exit 3 ;;
  esac
  # Debian 11 的安全更新包已迁到官方归档；旧入口仍能返回索引，但下载包会 404。
  # 只迁移 bullseye-security 的两个官方入口，保留自定义镜像、其他套件与签名检查。
  if [ "${ID}:${VERSION_ID}" = debian:11 ]; then
    for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
      [ -f "$source" ] || continue
      migrated="$(mktemp)"
      if [[ "$source" == *.sources ]]; then
        awk 'BEGIN { RS=""; ORS="\n\n" }
          /(^|\n)Suites:[ \t]*bullseye-security[ \t]*(\n|$)/ {
            gsub(/\/\/(deb|security)\.debian\.org\/debian-security/, "//archive.debian.org/debian-security")
          }
          { print }' "$source" >"$migrated"
      else
        sed -E 's#^([[:space:]]*deb(-src)?[[:space:]]+(\[[^]]*\][[:space:]]+)?https?://)(deb|security)\.debian\.org/debian-security(/?[[:space:]]+bullseye-security([[:space:]]|$))#\1archive.debian.org/debian-security\5#' \
          "$source" >"$migrated"
      fi
      if ! cmp -s "$source" "$migrated"; then
        [ -e "${source}.myworkspace-before-archive" ] || cp -p "$source" "${source}.myworkspace-before-archive"
        cat "$migrated" >"$source"
        echo "Debian 11 安全更新源已迁移到官方 archive.debian.org（保留原文件备份）"
      fi
      rm -f "$migrated"
    done
  fi
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
