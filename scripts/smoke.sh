#!/usr/bin/env bash
# 经 Caddy 对外入口检查 core 服务（Sprint 0 DoD）。make smoke 和 CI 共用。
# 用法：scripts/smoke.sh [BASE_URL]，默认 http://127.0.0.1:${HTTP_PORT:-8080}
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:${HTTP_PORT:-8080}}"
failures=0
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT

check() {
  local name="$1" path="$2" want_status="$3" want_body="$4"
  local status
  status="$(curl -sS -o "$tmp/body" -D "$tmp/headers" -w '%{http_code}' "$BASE_URL$path" || echo 000)"
  if [[ "$status" == "$want_status" ]] && grep -q -- "$want_body" "$tmp/body"; then
    echo "通过  $name（$path → $status）"
  else
    echo "失败  $name（$path → $status，期望 $want_status 且包含 '$want_body'）"
    head -c 300 "$tmp/body"; echo
    failures=$((failures + 1))
  fi
}

check_header() {
  local name="$1" header="$2"
  if grep -qi "^$header:" "$tmp/headers"; then
    echo "通过  响应头 $name"
  else
    echo "失败  缺少响应头 $header"
    failures=$((failures + 1))
  fi
}

echo "冒烟检查：$BASE_URL"
check "API 存活"          /healthz            200 '"status":"ok"'
check "API 就绪（数据库 + Redis）" /readyz   200 '"database":"ok","redis":"ok"'
check "统一错误格式"      /api/v1/nope        404 '"code":"NOT_FOUND"'
check "Web 首页"          /                   200 '<div id="app"></div>'
check "前端路由回退"      /settings/security  200 '<div id="app"></div>'
check_header "X-Content-Type-Options" "X-Content-Type-Options"
check_header "Content-Security-Policy" "Content-Security-Policy"
check_header "X-Frame-Options" "X-Frame-Options"
if grep -qi '^Server:' "$tmp/headers"; then
  echo "失败  响应里仍有 Server 头"
  failures=$((failures + 1))
else
  echo "通过  不暴露 Server 头"
fi

if (( failures > 0 )); then
  echo "冒烟检查失败：$failures 项"
  exit 1
fi
echo "冒烟检查全部通过"
