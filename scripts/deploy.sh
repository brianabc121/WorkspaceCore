#!/usr/bin/env bash
# My Workspace 一键部署 / 升级 / 清空（T-0072）。
#
# 一条命令（在全新服务器上运行；会把代码放到 /srv/my-workspace）。root 登录时：
#   curl -fsSL https://raw.githubusercontent.com/brianabc121/WorkspaceCore/main/scripts/deploy.sh | bash
# 普通用户把最后的 bash 换成 sudo bash（Debian 最小安装的 root 没有 sudo，不要加）。
# 提示 curl: command not found（Debian 最小安装常见）时先安装：apt-get update && apt-get install -y curl ca-certificates
# 已有代码时：
#   sudo bash scripts/deploy.sh [install|upgrade|reset|status|connect|logs] [选项]
#
# 部署、升级、接入组件分两段（T-0094）：
#   1. 前台：把需要你回答的问题一次问完——SITE_DOMAIN、ACME_EMAIL、PUBLIC_BASE_URL（默认 https://<域名>），
#      要不要部署 n8n / OpenIM / OpenClaw 接入入口及其参数、防火墙等；回答保存在 <代码目录>/.deploy/answers.env（不含密码）；
#   2. 后台：作为系统服务（systemd-run，没有 systemd 时 setsid nohup）运行，不再提问，输出写进 .deploy/deploy.log：
#      安装缺少的工具与 Docker → 生成 .env（随机密钥）→ 启用 HTTPS 与生产模式 → make up → 迁移 → 冒烟检查 → 检查 HTTPS →
#      等你在浏览器完成「首次安装」创建管理员（自动检测，不用回终端按回车）→ 按第 1 段的选择接入 n8n、部署 OpenIM、
#      配置 OpenClaw 接入入口（T-0076、T-0088）。GitHub 在工作台「集成设置」页面上接入（T-0091）。
# 前台接着显示日志；按 Ctrl+C、SSH 断开都不影响后台。之后查看：deploy.sh status（进度、安装凭据、失败原因）、
# deploy.sh logs（日志）。失败后重新运行同样的命令，会问是否沿用上次的回答。
#
# 选项：
#   --dir DIR          安装目录（默认 /srv/my-workspace；在代码目录里运行时就是该目录）
#   --ref REF          部署 / 升级到的分支或 tag（默认 main）
#   --domain D --email E --base-url URL   直接给出三个参数，不再询问
#   --yes              所有确认都按默认回答（无人值守）；清空还必须同时给 --confirm my-workspace
#   --answers FILE     用回答文件（格式同 .deploy/answers.env）回答问题，没写到的按默认值
#   --foreground       不转到后台，在当前终端边问边做（调试用；SSH 断开会中断）
#   --confirm NAME     无人值守清空时的确认（必须是项目名 my-workspace）
#   --keep-env / --delete-env   清空时是否保留 .env（默认保留）
#   --delete-dir       清空时同时删除代码目录（backups/ 与 .env 先移到 /var/backups/my-workspace-<时间>/）
#
# 说明见 docs/deploy-guide.md 开头的「一键脚本」一节。
set -Eeuo pipefail

REPO_URL="${WORKSPACE_REPO:-https://github.com/brianabc121/WorkspaceCore.git}"
DEFAULT_DIR="${WORKSPACE_DIR:-/srv/my-workspace}"
PROJECT="my-workspace"
# $PROJECT-vikunja：T-0082 移除了 Vikunja；留在列表里，uninstall 时还能清理以前部署的
OPTIONAL_PROJECTS=("$PROJECT-vikunja" "$PROJECT-n8n" "$PROJECT-openim")
BACKUP_ENV_FILE="/etc/my-workspace/backup.env"

ACTION="" DIR="" REF="" DOMAIN="" EMAIL="" BASE_URL="" ASSUME_YES=0 CONFIRM="" ENV_CHOICE="" DIR_CHOICE="" AFTER_PULL="" ROOT=""
RUN=0 LAST_ERROR="" UPGRADE_OLD="" UPGRADE_NEW=""

