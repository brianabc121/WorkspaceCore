#!/bin/sh
# 只读检查（T-0089）：发行版、架构、账号、Docker / Compose / sudo / crontab 是否可用。输出 key=value。
set -u
os_id="" os_version="" os_codename=""
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  os_id="${ID:-}" os_version="${VERSION_ID:-}" os_codename="${VERSION_CODENAME:-}"
fi
echo "os_id=$os_id"
echo "os_version=$os_version"
echo "os_codename=$os_codename"
echo "arch=$(uname -m)"
echo "uid=$(id -u)"
echo "gid=$(id -g)"
echo "user=$(id -un)"
echo "home=$HOME"
if command -v docker >/dev/null 2>&1; then echo "docker=yes"; else echo "docker=no"; fi
if docker info >/dev/null 2>&1; then echo "docker_access=yes"; else echo "docker_access=no"; fi
if docker compose version >/dev/null 2>&1; then echo "compose=yes"; else echo "compose=no"; fi
if command -v sudo >/dev/null 2>&1; then echo "sudo=yes"; else echo "sudo=no"; fi
if command -v crontab >/dev/null 2>&1; then echo "crontab=yes"; else echo "crontab=no"; fi
