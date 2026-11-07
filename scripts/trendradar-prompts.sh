#!/usr/bin/env bash
# 下载 TrendRadar 的 AI 提示词并写进配置卷（T-0152，docs/v2/NEWS_PLAN.md 第 4、10 节）。
#
# 提示词是上游的文件（GPL-3.0），不放进本仓库：按锁定的提交（v6.11.0）从 GitHub 下载，逐个校验
# infra/trendradar/prompts.sha256，全部通过后才写进卷（属主是 api 的 app 用户 10001，权限 0640）。
# 已经是正确内容的不重新下载；可以重复运行。
#
# 用法：scripts/trendradar-prompts.sh <配置卷名>      例如 my-workspace_trendradar-config
# 变量：TRENDRADAR_RAW_BASE  下载地址前缀（默认 https://raw.githubusercontent.com/sansan0/TrendRadar）
set -euo pipefail

VOLUME="${1:?用法：scripts/trendradar-prompts.sh <配置卷名>}"
COMMIT=ceabb027fe9bbdafec59dbbfa12cb46c2ba256ce
BASE="${TRENDRADAR_RAW_BASE:-https://raw.githubusercontent.com/sansan0/TrendRadar}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/infra/trendradar/prompts.sha256"
# 写卷用的小镜像（与 OpenIM 的初始化容器相同的锁定版本）
HELPER=alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

docker volume inspect "$VOLUME" >/dev/null 2>&1 || { echo "没有卷 $VOLUME：请先 make up" >&2; exit 1; }

# 卷里已经都是锁定的内容：不用下载
if docker run --rm -v "$VOLUME:/conf:ro" -v "$LOCK:/prompts.sha256:ro" "$HELPER" \
  sh -c 'cd /conf && sha256sum -c -s /prompts.sha256' >/dev/null 2>&1; then
  echo "TrendRadar 提示词已经是锁定的版本（$COMMIT）"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
while read -r sum path; do
  [[ -n $sum ]] || continue
  mkdir -p "$tmp/$(dirname "$path")"
  curl -fsSL --retry 3 --connect-timeout 10 --max-time 60 "$BASE/$COMMIT/config/$path" -o "$tmp/$path"
  if ! echo "$sum  $tmp/$path" | sha256sum -c --quiet - >/dev/null 2>&1; then
    echo "提示词 $path 的 sha256 与锁定的不一致，没有写入" >&2
    exit 1
  fi
done <"$LOCK"
chmod -R a+rX "$tmp"

docker run --rm --user 10001:10001 -v "$VOLUME:/conf" -v "$tmp:/src:ro" "$HELPER" sh -c '
  set -e
  mkdir -p /conf/ai_filter
  for f in ai_analysis_prompt.txt ai_translation_prompt.txt ai_filter/prompt.txt \
           ai_filter/extract_prompt.txt ai_filter/update_tags_prompt.txt; do
    cp "/src/$f" "/conf/$f.tmp" && chmod 640 "/conf/$f.tmp" && mv "/conf/$f.tmp" "/conf/$f"
  done
  chmod 750 /conf/ai_filter'
echo "已写入 TrendRadar 提示词（$COMMIT）到卷 $VOLUME"