# ---------------------------------------------------------------- 输出与交互
if [[ -t 2 ]]; then C_B=$'\e[1m' C_G=$'\e[32m' C_Y=$'\e[33m' C_R=$'\e[31m' C_0=$'\e[0m'; else C_B="" C_G="" C_Y="" C_R="" C_0=""; fi
step() {
  if [[ ${PHASE:-} == run ]]; then
    printf '\n==> [%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2
    state_set step "$*"
  else
    printf '\n%s==> %s%s\n' "$C_B" "$*" "$C_0" >&2
  fi
}
ok() { printf '%s通过%s  %s\n' "$C_G" "$C_0" "$*" >&2; }
info() { printf '      %s\n' "$*" >&2; }
warn() { printf '%s注意%s  %s\n' "$C_Y" "$C_0" "$*" >&2; }
die() { LAST_ERROR="$*"; printf '%s失败%s  %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }
trap 'printf "%s失败%s  第 %s 行的命令出错：%s\n" "$C_R" "$C_0" "$LINENO" "$BASH_COMMAND" >&2' ERR

interactive() { [[ -t 0 ]]; }

# ask "问题" "默认值" → 回答（为空时取默认值）
ask() {
  local prompt="$1" default="${2:-}" answer=""
  if (( ASSUME_YES )) || ! interactive; then
    [[ -n $default ]] || die "需要输入：$prompt（无人值守时请用参数给出）"
    printf '%s\n' "$default"; return
  fi
  read -r -p "$prompt${default:+ [$default]}: " answer || true
  printf '%s\n' "${answer:-$default}"
}

# confirm "问题" y|n → 返回 0 表示是
confirm() {
  local prompt="$1" default="${2:-n}" answer=""
  if (( ASSUME_YES )) || ! interactive; then [[ $default == y ]]; return; fi
  read -r -p "$prompt [$([[ $default == y ]] && echo Y/n || echo y/N)]: " answer || true
  answer="${answer:-$default}"
  [[ $answer =~ ^[Yy] ]]
}

as_root() { if (( EUID == 0 )); then "$@"; elif command -v sudo >/dev/null; then sudo "$@"; else die "需要 root 权限：$*"; fi; }

# ---------------------------------------------------------------- 先问完，再后台运行（T-0094）
# 首次部署要下载几 GB 镜像、在服务器上编译 OpenIM，可能要几十分钟，SSH 随时可能断开。所以部署、升级、接入分两段：
#   1. 前台：把需要你回答的问题一次问完（参数、要接入的组件、防火墙等），保存成回答文件；
#   2. 后台：作为系统服务（systemd-run；没有 systemd 时 setsid nohup）运行，一个问题都不再问，输出写进日志。
# 网页上的“首次安装”（创建管理员）由后台自动检测，不需要回到终端按回车。前台只是显示日志：
# 按 Ctrl+C、断开 SSH 都不影响部署；之后运行 deploy.sh status（进度）或 deploy.sh logs（日志）。
# 回答文件只有参数与选择，不含任何密码；需要密码的少数情况（例如 n8n 已经有 owner）后台跳过并列为待办。
PHASE=direct        # direct：边问边做（--foreground、CI）；plan：前台提问；run：后台运行
FOREGROUND=0
ANSWERS_PATH=""
ORIG_ARGS=()
declare -A ANS=()
RUN_UNIT="my-workspace-deploy"
SETUP_WAIT_SECONDS=$((24 * 3600))
SETUP_POLL=5
# 后台进程从这里继承的环境变量（systemd-run 默认不传环境）
RUN_ENV_VARS=(HOME PATH LANG LC_ALL DOCKER_CONFIG DOCKER_HOST HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy
  WORKSPACE_REPO WORKSPACE_DIR COMPOSE_PROJECT_NAME)

deploy_dir() { printf '%s/.deploy\n' "$ROOT"; }
deploy_log() { printf '%s/deploy.log\n' "$(deploy_dir)"; }
state_file() { printf '%s/state\n' "$(deploy_dir)"; }
todo_file() { printf '%s/todo\n' "$(deploy_dir)"; }
answers_file() { printf '%s\n' "${ANSWERS_PATH:-$(deploy_dir)/answers.env}"; }
deploy_dir_init() { local d; d="$(deploy_dir)"; mkdir -p "$d" && chmod 700 "$d"; }

state_get() { local f; f="$(state_file)"; [[ -r $f ]] && sed -n "s/^$1=//p" "$f" | tail -1; return 0; }
# state_set 键 值 [键 值 …]：原子地替换这几项（值里的换行换成空格）
state_set() {
  local f tmp keys="" pairs=() k v
  f="$(state_file)"; [[ -d ${f%/*} ]] || return 0
  while (($# >= 2)); do
    k="$1" v="${2//$'\n'/ }"; shift 2
    keys+="${keys:+|}$k"; pairs+=("$k=$v")
  done
  tmp="$f.tmp.$$"
  { [[ -f $f ]] && grep -Ev "^($keys)=" "$f"; printf '%s\n' "${pairs[@]}"; } >"$tmp" 2>/dev/null || true
  chmod 600 "$tmp" && mv -f "$tmp" "$f"
}
todo_add() { local f; f="$(todo_file)"; [[ -d ${f%/*} ]] && printf '%s\n' "$*" >>"$f"; warn "待办：$*"; }

# 回答文件：每行 键=值，只接受大写键名；读的时候不执行任何内容
answers_load() {
  local file="$1" key value
  [[ -r $file ]] || die "读不到回答文件 $file"
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}" value="${BASH_REMATCH[2]}"
    ANS[$key]="$value"
  done <"$file"
  DOMAIN="${DOMAIN:-${ANS[SITE_DOMAIN]:-}}"; EMAIL="${EMAIL:-${ANS[ACME_EMAIL]:-}}"; BASE_URL="${BASE_URL:-${ANS[PUBLIC_BASE_URL]:-}}"
}
answers_save() {
  local file key tmp
  file="$(answers_file)"; tmp="$file.tmp.$$"
  [[ -n $DOMAIN ]] && ANS[SITE_DOMAIN]="$DOMAIN"
  [[ -n $EMAIL ]] && ANS[ACME_EMAIL]="$EMAIL"
  [[ -n $BASE_URL ]] && ANS[PUBLIC_BASE_URL]="$BASE_URL"
  {
    printf '# deploy.sh 的回答（%s 生成；只有参数与选择，不含密码）\n' "$(date '+%F %T')"
    for key in $(printf '%s\n' "${!ANS[@]}" | sort); do printf '%s=%s\n' "$key" "${ANS[$key]//$'\n'/ }"; done
  } >"$tmp"
  chmod 600 "$tmp" && mv -f "$tmp" "$file"
}

# decide 键 "问题" y|n：是/否的选择。前台问并记下；后台只读记下的回答，没有就按默认值（写进日志），不会停下来等人
decide() {
  local key="$1" prompt="$2" default="${3:-n}"
  if [[ -v ANS[$key] ]]; then [[ ${ANS[$key]} == y ]]; return; fi
  if [[ $PHASE == run ]]; then
    ANS[$key]="$default"
    info "没有事先回答「$prompt」，按默认：$([[ $default == y ]] && echo 是 || echo 否)"
    [[ $default == y ]]; return
  fi
  if confirm "$prompt" "$default"; then ANS[$key]=y; return 0; fi
  ANS[$key]=n; return 1
}

# choose 变量名 键 "问题" "默认值"：要填写的值，规则同 decide
# （内部变量都带 __ 前缀：printf -v 按动态作用域赋值，不能和调用方的变量同名）
choose() {
  local __out="$1" __key="$2" __prompt="$3" __default="${4:-}" __value
  if [[ -v ANS[$__key] ]]; then __value="${ANS[$__key]}"
  elif [[ $PHASE == run ]]; then
    __value="$__default"
    info "没有事先回答「$__prompt」，按默认：${__default:-（空）}"
  else
    __value="$(ask "$__prompt" "$__default")"
    ANS[$__key]="$__value"
  fi
  printf -v "$__out" '%s' "$__value"
}

# 后台进程还在运行吗（状态是运行中，并且记下的进程还在）
run_alive() {
  local status pid started
  status="$(state_get status)"
  case "$status" in starting | running | waiting_setup) ;; *) return 1 ;; esac
  pid="$(state_get pid)"
  if [[ $pid =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null && grep -qa deploy.sh "/proc/$pid/cmdline" 2>/dev/null; then return 0; fi
  started="$(state_get started)"
  [[ $status == starting && $started =~ ^[0-9]+$ ]] && (( $(date +%s) - started < 60 ))
}

have_systemd() { (( EUID == 0 )) && command -v systemd-run >/dev/null && [[ -d /run/systemd/system ]]; }

# 部署、升级、接入要改系统（装 Docker、写 /opt、防火墙）：不是 root 时用 sudo 重新运行。后台阶段没有终端，不能再输入 sudo 密码
ensure_root() {
  (( EUID == 0 )) && return 0
  command -v sudo >/dev/null || die "需要 root：用 root 运行，或安装 sudo"
  info "需要 root 权限：用 sudo 重新运行（后台阶段不能再输入 sudo 密码）"
  exec sudo bash "$ROOT/scripts/deploy.sh" "$ACTION" "${ORIG_ARGS[@]}"
}

# 以前的版本（T-0092 / T-0093）把部署放在 tmux 会话里：还在的话先处理完，避免同时部署两次
legacy_tmux_session() {
  [[ -z ${TMUX:-}${WORKSPACE_IN_TMUX:-} ]] || return 1
  command -v tmux >/dev/null && tmux -L my-workspace has-session -t deploy 2>/dev/null
}

# 显示安装凭据（只显示在终端，不写进日志）
setup_box() {
  local base token
  base="$(env_get PUBLIC_BASE_URL)"; token="$(env_get SETUP_TOKEN)"
  step "现在去浏览器创建管理员"
  info "打开 ${base:-工作台的地址}，进入「首次安装」页"
  [[ -n $token ]] && info "安装凭据：$token"
  info "建好管理员后，后台几秒内会自动检测到并继续（不需要回到这里按回车）"
}

# 启动后台阶段：保存回答 → systemd-run（或 setsid nohup）→ 前台显示日志
launch_run() {
  local script="$ROOT/scripts/deploy.sh" log args=() offset name envs=()
  deploy_dir_init
  answers_save
  log="$(deploy_log)"
  if [[ -f $log ]] && (( $(stat -c %s "$log") > 5 * 1024 * 1024 )); then mv -f "$log" "$log.1"; fi
  touch "$log" && chmod 600 "$log"
  offset="$(stat -c %s "$log")"
  : >"$(todo_file)" && chmod 600 "$(todo_file)"
  rm -f "$(state_file)"
  state_set action "$ACTION" status starting step "启动后台任务" started "$(date +%s)" log_start "$offset"
  args=("$ACTION" --dir "$ROOT" --run --answers "$(answers_file)")
  [[ -n $REF ]] && args+=(--ref "$REF")
  (( ASSUME_YES )) && args+=(--yes)
  if have_systemd; then
    for name in "${RUN_ENV_VARS[@]}"; do [[ -n ${!name:-} ]] && envs+=(--setenv "$name=${!name}"); done
    systemctl reset-failed "$RUN_UNIT" >/dev/null 2>&1 || true
    systemd-run --quiet --unit "$RUN_UNIT" --description "My Workspace 部署（$ACTION）" --collect \
      --property "StandardOutput=append:$log" --property "StandardError=append:$log" \
      --working-directory "$ROOT" "${envs[@]}" /bin/bash "$script" "${args[@]}" \
      || die "没能启动后台任务（systemd-run）：用 --foreground 在当前终端运行"
    state_set runner "systemd:$RUN_UNIT"
  else
    setsid nohup /bin/bash "$script" "${args[@]}" </dev/null >>"$log" 2>&1 &
    state_set runner "pid:$!"
  fi
  follow_run
}

# 前台显示后台任务的日志，直到结束后按任务结果退出（成功 0、失败 1）；Ctrl+C 只是不看了。
# 等网页首次安装时在终端显示安装凭据（不写进日志）
follow_run() {
  local log start status shown=0 tail_pid code=0 waited=0
  log="$(deploy_log)"; start="$(state_get log_start)"; [[ $start =~ ^[0-9]+$ ]] || start=0
  step "部署在后台运行：SSH 断开、关掉终端都不影响"
  info "按 Ctrl+C 只是不看了；之后查看：bash $ROOT/scripts/deploy.sh status（进度） / bash $ROOT/scripts/deploy.sh logs（日志）"
  tail -c "+$((start + 1))" -F "$log" 2>/dev/null &
  tail_pid=$!
  trap 'kill "$tail_pid" 2>/dev/null; printf "\n" >&2; info "已停止显示，部署在后台继续：bash $ROOT/scripts/deploy.sh status"; exit 0' INT TERM
  while :; do
    sleep 2
    status="$(state_get status)"
    if [[ $status == waiting_setup ]] && (( ! shown )); then sleep 1; setup_box; shown=1; fi
    [[ $status == waiting_setup ]] || shown=0
    case "$status" in done | failed) break ;; esac
    if ! run_alive; then
      waited=$((waited + 1))
      (( waited >= 3 )) && { status=interrupted; break; }
    else
      waited=0
    fi
  done
  sleep 1
  kill "$tail_pid" 2>/dev/null; wait "$tail_pid" 2>/dev/null || true
  trap - INT TERM
  case "$status" in
    done) ok "后台任务已完成" ;;
    failed) warn "后台任务失败：$(state_get error)"; info "处理后重新运行同样的命令（会沿用这次的回答，已完成的步骤很快跳过）"; code=1 ;;
    *) warn "后台任务意外结束（服务器重启或被停止？）：最后一步是「$(state_get step)」"
       info "重新运行同样的命令继续；日志：bash $ROOT/scripts/deploy.sh logs"; code=1 ;;
  esac
  exit "$code"
}

# 后台阶段的入口（--run）：同一时间只运行一个；结束时记下成功或失败与原因、汇总待办
run_entry() {
  deploy_dir_init
  exec 9>"$(deploy_dir)/lock"
  if command -v flock >/dev/null && ! flock -n 9; then die "已经有一个部署任务在运行"; fi
  answers_load "$(answers_file)"
  state_set pid "$$" status running
  trap 'run_finish $?' EXIT
  step "后台任务开始：$ACTION（$(date '+%F %T')，代码 $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo 未知)）"
}

run_finish() {
  local code="$1" f
  trap - EXIT
  f="$(todo_file)"
  if [[ -s $f ]]; then
    step "需要你处理的事项"
    while IFS= read -r line; do info "- $line"; done <"$f"
  fi
  if (( code == 0 )); then
    state_set status "done" finished "$(date +%s)"
    step "全部完成（$(date '+%F %T')）"
  else
    state_set status failed finished "$(date +%s)" error "${LAST_ERROR:-第「$(state_get step)」步出错，见日志}"
  fi
  exit "$code"
}

# 这个终端真正的设备（/dev/pts/N）。curl | bash 时脚本的标准输入是管道：克隆后用它重新启动脚本，菜单与提问才能读输入
# （T-0093：比 /dev/tty 更可靠，tmux 等程序拒绝以 /dev/tty 打开的终端）
real_tty() {
  local fd dev
  for fd in 0 1 2; do
    dev="$(readlink "/proc/$$/fd/$fd" 2>/dev/null)" || continue
    [[ $dev == /dev/pts/* || $dev =~ ^/dev/tty(S)?[0-9]+$ ]] && [[ -r $dev && -w $dev ]] && { printf '%s\n' "$dev"; return 0; }
  done
  dev="$(ps -o tty= -p $$ 2>/dev/null | tr -d ' ')"
  [[ -n $dev && $dev != "?" && -r /dev/$dev && -w /dev/$dev ]] && { printf '/dev/%s\n' "$dev"; return 0; }
  return 1
}

# 把命令行参数原样传给重新启动的脚本（args 由调用方声明）
forward_args() {
  [[ -n $ACTION ]] && args+=("$ACTION")
  args+=(--dir "${ROOT:-${DIR:-$DEFAULT_DIR}}")
  [[ -n $REF ]] && args+=(--ref "$REF")
  [[ -n $DOMAIN ]] && args+=(--domain "$DOMAIN")
  [[ -n $EMAIL ]] && args+=(--email "$EMAIL")
  [[ -n $BASE_URL ]] && args+=(--base-url "$BASE_URL")
  (( ASSUME_YES )) && args+=(--yes)
  [[ -n $CONFIRM ]] && args+=(--confirm "$CONFIRM")
  [[ -n $ENV_CHOICE ]] && args+=("$ENV_CHOICE")
  [[ -n $DIR_CHOICE ]] && args+=("$DIR_CHOICE")
  [[ -n $AFTER_PULL ]] && args+=(--after-pull "$AFTER_PULL")
  (( FOREGROUND )) && args+=(--foreground)
  [[ -n $ANSWERS_PATH ]] && args+=(--answers "$ANSWERS_PATH")
  return 0
}

# 拉取镜像：网络不稳时重试（已经下载的层不会重新下载）；三次都失败时返回 1，
# 后面的 make up 会再试一次并给出具体的错误
PULL_ATTEMPTS=3
PULL_PAUSE=15
pull_images() {
  local target="$1" label="$2" i log
  log="$(mktemp)"
  for ((i = 1; i <= PULL_ATTEMPTS; i++)); do
    if (cd "$ROOT" && make -s "$target" </dev/null) 2>&1 | tee "$log"; then rm -f "$log"; return 0; fi
    if grep -qiE "toomanyrequests|429 Too Many Requests" "$log"; then
      # Docker Hub 匿名拉取的次数限制：马上重试没有用
      rm -f "$log"
      warn "Docker Hub 限制了匿名拉取次数（429 toomanyrequests）：等一小时，或 docker login 后重新运行"
      return 1
    fi
    (( i < PULL_ATTEMPTS )) || break
    warn "拉取 $label 的镜像没有成功（第 $i 次，见上面的输出）；${PULL_PAUSE} 秒后重试，已经下载的部分不会重新下载"
    sleep "$PULL_PAUSE"
  done
  rm -f "$log"
  warn "拉取 $label 的镜像失败了 $PULL_ATTEMPTS 次（见上面的输出）：检查网络后重新运行"
  return 1
}

# ---------------------------------------------------------------- .env 读写（保留其他行与注释）
env_get() {
  [[ -f $ROOT/.env ]] || return 0
  sed -n "s/^$1=//p" "$ROOT/.env" | tail -1 | tr -d '\r'
}
env_set() {
  "$PY" - "$ROOT/.env" "$1" "$2" <<'PY'
import pathlib, re, sys
path, key, value = sys.argv[1:4]
p = pathlib.Path(path)
lines = p.read_text(encoding="utf-8").splitlines()
exact = re.compile(rf"^{re.escape(key)}=")
commented = re.compile(rf"^#\s*{re.escape(key)}=")
for pattern in (exact, commented):
    hit = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
    if hit is not None:
        lines[hit] = f"{key}={value}"
        break
else:
    lines.append(f"{key}={value}")
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
p.chmod(0o600)
PY
}

compose() { (cd "$ROOT" && docker compose "$@"); }
http_port() { local p; p="$(env_get HTTP_PORT)"; printf '%s\n' "${p:-8080}"; }

# ---------------------------------------------------------------- 检查与安装依赖
# shellcheck source=/dev/null
os_id() { (. /etc/os-release 2>/dev/null && printf '%s\n' "${ID:-unknown}") || echo unknown; }
apt_based() { command -v apt-get >/dev/null; }

ensure_tools() {
  step "检查工具（git、make、curl、Python 3.12）"
  local missing=() cmd
  for cmd in git make curl tar sha256sum; do command -v "$cmd" >/dev/null || missing+=("$cmd"); done
  if ((${#missing[@]})); then
    apt_based || die "缺少 ${missing[*]}，当前系统（$(os_id)）不是 apt 系，请先手动安装"
    confirm "缺少 ${missing[*]}，现在用 apt 安装？" y || die "请先安装 ${missing[*]}"
    as_root apt-get update -qq
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git make curl ca-certificates tar coreutils >/dev/null
  fi
  ensure_python
  ok "工具齐全（git、make、curl；Python $(py_version "$PY")：$PY）"
}

# ---------------------------------------------------------------- Python 3.12（T-0074）
# 服务器上的项目脚本（make env、备份）用 Python 3.12，与 CI、api 镜像是同一个小版本。
# 系统自带的 python3 不替换（apt 等系统工具依赖它）。没有可用的 python3.12 时，安装 python-build-standalone
# 的独立构建到 /opt/my-workspace/python-<版本>，并链接为 /usr/local/bin/python3.12。
# 版本与校验和的来源见 docs/decisions/2026-11-02-host-python-3-12.md；升级时这四行一起改。
PY_VERSION="3.12.16"
PY_BUILD="20261026"
PY_SHA256_X86_64="ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20"
PY_SHA256_AARCH64="bb4b6392670c5d3f93876996fcaa19858a4a413a0c7720144c18b0d6f1f0db2d"
PY_PREFIX="/opt/my-workspace"
PY_LINK="/usr/local/bin/python3.12"
PY="python3.12"

py_ok() { "$1" -c 'import sys, json, secrets; sys.exit(sys.version_info[:2] != (3, 12))' >/dev/null 2>&1; }
py_version() { "$1" -c 'import platform; print(platform.python_version())' 2>/dev/null; }
py_managed() { [[ $(readlink -f "$1" 2>/dev/null) == "$PY_PREFIX"/python-*/bin/python3.12 ]]; }

ensure_python() {
  local found current
  for found in "$PY_LINK" "$(command -v python3.12 || true)"; do
    if [[ -z $found ]] || ! py_ok "$found"; then continue; fi
    current="$(py_version "$found")"
    # 系统包或自己装的 3.12 照用；本脚本以前装的旧版本换成锁定的版本
    if py_managed "$found" && [[ $current != "$PY_VERSION" ]]; then
      info "本脚本以前安装的 Python $current 更新为 $PY_VERSION"
      install_python
    else
      PY="$found"
    fi
    return 0
  done
  if command -v python3 >/dev/null; then current="$(python3 --version 2>&1)"; else current="没有 python3"; fi
  info "项目脚本需要 Python 3.12（系统自带：$current）。另行安装，系统自带的 python3 保持不变"
  confirm "安装 Python $PY_VERSION 到 $PY_PREFIX/python-$PY_VERSION？" y || die "请先安装 python3.12 再运行"
  install_python
}

install_python() {
  local arch sha file tmp base dest="$PY_PREFIX/python-$PY_VERSION" old
  if [[ -e $PY_LINK || -L $PY_LINK ]] && ! py_managed "$PY_LINK"; then
    die "$PY_LINK 已存在但不能用（不是 3.12，或缺少标准库模块），也不是本脚本安装的；修好或移走后重试"
  fi
  case "$(uname -m)" in
    x86_64 | amd64) arch=x86_64 sha="$PY_SHA256_X86_64" ;;
    aarch64 | arm64) arch=aarch64 sha="$PY_SHA256_AARCH64" ;;
    *) die "没有 $(uname -m) 架构的 Python 安装包；请手动安装 python3.12 后重试" ;;
  esac
  file="cpython-$PY_VERSION%2B$PY_BUILD-$arch-unknown-linux-gnu-install_only_stripped.tar.gz"
  tmp="$(mktemp -d)"
  info "下载 Python $PY_VERSION（$arch，约 35 MB）"
  for base in https://github.com/astral-sh/python-build-standalone/releases/download \
    https://releases.astral.sh/github/python-build-standalone/releases/download; do
    if curl -fsSL --retry 3 --connect-timeout 15 --max-time 900 -o "$tmp/python.tar.gz" "$base/$PY_BUILD/$file"; then break; fi
    warn "从 ${base#https://} 下载失败"
    rm -f "$tmp/python.tar.gz"
  done
  [[ -s $tmp/python.tar.gz ]] || { rm -rf "$tmp"; die "下载 Python 失败：检查服务器能否访问 github.com"; }
  if ! printf '%s  %s\n' "$sha" "$tmp/python.tar.gz" | sha256sum -c --status -; then
    rm -rf "$tmp"
    die "Python 安装包的 SHA-256 与脚本里锁定的不一致，已停止（没有安装任何东西）"
  fi
  tar -xzf "$tmp/python.tar.gz" -C "$tmp" --no-same-owner
  as_root mkdir -p "$PY_PREFIX"
  as_root rm -rf -- "$dest"
  as_root mv "$tmp/python" "$dest"
  rm -rf "$tmp"
  as_root chown -R 0:0 "$dest"
  as_root chmod -R go-w "$dest"
  as_root ln -sfn "$dest/bin/python3.12" "$PY_LINK"
  py_ok "$PY_LINK" || die "Python $PY_VERSION 安装后不能运行（$PY_LINK）"
  for old in "$PY_PREFIX"/python-*; do
    [[ $old == "$dest" || ! -d $old ]] || as_root rm -rf -- "$old"
  done
  PY="$PY_LINK"
  ok "已安装 Python $(py_version "$PY")（$PY → $dest；系统自带的 python3 没有改动）"
}

