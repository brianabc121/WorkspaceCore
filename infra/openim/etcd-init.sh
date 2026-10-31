#!/bin/sh
# etcd 开启认证（V2 OC-3）：compose 的 etcd-init 一次性服务执行（alpine，busybox wget 调 etcd 的 JSON 网关）。
#
# - 第一次：建 root（角色 root）与 openim 两个用户，然后开启认证；
# - 以后每次启动：用 root 登录，把 openim 的密码改成当前环境变量里的值（支持轮换），已经开启的认证不动；
# - openim 只能读写：openim 前缀（服务注册、配置，rootDirectory: openim）与 /check_openim_component
#   （启动时 check-component 检查 etcd 用的键，openimsdk/tools discovery/etcd/check.go）。每次启动都重新授权（幂等）；
# - etcd 官方镜像是 distroless，没有 shell，所以不在 etcd 容器里执行。
# 环境变量：ETCD_ENDPOINT（默认 http://etcd:2379）、OPENIM_ETCD_ROOT_PASSWORD、OPENIM_ETCD_PASSWORD。
# 密码只接受字母和数字（make env / openssl rand -hex 生成），这样可以直接放进 JSON。
set -eu

api="${ETCD_ENDPOINT:-http://etcd:2379}/v3"
root_pw="${OPENIM_ETCD_ROOT_PASSWORD:?}"
user_pw="${OPENIM_ETCD_PASSWORD:?}"
for pw in "$root_pw" "$user_pw"; do
  case "$pw" in *[!A-Za-z0-9]*|'') echo "etcd 密码只能包含字母和数字" >&2; exit 1 ;; esac
done

# post <路径> <JSON> [令牌]：失败时返回非 0，不打印请求体（里面有密码）
post() {
  if [ -n "${3:-}" ]; then
    wget -qO- -T 10 --header "Content-Type: application/json" --header "Authorization: $3" --post-data "$2" "$api/$1"
  else
    wget -qO- -T 10 --header "Content-Type: application/json" --post-data "$2" "$api/$1"
  fi
}
token_of() {
  post auth/authenticate "{\"name\":\"$1\",\"password\":\"$2\"}" 2>/dev/null \
    | sed -n 's/.*"token":"\([^"]*\)".*/\1/p'
}

i=0
until wget -qO- -T 5 "${ETCD_ENDPOINT:-http://etcd:2379}/health" >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -ge 60 ] && { echo "etcd 没有就绪" >&2; exit 1; }
  sleep 2
done

# grant <令牌或空>：给 openim 角色授权（重复授权不报错）
grant() {
  prefix="$(printf openim | base64)"
  prefix_end="$(printf openin | base64)"   # 前缀 openim 的区间终点：最后一个字符加一
  check_key="$(printf /check_openim_component | base64)"
  post auth/role/grant "{\"name\":\"openim\",\"perm\":{\"permType\":\"READWRITE\",\"key\":\"$prefix\",\"range_end\":\"$prefix_end\"}}" "$1" >/dev/null
  post auth/role/grant "{\"name\":\"openim\",\"perm\":{\"permType\":\"READWRITE\",\"key\":\"$check_key\"}}" "$1" >/dev/null
}

root_token="$(token_of root "$root_pw" || true)"
if [ -n "$root_token" ]; then
  # 已经开启认证：同步授权与 openim 的密码
  grant "$root_token"
  post auth/user/changepw "{\"name\":\"openim\",\"password\":\"$user_pw\"}" "$root_token" >/dev/null
  echo "etcd 认证已开启；已同步 openim 用户的授权与密码"
  exit 0
fi

# 没有开启认证时，不带凭证也能调用 auth/status
post auth/status '{}' >/dev/null 2>&1 || { echo "etcd 已开启认证，但 root 密码不对" >&2; exit 1; }

post auth/user/add "{\"name\":\"root\",\"password\":\"$root_pw\"}" >/dev/null
post auth/user/grant "{\"user\":\"root\",\"role\":\"root\"}" >/dev/null
post auth/role/add '{"name":"openim"}' >/dev/null
grant ""
post auth/user/add "{\"name\":\"openim\",\"password\":\"$user_pw\"}" >/dev/null
post auth/user/grant '{"user":"openim","role":"openim"}' >/dev/null
post auth/enable '{}' >/dev/null
echo "etcd 已开启认证：root（管理）与 openim（只能读写 openim 前缀与 /check_openim_component）"
