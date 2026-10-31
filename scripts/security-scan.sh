#!/usr/bin/env bash
# 安全扫描（S6-05，T-0044）：依赖漏洞、密钥泄露、容器镜像。make scan 与 CI（.github/workflows/ci-security.yml）共用。
# 工具、版本与策略的核验见 docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md 第 5、8 节。
#
# 用法：scripts/security-scan.sh [deps|secrets|images|all]   默认 all，可以写多个
#   deps     Python（pip-audit，按 services/api/uv.lock）与 npm（npm audit，按各 package-lock.json）依赖，
#            都包含开发依赖：有高危或严重漏洞就失败，中低危只报告
#   secrets  gitleaks 扫描整个 git 历史：有任何发现就失败；已知误报按指纹登记在 .gitleaksignore
#   images   trivy 扫描已有修复版本的高危 / 严重漏洞：
#            - 本仓库构建的镜像（my-workspace-api:local、my-workspace-web:local）有就失败，要先构建（make up）
#            - 第三方锁定镜像（compose 文件里的其余镜像与 restic）只报告：修复只能来自上游的新版本，
#              升级要按项目规则重新核验，发布前按 checklist 跟进（docs/deployment.md）
# 需要：deps 要 uv 与 npm；secrets 与 images 要 Docker。
# 环境变量：
#   TRIVY_CACHE_DIR       trivy 漏洞库的缓存目录（默认 ~/.cache/trivy，约 1 GB）
#   SCAN_THIRD_PARTY=0    images 只扫描本仓库构建的镜像
#   SSL_CERT_FILE         设置时挂载进 trivy 容器（出网经过 TLS 代理的环境）；HTTPS_PROXY 等代理变量原样传入
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/backup_common.sh
source "$ROOT/scripts/backup_common.sh"   # RESTIC_IMAGE（第三方镜像清单的一部分）与 env_value

PIP_AUDIT_VERSION="2.10.1"
GITLEAKS_IMAGE="zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
TRIVY_IMAGE="aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c"
OWN_IMAGES=(my-workspace-api:local my-workspace-web:local)
NPM_PROJECTS=(apps/web tests/e2e)

failed=()
reports=""   # 临时目录：pip-audit 的输入与 trivy 的 JSON 报告；退出时删除

section() { printf '\n== %s\n' "$*"; }

scan_deps() {
  section "Python 依赖：pip-audit $PIP_AUDIT_VERSION（services/api/uv.lock，含开发依赖）"
  # 先导出到文件：导出失败时不能把空的依赖列表交给 pip-audit（那样会报“没有漏洞”）
  (cd services/api && uv export --frozen --no-emit-project --format requirements-txt --quiet) > "$reports/requirements.txt"
  echo "$(grep -c '==' "$reports/requirements.txt") 个锁定的包"
  uvx --quiet --from "pip-audit==$PIP_AUDIT_VERSION" pip-audit --progress-spinner off \
    --require-hashes --disable-pip -r "$reports/requirements.txt" || failed+=("Python 依赖")

  local dir
  for dir in "${NPM_PROJECTS[@]}"; do
    section "npm 依赖：npm audit（$dir/package-lock.json，含开发依赖，高危及以上失败）"
    (cd "$dir" && npm audit --audit-level=high) || failed+=("npm 依赖（$dir）")
  done
}

scan_secrets() {
  section "密钥泄露：gitleaks（整个 git 历史，已知误报见 .gitleaksignore）"
  if [[ "$(git rev-parse --is-shallow-repository)" == true ]]; then
    echo "提示：这是浅克隆，只扫描已经拉取的提交；完整扫描先 git fetch --unshallow（CI 用 fetch-depth: 0）" >&2
  fi
  # 用当前用户运行：容器里的 git 才会信任这个仓库目录，也不会写入任何文件
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$ROOT:/repo:ro" "$GITLEAKS_IMAGE" \
    git /repo --no-banner --redact --gitleaks-ignore-path /repo/.gitleaksignore || failed+=("密钥泄露")
}