ensure_docker() {
  step "检查 Docker 与 Compose v2"
  if ! command -v docker >/dev/null; then
    decide DOCKER_INSTALL "没有安装 Docker，现在用官方脚本（get.docker.com）安装？" y || die "请先安装 Docker（docs/deploy-guide.md 第 2 步）"
    if ! curl -fsSL https://get.docker.com | as_root sh; then
      # 官方脚本默认装最新版，并从 28.2 起加装 docker-model-plugin；已停止支持的发行版（例如 Ubuntu 20.04）
      # 的 Docker 仓库最高只到 28.1，没有这个包，整条安装失败（T-0075）。仓库已经配好时只装核心组件
      if ! apt_based || [[ ! -f /etc/apt/sources.list.d/docker.list ]]; then die "Docker 安装失败（见上面的输出）"; fi
      warn "官方脚本安装失败；Docker 仓库已配好，改为只安装核心组件（不含 docker-model-plugin）"
      as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin >/dev/null \
        || die "Docker 安装失败（见上面的输出）"
    fi
  fi
  if ! docker info >/dev/null 2>&1; then
    as_root systemctl enable --now docker >/dev/null 2>&1 || true
    docker info >/dev/null 2>&1 || die "Docker 没有运行，或当前用户不能使用 Docker（用 root 运行，或把用户加入 docker 组后重新登录）"
  fi
  if ! docker compose version >/dev/null 2>&1; then
    if apt_based && decide COMPOSE_INSTALL "缺少 Compose v2 插件，现在安装 docker-compose-plugin？" y; then
      as_root apt-get install -y -qq docker-compose-plugin >/dev/null
    fi
    docker compose version >/dev/null 2>&1 || die "缺少 docker compose v2（安装 docker-compose-plugin）"
  fi
  ok "$(docker version --format 'Docker {{.Server.Version}}' 2>/dev/null)，$(docker compose version --short 2>/dev/null | sed 's/^/Compose /')"
}

# 已停止常规安全更新的系统：能部署，但提醒换系统（T-0075）
check_os() {
  local id ver name major
  id="$(os_id)"
  ver="$( (. /etc/os-release 2>/dev/null && printf '%s\n' "${VERSION_ID:-}") || true)"
  name="$( (. /etc/os-release 2>/dev/null && printf '%s\n' "${PRETTY_NAME:-$id $ver}") || true)"
  major="${ver%%.*}"
  [[ $major =~ ^[0-9]+$ ]] || return 0
  if { [[ $id == ubuntu ]] && (( major < 22 )); } || { [[ $id == debian ]] && (( major < 12 )); }; then
    warn "$name 已停止常规安全更新，Docker 也会停止为它发布新版本（Ubuntu 20.04 停在 28.1.1）；能部署，但生产环境建议换成 Ubuntu 24.04 或 Debian 12 及以上"
  fi
  return 0
}

check_resources() {
  local free_kb mem_kb
  free_kb="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
  (( free_kb < 10 * 1024 * 1024 )) && warn "磁盘剩余 $((free_kb / 1024 / 1024)) GB，建议至少 10 GB（镜像构建与备份）"
  mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  (( mem_kb > 0 && mem_kb < 1900 * 1024 )) && warn "内存 $((mem_kb / 1024)) MB，建议至少 2 GB"
  return 0
}

# ---------------------------------------------------------------- 获取代码
find_checkout() {
  local src="${BASH_SOURCE[0]:-}" dir
  [[ -n $src && -f $src ]] || return 1
  dir="$(cd "$(dirname "$src")/.." && pwd)"
  [[ -f $dir/compose.yaml && -f $dir/scripts/init_env.py && -f $dir/Makefile ]] || return 1
  printf '%s\n' "$dir"
}

# 用 curl | bash 运行时没有本地代码：克隆后改用克隆下来的脚本继续（stdin 换成终端，菜单才能读输入；
# 用真正的终端设备而不是 /dev/tty，之后才能接回 tmux 会话，T-0093）
bootstrap() {
  local target="${DIR:-$DEFAULT_DIR}"
  ensure_tools
  if [[ -d $target/.git ]]; then
    ok "代码已在 $target"
  else
    [[ -d $target && -n "$(ls -A "$target" 2>/dev/null)" ]] && die "$target 已存在且不是本项目的代码目录，换一个目录（--dir）"
    step "获取代码 → $target"
    as_root mkdir -p "$target"
    (( EUID == 0 )) || as_root chown "$(id -u):$(id -g)" "$target"
    git clone -q --branch "${REF:-main}" "$REPO_URL" "$target"
    ok "已克隆 $(git -C "$target" rev-parse --short HEAD)"
  fi
  local args=()
  DIR="$target" forward_args
  local tty
  if tty="$(real_tty)" && (exec <"$tty") 2>/dev/null; then exec bash "$target/scripts/deploy.sh" "${args[@]}" <"$tty"; fi
  if (exec </dev/tty) 2>/dev/null; then exec bash "$target/scripts/deploy.sh" "${args[@]}" </dev/tty; fi
  exec bash "$target/scripts/deploy.sh" "${args[@]}" </dev/null
}

# ---------------------------------------------------------------- 三个参数
valid_domain() { [[ $1 =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }
valid_email() { [[ $1 =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]]; }

ask_parameters() {
  if [[ $PHASE == run ]]; then
    [[ -n $DOMAIN && -n $EMAIL && -n $BASE_URL ]] || die "回答文件里缺少 SITE_DOMAIN / ACME_EMAIL / PUBLIC_BASE_URL"
    ok "SITE_DOMAIN=$DOMAIN  ACME_EMAIL=$EMAIL  PUBLIC_BASE_URL=$BASE_URL（部署前已确认）"
    return 0
  fi
  step "填写三个参数"
  local current
  current="$(env_get SITE_DOMAIN)"
  while :; do
    DOMAIN="${DOMAIN:-$(ask "SITE_DOMAIN（工作台的域名，例如 workspace.example.com）" "$current")}"
    DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]' | sed -E 's#^https?://##; s#/.*$##')"
    valid_domain "$DOMAIN" && break
    warn "「$DOMAIN」不是有效的域名"; DOMAIN=""; (( ASSUME_YES )) && die "域名无效"
  done
  current="$(env_get ACME_EMAIL)"
  while :; do
    EMAIL="${EMAIL:-$(ask "ACME_EMAIL（Let's Encrypt 证书联系邮箱）" "$current")}"
    valid_email "$EMAIL" && break
    warn "「$EMAIL」不是有效的邮箱"; EMAIL=""; (( ASSUME_YES )) && die "邮箱无效"
  done
  current="$(env_get PUBLIC_BASE_URL)"
  [[ $current == "https://$DOMAIN" ]] || current="https://$DOMAIN"
  while :; do
    BASE_URL="${BASE_URL:-$(ask "PUBLIC_BASE_URL（浏览器访问的地址，必须和地址栏完全一致）" "$current")}"
    BASE_URL="${BASE_URL%/}"
    if [[ ! $BASE_URL =~ ^https://[^/[:space:]]+$ ]]; then
      warn "PUBLIC_BASE_URL 要以 https:// 开头、不带路径"; BASE_URL=""; (( ASSUME_YES )) && die "PUBLIC_BASE_URL 无效"; continue
    fi
    if [[ $BASE_URL != "https://$DOMAIN" ]]; then
      warn "PUBLIC_BASE_URL（$BASE_URL）和域名（https://$DOMAIN）不同：登录会因为地址不一致被拒绝（403 CSRF_FAILED）"
      confirm "确定使用 $BASE_URL？" n || { BASE_URL=""; continue; }
    fi
    break
  done
  ok "SITE_DOMAIN=$DOMAIN  ACME_EMAIL=$EMAIL  PUBLIC_BASE_URL=$BASE_URL"
}

check_dns() {
  step "检查域名解析"
  local resolved local_ips
  resolved="$( (getent ahosts "$DOMAIN" 2>/dev/null || true) | awk '{print $1}' | sort -u | tr '\n' ' ')"
  if [[ -z $resolved ]]; then
    warn "$DOMAIN 还没有解析。先在域名服务商那里加 A 记录指向本机公网 IP，否则证书申请会失败"
    [[ $PHASE == run ]] && return 0
    decide DNS_CONTINUE "仍然继续？（服务能启动，证书等解析生效后会自动申请）" n || die "请先配置 DNS"
    return 0
  fi
  local_ips="$(hostname -I 2>/dev/null || true)"
  local ip match=0 outbound
  for ip in $resolved; do [[ " $local_ips " == *" $ip "* ]] && match=1; done
  outbound="$( (ip -4 route get 1.1.1.1 2>/dev/null || true) | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')"
  if (( match )); then ok "$DOMAIN → $resolved（是本机地址）"
  else
    warn "$DOMAIN → $resolved，本机出口地址是 ${outbound:-（未知）}"
    info "云服务器的公网 IP 通常不在网卡上，这种情况可以忽略；否则请检查 A 记录"
  fi
}

check_ports() {
  step "检查 80 / 443 端口"
  local port line busy=0
  for port in 80 443; do
    line="$(ss -Hltnp "sport = :$port" 2>/dev/null | head -1 || true)"
    [[ -z $line ]] && continue
    [[ $line == *docker-proxy* ]] && docker ps --format '{{.Names}} {{.Ports}}' | grep -q "^$PROJECT-caddy-1 .*:$port->" && continue
    warn "端口 $port 已被占用：$line"; busy=1
  done
  (( busy )) && die "先停掉占用 80 / 443 的程序（例如 systemctl disable --now nginx apache2），Caddy 需要这两个端口申请证书"
  ok "80 / 443 可用"
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    if ! ufw status | grep -Eq '^(80|443)(/tcp)?\b.*ALLOW'; then
      if decide UFW_WEB "ufw 防火墙已启用但没有放行 80 / 443，部署时放行？" y && [[ $PHASE != plan ]]; then
        as_root ufw allow 80/tcp >/dev/null && as_root ufw allow 443 >/dev/null && ok "ufw 已放行 80 / 443"
      fi
    fi
  fi
  info "云服务器还要在控制台的安全组里放行 80 和 443（不要开放 8080）"
}

# ---------------------------------------------------------------- .env
postgres_volume_exists() { docker volume inspect "${PROJECT}_postgres-data" >/dev/null 2>&1; }

# 新生成的 .env 有新的 BACKUP_PASSWORD；之前的备份是用旧密码加密的，换了密码 make backup 会失败、也无法恢复。
# 有之前留下的 .env 副本（清空、重新生成、升级时保存的）就沿用其中的 BACKUP_PASSWORD
backup_password_of() { sed -n 's/^BACKUP_PASSWORD=//p' "$1" | tail -1 | tr -d '\r'; }

# 最新的、带可用 BACKUP_PASSWORD 的 .env 副本（参数是额外的候选，例如即将被备份的 .env）
backup_password_candidate() {
  local candidates=("$@") file old _
  for file in "$ROOT"/.env.bak-* "$ROOT"/backups/env-*; do [[ -f $file ]] && candidates+=("$file"); done
  ((${#candidates[@]})) || return 1
  while read -r _ file; do
    old="$(backup_password_of "$file")"
    if [[ -n $old && $old != GENERATE_* && $old != CHANGE_ME ]]; then printf '%s\n' "$file"; return 0; fi
  done < <(stat -c '%Y %n' "${candidates[@]}" | sort -rn)
  return 1
}

reuse_backup_password() {
  local saved old
  saved="$(backup_password_candidate)" || return 0
  old="$(backup_password_of "$saved")"
  if decide REUSE_BACKUP_PASSWORD "找到之前的 .env（$saved）。沿用其中的 BACKUP_PASSWORD？之前的备份用它加密，沿用才能继续备份到同一个仓库、并从中恢复" y; then
    env_set BACKUP_PASSWORD "$old"
    ok "已沿用之前的 BACKUP_PASSWORD"
  else
    warn "新 .env 的 BACKUP_PASSWORD 和之前的备份不同：往原来的仓库备份会失败；不需要旧备份的话把 backups/restic 改名后重新备份"
  fi
}

prepare_env() {
  step "生成并检查 .env"
  if [[ ! -f $ROOT/.env ]]; then
    (cd "$ROOT" && "$PY" scripts/init_env.py >/dev/null)
    ok "已生成 .env（随机密码与密钥，权限 600）"
    reuse_backup_password
  elif ! (cd "$ROOT" && "$PY" scripts/init_env.py >/dev/null 2>&1); then
    (cd "$ROOT" && "$PY" scripts/init_env.py >/dev/null) || true
    warn ".env 有上面列出的问题（多半是从 .env.example 复制的，占位符没有替换）"
    decide REGENERATE_ENV "备份当前 .env 并重新生成？（新的随机密钥；数据库密码之后会自动同步）" y || die "请按提示修改 .env 后重试"
    local backup
    backup="$ROOT/.env.bak-$(date +%Y%m%d%H%M%S)"
    mv "$ROOT/.env" "$backup" && chmod 600 "$backup"
    (cd "$ROOT" && "$PY" scripts/init_env.py >/dev/null)
    ok "已重新生成 .env（旧文件：$backup）"
    reuse_backup_password
  else
    ok ".env 已存在，保留原有密钥"
  fi
  env_set APP_ENV production
  env_set COMPOSE_FILE compose.yaml:compose.https.yaml
  env_set SITE_DOMAIN "$DOMAIN"
  env_set ACME_EMAIL "$EMAIL"
  env_set PUBLIC_BASE_URL "$BASE_URL"
  env_set HTTP_BIND 127.0.0.1
  (cd "$ROOT" && "$PY" scripts/init_env.py >/dev/null) || die ".env 检查没有通过（见上面的说明）"
  ok "生产模式、HTTPS（compose.https.yaml）、域名与邮箱已写入 .env"
}

# ---------------------------------------------------------------- 启动、迁移、检查
start_services() {
  step "构建并启动服务（make up，首次需要几分钟）"
  pull_images pull "工作台" || true
  local log
  log="$(mktemp)"
  if (cd "$ROOT" && make up) 2>&1 | tee "$log"; then rm -f "$log"; return 0; fi
  if grep -q "Pool overlaps" "$log"; then
    warn "Docker 网段冲突：本项目的 im / agent-ingress 网络默认用 172.31.252.0/24、172.31.250.0/24，已被别的网络占用"
    info "在 $ROOT/.env 里把 IM_NET、AGENT_INGRESS_NET 改成未使用的网段（例如 IM_NET=172.31.242、AGENT_INGRESS_NET=172.31.240）后重新运行"
  fi
  rm -f "$log"
  die "make up 失败：运行 docker compose ps -a 与 docker compose logs api 查看原因"
}

sync_db_password() {
  local user db
  user="$(env_get POSTGRES_USER)"; db="$(env_get POSTGRES_DB)"
  user="${user:-workspace}"; db="${db:-workspace}"
  # 数据卷只在第一次初始化时读取密码：把数据库里的密码改成 .env 里的（容器内本地连接，不需要旧密码，数据不变）
  printf "ALTER USER \"%s\" WITH PASSWORD '%s';\n" "$user" "$(env_get POSTGRES_PASSWORD)" \
    | compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$user" -d "$db" >/dev/null
}

migrate() {
  step "迁移数据库（make migrate）"
  local log
  log="$(mktemp)"
  if (cd "$ROOT" && make migrate) >"$log" 2>&1; then
    grep -E "Running upgrade" "$log" | sed 's/^.*Running upgrade/      升级/' >&2 || true
    rm -f "$log"; ok "数据库已是最新版本"; return
  fi
  if grep -q "password authentication failed" "$log"; then
    warn "数据库里的密码和 .env 不一致（数据卷是用旧密码初始化的），自动同步为 .env 里的密码"
    sync_db_password
    (cd "$ROOT" && make migrate) >"$log" 2>&1 || { tail -20 "$log" >&2; rm -f "$log"; die "迁移失败"; }
    rm -f "$log"; ok "已同步数据库密码，迁移完成"; return
  fi
  tail -30 "$log" >&2; rm -f "$log"
  die "迁移失败（见上面的输出）"
}

smoke() {
  step "冒烟检查（make smoke）"
  (cd "$ROOT" && make smoke) || die "冒烟检查没有通过"
}

check_https() {
  step "检查 HTTPS（$BASE_URL/healthz）"
  local code="" _
  for _ in $(seq 1 24); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/healthz" 2>/dev/null || true)"
    [[ $code == 200 ]] && { ok "证书已签发，HTTPS 正常"; return 0; }
    sleep 5
  done
  warn "两分钟内没能通过 HTTPS 访问（最后的状态码：${code:-无}）"
  info "常见原因：域名还没解析到本机；80 / 443 没在防火墙或云安全组放行；Let's Encrypt 限流"
  info "排查：docker compose logs caddy --tail 50；解决后不用重装，Caddy 会自动重试"
  return 0
}

show_setup() {
  local status
  status="$(curl -sS --max-time 10 "http://127.0.0.1:$(http_port)/api/v1/setup/status" 2>/dev/null || true)"
  if [[ $status == *'"setup_required":true'* ]]; then
    step "下一步：创建管理员"
    info "浏览器打开 $BASE_URL ，进入「首次安装」页"
    if [[ $PHASE == run ]]; then info "安装凭据不写进日志：运行 bash $ROOT/scripts/deploy.sh status 查看"
    else info "安装凭据：$(cd "$ROOT" && make -s setup-token)"; fi
    info "装好后把 $ROOT/.env 里 SETUP_TOKEN= 后面的值清空，再在 $ROOT 里运行 make up"
  else
    ok "已经完成首次安装，直接用管理员账号登录 $BASE_URL"
  fi
}

remind_backup_env() {
  info "请把 $ROOT/.env 备份到服务器以外的地方（密码管理器）：其中的 CREDENTIAL_ENCRYPTION_KEY、BACKUP_PASSWORD 丢了就无法解密凭证、打开备份"
}

# ---------------------------------------------------------------- 接入组件（T-0076）
# 管理员在浏览器里创建好之后，自动接入 n8n（项目与任务由工作台自己管理，T-0082）。真正的接入在 api 容器里由
# app/commands/connect.py 完成（和网页同一套服务：凭证加密保存、写审计、要求管理员角色）。
# 密码只经标准输入传给容器，不出现在命令行参数里。
# GitHub 在工作台「集成设置」页面上接入（T-0091）：生成 Token 的链接、读取可访问的仓库都在网页上，这里只显示状态。

connect_cmd() { compose exec -T api python -m app.commands.connect "$@"; }
# 不需要输入的调用都接上 /dev/null：docker compose exec -T 会读走终端里提前敲下的回答
connect_status() { connect_cmd status </dev/null 2>/dev/null || true; }
status_of() { sed -n "s/^$1=//p" <<<"$2" | head -1; }
public_url_of() { local v; v="$(env_get "$1")"; v="${v:-$2}"; printf '%s\n' "${v%/}"; }

# n8n 的密码规则：8 ~ 64 位，至少一个数字和一个大写字母
random_password() {
  "$PY" -c 'import secrets, string; a = string.ascii_letters + string.digits; print("Ws" + "".join(secrets.choice(a) for _ in range(20)) + "7")'
}

ask_secret() {
  local answer=""
  if (( ASSUME_YES )) || ! interactive; then printf '\n'; return; fi
  read -r -s -p "$1: " answer || true
  printf '\n' >&2
  printf '%s\n' "$answer"
}

show_credential() {
  warn "$1 的登录信息如下，建议存进密码管理器；工作台「$6」页也能查看（只有管理员看得到）："
  info "  地址：$2"
  info "  账号：$3"
  if [[ $PHASE == run ]]; then info "  密码：已加密保存在工作台「$6」页（日志里不显示）"; else info "  密码：$4"; fi
  info "  管理界面只绑定服务器本机，从自己的电脑访问用 SSH 隧道：ssh -L $5:127.0.0.1:$5 <用户>@<服务器>"
}

# 集成状态（app/schemas/integrations.py 的 IntegrationStatus；none 是 connect status 表示还没有集成）的中文说明
status_label() {
  case "$1" in
    active) echo "已接入" ;;
    none | "") echo "未接入" ;;
    pending) echo "已保存，还没通过测试连接" ;;
    unauthorized) echo "凭证失效" ;;
    unavailable) echo "暂时连不上" ;;
    disabled) echo "已停用" ;;
    *) echo "状态未知（$1）" ;;
  esac
}

