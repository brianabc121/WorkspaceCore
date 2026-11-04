#!/usr/bin/env bash
# 在已锁定的 Garage（dxflrs/garage:v2.4.1，OpenIM 已在用）上验证知识库图片的 S3 兼容存储（T-0103、T-0106）：
# 路径风格（R2）与虚拟主机风格（OSS）的 PUT / GET / HEAD / DELETE、错误密钥的分类、按 R2 / OSS 配置建存储，
# 主存储 + 副本（复制、读主失败读副本、清理两边）。CI（ci-api）与本机共用。
#
# 步骤：起一个临时 Garage（随机密钥、只在本机端口；配置在 OpenIM 的 garage.toml 上加 root_domain，
#       支持虚拟主机风格）→ 用 infra/openim/garage-init.sh 应用布局、导入密钥、建两个桶
#       → 运行 tests/test_object_storage_garage.py（副本那条要 TEST_DATABASE_URL / TEST_REDIS_URL）→ 删除容器与网络。
# 用法：scripts/garage_s3_check.sh            （需要 Docker；在 services/api 的虚拟环境里运行 pytest）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GARAGE_IMAGE="$(sed -n 's/^ *image: \(dxflrs\/garage:[^ ]*\)$/\1/p' "$ROOT/infra/openim/compose.yaml" | head -n 1)"
ALPINE_IMAGE="$(sed -n 's/^OPENIM_ALPINE_IMAGE="\(.*\)"$/\1/p' "$ROOT/scripts/openim_backup.sh")"
[[ -n $GARAGE_IMAGE && -n $ALPINE_IMAGE ]] || { echo "找不到锁定的 Garage / alpine 镜像" >&2; exit 1; }

tag="kb-garage-$$"
port="${GARAGE_S3_PORT:-39$(( RANDOM % 90 + 10 ))0}"
admin_token="$(openssl rand -hex 16)"
key_id="GK$(openssl rand -hex 12)"
key_secret="$(openssl rand -hex 32)"
root_domain="s3.garage.test"
config="$(mktemp)"
cleanup() {
  docker rm -f "$tag" >/dev/null 2>&1 || true; docker network rm "$tag" >/dev/null 2>&1 || true; rm -f "$config"
}
trap cleanup EXIT
# 虚拟主机风格（<桶>.s3.garage.test）：在 [s3_api] 里加 root_domain，其余与 OpenIM 用的配置一致
sed "/^api_bind_addr = \"0.0.0.0:3900\"$/a root_domain = \".$root_domain\"" "$ROOT/infra/openim/garage.toml" >"$config"
grep -q "^root_domain = \".$root_domain\"$" "$config" || { echo "没能给 garage.toml 加 root_domain" >&2; exit 1; }
chmod 644 "$config"

docker network create "$tag" >/dev/null
docker run -d --name "$tag" --network "$tag" -p "127.0.0.1:$port:3900" \
  -v "$config:/etc/garage.toml:ro" \
  -e GARAGE_RPC_SECRET="$(openssl rand -hex 32)" -e GARAGE_ADMIN_TOKEN="$admin_token" \
  "$GARAGE_IMAGE" >/dev/null
# 主存储的桶与副本的桶（garage-init.sh 幂等：第二次只建桶、授权）
for bucket in kb-test kb-replica; do
  docker run --rm --network "$tag" -v "$ROOT/infra/openim/garage-init.sh:/garage-init.sh:ro" \
    -e GARAGE_ADMIN_ENDPOINT="http://$tag:3903" -e OPENIM_GARAGE_ADMIN_TOKEN="$admin_token" \
    -e OPENIM_S3_ACCESS_KEY="$key_id" -e OPENIM_S3_SECRET_KEY="$key_secret" -e OPENIM_S3_BUCKET="$bucket" \
    "$ALPINE_IMAGE" sh /garage-init.sh
done

cd "$ROOT/services/api"
runner=(uv run pytest)
[[ -x .venv/bin/pytest ]] && runner=(.venv/bin/pytest)
TEST_S3_ENDPOINT="http://127.0.0.1:$port" TEST_S3_REGION=garage TEST_S3_BUCKET=kb-test \
  TEST_S3_REPLICA_BUCKET=kb-replica TEST_S3_ROOT_DOMAIN="$root_domain" \
  TEST_S3_ACCESS_KEY="$key_id" TEST_S3_SECRET_KEY="$key_secret" \
  "${runner[@]}" -q -p no:cacheprovider tests/test_object_storage_garage.py
