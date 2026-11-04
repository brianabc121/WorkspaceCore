#!/bin/sh
# 建（或更新）每晚复盘的 OpenClaw 定时任务（知识库方案 3.9 第 2 条，T-0100）。
# 在接入包目录里运行，OpenClaw 要已经启动：  sh setup-reflection.sh
# 用 --declaration-key：重复运行只更新同一个任务（提示词、时间），不会重复建。
# OpenClaw 刚启动时网关还没就绪（冷启动 2 ~ 4 分钟）：失败就每 MYWS_REFLECTION_WAIT 秒重试，
# 最多 MYWS_REFLECTION_TRIES 次（默认 10 秒、24 次）。
# 已在 OpenClaw 2026.9.36 上核验（docs/decisions/2026-11-03-knowledge-k0-verification.md 第 3.3、5 节）。
set -eu
cd "$(dirname "$0")"
tries="${MYWS_REFLECTION_TRIES:-24}"
wait="${MYWS_REFLECTION_WAIT:-10}"
n=1
while :; do
  if docker compose exec -T openclaw node openclaw.mjs automations add \
    --name myworkspace-knowledge-reflection \
    --declaration-key myworkspace-knowledge-reflection \
    --description "工作台知识库：每晚复盘" \
    --cron "@CRON@" --tz "@TZ@" \
    --session isolated --no-deliver \
    --message "$(cat reflection-prompt.md)"; then
    exit 0
  fi
  if [ "$n" -ge "$tries" ]; then
    echo "OpenClaw 的网关一直没有就绪，没建成每晚复盘任务；确认 OpenClaw 在运行后再运行一次 sh setup-reflection.sh" >&2
    exit 1
  fi
  n=$((n + 1))
  sleep "$wait"
done