# 已经接入过的组件：active 才算通过，其他状态提示到工作台对应页面处理
show_state() {
  if [[ $1 == active ]]; then ok "已接入"
  else warn "已保存但现在不可用：$(status_label "$1")。到工作台「$2」页查看原因、重新测试连接"
  fi
}

# 已经接入、但工作台还没保存登录账号（T-0080 之前接入的）：问一次要不要补上。先登录一次核对密码
offer_save_login() {
  local app="$1" title="$2" page="$3" saved="$4" user_prompt="$5" default_user="$6" user password
  [[ $saved == none ]] || return 0
  if [[ $PHASE == run ]] || (( ASSUME_YES )) || ! interactive; then return 0; fi
  confirm "工作台还没有保存 $title 的登录账号，现在保存吗？（管理员可以在「$page」页查看、复制）" y || return 0
  user="$(ask "$user_prompt" "$default_user")"
  password="$(ask_secret "$title 的密码（输入不显示，直接回车跳过）")"
  [[ -n $password ]] || { info "跳过"; return 0; }
  if printf '%s\n' "$password" | connect_cmd save-login --app "$app" --username "$user"; then
    ok "已保存：到工作台「$page」页查看"
  else
    warn "没有保存（见上面的输出）：也可以直接在工作台「$page」页填写"
  fi
}

wait_for_setup() {
  local status answer
  [[ $PHASE == run ]] && { wait_for_setup_run; return; }
  while :; do
    status="$(connect_status)"
    [[ -n $status ]] || { warn "读不到工作台的状态（api 容器没有运行？）"; return 1; }
    [[ $(status_of setup "$status") == "done" ]] && return 0
    if (( ASSUME_YES )) || ! interactive; then
      info "还没有创建管理员：在浏览器完成首次安装后，运行 bash $ROOT/scripts/deploy.sh connect 接入组件"
      return 1
    fi
    info "接入组件之前，先在浏览器打开 ${BASE_URL:-工作台} 完成「首次安装」创建管理员（安装凭据见上面）"
    read -r -p "创建好后按回车继续（输入 s 跳过，以后运行 deploy.sh connect）: " answer || answer=s
    [[ $answer =~ ^[Ss] ]] && { info "已跳过；以后运行 bash $ROOT/scripts/deploy.sh connect"; return 1; }
  done
}

# 后台：每几秒检测一次网页上的首次安装是否完成，最多等 24 小时；安装凭据不写进日志（前台和 status 会显示）
wait_for_setup_run() {
  local status waited=0 next=600 tries
  # api 可能刚重启：读不到状态时再试 2 分钟，还是不行就列为待办（不让接入悄悄地被跳过）
  for ((tries = 0; tries < 24; tries++)); do
    status="$(connect_status)"
    [[ -n $status ]] && break
    sleep 5
  done
  if [[ -z $status ]]; then
    todo_add "读不到工作台的状态（api 没有正常运行？），没有接入组件：确认 bash $ROOT/scripts/deploy.sh status 正常后运行 bash $ROOT/scripts/deploy.sh connect"
    return 1
  fi
  [[ $(status_of setup "$status") == "done" ]] && return 0
  step "等待在浏览器完成「首次安装」（创建管理员）"
  state_set status waiting_setup
  info "打开 ${BASE_URL:-工作台} 进入「首次安装」页；安装凭据不写进日志，运行 bash $ROOT/scripts/deploy.sh status 查看"
  info "建好管理员后几秒内自动继续；最多等 24 小时"
  while (( waited < SETUP_WAIT_SECONDS )); do
    sleep "$SETUP_POLL"
    waited=$((waited + SETUP_POLL))
    status="$(connect_status)"
    if [[ $(status_of setup "$status") == "done" ]]; then
      state_set status running
      ok "已经创建管理员，继续接入组件"
      return 0
    fi
    if (( waited >= next )); then info "仍在等待创建管理员（已等 $((waited / 60)) 分钟）"; next=$((next + 600)); fi
  done
  state_set status running
  todo_add "还没有创建管理员：在浏览器完成「首次安装」后，运行 bash $ROOT/scripts/deploy.sh connect 接入组件"
  return 1
}

connect_github() {
  local state="$1" page
  step "GitHub（只读：Issues、Pull requests、Actions）"
  page="$(public_url_of PUBLIC_BASE_URL "http://localhost:$(http_port)")/settings/integrations"
  if [[ -n $state && $state != none ]]; then
    show_state "$state" "集成设置"
  else
    info "还没有接入。GitHub 在工作台网页上接入：管理员打开「管理 → 集成设置」（$page），"
    info "点「新建 GitHub 连接」，按页面上的步骤生成只读 Token、勾选要同步的仓库，保存后会自动检查权限并开始同步"
  fi
  return 0
}

connect_n8n() {
  local state="$1" login="${2:-}" email password created=0 url owner
  step "n8n（自动化，可选）"
  if [[ -n $state && $state != none ]]; then
    show_state "$state" "自动化"
    offer_save_login n8n n8n 自动化 "$login" "n8n owner 的登录邮箱" "$(env_get ACME_EMAIL)"
    return 0
  fi
  decide N8N "部署 n8n 并自动接入？（另外占用约 1 GB 内存）" y || { info "跳过"; return 0; }
  pull_images n8n-pull "n8n" || true
  (cd "$ROOT" && make -s n8n-up </dev/null) || { warn "n8n 没有启动（见上面的输出），跳过"; return 0; }
  url="$(public_url_of N8N_PUBLIC_URL http://localhost:5678)"
  owner="$(connect_cmd n8n-owner </dev/null 2>/dev/null || true)"
  if [[ $owner == owner=exists && $PHASE == run ]]; then
    todo_add "n8n 已经有 owner 账号，接入要它的密码：在终端运行 bash $ROOT/scripts/deploy.sh connect（当场输入，不保存）"
    return 0
  elif [[ $owner == owner=exists ]]; then
    email="$(ask "n8n 已经有 owner 账号，输入它的邮箱" "$(env_get ACME_EMAIL)")"
    password="$(ask_secret "n8n owner 的密码（直接回车跳过）")"
    [[ -n $password ]] || { info "跳过 n8n"; return 0; }
  elif [[ $owner == owner=none ]]; then
    choose email N8N_EMAIL "n8n owner 账号的邮箱" "$(env_get ACME_EMAIL)"
    password="$(random_password)"
    created=1
  else
    warn "读不到 n8n 的状态，跳过"; return 0
  fi
  if printf '%s\n' "$password" | connect_cmd n8n --owner-email "$email" --public-url "$url"; then
    ok "n8n 已接入：工作流的 Webhook 节点选 Header Auth 凭据 X-Workspace-Trigger，再到工作台「自动化」页登记"
  else
    warn "n8n 没有接入成功（见上面的输出）：修好后重新运行 deploy.sh connect，或到工作台「自动化」页手动配置"
  fi
  (( created )) && show_credential "n8n" "$url" "$email" "$password" "$(env_get N8N_PORT | grep . || echo 5678)" "自动化"
  return 0
}

# ---------------------------------------------------------------- n8n 的对外子域名（T-0078）
# 默认只在服务器本机开放（SSH 隧道）。设置 N8N_DOMAIN 后，在 infra/caddy/apps-enabled/ 生成 Caddy 站点
# （写入具体域名），Caddy 自动申请证书并转发到 integrations 网络里的容器；同时改公开地址、更新工作台里的打开链接。
APPS_DIR="infra/caddy/apps-enabled"
app_site_file() { printf '%s\n' "$ROOT/$APPS_DIR/$1.caddy"; }

