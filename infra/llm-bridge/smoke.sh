#!/usr/bin/env bash
# 会话桥镜像的冒烟检查（CI 用，也可以在部署后手动跑）：
#   infra/llm-bridge/smoke.sh <容器名> <地址，如 http://127.0.0.1:8787> <管理令牌>
# 检查：以普通用户运行、三家官方命令行是锁定的版本、1 号进程与环境变量不可读、llm-task、
# 健康检查、管理接口与 /mcp 的认证、工具清单。
set -euo pipefail
container="$1" base="$2" token="$3"

fail() { echo "FAIL: $*" >&2; exit 1; }

[ "$(docker exec "$container" id -u)" = "10001" ] || fail "容器不是以 uid 10001 运行"
docker exec "$container" codex --version | grep -q "0.169.0" || fail "codex 版本不是 0.169.0"
docker exec "$container" claude --version | grep -q "2.1.323" || fail "claude 版本不是 2.1.323"
docker exec "$container" codebuddy --version | grep -q "2.184.0" || fail "codebuddy 版本不是 2.184.0"
docker exec "$container" git --version >/dev/null || fail "镜像里没有 git"
docker exec "$container" bwrap --version >/dev/null || fail "镜像里没有 bubblewrap"
# 1 号进程是 bridge/pid1.py（回收孤儿进程，不留僵尸）；它和管理器都设成不可转储，
# 同一个用户的进程（后台作业、官方程序跑的命令）读不到它们的环境变量（里面有管理令牌）（T-0292）
docker exec "$container" sh -c 'tr "\0" " " </proc/1/cmdline' | grep -q 'bridge/pid1.py uvicorn' \
  || fail "1 号进程不是 bridge/pid1.py"
readable="$(docker exec "$container" sh -c '
  for p in /proc/[0-9]*; do
    [ "${p#/proc/}" = "$$" ] && continue
    case "$(tr "\0" " " 2>/dev/null <"$p/cmdline")" in
      *pid1.py*|*uvicorn*) if cat "$p/environ" >/dev/null 2>&1; then echo "${p#/proc/}"; fi ;;
    esac
  done')"
[ -z "$readable" ] || fail "这些进程的环境变量能被同一个用户读到：$readable"
# llm-task 装在 root 拥有的目录：会话里的命令改不了它
[ "$(docker exec "$container" sh -c 'command -v llm-task')" = "/usr/local/bin/llm-task" ] \
  || fail "llm-task 不在 /usr/local/bin"
docker exec "$container" sh -c '[ ! -w /usr/local/bin/llm-task ] && [ ! -w /usr/local/bin ]' \
  || fail "llm-task 或它所在的目录能被改写"
docker exec "$container" llm-task --help | grep -q 'llm-task job start' || fail "llm-task 不能运行"

curl -fsS "$base/healthz" | grep -q '"ok"' || fail "healthz"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$base/admin/status")" = "401" ] || fail "管理接口没有要求令牌"

status="$(curl -fsS -H "Authorization: Bearer $token" "$base/admin/status")"
echo "$status" | python3 -c '
import json, sys
data = json.load(sys.stdin)
assert set(data["accounts"]) == {"openai", "claude", "workbuddy"}, data["accounts"].keys()
# 没有选过控制的平台时是全部
assert data["platforms"] == ["codex", "claude", "workbuddy"], data["platforms"]   # 没有单独的 ChatGPT 平台（T-0280）
for slot, account in data["accounts"].items():
    assert account["enabled"], (slot, account)
    assert account["state"] == "logged_out", (slot, account)   # 新容器：都还没登录，官方程序都能启动
channels = {(c["platform"], c["mode"]): c["available"] for c in data["channels"]}
assert channels[("codex", "chat")] and channels[("codex", "dev")] and channels[("claude", "chat")]
assert ("chatgpt", "chat") not in channels
assert not channels[("codex", "cloud")] and not channels[("claude", "cloud")]
' || fail "管理接口返回的状态不对：$status"

mcp() {
  curl -fsS "$base/mcp" -H "Authorization: Bearer $token" -H "X-Bridge-Caller: smoke" \
    -H "X-Bridge-Scopes: write" -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" -H "MCP-Protocol-Version: 2025-06-18" -d "$1"
}
mcp '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | grep -q '"llm_send"' || fail "工具清单"
# Codex 的模型列表来自真实的 app-server（不需要登录）
mcp '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"llm_models","arguments":{"platform":"codex","mode":"chat"}}}' \
  | grep -q '"default": *true' || fail "Codex 模型列表"
# 官方程序的进程（这里是刚才列模型时起来的 codex app-server）环境里不能有管理令牌：
# 它们跑的命令继承这份环境，同一个用户也读得到它们的 /proc/<pid>/environ（T-0293）。
# docker exec 自己带着容器的环境变量，所以这里把它的 BRIDGE_ADMIN_TOKEN 置空，只找非空的
docker exec "$container" sh -c 'grep -l "app-serve[r]" /proc/[0-9]*/cmdline' >/dev/null 2>&1 \
  || fail "没找到 codex app-server，下一项查不到它"
leaky="$(docker exec -e BRIDGE_ADMIN_TOKEN= "$container" sh -c '
  for p in /proc/[0-9]*; do
    [ "${p#/proc/}" = "$$" ] && continue
    if tr "\0" "\n" 2>/dev/null <"$p/environ" | grep -q "^BRIDGE_ADMIN_TOKEN=."; then
      echo "${p#/proc/}: $(tr "\0" " " 2>/dev/null <"$p/cmdline" | cut -c1-80)"
    fi
  done')"
[ -z "$leaky" ] || fail "这些进程的环境变量里有管理令牌：$leaky"
# 没登录时新建会话要被明确拒绝
mcp '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"llm_session_create","arguments":{"platform":"claude","mode":"chat"}}}' \
  | grep -q 'NOT_LOGGED_IN' || fail "未登录时应返回 NOT_LOGGED_IN"
echo "会话桥冒烟检查通过"
