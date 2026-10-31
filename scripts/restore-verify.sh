#!/usr/bin/env bash
# 在隔离的临时环境里恢复备份并验证（S6-04，规范 11.3 / 11.4、A15）。make restore-verify 与 CI 共用。
# 依据：docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md 第 2 ~ 4 节。
#
# 临时环境是另一个 Compose 项目（默认 <项目名>-restore-verify）：自己的网络与数据卷、另一个端口，
# 不影响正在运行的栈。只启动 postgres、redis、api、web、caddy——不启动 Worker，
# 恢复出来的集成凭证不会去连接真实的第三方服务。
#
# 步骤：从 restic 仓库取出快照 → 启动临时的 postgres / redis → pg_restore → alembic upgrade head（检查迁移）
#       → 启动 api / web / caddy → 冒烟检查与数据检查 → 删除临时环境（--keep 时保留，供进一步检查）。
#
# 用法：scripts/restore-verify.sh [--keep] [快照 ID，默认 latest]
#       scripts/restore-verify.sh --cleanup        # 删除 --keep 留下的临时环境
# 配置：同 scripts/backup.sh；RESTORE_HTTP_PORT 是临时环境的端口（默认 18080）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/backup_common.sh
source "$ROOT/scripts/backup_common.sh"

project="$(env_value COMPOSE_PROJECT_NAME my-workspace)-restore-verify"
port="$(env_value RESTORE_HTTP_PORT 18080)"
pg_user="$(env_value POSTGRES_USER workspace)"
pg_db="$(env_value POSTGRES_DB workspace)"
host="$(env_value COMPOSE_PROJECT_NAME my-workspace)"
keep=0
snapshot=latest

teardown() {
  # 只删除本脚本创建的临时项目：名字必须以 -restore-verify 结尾，绝不会是正在运行的栈
  [[ "$project" == *-restore-verify ]] || { echo "拒绝删除项目 $project" >&2; exit 1; }
  docker compose -p "$project" down -v --remove-orphans >/dev/null 2>&1 || true
}

case "${1:-}" in
  --cleanup) teardown; echo "已删除临时环境 $project"; exit 0 ;;
  --keep) keep=1; shift ;;
esac
snapshot="${1:-latest}"

restic_prepare
mkdir -p "$work/restore"
echo "从 restic 仓库取出快照 $snapshot"
restic_run -v "$work/restore:/restore" -- restore "$snapshot" --tag workspace --host "$host" --target /restore
dump="$work/restore/backup/workspace.dump"
[[ -s "$dump" ]] || { echo "快照里没有 workspace.dump" >&2; exit 1; }
echo "备份信息：$(cat "$work/restore/backup/backup-info.json" | tr -d '\n')"

# 临时环境的 .env：沿用原来的密钥（恢复出来的凭证才能解密），换掉端口、对外地址与 agent-ingress 网段，不连接监控
(umask 077 && grep -v -E '^(HTTP_PORT|PUBLIC_BASE_URL|CORS_ALLOWED_ORIGINS|PROMETHEUS_URL|GRAFANA_URL|GRAFANA_DASHBOARD_URL|AGENT_INGRESS_NET|AGENT_INGRESS_TRUSTED_PROXIES)=' .env > "$work/restore.env")
cat >> "$work/restore.env" <<EOF
HTTP_PORT=$port
PUBLIC_BASE_URL=http://localhost:$port
CORS_ALLOWED_ORIGINS=http://127.0.0.1:$port
PROMETHEUS_URL=
GRAFANA_URL=
GRAFANA_DASHBOARD_URL=
# agent-ingress 网络用另一个固定网段，免得和正在运行的环境冲突
AGENT_INGRESS_NET=172.31.251
EOF
compose=(docker compose -p "$project" --env-file "$work/restore.env")

teardown   # 清掉上一次留下的临时环境
[[ "$keep" == 1 ]] || trap 'teardown; rm -rf -- "$work"' EXIT
echo "启动临时环境 $project（端口 $port）"
"${compose[@]}" up -d --no-build --wait --wait-timeout 180 postgres redis
start="$(date +%s)"
"${compose[@]}" exec -T postgres pg_restore -U "$pg_user" -d "$pg_db" --exit-on-error < "$dump"
echo "恢复数据库：$(( $(date +%s) - start )) 秒"
"${compose[@]}" up -d --no-build --wait --wait-timeout 180 api web caddy
# 备份可能来自旧版本：升级到当前代码的最新迁移，验证迁移能在恢复出来的数据上执行
migrated="$("${compose[@]}" exec -T api alembic upgrade head 2>&1)" || { echo "$migrated" >&2; exit 1; }
"${compose[@]}" exec -T api alembic current 2>/dev/null | grep -q '(head)' || { echo "迁移没有到最新版本" >&2; exit 1; }
echo "迁移检查：已到最新版本"

scripts/smoke.sh "http://127.0.0.1:$port"
counts="$("${compose[@]}" exec -T postgres psql -U "$pg_user" -d "$pg_db" -tA -F ' ' -c \
  'select (select count(*) from workspace.users), (select count(*) from workspace.workspaces), (select count(*) from workspace.dashboards)')"
read -r users workspaces dashboards <<< "$counts"
echo "数据检查：$users 个用户、$workspaces 个工作空间、$dashboards 个仪表盘"
(( users > 0 && workspaces > 0 && dashboards > 0 )) || { echo "恢复出来的数据是空的" >&2; exit 1; }

if [[ "$keep" == 1 ]]; then
  echo "临时环境保留在 http://127.0.0.1:$port（项目 $project）；检查完运行 scripts/restore-verify.sh --cleanup"
else
  echo "恢复验证通过；删除临时环境"
fi