write_app_site() {
  local name="$1" domain="$2" upstream="$3"
  mkdir -p "$ROOT/$APPS_DIR"
  cat >"$(app_site_file "$name")" <<EOF
# 由 scripts/deploy.sh connect 生成（T-0078）：$name 的对外入口。删除本文件并重启 caddy 即取消对外
$domain {
	encode zstd gzip
	reverse_proxy $upstream
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options nosniff
		Referrer-Policy strict-origin-when-cross-origin
		-Server
	}
}
EOF
}

# choose_domain <名称> <显示名> <变量> <建议> → 选定的域名（空 = 不对外）；无人值守时沿用 .env 里的设置
choose_domain() {
  local title="$2" key="$3" suggest="$4" current answer=""
  current="$(env_get "$key")"
  if [[ -v ANS[$key] ]]; then printf '%s\n' "${ANS[$key]}"; return; fi
  if [[ $PHASE == run ]] || (( ASSUME_YES )) || ! interactive; then printf '%s\n' "$current"; return; fi
  if [[ -n $current ]]; then
    answer="$(ask "$title 的对外域名（回车保持 $current；输入 - 取消对外）" "$current")"
    [[ $answer == "-" ]] && answer=""
  elif confirm "用子域名经 HTTPS 对外打开 $title？（登录页会暴露在公网；不打开时只能经 SSH 隧道访问）" n; then
    answer="$(ask "$title 的对外域名（先在域名服务商那里把它解析到本机）" "$suggest")"
  fi
  printf '%s' "$answer" | tr '[:upper:]' '[:lower:]' | sed -E 's#^https?://##; s#/.*$##'
  printf '\n'
}

# apply_app_domain <名称> <显示名> <域名变量> <地址变量> <本机地址> <上游> <域名> → 0 表示有变化
apply_app_domain() {
  local name="$1" title="$2" key="$3" url_key="$4" local_url="$5" upstream="$6" domain="$7" file
  file="$(app_site_file "$name")"
  if [[ -z $domain ]]; then
    [[ -n $(env_get "$key") || -f $file ]] || return 1
    rm -f "$file"
    env_set "$key" ""
    env_set "$url_key" "$local_url"
    info "$title 取消对外：只能在服务器本机访问（SSH 隧道）"
    return 0
  fi
  if ! valid_domain "$domain" || [[ $domain == "$(env_get SITE_DOMAIN)" ]]; then
    warn "「$domain」不能用作 $title 的域名（要是一个单独的子域名），保持原样"
    return 1
  fi
  if [[ -f $file ]] && grep -qx "$domain {" "$file" && [[ $(env_get "$key") == "$domain" ]]; then return 1; fi
  write_app_site "$name" "$domain" "$upstream"
  env_set "$key" "$domain"
  env_set "$url_key" "https://$domain/"
  [[ -n "$( (getent ahosts "$domain" 2>/dev/null || true) | head -1)" ]] \
    || warn "$domain 还没有解析：在域名服务商那里加 A 记录指向本机，证书会在解析生效后自动申请"
  ok "$title 的对外地址：https://$domain"
  return 0
}

# 先校验再重启：admin 接口关闭，新站点要重启 caddy 才生效；配置有错时撤销生成的站点，不让整个工作台停掉
restart_caddy_with_apps() {
  compose up -d --no-deps caddy </dev/null >/dev/null 2>&1 || true
  if compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile </dev/null >/dev/null 2>&1; then
    compose restart caddy </dev/null >/dev/null && return 0
  fi
  return 1
}

check_app_https() {
  local url="$1" _ code=""
  for _ in $(seq 1 18); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$url" 2>/dev/null || true)"
    [[ $code =~ ^(200|204|301|302)$ ]] && { ok "$url 可以访问"; return 0; }
    sleep 5
  done
  warn "$url 暂时访问不到（状态码：${code:-无}）：多半是 DNS 还没生效或证书还在申请，Caddy 会自动重试"
}

connected() { [[ -n $1 && $1 != none ]]; }

expose_apps() {
  local status="$1" site n8n_changed=0 domain
  step "n8n 的对外访问"
  if [[ $(env_get COMPOSE_FILE) != *compose.https.yaml* ]]; then
    info "没有启用域名与 HTTPS，n8n 只能在服务器本机访问（SSH 隧道）"
    return 0
  fi
  site="$(env_get SITE_DOMAIN)"
  if connected "$(status_of n8n "$status")" || [[ -n $(env_get N8N_DOMAIN) ]]; then
    domain="$(choose_domain n8n n8n N8N_DOMAIN "n8n.$site")"
    if apply_app_domain n8n n8n N8N_DOMAIN N8N_PUBLIC_URL \
      "http://localhost:$(env_get N8N_PORT | grep . || echo 5678)/" n8n:5678 "$domain"; then n8n_changed=1; fi
    # 经 HTTPS 反向代理访问：Cookie 只走 HTTPS，并按 X-Forwarded-For 识别客户端；取消对外时恢复
    if [[ -n $(env_get N8N_DOMAIN) ]]; then env_set N8N_SECURE_COOKIE true; env_set N8N_PROXY_HOPS 1
    else env_set N8N_SECURE_COOKIE false; env_set N8N_PROXY_HOPS 0
    fi
  fi
  if (( ! n8n_changed )); then
    info "对外访问设置没有变化"
    return 0
  fi
  # 让新地址生效：重建 n8n（读取新的公开地址），校验后重启 Caddy，再更新工作台里的打开链接
  (cd "$ROOT" && make -s n8n-up </dev/null) || warn "n8n 没有重启成功（见上面的输出）"
  if ! restart_caddy_with_apps; then
    warn "Caddy 配置校验没有通过，撤销刚生成的对外站点（工作台不受影响）"
    rm -f "$(app_site_file n8n)"
    restart_caddy_with_apps || warn "Caddy 重启失败：运行 docker compose logs caddy 查看原因"
    return 0
  fi
  connect_cmd public-urls --n8n "$(public_url_of N8N_PUBLIC_URL http://localhost:5678)" </dev/null \
    || warn "工作台里的打开链接没有更新（见上面的输出）"
  if [[ -n $(env_get N8N_DOMAIN) ]]; then check_app_https "https://$(env_get N8N_DOMAIN)/healthz"; fi
  return 0
}

# ---------------------------------------------------------------- OpenIM（T-0081）
# 「消息」页（管理员之间、管理员与 OpenClaw 实例）的后端。openim-server 用打过补丁的镜像（官方镜像有越权问题），
# 只能在服务器上构建：从 GitHub 取锁定的上游提交、打 infra/openim/patches/ 的补丁、编译。
openim_image() { sed -n 's|^    image: \(my-workspace/openim-server:.*\)$|\1|p' "$ROOT/infra/openim/compose.yaml" | head -1; }
openim_running() { [[ -n "$(docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-$PROJECT}-openim" 2>/dev/null)" ]]; }
openim_label() {
  case "$1" in
    ok) echo "已部署" ;;
    none | "") echo "未部署" ;;
    unavailable) echo "已配置，但工作台现在连不上" ;;
    *) echo "状态未知（$1）" ;;
  esac
}

build_openim() {
  local image
  image="$(openim_image)"
  [[ -n $image ]] || { warn "读不到 infra/openim/compose.yaml 里的镜像名"; return 1; }
  if docker image inspect "$image" >/dev/null 2>&1; then ok "镜像 $image 已经构建过"; return 0; fi
  info "构建 $image：取锁定的上游源码、打补丁、编译（第一次约 5 ~ 15 分钟，临时占用约 8 GB 磁盘）"
  (cd "$ROOT" && make -s openim-build </dev/null) && return 0
  # 基础镜像（golang、alpine）默认从 Docker Hub 拉取；匿名拉取有次数限制（429 toomanyrequests）
  warn "构建没有成功；改用 Google 的 Docker Hub 镜像站（mirror.gcr.io，镜像 digest 不变）再试一次"
  (cd "$ROOT" && BASE_REGISTRY=mirror.gcr.io/library make -s openim-build </dev/null)
}

# 让 api、worker 连上 OpenIM：.env 里两个地址为空时填上，有变化才重启 api、worker
openim_env() {
  local changed=0 key want current
  for key in OPENIM_API_URL OPENIM_OBJECT_URL; do
    if [[ $key == OPENIM_API_URL ]]; then want=http://openim-server:10002; else want=http://garage:3900; fi
    current="$(env_get "$key")"
    if [[ -z $current ]]; then env_set "$key" "$want"; changed=1
    elif [[ $current != "$want" ]]; then info "$key 已经设置为 $current，保持不变"
    fi
  done
  (( changed )) || return 0
  info "已在 .env 里设置 OPENIM_API_URL、OPENIM_OBJECT_URL，重启 api、worker 让它们生效"
  (cd "$ROOT" && make -s up </dev/null)
}

connect_openim() {
  local state="$1" free_kb mem_kb default=y i
  step "OpenIM（「消息」页：管理员之间、管理员与 OpenClaw 实例，可选）"
  if [[ $state == ok ]]; then ok "已部署"; return 0; fi
  if [[ $state == unavailable ]]; then
    warn "已配置，但工作台现在连不上 OpenIM：重新启动一次"
  else
    mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    if (( mem_kb > 0 && mem_kb < 3800 * 1024 )); then
      warn "内存 $((mem_kb / 1024)) MB：OpenIM 另外占用约 1 GB，建议总内存至少 4 GB"; default=n
    fi
    free_kb="$(df -Pk "$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /)" 2>/dev/null | awk 'NR==2 {print $4}')"
    if [[ -n $free_kb ]] && (( free_kb < 12 * 1024 * 1024 )) && ! docker image inspect "$(openim_image)" >/dev/null 2>&1; then
      warn "Docker 所在磁盘剩余 $((free_kb / 1024 / 1024)) GB：第一次构建要临时占用约 8 GB"; default=n
    fi
    decide OPENIM "部署 OpenIM？（另外占用约 1 GB 内存；第一次要在服务器上构建镜像，约 5 ~ 15 分钟）" "$default" \
      || { info "跳过；以后运行 deploy.sh connect"; return 0; }
  fi
  build_openim || { warn "OpenIM 镜像没有构建成功（见上面的输出），跳过；以后重新运行 deploy.sh connect"; return 0; }
  pull_images openim-pull "OpenIM" || true
  if ! (cd "$ROOT" && make -s openim-up </dev/null); then
    warn "OpenIM 没有启动成功（见上面的输出）：运行 make openim-logs 查看日志；拉取镜像遇到 429 toomanyrequests 时等一小时，或 docker login 后再运行 deploy.sh connect"
    return 0
  fi
  openim_env || { warn "api、worker 没有重启成功（见上面的输出）"; return 0; }
  for i in $(seq 1 30); do
    [[ $(status_of openim "$(connect_status)") == ok ]] && { ok "OpenIM 已部署：工作台的「消息」页可以用了"; return 0; }
    sleep 3
  done
  warn "OpenIM 已启动，但工作台还连不上：到工作台「运维」页查看 OpenIM 的状态，或运行 make openim-logs"
  return 0
}

# ---------------------------------------------------------------- OpenClaw 接入入口（T-0088）
# 让其他服务器上的 OpenClaw 连回工作台：外网入口（9443，双向证书）的服务器证书由工作台内部 CA 签发，
# 不用购买证书、不用 80 端口验证；接入包附带 CA，OpenClaw 那边不用另外配置（ADR 2026-11-03-openclaw-ssh-provisioning）。
# 主机名默认用 SITE_DOMAIN（已经指向这台服务器）；没有公网域名时用固定的内部名字，接入包把它映射到这台服务器的 IP。
AGENT_INTERNAL_HOST=agent-gateway.myworkspace.internal

