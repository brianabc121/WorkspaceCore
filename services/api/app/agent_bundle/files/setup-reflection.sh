#!/bin/sh
# 建（或更新）每晚复盘的 OpenClaw 定时任务（知识库方案 3.9 第 2 条，T-0100）。
# 在接入包目录里运行，OpenClaw 要已经启动：  sh setup-reflection.sh
# 用 --declaration-key：重复运行只更新同一个任务（提示词、时间），不会重复建。
# 已在 OpenClaw 2026.9.36 上核验（docs/decisions/2026-11-03-knowledge-k0-verification.md 第 3.3 节）。
set -eu
cd "$(dirname "$0")"
exec docker compose exec -T openclaw node openclaw.mjs automations add \
  --name myworkspace-knowledge-reflection \
  --declaration-key myworkspace-knowledge-reflection \
  --description "工作台知识库：每晚复盘" \
  --cron "@CRON@" --tz "@TZ@" \
  --session isolated --no-deliver \
  --message "$(cat reflection-prompt.md)"