# trivy <镜像> <报告文件名>：只看已有修复版本的高危 / 严重漏洞，结果写成 JSON
trivy_scan() {
  local cache="${TRIVY_CACHE_DIR:-$HOME/.cache/trivy}" args=() name
  mkdir -p "$cache"
  # 读本机的镜像要访问 Docker；不在本机的第三方镜像由 trivy 直接从镜像仓库读取，不占本机的镜像空间
  args=(--rm --network host -u "$(id -u):$(id -g)" --group-add "$(stat -c %g /var/run/docker.sock)"
    -e HOME=/tmp -v /var/run/docker.sock:/var/run/docker.sock -v "$cache:/cache" -v "$reports:/reports")
  for name in HTTPS_PROXY HTTP_PROXY NO_PROXY https_proxy http_proxy no_proxy; do
    [[ -n "${!name:-}" ]] && args+=(-e "$name")
  done
  if [[ -n "${SSL_CERT_FILE:-}" ]]; then
    args+=(-v "$SSL_CERT_FILE:/run/ca-bundle.crt:ro" -e SSL_CERT_FILE=/run/ca-bundle.crt)
  fi
  docker run "${args[@]}" "$TRIVY_IMAGE" image --quiet --skip-version-check --cache-dir /cache \
    --severity HIGH,CRITICAL --ignore-unfixed --format json --output "/reports/$2" "$1"
}

# summarize <报告文件> <最多列出几条>：打印每个漏洞一行，返回漏洞数（0 ~ 255）
summarize() {
  python3 - "$reports/$1" "$2" <<'PY'
import json, sys
report, limit = json.load(open(sys.argv[1])), int(sys.argv[2])
found = [
    (v["Severity"], v["PkgName"], v["InstalledVersion"], v.get("FixedVersion", ""), v["VulnerabilityID"], r["Target"])
    for r in report.get("Results", []) for v in r.get("Vulnerabilities") or []
]
found.sort(key=lambda f: (f[0] != "CRITICAL", f[1]))
for severity, pkg, installed, fixed, vid, target in found[:limit]:
    print(f"  {severity:<8} {pkg} {installed} → {fixed}  {vid}  （{target}）")
if len(found) > limit:
    print(f"  …… 另有 {len(found) - limit} 个，完整列表用 trivy image 查看")
counts = {s: sum(1 for f in found if f[0] == s) for s in ("CRITICAL", "HIGH")}
print(f"  合计：严重 {counts['CRITICAL']}，高危 {counts['HIGH']}")
sys.exit(min(len(found), 255))
PY
}

third_party_images() {
  local project
  project="$(env_value COMPOSE_PROJECT_NAME my-workspace)"
  {
    docker compose --profile monitoring config --images
    WORKSPACE_PROJECT="$project" docker compose -p "$project-vikunja" -f infra/vikunja/compose.yaml --env-file .env config --images
    WORKSPACE_PROJECT="$project" docker compose -p "$project-n8n" -f infra/n8n/compose.yaml --env-file .env config --images
    echo "$RESTIC_IMAGE"
  } | grep -v -x -F -f <(printf '%s\n' "${OWN_IMAGES[@]}") | sort -u
}

scan_images() {
  local image file count=0 index=0
  for image in "${OWN_IMAGES[@]}"; do
    section "本仓库构建的镜像：$image（trivy，已有修复的高危 / 严重漏洞就失败）"
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      echo "找不到镜像 $image：先运行 make up（或 docker compose build）" >&2
      failed+=("镜像 $image（未构建）")
      continue
    fi
    file="own-$((index += 1)).json"
    trivy_scan "$image" "$file" || { failed+=("镜像 $image（trivy 运行失败）"); continue; }
    summarize "$file" 100 || failed+=("镜像 $image")
  done

  [[ "${SCAN_THIRD_PARTY:-1}" == 0 ]] && return 0
  [[ -f .env ]] || { echo "缺少 .env：第三方镜像清单来自 compose 文件，先运行 make env" >&2; failed+=("第三方镜像（缺少 .env）"); return 0; }
  while read -r image; do
    section "第三方锁定镜像：$image（只报告）"
    file="third-$((index += 1)).json"
    trivy_scan "$image" "$file" || { failed+=("镜像 $image（trivy 运行失败）"); continue; }
    summarize "$file" 15 || count=$((count + 1))
  done < <(third_party_images)
  echo
  echo "第三方镜像：$count 个有已修复的高危 / 严重漏洞（不让扫描失败；发布前按 docs/deployment.md 的 checklist 跟进上游版本）"
}

reports="$(mktemp -d)"
trap 'rm -rf -- "$reports"' EXIT
targets=("$@")
[[ ${#targets[@]} -gt 0 ]] || targets=(all)
for target in "${targets[@]}"; do
  case "$target" in
    deps) scan_deps ;;
    secrets) scan_secrets ;;
    images) scan_images ;;
    all) scan_deps; scan_secrets; scan_images ;;
    *) echo "用法：$0 [deps|secrets|images|all]" >&2; exit 2 ;;
  esac
done

echo
if [[ ${#failed[@]} -gt 0 ]]; then
  printf '安全扫描未通过：%s\n' "${failed[@]}"
  exit 1
fi
echo "安全扫描通过（${targets[*]}）"
