#!/bin/sh
# 初始化的最后一步（T-0179）：以登录账号检查 Docker / Compose、平台受控的部署根目录、
# 能否连到工作台的外网接入地址。只读（只在部署根目录里建一个临时文件再删掉）。输出 key=value。
# 参数：$1 部署根目录；$2 接入地址的主机、$3 端口（工作台还没配置外网接入入口时不传）。
set -u
root="$1"
host="${2:-}"
port="${3:-}"
if docker info >/dev/null 2>&1; then echo "docker=yes"; else echo "docker=no"; fi
if docker compose version >/dev/null 2>&1; then echo "compose=yes"; else echo "compose=no"; fi
probe="$root/.myworkspace-check-$$"
if [ -d "$root" ] && (umask 077 && : >"$probe") 2>/dev/null; then
  rm -f "$probe"
  echo "workspace=yes"
else
  echo "workspace=no"
fi
# 只建立一次 TCP 连接（10 秒超时），不发送任何数据
if [ -z "$host" ]; then
  echo "ingress=skipped"
elif command -v timeout >/dev/null 2>&1 && command -v bash >/dev/null 2>&1; then
  # shellcheck disable=SC2016
  if timeout 10 bash -c 'exec 3<>"/dev/tcp/$1/$2"' ingress-check "$host" "$port" >/dev/null 2>&1; then
    echo "ingress=yes"
  else
    echo "ingress=no"
  fi
elif command -v nc >/dev/null 2>&1; then
  if nc -z -w 10 "$host" "$port" >/dev/null 2>&1; then echo "ingress=yes"; else echo "ingress=no"; fi
else
  echo "ingress=unknown"
fi
