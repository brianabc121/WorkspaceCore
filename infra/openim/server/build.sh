#!/usr/bin/env bash
# 构建打过补丁的 OpenIM Server 镜像（V2 OC-3）。
#
#   infra/openim/server/build.sh [镜像名]
#
# - 源码：上游 v3.8.3-patch.18，校验提交 SHA，不一致就退出；
# - 补丁：infra/openim/patches/*.patch，按文件名顺序 git am，任何一个打不上就退出；
# - 默认镜像名见 IMAGE；构建目录在临时目录，结束后删除。
# 环境变量：
#   OPENIM_SRC_DIR  已有的上游源码目录（跳过 clone，仍然校验 SHA 并在副本上打补丁）
#   PROXY_CA_FILE   本机 TLS 拦截代理的 CA 证书（作为 BuildKit secret，只在下载依赖时使用）
#   BASE_REGISTRY   基础镜像的镜像站（默认 docker.io/library；digest 不变）
set -euo pipefail

UPSTREAM_URL="https://github.com/openimsdk/open-im-server.git"
UPSTREAM_TAG="v3.8.3-patch.18"
UPSTREAM_SHA="120cee6d5783959a02ea1acfa414589fe5aae90f"
IMAGE="${1:-my-workspace/openim-server:v3.8.3-patch.18-myws.1}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
patches="$here/../patches"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if [[ -n "${OPENIM_SRC_DIR:-}" ]]; then
  git clone --quiet --no-hardlinks "$OPENIM_SRC_DIR" "$work/src"
  git -C "$work/src" checkout --quiet "$UPSTREAM_SHA"
else
  git clone --quiet --depth 1 --branch "$UPSTREAM_TAG" "$UPSTREAM_URL" "$work/src"
fi

actual="$(git -C "$work/src" rev-parse HEAD)"
if [[ "$actual" != "$UPSTREAM_SHA" ]]; then
  echo "上游提交不符：期望 $UPSTREAM_SHA，实际 $actual" >&2
  exit 1
fi

git -C "$work/src" -c user.name="My Workspace build" -c user.email="build@localhost" \
  am --quiet --whitespace=nowarn "$patches"/*.patch
echo "已应用补丁：" && git -C "$work/src" log --oneline "$UPSTREAM_SHA"..HEAD

cp "$here/Dockerfile" "$work/src/Dockerfile.myws"
args=(build -f "$work/src/Dockerfile.myws" -t "$IMAGE")
[[ -n "${BASE_REGISTRY:-}" ]] && args+=(--build-arg "BASE_REGISTRY=$BASE_REGISTRY")
if [[ -n "${PROXY_CA_FILE:-}" ]]; then
  # 本机代理监听在回环地址上，构建容器要用宿主机网络才能连到它
  args+=(--network host --secret "id=proxy_ca,src=$PROXY_CA_FILE")
  for v in HTTPS_PROXY https_proxy NO_PROXY no_proxy; do
    [[ -n "${!v:-}" ]] && args+=(--build-arg "$v=${!v}")
  done
fi
DOCKER_BUILDKIT=1 docker "${args[@]}" "$work/src"
echo "镜像：$IMAGE"
docker image inspect "$IMAGE" --format '镜像 ID：{{.Id}}'