valid_ip() { "$PY" -c 'import ipaddress, sys; ipaddress.ip_address(sys.argv[1])' "$1" 2>/dev/null; }
outbound_ip() {
  local addr=""
  command -v ip >/dev/null && addr="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')"
  [[ -n $addr ]] || addr="$(hostname -I 2>/dev/null | awk '{print $1}')"
  printf '%s\n' "$addr"
}
agent_tls_dir() {
  local dir; dir="$(env_get AGENT_TLS_DIR)"; dir="${dir:-./infra/caddy/agent-tls}"
  [[ $dir == /* ]] && printf '%s\n' "$dir" || printf '%s\n' "$ROOT/${dir#./}"
}

# 把 issue-server 的 JSON（标准输入）写成证书与私钥（私钥 600）；私钥只经过本机管道。
# agent-gateway 以 root 运行但去掉了全部 capability，只能读属主是 root 的 600 文件、进入其他用户可进入的目录：
# 新建的目录是 755，不是 root 运行时改文件属主
write_server_cert() {
  "$PY" -c '
import json, os, sys
data = json.load(sys.stdin)
cert, key = sys.argv[1:3]
folder = os.path.dirname(cert)
if not os.path.isdir(folder):
    # 容器里的 root 没有 DAC 能力，要能进入目录（755）；私钥本身是 600
    os.makedirs(folder, exist_ok=True)
    os.chmod(folder, 0o755)
for path, body, mode in ((key, data["key"], 0o600), (cert, data["cert"], 0o644)):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
print(data["not_after"][:10])
' "$1" "$2" || return 1
  (( EUID == 0 )) || as_root chown 0:0 "$1" "$2"
}

# 用内部 CA 签发一张入口证书，写到 <目录>/<名字>.crt / .key；输出到期日
issue_agent_cert() {
  compose exec -T api python -m app.commands.agent_ca issue-server --host "$1" </dev/null | write_server_cert "$2.crt" "$2.key"
}

# 用内部 CA 校验入口出示的服务器证书（外网入口要求客户端证书，TLS 1.3 下客户端这边的握手仍会完成）
check_agent_ingress() {
  "$PY" -c '
import socket, ssl, sys
host, port, ca = sys.argv[1], int(sys.argv[2]), sys.argv[3]
ctx = ssl.create_default_context(cafile=ca)
with socket.create_connection(("127.0.0.1", port), timeout=5) as raw, ctx.wrap_socket(raw, server_hostname=host):
    pass
' "$1" "$2" "$3" 2>/dev/null
}

connect_agent_ingress() {
  local url host site address port dir cert key status expiry ca changed=0
  step "OpenClaw 接入入口（让其他服务器上的 OpenClaw 连回工作台，可选）"
  url="$(env_get AGENT_PUBLIC_URL)"
  if [[ -n $url && $(env_get AGENT_PUBLIC_INTERNAL_CA) != true ]]; then
    ok "已经手动配置了外网入口（$url），保持不变"; return 0
  fi
  if [[ -z $url ]]; then
    decide INGRESS "配置接入入口？（内部 CA 自动签发证书、开放 TCP 9443；之后可以在网页上通过 SSH 自动部署 OpenClaw）" n \
      || { info "跳过；以后运行 deploy.sh connect"; return 0; }
  fi
  site="$(env_get SITE_DOMAIN)"
  host="$(env_get AGENT_PUBLIC_HOST)"
  [[ -n $host && $host != public.invalid ]] || host="$site"
  choose host INGRESS_HOST "OpenClaw 服务器访问工作台用的域名（没有公网域名时输入 -，改用 IP）" "${host:--}"
  host="$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')"
  address=""
  if [[ $host == - || $host == "$AGENT_INTERNAL_HOST" ]]; then
    host="$AGENT_INTERNAL_HOST"
    address="$(env_get AGENT_PUBLIC_ADDRESS)"; address="${address:-$(outbound_ip)}"
    info "云服务器上本机看到的常是内网地址：这里要填 OpenClaw 服务器能访问到的地址（通常是公网 IP）"
    choose address INGRESS_ADDRESS "OpenClaw 服务器访问这台服务器用的 IP" "$address"
    valid_ip "$address" || { warn "「$address」不是 IP 地址，跳过"; return 0; }
  elif ! valid_domain "$host"; then
    warn "「$host」不是有效的域名，跳过"; return 0
  fi
  port="$(env_get AGENT_PUBLIC_PORT)"; [[ $port =~ ^[0-9]+$ ]] || port=9443
  dir="$(agent_tls_dir)"; cert="$dir/public.crt"; key="$dir/public.key"
  status=renew
  if [[ -s $cert && -s $key ]]; then
    status="$(compose exec -T api python -m app.commands.agent_ca check-server --host "$host" <"$cert" 2>/dev/null | tail -1 || true)"
  fi
  if [[ $status == ok ]]; then
    ok "入口证书仍然有效（$host）"
  else
    expiry="$(issue_agent_cert "$host" "$dir/public")" || { warn "入口证书签发失败（见上面的输出），跳过"; return 0; }
    ok "已用内部 CA 签发入口证书：$host，有效期到 $expiry"; changed=1
  fi
  # 内网入口与外网入口在同一个 Caddy 里，没有内网证书时 Caddy 起不来：签一张占位证书（内网入口默认只监听 127.0.0.1）
  if [[ ! -s $dir/intranet.crt || ! -s $dir/intranet.key ]]; then
    local intranet; intranet="$(env_get AGENT_INTRANET_HOST)"
    issue_agent_cert "${intranet:-intranet.invalid}" "$dir/intranet" >/dev/null \
      || { warn "内网入口的占位证书签发失败（见上面的输出），跳过"; return 0; }
    changed=1
  fi
  local key_name want
  for key_name in AGENT_PUBLIC_HOST AGENT_PUBLIC_BIND AGENT_PUBLIC_PORT AGENT_PUBLIC_URL AGENT_PUBLIC_ADDRESS AGENT_PUBLIC_INTERNAL_CA; do
    case $key_name in
      AGENT_PUBLIC_HOST) want="$host" ;;
      AGENT_PUBLIC_BIND) want=0.0.0.0 ;;
      AGENT_PUBLIC_PORT) want="$port" ;;
      AGENT_PUBLIC_URL) want="https://$host:$port" ;;
      AGENT_PUBLIC_ADDRESS) want="$address" ;;
      AGENT_PUBLIC_INTERNAL_CA) want=true ;;
    esac
    [[ $(env_get "$key_name") == "$want" ]] || { env_set "$key_name" "$want"; changed=1; }
  done
  if (( changed )); then
    compose up -d api worker </dev/null >/dev/null || { warn "api、worker 没有重启成功（见上面的输出）"; return 0; }
  fi
  (cd "$ROOT" && make -s agents-up </dev/null >/dev/null) || { warn "接入入口没有启动成功：运行 docker compose --profile agents logs agent-gateway 查看"; return 0; }
  (( changed )) && compose --profile agents restart agent-gateway </dev/null >/dev/null 2>&1
  ca="$ROOT/infra/caddy/agent-ca/agent-ca.pem"
  local i
  for i in 1 2 3 4 5; do
    if check_agent_ingress "$host" "$port" "$ca"; then ok "接入入口已就绪：https://$host:$port（内部 CA 签发的证书）"; break; fi
    (( i == 5 )) && warn "入口还没有响应：运行 docker compose --profile agents logs agent-gateway 查看"
    sleep 2
  done
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    if ! ufw status 2>/dev/null | grep -qE "^$port(/tcp)?[[:space:]]+ALLOW"; then
      if decide UFW_AGENT "防火墙（ufw）还没有放行 TCP $port，现在放行？" y; then
        as_root ufw allow "$port/tcp" >/dev/null && ok "已放行 TCP $port"
      fi
    fi
  fi
  info "云服务器的安全组 / 防火墙也要放行 TCP $port（OpenClaw 服务器只需要能访问这个端口）"
  return 0
}

# 升级：已经部署了 OpenIM 时，按新版本锁定的镜像重新构建（标签没变就跳过）并重建容器
upgrade_openim() {
  openim_running || return 0
  step "OpenIM"
  build_openim || { warn "OpenIM 镜像没有构建成功，继续用正在运行的版本；以后重新运行 deploy.sh upgrade"; return 0; }
  (cd "$ROOT" && make -s openim-up </dev/null) || warn "OpenIM 没有启动成功：运行 make openim-logs 查看日志"
  return 0
}

# ---------------------------------------------------------------- 旧的 Vikunja（T-0082）
# 项目与任务改由工作台自己管理（迁移 0020 已把同步过来的项目、任务转成工作台自己的数据）：
# 升级时停掉并删除以前部署的 Vikunja 容器与对外站点，数据卷保留，打印手动删除的命令。
# 按 Compose 项目标签找容器，不依赖 infra/vikunja/compose.yaml（之后会删除）。
retire_vikunja() {
  local core name site containers networks volumes
  core="$(env_get COMPOSE_PROJECT_NAME)"
  name="${core:-$PROJECT}-vikunja"
  site="$(app_site_file vikunja)"
  containers="$(docker ps -aq --filter "label=com.docker.compose.project=$name" 2>/dev/null || true)"
  [[ -n $containers || -f $site ]] || return 0
  step "停用旧的 Vikunja（项目与任务已转成工作台自己的数据）"
  if [[ -n $containers ]]; then
    # shellcheck disable=SC2086 # 容器 ID 按空白分开传给 docker
    if docker rm -f $containers >/dev/null; then ok "已停止并删除 Vikunja 的容器"
    else warn "没能删除 Vikunja 的容器：运行 docker ps -a --filter label=com.docker.compose.project=$name 查看"
    fi
    networks="$(docker network ls -q --filter "label=com.docker.compose.project=$name" 2>/dev/null || true)"
    # shellcheck disable=SC2086
    [[ -z $networks ]] || docker network rm $networks >/dev/null 2>&1 || true
  fi
  if [[ -f $site ]]; then
    rm -f "$site"
    if restart_caddy_with_apps; then ok "已删除 Vikunja 的对外站点"
    else warn "Caddy 重启失败：运行 docker compose logs caddy 查看原因"
    fi
  fi
  [[ -z $(env_get VIKUNJA_DOMAIN) ]] || env_set VIKUNJA_DOMAIN ""
  volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$name" 2>/dev/null | tr '\n' ' ' || true)"
  if [[ -n ${volumes// /} ]]; then
    info "Vikunja 的数据卷保留着（以前的数据还在里面）。确认不再需要后，可以手动删除："
    info "  docker volume rm ${volumes% }"
  fi
  return 0
}

connect_components() {
  step "接入组件：GitHub（只读）、n8n（自动化）、OpenIM（消息）、OpenClaw 接入入口；项目与任务由工作台自己管理"
  compose ps --status running --services </dev/null 2>/dev/null | grep -qx api || { warn "api 没有运行，先部署（install）"; return 0; }
  wait_for_setup || return 0
  local status
  status="$(connect_status)"
  info "以管理员 $(status_of user "$status") 的身份接入：凭证加密保存在工作台，操作写入审计日志"
  connect_github "$(status_of github "$status")"
  connect_n8n "$(status_of n8n "$status")" "$(status_of n8n_login "$status")"
  connect_openim "$(status_of openim "$status")"
  connect_agent_ingress
  status="$(connect_status)"
  expose_apps "$status"
  step "接入状态"
  info "GitHub：$(status_label "$(status_of github "$status")")　n8n：$(status_label "$(status_of n8n "$status")")"
  info "OpenIM：$(openim_label "$(status_of openim "$status")")"
  info "OpenClaw 接入入口：$(public_url_of AGENT_PUBLIC_URL 未配置)"
  info "n8n 打开地址：$(public_url_of N8N_PUBLIC_URL http://localhost:5678)"
  info "以后要接入或重试：bash $ROOT/scripts/deploy.sh connect（GitHub 在工作台「集成设置」里接入）"
}

# ---------------------------------------------------------------- 前台：一次问完（T-0094）
# 每个 plan_* 只检查和提问，不改系统；回答记在 ANS 里，后台阶段的同名 decide / choose 直接取用

# 上次的同一动作没有完成：问是否沿用上次的回答
plan_reuse_previous() {
  local file status
  file="$(answers_file)"
  [[ -r $file && $(state_get action) == "$ACTION" ]] || return 0
  status="$(state_get status)"
  case "$status" in failed | starting | running | waiting_setup) ;; *) return 0 ;; esac
  warn "上次的任务没有完成（停在「$(state_get step)」）"
  if confirm "沿用上次的回答重新运行？（已完成的步骤会很快跳过）" y; then
    answers_load "$file"
    ok "已沿用上次的回答"
  fi
}

# plan_value 变量 键 "问题" "默认值" 校验函数：问到格式正确为止（无人值守时格式不对就停止）
plan_value() {
  local __o="$1" __k="$2" __q="$3" __d="$4" __check="$5" __pv
  while :; do
    choose __pv "$__k" "$__q" "$__d"
    "$__check" "$__pv" && break
    warn "「$__pv」格式不对"
    unset "ANS[$__k]"
    if (( ASSUME_YES )) || ! interactive; then die "「$__q」的回答格式不对：$__pv"; fi
  done
  printf -v "$__o" '%s' "$__pv"
}

plan_docker() {
  if ! command -v docker >/dev/null; then
    decide DOCKER_INSTALL "没有安装 Docker，部署时用官方脚本（get.docker.com）安装？" y || die "请先安装 Docker（docs/deploy-guide.md 第 2 步）"
  elif ! docker compose version >/dev/null 2>&1; then
    apt_based || die "缺少 docker compose v2（安装 docker-compose-plugin）"
    decide COMPOSE_INSTALL "缺少 Compose v2 插件，部署时安装 docker-compose-plugin？" y || die "缺少 docker compose v2（安装 docker-compose-plugin）"
  fi
  return 0
}

env_valid() { (cd "$ROOT" && "$PY" scripts/init_env.py >/dev/null 2>&1); }

plan_env() {
  if [[ ! -f $ROOT/.env ]]; then
    plan_backup_password
  elif ! env_valid; then
    (cd "$ROOT" && "$PY" scripts/init_env.py >/dev/null) || true
    warn ".env 有上面列出的问题（多半是从 .env.example 复制的，占位符没有替换）"
    decide REGENERATE_ENV "部署时备份当前 .env 并重新生成？（新的随机密钥；数据库密码之后会自动同步）" y || die "请按提示修改 .env 后重试"
    plan_backup_password "$ROOT/.env"
  fi
  return 0
}

plan_backup_password() {
  local saved
  saved="$(backup_password_candidate "$@")" || return 0
  decide REUSE_BACKUP_PASSWORD "找到之前的 .env（$saved）。新 .env 沿用其中的 BACKUP_PASSWORD？之前的备份用它加密，沿用才能继续备份到同一个仓库、并从中恢复" y || true
}

n8n_running() { [[ -n "$(docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-$PROJECT}-n8n" 2>/dev/null)" ]]; }
api_running() { command -v docker >/dev/null && compose ps --status running --services </dev/null 2>/dev/null | grep -qx api; }
domain_ok() { valid_domain "$1"; }
email_ok() { valid_email "$1"; }
ip_ok() { valid_ip "$1"; }

# 可选组件：n8n（及对外子域名）、OpenIM、OpenClaw 接入入口
plan_components() {
  local status="" state site https=0 default mem_kb free_kb dir email
  api_running && status="$(connect_status)"
  site="${DOMAIN:-$(env_get SITE_DOMAIN)}"
  [[ $ACTION == install || $(env_get COMPOSE_FILE) == *compose.https.yaml* ]] && https=1
  step "可选组件：现在选好，后台按选择自动部署、接入"
  state="$(status_of n8n "$status")"
  if connected "$state"; then
    ok "n8n：已接入"
  elif decide N8N "部署 n8n 并自动接入？（自动化；另外占用约 1 GB 内存）" y; then
    plan_value email N8N_EMAIL "n8n owner 账号的邮箱" "${EMAIL:-$(env_get ACME_EMAIL)}" email_ok
  fi
  if (( https )) && { connected "$state" || [[ ${ANS[N8N]:-} == y ]]; }; then plan_app_domain "$site"; fi
  state="$(status_of openim "$status")"
  case "$state" in
    ok) ok "OpenIM：已部署" ;;
    unavailable) info "OpenIM：已配置但工作台现在连不上，后台会重新启动它" ;;
    *)
      default=y
      mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
      if (( mem_kb > 0 && mem_kb < 3800 * 1024 )); then warn "内存 $((mem_kb / 1024)) MB：OpenIM 另外占用约 1 GB，建议总内存至少 4 GB"; default=n; fi
      dir="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"; [[ -d $dir ]] || dir=/
      free_kb="$(df -Pk "$dir" 2>/dev/null | awk 'NR==2 {print $4}')"
      if [[ -n $free_kb ]] && (( free_kb < 12 * 1024 * 1024 )) && ! docker image inspect "$(openim_image)" >/dev/null 2>&1; then
        warn "Docker 所在磁盘剩余 $((free_kb / 1024 / 1024)) GB：第一次构建要临时占用约 8 GB"; default=n
      fi
      decide OPENIM "部署 OpenIM？（「消息」页；另外占用约 1 GB 内存；第一次要在服务器上构建镜像，约 5 ~ 15 分钟）" "$default" || true
      ;;
  esac
  plan_ingress "$site"
}

plan_app_domain() {
  local site="$1" current domain
  current="$(env_get N8N_DOMAIN)"
  while :; do
    if [[ -n $current ]]; then
      choose domain N8N_DOMAIN "n8n 的对外域名（回车保持 $current；输入 - 取消对外）" "$current"
    elif decide N8N_EXPOSE "用子域名经 HTTPS 对外打开 n8n？（登录页会暴露在公网；不打开时只能经 SSH 隧道访问）" n; then
      choose domain N8N_DOMAIN "n8n 的对外域名（先在域名服务商那里把它解析到本机）" "n8n.$site"
    else
      ANS[N8N_DOMAIN]=""; return 0
    fi
    domain="$(printf '%s' "$domain" | tr '[:upper:]' '[:lower:]' | sed -E 's#^https?://##; s#/.*$##')"
    [[ $domain == - ]] && { ANS[N8N_DOMAIN]=""; return 0; }
    if valid_domain "$domain" && [[ $domain != "$site" ]]; then ANS[N8N_DOMAIN]="$domain"; return 0; fi
    warn "「$domain」不能用作 n8n 的域名（要是一个单独的子域名）"
    unset "ANS[N8N_DOMAIN]"
    if (( ASSUME_YES )) || ! interactive; then die "n8n 的对外域名无效：$domain"; fi
  done
}

plan_ingress() {
  local site="$1" url host address port
  url="$(env_get AGENT_PUBLIC_URL)"
  if [[ -n $url && $(env_get AGENT_PUBLIC_INTERNAL_CA) != true ]]; then ok "OpenClaw 接入入口：已经手动配置（$url），保持不变"; return 0; fi
  if [[ -z $url ]]; then
    decide INGRESS "配置 OpenClaw 接入入口？（内部 CA 自动签发证书、开放 TCP 9443；之后可以在网页上通过 SSH 自动部署 OpenClaw）" n || return 0
  fi
  host="$(env_get AGENT_PUBLIC_HOST)"
  [[ -n $host && $host != public.invalid ]] || host="$site"
  while :; do
    choose host INGRESS_HOST "OpenClaw 服务器访问工作台用的域名（没有公网域名时输入 -，改用 IP）" "${host:--}"
    host="$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')"
    ANS[INGRESS_HOST]="$host"
    [[ $host == - || $host == "$AGENT_INTERNAL_HOST" ]] && break
    valid_domain "$host" && break
    warn "「$host」不是有效的域名"
    unset "ANS[INGRESS_HOST]"; host=""
    if (( ASSUME_YES )) || ! interactive; then die "OpenClaw 接入入口的域名无效"; fi
  done
  if [[ $host == - || $host == "$AGENT_INTERNAL_HOST" ]]; then
    address="$(env_get AGENT_PUBLIC_ADDRESS)"; address="${address:-$(outbound_ip)}"
    info "云服务器上本机看到的常是内网地址：这里要填 OpenClaw 服务器能访问到的地址（通常是公网 IP）"
    plan_value address INGRESS_ADDRESS "OpenClaw 服务器访问这台服务器用的 IP" "$address" ip_ok
  fi
  port="$(env_get AGENT_PUBLIC_PORT)"; [[ $port =~ ^[0-9]+$ ]] || port=9443
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active" \
    && ! ufw status 2>/dev/null | grep -qE "^$port(/tcp)?[[:space:]]+ALLOW"; then
    decide UFW_AGENT "防火墙（ufw）还没有放行 TCP $port，部署时放行？" y || true
  fi
  return 0
}

plan_install() {
  step "部署前先把问题问完：之后在后台运行，不再需要你回答"
  check_os
  check_resources
  plan_docker
  ask_parameters
  check_dns
  check_ports
  plan_env
  plan_components
  plan_summary
}

plan_connect() {
  [[ -f $ROOT/.env ]] || die "还没有部署过（找不到 $ROOT/.env），先运行 install"
  BASE_URL="$(env_get PUBLIC_BASE_URL)"
  api_running || die "api 没有运行：先部署（install）"
  local status
  status="$(connect_status)"
  step "接入组件前先把问题问完：之后在后台运行"
  if [[ $(status_of setup "$status") != "done" ]]; then
    info "还没有创建管理员：后台会等你在浏览器完成「首次安装」后再接入"
  else
    plan_connect_now "$status"
  fi
  plan_components
  plan_summary
}

# 需要输入密码的接入当场在前台做完，密码不带到后台
plan_connect_now() {
  local status="$1" n8n owner
  n8n="$(status_of n8n "$status")"
  if connected "$n8n"; then
    PHASE=direct offer_save_login n8n n8n 自动化 "$(status_of n8n_login "$status")" "n8n owner 的登录邮箱" "$(env_get ACME_EMAIL)"
  elif n8n_running; then
    owner="$(connect_cmd n8n-owner </dev/null 2>/dev/null || true)"
    if [[ $owner == owner=exists ]]; then
      info "n8n 已经有 owner 账号：现在输入它的邮箱和密码完成接入（密码只在这一步使用，不会带到后台）"
      ANS[N8N]=y
      PHASE=direct connect_n8n none ""
      ANS[N8N]=n
    fi
  fi
  return 0
}

plan_upgrade() {
  [[ -f $ROOT/.env ]] || die "还没有部署过（找不到 $ROOT/.env），先运行 install"
  upgrade_target
  if [[ $UPGRADE_NEW == "$UPGRADE_OLD" ]]; then
    ok "已经是最新版本（$(git -C "$ROOT" rev-parse --short HEAD)）"
    decide REBUILD "仍然重新构建并检查一遍？" n || exit 0
  else
    info "当前 $(git -C "$ROOT" log -1 --format='%h %cs %s' "$UPGRADE_OLD")"
    info "目标 $(git -C "$ROOT" log -1 --format='%h %cs %s' "$UPGRADE_NEW")（$(git -C "$ROOT" rev-list --count "$UPGRADE_OLD..$UPGRADE_NEW") 个新提交）"
    decide UPGRADE "开始升级？（先自动备份数据库）" y || exit 0
  fi
  ANS[UPGRADE_TO]="$UPGRADE_NEW"
  plan_summary
}

answer_label() {
  case "$1" in
    DOCKER_INSTALL) echo "安装 Docker" ;;
    COMPOSE_INSTALL) echo "安装 Compose 插件" ;;
    DNS_CONTINUE) echo "域名还没解析也继续" ;;
    UFW_WEB) echo "防火墙放行 80 / 443" ;;
    REGENERATE_ENV) echo "重新生成 .env" ;;
    REUSE_BACKUP_PASSWORD) echo "沿用之前的 BACKUP_PASSWORD" ;;
    N8N) echo "部署并接入 n8n" ;;
    N8N_EMAIL) echo "n8n owner 邮箱" ;;
    N8N_EXPOSE) echo "n8n 用子域名对外" ;;
    N8N_DOMAIN) echo "n8n 对外域名" ;;
    OPENIM) echo "部署 OpenIM" ;;
    INGRESS) echo "配置 OpenClaw 接入入口" ;;
    INGRESS_HOST) echo "接入入口的域名（- 表示用 IP）" ;;
    INGRESS_ADDRESS) echo "接入入口的 IP" ;;
    UFW_AGENT) echo "防火墙放行接入入口端口" ;;
    UPGRADE) echo "升级" ;;
    UPGRADE_TO) echo "升级到" ;;
    REBUILD) echo "重新构建" ;;
    *) echo "$1" ;;
  esac
}

plan_summary() {
  local key value
  step "确认设置（保存到 $(deploy_dir)/answers.env，权限 600，不含密码）"
  [[ -n $DOMAIN ]] && info "域名：$DOMAIN　证书邮箱：$EMAIL　访问地址：$BASE_URL"
  for key in DOCKER_INSTALL COMPOSE_INSTALL DNS_CONTINUE UFW_WEB REGENERATE_ENV REUSE_BACKUP_PASSWORD \
    N8N N8N_EMAIL N8N_DOMAIN OPENIM INGRESS INGRESS_HOST INGRESS_ADDRESS UFW_AGENT UPGRADE UPGRADE_TO REBUILD; do
    [[ -v ANS[$key] ]] || continue
    value="${ANS[$key]}"
    case "$value" in y) value=是 ;; n) value=否 ;; "") value="（不设置）" ;; esac
    [[ $key == UPGRADE_TO ]] && value="$(git -C "$ROOT" rev-parse --short "$value" 2>/dev/null || echo "$value")"
    info "$(answer_label "$key")：$value"
  done
  confirm "按上面的设置开始？（开始后在后台运行，不再需要你回答）" y || die "已取消"
}

# ---------------------------------------------------------------- 动作
do_install() {
  check_os
  ensure_docker
  check_resources
  ask_parameters
  check_dns
  check_ports
  prepare_env
  start_services
  migrate
  smoke
  check_https
  step "部署完成：$(git -C "$ROOT" rev-parse --short HEAD)"
  show_setup
  connect_components
  remind_backup_env
}

do_connect() {
  [[ -f $ROOT/.env ]] || die "还没有部署过（找不到 $ROOT/.env），先运行 install"
  ensure_docker
  BASE_URL="$(env_get PUBLIC_BASE_URL)"
  connect_components
}

run_backup() {
  step "${1:-备份数据库}（make backup，加密的 restic 仓库）"
  # shellcheck source=/dev/null
  if [[ -f $BACKUP_ENV_FILE ]]; then set -a; . "$BACKUP_ENV_FILE"; set +a; info "已读取 $BACKUP_ENV_FILE"; fi
  if (cd "$ROOT" && make backup); then ok "备份完成"; return 0; fi
  warn "备份失败（见上面的输出）"
  decide BACKUP_SKIP "不备份继续？（不推荐）" n || die "备份失败，已停止（代码与服务都没有改动）；修好备份后重新运行"
}

# 检查更新：UPGRADE_OLD 当前提交、UPGRADE_NEW 目标提交（前台确认过的目标优先，避免后台升级到别的版本）
upgrade_target() {
  step "检查更新"
  local dirty target
  dirty="$(git -C "$ROOT" status --porcelain --untracked-files=no)"
  [[ -z $dirty ]] || die "代码目录有本地修改，先处理（git -C $ROOT status）：$(printf '%s' "$dirty" | head -5 | tr '\n' ' ')"
  UPGRADE_OLD="$(git -C "$ROOT" rev-parse HEAD)"
  target="${REF:-main}"
  git -C "$ROOT" fetch -q --tags origin
  UPGRADE_NEW="$(git -C "$ROOT" rev-parse "origin/$target" 2>/dev/null || git -C "$ROOT" rev-parse "$target^{commit}")"
  if [[ -n ${ANS[UPGRADE_TO]:-} ]] && git -C "$ROOT" cat-file -e "${ANS[UPGRADE_TO]}^{commit}" 2>/dev/null; then
    UPGRADE_NEW="${ANS[UPGRADE_TO]}"
  fi
}

do_upgrade() {
  [[ -f $ROOT/.env ]] || die "还没有部署过（找不到 $ROOT/.env），先运行 install"
  ensure_docker
  if [[ -z $AFTER_PULL ]]; then
    upgrade_target
    local old="$UPGRADE_OLD" new="$UPGRADE_NEW" target="${REF:-main}"
    if [[ $new == "$old" ]]; then
      ok "已经是最新版本（$(git -C "$ROOT" rev-parse --short HEAD)）"
      decide REBUILD "仍然重新构建并检查一遍？" n || return 0
    else
      info "当前 $(git -C "$ROOT" log -1 --format='%h %cs %s' "$old")"
      info "目标 $(git -C "$ROOT" log -1 --format='%h %cs %s' "$new")（$(git -C "$ROOT" rev-list --count "$old..$new") 个新提交）"
      decide UPGRADE "开始升级？" y || return 0
      if compose ps --status running --services 2>/dev/null | grep -qx postgres; then run_backup "升级前备份数据库"; else warn "服务没有运行，跳过升级前备份"; fi
      mkdir -p "$ROOT/backups" && cp "$ROOT/.env" "$ROOT/backups/env-$(git -C "$ROOT" rev-parse --short "$old")-$(date +%Y%m%d%H%M%S)" && chmod 600 "$ROOT"/backups/env-*
      if git -C "$ROOT" show-ref -q --verify "refs/remotes/origin/$target" && [[ $new == "$(git -C "$ROOT" rev-parse "origin/$target")" ]]; then
        git -C "$ROOT" checkout -q "$target" && git -C "$ROOT" merge -q --ff-only "$new"
      else
        git -C "$ROOT" checkout -q "$new"
      fi
      ok "代码已更新到 $(git -C "$ROOT" rev-parse --short HEAD)"
    fi
    # 用新版本的脚本完成剩下的步骤（后台阶段接着在同一个后台任务里运行）
    local args=(upgrade --dir "$ROOT" --after-pull "$old")
    (( ASSUME_YES )) && args+=(--yes)
    [[ $PHASE == run ]] && args+=(--run --answers "$(answers_file)")
    exec bash "$ROOT/scripts/deploy.sh" "${args[@]}"
  fi
  step "更新 .env（补上新版本新增的变量并检查）"
  (cd "$ROOT" && "$PY" scripts/init_env.py) || die ".env 检查没有通过"
  BASE_URL="$(env_get PUBLIC_BASE_URL)"
  start_services
  migrate
  smoke
  [[ $BASE_URL == https://* ]] && check_https
  retire_vikunja
  upgrade_openim
  step "升级完成：$(git -C "$ROOT" log -1 --format='%h %cs %s')"
  if [[ $AFTER_PULL != "$(git -C "$ROOT" rev-parse HEAD)" ]]; then
    info "回滚：没有新迁移时 git -C $ROOT checkout $(git -C "$ROOT" rev-parse --short "$AFTER_PULL") 后再 make up；有新迁移见 docs/deployment.md 第 5.3 节"
  fi
}

project_exists() {
  [[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$1" 2>/dev/null)" ]] \
    || [[ -n "$(docker volume ls -q --filter "label=com.docker.compose.project=$1" 2>/dev/null)" ]]
}

# 删除代码目录前的检查：必须是本项目的 git 检出，不能是 /、家目录、/srv 这类目录
safe_to_delete_dir() {
  local real
  real="$(cd "$ROOT" && pwd -P)" || return 1
  [[ -d $real/.git && -f $real/compose.yaml && -f $real/scripts/deploy.sh ]] || return 1
  [[ $real == /*/* && $real != "$HOME" && ! $real =~ ^/home/[^/]+$ ]]
}

