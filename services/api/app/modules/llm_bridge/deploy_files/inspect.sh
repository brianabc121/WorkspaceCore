#!/bin/sh
# 一键部署 MCP 管理器前的只读检查（T-0233，docs/v2/LLM_BRIDGE_PLAN.md 第 20 节）。不改任何东西。
# 参数：$1 工作台后端容器的主机名（不知道就传 -）；$2 不要选的端口（空格分隔，分给了别的管理器的）。
# 输出 key=value。
set -u
want="${1:--}"
skip=" ${2:-} "
echo "home=$HOME"
echo "uid=$(id -u)"
echo "arch=$(uname -m)"
if docker info >/dev/null 2>&1; then echo "docker_access=yes"; else echo "docker_access=no"; fi
if docker compose version >/dev/null 2>&1; then echo "compose=yes"; else echo "compose=no"; fi

# 和工作台是不是同一台机器：这台机器上有没有主机名等于工作台后端容器的容器。
# 有的话取它所在的 integrations 网络：管理器加入这个内部网络，端口不用开到宿主机上。
network=""
if [ "$want" != "-" ]; then
  for id in $(docker ps -q 2>/dev/null); do
    name="$(docker inspect -f '{{.Config.Hostname}}' "$id" 2>/dev/null)"
    if [ "$name" = "$want" ]; then
      network="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{println $k}}{{end}}' "$id" 2>/dev/null | grep '_integrations$' | head -n 1)"
      break
    fi
  done
fi
echo "network=$network"

# 不同机器时要开一个端口：18787 起第一个没人监听、也没分给别的管理器的
listening=" $( (ss -ltnH 2>/dev/null || netstat -ltn 2>/dev/null) | awk '{print $4}' | sed 's/.*[:.]//' | tr '\n' ' ') "
port=""
p=18787
while [ "$p" -le 18886 ]; do
  case "$skip" in
    *" $p "*) ;;
    *) case "$listening" in *" $p "*) ;; *) port="$p"; break ;; esac ;;
  esac
  p=$((p + 1))
done
echo "port=$port"
