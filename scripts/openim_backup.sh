#!/usr/bin/env bash
# OpenIM 的备份与恢复检查（V2 OC-3，T-0055；方案第 6.1 节“备份覆盖 OpenIM”）。
# backup.sh、restore-verify.sh 用 source 引入下面的函数。单独运行：
#   scripts/openim_backup.sh check [项目名]            导出一次并在隔离的临时容器里恢复检查（CI 的 ci-openim 用）
#   scripts/openim_backup.sh restore <归档目录> [项目名]  真实恢复：把 restic 取出的归档写进一个还没有数据卷的 OpenIM 项目
# 项目名是 OpenIM 的 Compose 项目，默认 <COMPOSE_PROJECT_NAME 或 my-workspace>-openim（make openim-up 用的名字）。
#
# 备份什么（OC-3 ADR 第 17 节）：
#   - MongoDB 的 openim_v3 库：mongodump --archive --gzip（OpenIM 的用户、好友、群、会话、消息、序号都在这里）；
#   - Garage（图片等对象）：先 garage meta snapshot 得到一致的元数据快照，再打包快照、节点身份与数据块；
#   - 不备份 Kafka（只是中转）、Redis（缓存与在线令牌）、etcd（服务发现），恢复后会自己重建。
# 恢复检查：在不联网的临时容器里分别恢复两份归档，核对文档数与对象数，与备份时记下的一致。
set -euo pipefail

OPENIM_ALPINE_IMAGE="alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6"
# 核对数量的集合：用户、好友、群、群成员、会话、消息（每个文档存一段消息）、序号
OPENIM_COLLECTIONS="user friend group group_member conversation msg seq seq_user"

openim_root() { cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd; }

# 从 infra/openim/compose.yaml 取锁定的镜像（与部署用的完全一致）
openim_image() {
  sed -n "s|^    image: \($1:.*\)$|\1|p" "$(openim_root)/infra/openim/compose.yaml" | head -n 1
}

# 某个服务正在运行的容器 ID（按 Compose 标签找，不需要 .env 也不需要 compose 文件里的变量）
openim_container() {
  docker ps -q --filter "label=com.docker.compose.project=$1" --filter "label=com.docker.compose.service=$2" | head -n 1
}

openim_running() {
  [[ -n "$(openim_container "$1" mongo)" && -n "$(openim_container "$1" garage)" ]]
}

# 数量统计：mongosh 在 mongo 容器里执行，输出 “集合 数量” 每行一个
openim_mongo_counts_js() {
  local js="" c
  for c in $OPENIM_COLLECTIONS; do js+="print('$c', db.getCollection('$c').countDocuments());"; done
  printf '%s' "$js"
}

openim_mongo_auth_js() {
  printf '%s' "db = new Mongo('mongodb://openim:' + encodeURIComponent(process.env.OPENIM_MONGO_PASSWORD) +
    '@127.0.0.1:27017/openim_v3?authSource=openim_v3').getDB('openim_v3');"
}