# 删除代码目录时，本机备份（backups/）与 .env 先移到这里保留
preserve_root() { if [[ -w /var/backups ]]; then echo /var/backups; else echo "$HOME"; fi; }

do_reset() {
  run_alive && die "后台有部署任务在运行（$(state_get step)）：等它结束，或先停止它（systemctl stop $RUN_UNIT）"
  if (( ASSUME_YES )) || ! interactive; then
    [[ $CONFIRM == "$PROJECT" ]] || die "无人值守清空必须加 --confirm $PROJECT"
  fi
  ensure_docker
  step "清空项目"
  local projects=() name delete_dir=0 delete_env=0
  [[ $DIR_CHOICE == --delete-dir ]] && delete_dir=1
  for name in "$PROJECT" "${OPTIONAL_PROJECTS[@]}"; do project_exists "$name" && projects+=("$name"); done
  if ((${#projects[@]} == 0)); then
    ok "没有找到本项目的容器与数据卷"
    if (( ! delete_dir )) && ! (( ASSUME_YES )) && interactive; then
      confirm "要删除代码目录 $ROOT 吗？" n && delete_dir=1
    fi
    if (( ! delete_dir )) && [[ $ENV_CHOICE != --delete-env ]]; then info "不需要清空"; return 0; fi
  else
    warn "将删除下面这些 Compose 项目的全部容器、网络与数据卷（数据库、证书等，无法恢复）："
    for name in "${projects[@]}"; do
      info "$name：$(docker volume ls -q --filter "label=com.docker.compose.project=$name" | tr '\n' ' ')"
    done
    info "Caddy 的证书也会删除；重新部署会重新申请，Let's Encrypt 对同一域名每周最多签发 5 次"
    if compose ps --status running --services 2>/dev/null | grep -qx postgres && [[ -f $ROOT/.env ]]; then
      confirm "清空前先备份数据库？（make backup；备份在 BACKUP_REPOSITORY，不会被清空）" y && { run_backup "清空前备份数据库" || true; }
    fi
    if (( ! delete_dir )) && [[ -z $DIR_CHOICE ]] && ! (( ASSUME_YES )) && interactive; then
      confirm "同时删除代码目录 $ROOT？（其中的 backups/ 与 .env 会先移到 $(preserve_root)/ 下保留）" n && delete_dir=1
    fi
  fi
  local target=""
  if (( delete_dir )); then
    safe_to_delete_dir || die "$ROOT 看起来不是本项目的代码目录（需要有 .git、compose.yaml、scripts/deploy.sh），不删除"
    target="$(cd "$ROOT" && pwd -P)"
  else
    case "$ENV_CHOICE" in
      --delete-env) delete_env=1 ;;
      --keep-env) delete_env=0 ;;
      *) [[ -f $ROOT/.env ]] && confirm "同时删除 .env（所有密钥）？保留的话重新部署会沿用原来的密钥" n && delete_env=1 ;;
    esac
  fi
  if ! (( ASSUME_YES )) && interactive; then
    local typed
    (( delete_dir )) && warn "代码目录 $ROOT 也会被删除"
    read -r -p "确认清空请输入项目名 $PROJECT: " typed || true
    [[ $typed == "$PROJECT" ]] || die "输入不一致，已取消"
  fi
  for name in "${projects[@]}"; do
    # 只给项目名：按标签找到该项目的全部容器（包括 --profile 启动的 agent-gateway、监控等）、网络与数据卷
    docker compose -p "$name" down -v --remove-orphans
    ok "已删除 $name"
  done
  if (( delete_dir )); then
    local keep=""
    if [[ -f $ROOT/.env || -n "$(ls -A "$ROOT/backups" 2>/dev/null)" ]]; then
      keep="$(preserve_root)/$PROJECT-$(date +%Y%m%d%H%M%S)"
      mkdir -p "$keep" && chmod 700 "$keep"
      if [[ -d $ROOT/backups ]]; then mv "$ROOT/backups" "$keep/backups"; fi
      if [[ -f $ROOT/.env ]]; then cp -p "$ROOT/.env" "$keep/env" && chmod 600 "$keep/env"; fi
      ok "本机备份与 .env 已移到 $keep"
    fi
    cd / && rm -rf -- "$target"
    ok "已删除代码目录 $target"
    step "清空完成"
    if [[ -n $keep ]]; then
      info "保留的文件：$keep"
      [[ -d $keep/backups ]] && info "  backups/：本机备份（restic 仓库与之前的 .env 副本，都是加密或只有 root 可读）"
      [[ -f $keep/env ]] && info "  env：原来的 .env，里面有打开备份用的 BACKUP_PASSWORD 与加密密钥"
      info "确认不再需要后自己删除：rm -rf $keep"
    fi
    info "重新部署：curl -fsSL https://raw.githubusercontent.com/brianabc121/WorkspaceCore/main/scripts/deploy.sh | bash"
    info "当前终端如果还停在 $ROOT 里，先 cd ~"
    return 0
  fi
  if (( delete_env )) && [[ -f $ROOT/.env ]]; then
    local keep
    keep="$ROOT/backups/env-deleted-$(date +%Y%m%d%H%M%S)"
    mkdir -p "$ROOT/backups" && mv "$ROOT/.env" "$keep" && chmod 600 "$keep"
    ok "已移走 .env（留了一份在 $keep，确认不需要后自己删除）"
  fi
  step "清空完成"
  info "代码目录 $ROOT 与 backups/ 保留；重新部署：bash $ROOT/scripts/deploy.sh install"
}

