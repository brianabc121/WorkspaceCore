#!/usr/bin/env bash
# 备份工作台数据库到加密的 restic 仓库（S6-04，规范 11.4）。make backup 与 CI 的 A15 验收共用。
# 依据：docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md 第 2 ~ 4 节。
#
# 步骤：pg_dump（custom 格式）→ pg_restore --list 确认归档完整 → restic backup → forget（按 7/30/90 日保留）→ check。
# 归档只在权限 700 的临时目录里存在，结束后删除。.env 不进备份：它保存解密钥匙，要单独保管。
#
# 配置（.env，shell 里的同名变量优先）：
#   BACKUP_REPOSITORY      restic 仓库：本地目录（默认 ./backups/restic，只适合测试，不算异地），
#                          或 s3:https://…、sftp:…、rest:… 等 restic 支持的地址
#   BACKUP_PASSWORD        仓库密码（make env 随机生成）。丢失就无法恢复，与 .env 一起单独保管
#   BACKUP_KEEP_DAILY / BACKUP_KEEP_WEEKLY / BACKUP_KEEP_MONTHLY   保留时长，默认 7d / 30d / 90d
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION  S3 仓库的凭据（只从环境变量读取）
# 用法：scripts/backup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/backup_common.sh
source "$ROOT/scripts/backup_common.sh"

pg_user="$(env_value POSTGRES_USER workspace)"
pg_db="$(env_value POSTGRES_DB workspace)"
keep_daily="$(env_value BACKUP_KEEP_DAILY 7d)"
keep_weekly="$(env_value BACKUP_KEEP_WEEKLY 30d)"
keep_monthly="$(env_value BACKUP_KEEP_MONTHLY 90d)"
host="$(env_value COMPOSE_PROJECT_NAME my-workspace)"

restic_prepare
mkdir -p "$work/data"

echo "备份数据库 $pg_db（pg_dump custom 格式）"
docker compose exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" -Fc > "$work/data/workspace.dump"
# 归档不完整（例如 pg_dump 中途失败）时 pg_restore --list 会报错
entries="$(docker compose exec -T postgres pg_restore --list < "$work/data/workspace.dump" | grep -c '^[0-9]')"
revision="$(docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -tAc 'select version_num from public.alembic_version')"
server="$(docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -tAc 'show server_version')"
commit="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
cat > "$work/data/backup-info.json" <<EOF
{"created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "database": "$pg_db", "alembic_revision": "$revision",
 "postgres_version": "$server", "archive_entries": $entries, "app_commit": "$commit"}
EOF
echo "归档完整：$entries 项，迁移版本 $revision"

restic_init_if_needed
echo "写入 restic 仓库（加密）"
restic_run -v "$work/data:/backup:ro" -- backup --tag workspace --host "$host" /backup
echo "按保留策略清理：每天保留 $keep_daily，每周保留 $keep_weekly，每月保留 $keep_monthly"
restic_run -- forget --tag workspace --host "$host" \
  --keep-within-daily "$keep_daily" --keep-within-weekly "$keep_weekly" --keep-within-monthly "$keep_monthly" --prune
restic_run -- check
echo "备份完成：$(restic_run -- snapshots --tag workspace --host "$host" --latest 1 --json | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s[-1]["short_id"], s[-1]["time"][:19])')"