# openim_dump <OpenIM 项目名> <输出目录>：写出 openim-mongo.archive.gz、openim-garage.tar、openim-info.json
openim_dump() {
  local project="$1" out="$2" mongo garage volume objects counts
  mongo="$(openim_container "$project" mongo)"
  garage="$(openim_container "$project" garage)"
  [[ -n "$mongo" && -n "$garage" ]] || { echo "OpenIM（项目 $project）没有在运行" >&2; return 1; }

  echo "备份 OpenIM 的 MongoDB（openim_v3，mongodump 归档）"
  # 密码来自 mongo 容器自己的环境变量（Compose 给 mongo-init 用的 OPENIM_MONGO_PASSWORD），
  # 写进容器里权限 600 的临时配置文件，不出现在命令行
  # shellcheck disable=SC2016  # $OPENIM_MONGO_PASSWORD 要在容器里展开
  docker exec "$mongo" sh -c 'umask 077; f=$(mktemp); printf "password: \"%s\"\n" "$OPENIM_MONGO_PASSWORD" > "$f";
    mongodump --quiet --config="$f" --username=openim --authenticationDatabase=openim_v3 --db=openim_v3 --archive --gzip;
    rc=$?; rm -f "$f"; exit $rc' > "$out/openim-mongo.archive.gz"
  gzip -t "$out/openim-mongo.archive.gz"
  # 统计用 mongosh 在容器里读同一个环境变量拼连接串，密码同样不出现在命令行
  counts="$(docker exec "$mongo" mongosh --quiet --nodb --eval "$(openim_mongo_auth_js) $(openim_mongo_counts_js)")"

  echo "备份 OpenIM 的对象存储（Garage：元数据快照 + 数据块）"
  # garage 命令行把连接日志写到 stderr，这里丢掉；失败时退出码仍然生效
  docker exec "$garage" /garage meta snapshot >/dev/null 2>&1
  objects="$(docker exec "$garage" /garage bucket info openim 2>/dev/null | sed -n 's/^ *Objects: *\([0-9]*\).*/\1/p' | head -n 1)"
  volume="$(docker inspect "$garage" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/garage"}}{{.Name}}{{end}}{{end}}')"
  [[ -n "$volume" ]] || { echo "找不到 Garage 的数据卷" >&2; return 1; }
  # 只读挂载数据卷：元数据目录里除了正在使用的数据库与快照目录，其余（节点密钥、集群布局等）原样打包；
  # 数据库换成刚做的快照；数据块目录整个打包。用符号链接加 tar -h 一次输出，不在容器里复制数据
  # shellcheck disable=SC2016
  docker run --rm --network none -v "$volume:/g:ro" "$OPENIM_ALPINE_IMAGE" sh -euc '
    snapshot=$(ls -1 /g/meta/snapshots | sort | tail -n 1)
    [ -n "$snapshot" ] && [ -f "/g/meta/snapshots/$snapshot/db.sqlite" ] || { echo "没有元数据快照" >&2; exit 1; }
    mkdir -p /tmp/a/meta
    for f in /g/meta/*; do
      case "${f##*/}" in db.sqlite|db.sqlite-wal|db.sqlite-shm|snapshots) ;; *) ln -s "$f" "/tmp/a/meta/${f##*/}" ;; esac
    done
    ln -s "/g/meta/snapshots/$snapshot/db.sqlite" /tmp/a/meta/db.sqlite
    ln -s /g/data /tmp/a/data
    tar -chf - -C /tmp/a meta data' > "$out/openim-garage.tar"
  tar -tf "$out/openim-garage.tar" >/dev/null

  python3 - "$out/openim-info.json" "${objects:-0}" "$counts" <<'EOF'
import json, sys
path, objects, counts = sys.argv[1], int(sys.argv[2]), sys.argv[3]
collections = {k: int(v) for k, v in (line.split() for line in counts.splitlines() if line.strip())}
json.dump({"mongo_counts": collections, "garage_objects": objects}, open(path, "w"))
EOF
  echo "OpenIM 归档完整：MongoDB $(tr -d '\n' < "$out/openim-info.json")"
}

# openim_verify <归档目录>：在不联网的临时容器里恢复两份归档，与 openim-info.json 核对
openim_verify() {
  local dir="$1" tag mongo_image garage_image mongo_c="" garage_c="" volume="" restored objects
  [[ -s "$dir/openim-mongo.archive.gz" && -s "$dir/openim-garage.tar" && -s "$dir/openim-info.json" ]] || {
    echo "快照里没有完整的 OpenIM 归档" >&2; return 1; }
  tag="openim-verify-$$"
  mongo_image="$(openim_image mongo)"
  garage_image="$(openim_image dxflrs/garage)"
  # 函数返回时清理临时容器与数据卷（RETURN 陷阱不是函数局部的，所以清理后顺手撤掉）
  # shellcheck disable=SC2064  # 按当时的名字清理
  trap "docker rm -f $tag-mongo $tag-garage >/dev/null 2>&1 || true; docker volume rm -f $tag-garage >/dev/null 2>&1 || true; trap - RETURN" RETURN

  echo "恢复检查：MongoDB（临时容器，不联网）"
  mongo_c="$(docker run -d --name "$tag-mongo" --network none "$mongo_image" mongod --bind_ip 127.0.0.1)"
  for _ in $(seq 1 30); do docker exec "$mongo_c" mongosh --quiet --eval 'db.adminCommand({ping:1}).ok' >/dev/null 2>&1 && break; sleep 1; done
  docker exec -i "$mongo_c" mongorestore --quiet --archive --gzip --nsInclude='openim_v3.*' < "$dir/openim-mongo.archive.gz"
  restored="$(docker exec "$mongo_c" mongosh --quiet openim_v3 --eval "$(openim_mongo_counts_js)")"

  echo "恢复检查：Garage（临时容器，不联网）"
  volume="$(docker volume create "$tag-garage")"
  docker run --rm -i --network none -v "$volume:/g" "$OPENIM_ALPINE_IMAGE" tar -xf - -C /g < "$dir/openim-garage.tar"
  # RPC 密钥与管理令牌只用于这个临时节点：节点身份来自归档里的 node_key，与 RPC 密钥无关
  garage_c="$(docker run -d --name "$tag-garage" --network none -v "$volume:/var/lib/garage" \
    -v "$(openim_root)/infra/openim/garage.toml:/etc/garage.toml:ro" \
    -e GARAGE_RPC_SECRET="$(openssl rand -hex 32)" -e GARAGE_ADMIN_TOKEN="$(openssl rand -hex 16)" "$garage_image")"
  objects=""
  for _ in $(seq 1 30); do
    objects="$(docker exec "$garage_c" /garage bucket info openim 2>/dev/null | sed -n 's/^ *Objects: *\([0-9]*\).*/\1/p' | head -n 1)" || true
    [[ -n "$objects" ]] && break
    sleep 1
  done

  python3 - "$dir/openim-info.json" "$restored" "${objects:-missing}" <<'EOF'
import json, sys
info = json.load(open(sys.argv[1]))
restored = {k: int(v) for k, v in (line.split() for line in sys.argv[2].splitlines() if line.strip())}
objects = sys.argv[3]
problems = [f"{k}: 备份时 {v}，恢复后 {restored.get(k)}" for k, v in info["mongo_counts"].items() if restored.get(k) != v]
if objects != str(info["garage_objects"]):
    problems.append(f"Garage 对象数：备份时 {info['garage_objects']}，恢复后 {objects}")
if problems:
    print("OpenIM 恢复检查不一致：" + "；".join(problems), file=sys.stderr)
    sys.exit(1)
summary = "，".join(f"{k} {v}" for k, v in restored.items())
print(f"OpenIM 恢复检查通过：MongoDB {summary}；Garage 对象 {objects}")
EOF
}