action_label() { case "$1" in install) echo 部署 ;; upgrade) echo 升级 ;; connect) echo 接入组件 ;; *) echo "$1" ;; esac; }
fmt_time() { [[ $1 =~ ^[0-9]+$ ]] && date -d "@$1" '+%F %T' 2>/dev/null || echo 未知; }

# 后台任务的状态：进行到哪一步、等网页首次安装（显示安装凭据）、失败原因与最后的日志、待办
job_status() {
  local f status action log todo line
  f="$(state_file)"
  [[ -e $f ]] || return 0
  [[ -r $f ]] || { warn "看不到后台任务的状态：用 sudo 运行"; return 0; }
  status="$(state_get status)" action="$(state_get action)" log="$(deploy_log)" todo="$(todo_file)"
  step "后台任务：$(action_label "$action")（开始于 $(fmt_time "$(state_get started)")）"
  case "$status" in
    starting | running | waiting_setup)
      if ! run_alive; then
        warn "任务意外结束（服务器重启或被停止？），停在「$(state_get step)」"
        info "重新运行 bash $ROOT/scripts/deploy.sh $action 继续（会问是否沿用上次的回答）"
      elif [[ $status == waiting_setup ]]; then
        setup_box
      else
        ok "正在运行：$(state_get step)"
        info "看日志：bash $ROOT/scripts/deploy.sh logs"
      fi
      ;;
    done) ok "已完成（$(fmt_time "$(state_get finished)")）" ;;
    failed)
      warn "失败（$(fmt_time "$(state_get finished)")）：$(state_get error)"
      if [[ -r $log ]]; then info "日志的最后几行："; tail -n 15 "$log" | sed 's/^/        /' >&2; fi
      info "处理后重新运行 bash $ROOT/scripts/deploy.sh $action（会问是否沿用上次的回答）；完整日志：$log"
      ;;
  esac
  if [[ -s $todo ]]; then
    info "需要你处理的事项："
    while IFS= read -r line; do info "  - $line"; done <"$todo"
  fi
  return 0
}

do_logs() {
  local log
  log="$(deploy_log)"
  [[ -r $log ]] || { info "还没有部署日志（$log；看不到时用 sudo 运行）"; return 0; }
  if run_alive; then follow_run; return; fi
  tail -n 200 "$log" >&2
  job_status
}

do_status() {
  job_status
  step "状态"
  info "代码：$(git -C "$ROOT" log -1 --format='%h %cs %s' 2>/dev/null || echo 未知)"
  [[ -f $ROOT/.env ]] || { warn "还没有 .env（未部署）"; return 0; }
  docker info >/dev/null 2>&1 || { warn "Docker 还没有安装或没有运行"; return 0; }
  compose ps
  BASE_URL="$(env_get PUBLIC_BASE_URL)"
  info "PUBLIC_BASE_URL=$BASE_URL"
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:$(http_port)/readyz" 2>/dev/null || true)"
  if [[ $code == 200 ]]; then ok "本机就绪检查 /readyz → 200"; else warn "本机就绪检查 /readyz → ${code:-无响应}"; fi
  if [[ $BASE_URL == https://* ]]; then
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/healthz" 2>/dev/null || true)"
    if [[ $code == 200 ]]; then ok "$BASE_URL/healthz → 200"; else warn "$BASE_URL/healthz → ${code:-无响应}"; fi
  fi
  if api_running; then
    info "OpenIM：$(openim_label "$(status_of openim "$(connect_status)")")"
  fi
  [[ $(state_get status) == waiting_setup ]] || show_setup
}

menu() {
  interactive || die "没有终端可以显示菜单：请指定动作（install / upgrade / reset / status / connect / logs）"
  cat >&2 <<EOF

${C_B}My Workspace 部署脚本${C_0}（代码目录：$ROOT）
  1) 部署（首次安装，或重新检查并修复）
  2) 升级到最新版本（自动备份、迁移、检查）
  3) 清空项目（删除全部容器与数据，可选同时删除代码目录）
  4) 查看状态与后台任务的进度
  5) 接入组件：n8n、OpenIM、OpenClaw 接入入口（GitHub 在网页上接入）
  6) 查看部署日志
  0) 退出
EOF
  local choice
  read -r -p "请选择 [1]: " choice || true
  case "${choice:-1}" in
    1) ACTION=install ;; 2) ACTION=upgrade ;; 3) ACTION=reset ;; 4) ACTION=status ;; 5) ACTION=connect ;; 6) ACTION=logs ;; 0) exit 0 ;;
    *) die "无效的选择：$choice" ;;
  esac
}

parse_args() {
  while (($#)); do
    case "$1" in
      install|upgrade|reset|status|connect|logs) ACTION="$1" ;;
      --dir) DIR="${2:?--dir 需要目录}"; shift ;;
      --ref) REF="${2:?--ref 需要分支或 tag}"; shift ;;
      --domain) DOMAIN="${2:?}"; shift ;;
      --email) EMAIL="${2:?}"; shift ;;
      --base-url) BASE_URL="${2:?}"; shift ;;
      --yes|-y) ASSUME_YES=1 ;;
      --confirm) CONFIRM="${2:?}"; shift ;;
      --keep-env|--delete-env) ENV_CHOICE="$1" ;;
      --delete-dir) DIR_CHOICE="$1" ;;
      --after-pull) AFTER_PULL="${2:?}"; shift ;;
      --foreground) FOREGROUND=1 ;;
      --answers) ANSWERS_PATH="${2:?--answers 需要文件}"; shift ;;
      --run) RUN=1 ;;
      -h|--help) awk 'NR > 1 && /^#/ {sub(/^# ?/, ""); print; next} NR > 1 {exit}' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || echo "用法见 docs/deploy-guide.md"; exit 0 ;;
      *) die "未知参数：$1（--help 查看用法）" ;;
    esac
    shift
  done
}

main() {
  ORIG_ARGS=("$@")
  parse_args "$@"
  [[ ${WORKSPACE_FOREGROUND:-${WORKSPACE_NO_TMUX:-0}} == 1 ]] && FOREGROUND=1
  if [[ -n $DIR ]]; then
    [[ -f $DIR/compose.yaml && -f $DIR/scripts/init_env.py ]] && ROOT="$(cd "$DIR" && pwd)"
  else
    ROOT="$(find_checkout || true)"
  fi
  [[ -n $ROOT ]] || bootstrap
  # 后台阶段（由前台启动）：不提问，按回答文件运行
  if (( RUN )); then
    case "$ACTION" in install | upgrade | connect) ;; *) die "--run 只用于 install / upgrade / connect" ;; esac
    PHASE=run
    run_entry
    ensure_tools
    "do_$ACTION"
    exit 0
  fi
  case "$ACTION" in
    "" | install | upgrade | connect)
      if [[ -z $AFTER_PULL ]] && run_alive; then
        step "已经有一个后台任务在运行（$(action_label "$(state_get action)")：$(state_get step)），接着显示进度"
        follow_run
        exit $?
      fi
      if [[ -z $AFTER_PULL ]] && legacy_tmux_session; then
        warn "以前版本的部署还在 tmux 会话里（可能在等你回答问题），先处理完，避免同时部署两次："
        info "接回去：$( (( EUID == 0 )) && [[ -n ${SUDO_USER:-} ]] && echo 'sudo ')tmux -L my-workspace attach -t deploy"
        info "确认不再需要：tmux -L my-workspace kill-server，然后重新运行本脚本"
        exit 1
      fi
      ;;
  esac
  [[ -n $ACTION ]] || menu
  case "$ACTION" in
    install | upgrade | connect)
      # 升级时旧版本的脚本拉取代码后用 --after-pull 启动新版本：接着在原来的终端里做完；--foreground 也一样
      if (( FOREGROUND )) || [[ -n $AFTER_PULL ]]; then
        PHASE=direct
        ensure_tools
        "do_$ACTION"
        return 0
      fi
      ensure_root
      PHASE=plan
      ensure_tools
      if [[ -n $ANSWERS_PATH ]]; then
        answers_load "$ANSWERS_PATH"
        ANSWERS_PATH=""
      else
        plan_reuse_previous
      fi
      "plan_$ACTION"
      launch_run
      exit $?
      ;;
    reset) do_reset ;;
    status) do_status ;;
    logs) do_logs ;;
  esac
}

# 整个脚本读完再执行：curl | bash 时下载中断不会只执行一半
main "$@"
