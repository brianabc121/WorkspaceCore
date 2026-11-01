#!/bin/sh
# Garage 初始化（V2 OC-3）：compose 的 garage-init 一次性服务执行（alpine，busybox wget 调 Garage 管理 API v2）。
#
# 幂等，每次启动都可以重跑：
#   1. 集群布局还是 0 版时，把本节点加入布局并应用（单节点；capacity 不是配额，只用于多节点间分配）；
#   2. 访问密钥不存在就导入（OPENIM_S3_ACCESS_KEY / OPENIM_S3_SECRET_KEY）；存在但密钥不一致就报错退出——
#      Garage 不允许复用已删除的密钥 ID，轮换时要换一个新的 GK… ID（docs/deployment.md / OC-3 ADR）；
#   3. 桶 openim 不存在就创建；给这个密钥读、写、owner 权限（重复授权不报错）。
# Garage 镜像只有一个静态程序，没有 shell，所以不在 garage 容器里执行。
set -eu

api="${GARAGE_ADMIN_ENDPOINT:-http://garage:3903}/v2"
token="${OPENIM_GARAGE_ADMIN_TOKEN:?}"
kid="${OPENIM_S3_ACCESS_KEY:?}"
ksec="${OPENIM_S3_SECRET_KEY:?}"
bucket="${OPENIM_S3_BUCKET:-openim}"
case "$kid" in GK*) ;; *) echo "OPENIM_S3_ACCESS_KEY 要以 GK 开头（GK + 24 位十六进制）" >&2; exit 1 ;; esac
case "${kid#GK}" in *[!0-9a-f]*|'') echo "OPENIM_S3_ACCESS_KEY 格式不对" >&2; exit 1 ;; esac
[ "${#kid}" -eq 26 ] || { echo "OPENIM_S3_ACCESS_KEY 要 26 个字符" >&2; exit 1; }
case "$ksec" in *[!0-9a-f]*|'') echo "OPENIM_S3_SECRET_KEY 要是 64 位十六进制" >&2; exit 1 ;; esac
[ "${#ksec}" -eq 64 ] || { echo "OPENIM_S3_SECRET_KEY 要是 64 位十六进制" >&2; exit 1; }

# get <路径> / post <路径> <JSON>：成功时输出响应体；失败时输出 wget 的错误行（含 HTTP 状态码），返回非 0
get() { wget -qO- -T 10 --header "Authorization: Bearer $token" "$api/$1" 2>&1; }
post() {
  wget -qO- -T 10 --header "Authorization: Bearer $token" --header "Content-Type: application/json" \
    --post-data "$2" "$api/$1" 2>&1
}
first() { sed -n "s/.*\"$1\": *\"\{0,1\}\([^\",]*\)\"\{0,1\},\{0,1\}$/\1/p" | head -1; }

i=0
until status="$(get GetClusterStatus)"; do
  i=$((i + 1)); [ "$i" -ge 60 ] && { echo "Garage 管理 API 没有就绪：$status" >&2; exit 1; }
  sleep 2
done

version="$(get GetClusterLayout | first version)"
if [ "$version" = "0" ]; then
  node="$(printf '%s\n' "$status" | first id)"
  post UpdateClusterLayout "{\"roles\":[{\"id\":\"$node\",\"zone\":\"dc1\",\"capacity\":107374182400,\"tags\":[]}]}" >/dev/null
  post ApplyClusterLayout '{"version":1}' >/dev/null
  echo "已应用集群布局（单节点 $node）"
fi

if key="$(get "GetKeyInfo?id=$kid&showSecretKey=true")"; then
  [ "$(printf '%s\n' "$key" | first secretAccessKey)" = "$ksec" ] \
    || { echo "访问密钥 $kid 已存在但密钥不一致；轮换请换一个新的密钥 ID" >&2; exit 1; }
else
  case "$key" in *404*) ;; *) echo "查询访问密钥失败：$key" >&2; exit 1 ;; esac
  post ImportKey "{\"accessKeyId\":\"$kid\",\"secretAccessKey\":\"$ksec\",\"name\":\"openim\"}" >/dev/null
  echo "已导入访问密钥 $kid"
fi

if ! info="$(get "GetBucketInfo?globalAlias=$bucket")"; then
  case "$info" in *404*) ;; *) echo "查询桶失败：$info" >&2; exit 1 ;; esac
  info="$(post CreateBucket "{\"globalAlias\":\"$bucket\"}")"
  echo "已创建桶 $bucket"
fi
bucket_id="$(printf '%s\n' "$info" | first id)"
post AllowBucketKey "{\"bucketId\":\"$bucket_id\",\"accessKeyId\":\"$kid\",\"permissions\":{\"read\":true,\"write\":true,\"owner\":true}}" >/dev/null
echo "Garage 就绪：桶 $bucket，密钥 $kid 有读写权限"