# openim_restore <归档目录> <OpenIM 项目名>：在仓库根目录、用部署时的 .env 执行（docs/deployment.md 第 17.5 节）。
# 只写入全新的数据卷：项目还有容器或数据卷时拒绝，绝不覆盖现有数据。
openim_restore() {
  local dir="$1" project="$2" root mongo volume restored
  root="$(openim_root)"
  [[ -s "$dir/openim-mongo.archive.gz" && -s "$dir/openim-garage.tar" ]] || { echo "$dir 里没有 OpenIM 归档" >&2; return 1; }
  if [[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$project")" ]]; then
    echo "项目 $project 还有容器：先 make openim-down" >&2
    return 1
  fi
  for volume in openim-mongo openim-garage; do
    if docker volume inspect "${project}_$volume" >/dev/null 2>&1; then
      echo "数据卷 ${project}_$volume 已存在：恢复只写入全新的数据卷，不覆盖现有数据（确认不再需要后自行删除）" >&2
      return 1
    fi
  done
  local compose=(env "WORKSPACE_PROJECT=${WORKSPACE_PROJECT:-${project%-openim}}" docker compose -p "$project"
    -f "$root/infra/openim/compose.yaml" --env-file "$root/.env")

  echo "创建 $project 的 mongo、garage（不启动），写入 Garage 归档"
  "${compose[@]}" create garage mongo >/dev/null
  docker run --rm -i --network none -v "${project}_openim-garage:/g" "$OPENIM_ALPINE_IMAGE" tar -xf - -C /g < "$dir/openim-garage.tar"

  echo "启动 mongo（第一次启动会建 OpenIM 用户），写入 MongoDB 归档"
  "${compose[@]}" up -d mongo >/dev/null
  mongo="$(openim_container "$project" mongo)"
  for _ in $(seq 1 60); do
    docker exec "$mongo" mongosh --quiet --nodb --eval "$(openim_mongo_auth_js) db.runCommand({ping: 1}).ok" >/dev/null 2>&1 && break
    sleep 2
  done
  # 用 OpenIM 自己的用户（只有 openim_v3 的读写权限）恢复；密码同样经容器里的临时配置文件
  # shellcheck disable=SC2016
  docker exec -i "$mongo" sh -c 'umask 077; f=$(mktemp); printf "password: \"%s\"\n" "$OPENIM_MONGO_PASSWORD" > "$f";
    mongorestore --quiet --config="$f" --username=openim --authenticationDatabase=openim_v3 \
      --nsInclude="openim_v3.*" --drop --archive --gzip; rc=$?; rm -f "$f"; exit $rc' < "$dir/openim-mongo.archive.gz"
  restored="$(docker exec "$mongo" mongosh --quiet --nodb --eval "$(openim_mongo_auth_js) $(openim_mongo_counts_js)")"
  echo "MongoDB 已恢复：$(tr '\n' ' ' <<< "$restored")"
  echo "完成。运行 make openim-up 启动其余服务（garage-init 会确认布局、密钥与桶，已存在的不改）"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-}" in
    check)
      project="${2:-${COMPOSE_PROJECT_NAME:-my-workspace}-openim}"
      work="$(umask 077 && mktemp -d)"
      trap 'rm -rf -- "$work"' EXIT
      openim_dump "$project" "$work"
      openim_verify "$work"
      ;;
    restore)
      [[ -n "${2:-}" ]] || { echo "用法：scripts/openim_backup.sh restore <归档目录> [项目名]" >&2; exit 2; }
      openim_restore "$2" "${3:-${COMPOSE_PROJECT_NAME:-my-workspace}-openim}"
      ;;
    *)
      echo "用法：scripts/openim_backup.sh check [项目名] | restore <归档目录> [项目名]" >&2
      exit 2
      ;;
  esac
fi
