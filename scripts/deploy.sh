#!/usr/bin/env bash
# My Workspace 一键部署 / 升级 / 清空（T-0072）。
#
# 一条命令（在全新服务器上以 root 运行；会把代码放到 /srv/my-workspace）：
#   curl -fsSL https://raw.githubusercontent.com/brianabc121/WorkspaceCore/main/scripts/deploy.sh | sudo bash
# 已有代码时：
#   sudo bash scripts/deploy.sh [install|upgrade|reset|status] [选项]
#
# 部署时只需要回答三个问题：SITE_DOMAIN、ACME_EMAIL、PUBLIC_BASE_URL（默认 https://<域名>）。
# 其余的都自动完成：安装缺少的工具与 Docker → 获取代码 → 生成 .env（随机密钥）→ 启用 HTTPS 与生产模式 →
# 检查端口 / DNS / 防火墙 → make up → 迁移数据库 → 冒烟检查 → 检查 HTTPS → 显示安装凭据。
#
# 选项：
#   --dir DIR          安装目录（默认 /srv/my-workspace；在代码目录里运行时就是该目录）
#   --ref REF          部署 / 升级到的分支或 tag（默认 main）
#   --domain D --email E --base-url URL   直接给出三个参数，不再询问
#   --yes              所有确认都按默认回答（无人值守）；清空还必须同时给 --confirm my-workspace
#   --confirm NAME     无人值守清空时的确认（必须是项目名 my-workspace）
#   --keep-env / --delete-env   清空时是否保留 .env（默认保留）
#
# 说明见 docs/deploy-guide.md 开头的「一键脚本」一节。
set -Eeuo pipefail

REPO_URL="${WORKSPACE_REPO:-https://github.com/brianabc121/WorkspaceCore.git}"
DEFAULT_DIR="${WORKSPACE_DIR:-/srv/my-workspace}"
PROJECT="my-workspace"
OPTIONAL_PROJECTS=("$PROJECT-vikunja" "$PROJECT-n8n" "$PROJECT-openim")
BACKUP_ENV_FILE="/etc/my-workspace/backup.env"

ACTION="" DIR="" REF="" DOMAIN="" EMAIL="" BASE_URL="" ASSUME_YES=0 CONFIRM="" ENV_CHOICE="" AFTER_PULL="" ROOT=""

# ---------------------------------------------------------------- 输出与交互
if [[ -t 2 ]]; then C_B=$'\e[1m' C_G=$'\e[32m' C_Y=$'\e[33m' C_R=$'\e[31m' C_0=$'\e[0m'; else C_B="" C_G="" C_Y="" C_R="" C_0=""; fi
step() { printf '\n%s==> %s%s\n' "$C_B" "$*" "$C_0" >&2; }
ok() { printf '%s通过%s  %s\n' "$C_G" "$C_0" "$*" >&2; }
info() { printf '      %s\n' "$*" >&2; }
warn() { printf '%s注意%s  %s\n' "$C_Y" "$C_0" "$*" >&2; }
die() { printf '%s失败%s  %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }
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

# ---------------------------------------------------------------- .env 读写（保留其他行与注释）
env_get() {
  [[ -f $ROOT/.env ]] || return 0
  sed -n "s/^$1=//p" "$ROOT/.env" | tail -1 | tr -d '\r'
}
env_set() {
  python3 - "$ROOT/.env" "$1" "$2" <<'PY'
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
  step "检查工具（git、make、python3、curl）"
  local missing=() cmd
  for cmd in git make python3 curl; do command -v "$cmd" >/dev/null || missing+=("$cmd"); done
  if ((${#missing[@]})); then
    apt_based || die "缺少 ${missing[*]}，当前系统（$(os_id)）不是 apt 系，请先手动安装"
    confirm "缺少 ${missing[*]}，现在用 apt 安装？" y || die "请先安装 ${missing[*]}"
    as_root apt-get update -qq
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git make python3 curl ca-certificates >/dev/null
  fi
  ok "工具齐全"
}

ensure_docker() {
  step "检查 Docker 与 Compose v2"
  if ! command -v docker >/dev/null; then
    confirm "没有安装 Docker，现在用官方脚本（get.docker.com）安装？" y || die "请先安装 Docker（docs/deploy-guide.md 第 2 步）"
    curl -fsSL https://get.docker.com | as_root sh
  fi
  if ! docker info >/dev/null 2>&1; then
    as_root systemctl enable --now docker >/dev/null 2>&1 || true
    docker info >/dev/null 2>&1 || die "Docker 没有运行，或当前用户不能使用 Docker（用 root 运行，或把用户加入 docker 组后重新登录）"
  fi
  if ! docker compose version >/dev/null 2>&1; then
    apt_based && confirm "缺少 Compose v2 插件，现在安装 docker-compose-plugin？" y && as_root apt-get install -y -qq docker-compose-plugin >/dev/null
    docker compose version >/dev/null 2>&1 || die "缺少 docker compose v2（安装 docker-compose-plugin）"
  fi
  ok "$(docker version --format 'Docker {{.Server.Version}}' 2>/dev/null)，$(docker compose version --short 2>/dev/null | sed 's/^/Compose /')"
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

# 用 curl | bash 运行时没有本地代码：克隆后改用克隆下来的脚本继续（stdin 换成终端，菜单才能读输入）
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
  [[ -n $ACTION ]] && args+=("$ACTION")
  args+=(--dir "$target")
  [[ -n $REF ]] && args+=(--ref "$REF")
  [[ -n $DOMAIN ]] && args+=(--domain "$DOMAIN")
  [[ -n $EMAIL ]] && args+=(--email "$EMAIL")
  [[ -n $BASE_URL ]] && args+=(--base-url "$BASE_URL")
  (( ASSUME_YES )) && args+=(--yes)
  [[ -n $CONFIRM ]] && args+=(--confirm "$CONFIRM")
  [[ -n $ENV_CHOICE ]] && args+=("$ENV_CHOICE")
  if (exec </dev/tty) 2>/dev/null; then exec bash "$target/scripts/deploy.sh" "${args[@]}" </dev/tty; fi
  exec bash "$target/scripts/deploy.sh" "${args[@]}" </dev/null
}

# ---------------------------------------------------------------- 三个参数
valid_domain() { [[ $1 =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; }
valid_email() { [[ $1 =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]]; }

ask_parameters() {
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
  resolved="$(getent ahosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')"
  if [[ -z $resolved ]]; then
    warn "$DOMAIN 还没有解析。先在域名服务商那里加 A 记录指向本机公网 IP，否则证书申请会失败"
    confirm "仍然继续？（服务能启动，证书等解析生效后会自动申请）" n || die "请先配置 DNS"
    return 0
  fi
  local_ips="$(hostname -I 2>/dev/null || true)"
  local ip match=0
  for ip in $resolved; do [[ " $local_ips " == *" $ip "* ]] && match=1; done
  if (( match )); then ok "$DOMAIN → $resolved（是本机地址）"
  else
    warn "$DOMAIN → $resolved，本机网卡地址是 ${local_ips:-（未知）}"
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
      if confirm "ufw 防火墙已启用但没有放行 80 / 443，现在放行？" y; then
        as_root ufw allow 80/tcp >/dev/null && as_root ufw allow 443 >/dev/null && ok "ufw 已放行 80 / 443"
      fi
    fi
  fi
  info "云服务器还要在控制台的安全组里放行 80 和 443（不要开放 8080）"
}

# ---------------------------------------------------------------- .env
postgres_volume_exists() { docker volume inspect "${PROJECT}_postgres-data" >/dev/null 2>&1; }

prepare_env() {
  step "生成并检查 .env"
  if [[ ! -f $ROOT/.env ]]; then
    (cd "$ROOT" && python3 scripts/init_env.py >/dev/null)
    ok "已生成 .env（随机密码与密钥，权限 600）"
  elif ! (cd "$ROOT" && python3 scripts/init_env.py >/dev/null 2>&1); then
    (cd "$ROOT" && python3 scripts/init_env.py >/dev/null) || true
    warn ".env 有上面列出的问题（多半是从 .env.example 复制的，占位符没有替换）"
    confirm "备份当前 .env 并重新生成？（新的随机密钥；数据库密码之后会自动同步）" y || die "请按提示修改 .env 后重试"
    local backup
    backup="$ROOT/.env.bak-$(date +%Y%m%d%H%M%S)"
    mv "$ROOT/.env" "$backup" && chmod 600 "$backup"
    (cd "$ROOT" && python3 scripts/init_env.py >/dev/null)
    ok "已重新生成 .env（旧文件：$backup）"
  else
    ok ".env 已存在，保留原有密钥"
  fi
  env_set APP_ENV production
  env_set COMPOSE_FILE compose.yaml:compose.https.yaml
  env_set SITE_DOMAIN "$DOMAIN"
  env_set ACME_EMAIL "$EMAIL"
  env_set PUBLIC_BASE_URL "$BASE_URL"
  env_set HTTP_BIND 127.0.0.1
  (cd "$ROOT" && python3 scripts/init_env.py >/dev/null) || die ".env 检查没有通过（见上面的说明）"
  ok "生产模式、HTTPS（compose.https.yaml）、域名与邮箱已写入 .env"
}

# ---------------------------------------------------------------- 启动、迁移、检查
start_services() {
  step "构建并启动服务（make up，首次需要几分钟）"
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
    info "安装凭据：$(cd "$ROOT" && make -s setup-token)"
    info "装好后把 $ROOT/.env 里 SETUP_TOKEN= 后面的值清空，再在 $ROOT 里运行 make up"
  else
    ok "已经完成首次安装，直接用管理员账号登录 $BASE_URL"
  fi
}

remind_backup_env() {
  info "请把 $ROOT/.env 备份到服务器以外的地方（密码管理器）：其中的 CREDENTIAL_ENCRYPTION_KEY、BACKUP_PASSWORD 丢了就无法解密凭证、打开备份"
}

# ---------------------------------------------------------------- 动作
do_install() {
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
  remind_backup_env
}

run_backup() {
  step "升级前备份数据库（make backup，加密的 restic 仓库）"
  # shellcheck source=/dev/null
  if [[ -f $BACKUP_ENV_FILE ]]; then set -a; . "$BACKUP_ENV_FILE"; set +a; info "已读取 $BACKUP_ENV_FILE"; fi
  if (cd "$ROOT" && make backup); then ok "备份完成"; return 0; fi
  warn "备份失败"
  confirm "不备份继续升级？（不推荐）" n || die "已取消升级"
}

do_upgrade() {
  [[ -f $ROOT/.env ]] || die "还没有部署过（找不到 $ROOT/.env），先运行 install"
  ensure_docker
  if [[ -z $AFTER_PULL ]]; then
    step "检查更新"
    local dirty old target
    dirty="$(git -C "$ROOT" status --porcelain --untracked-files=no)"
    [[ -z $dirty ]] || die "代码目录有本地修改，先处理（git -C $ROOT status）：$(printf '%s' "$dirty" | head -5 | tr '\n' ' ')"
    old="$(git -C "$ROOT" rev-parse HEAD)"
    target="${REF:-main}"
    git -C "$ROOT" fetch -q --tags origin
    local new
    new="$(git -C "$ROOT" rev-parse "origin/$target" 2>/dev/null || git -C "$ROOT" rev-parse "$target^{commit}")"
    if [[ $new == "$old" ]]; then
      ok "已经是最新版本（$(git -C "$ROOT" rev-parse --short HEAD)）"
      confirm "仍然重新构建并检查一遍？" n || return 0
    else
      info "当前 $(git -C "$ROOT" log -1 --format='%h %cs %s' "$old")"
      info "目标 $(git -C "$ROOT" log -1 --format='%h %cs %s' "$new")（$(git -C "$ROOT" rev-list --count "$old..$new") 个新提交）"
      confirm "开始升级？" y || return 0
      if compose ps --status running --services 2>/dev/null | grep -qx postgres; then run_backup; else warn "服务没有运行，跳过升级前备份"; fi
      mkdir -p "$ROOT/backups" && cp "$ROOT/.env" "$ROOT/backups/env-$(git -C "$ROOT" rev-parse --short "$old")-$(date +%Y%m%d%H%M%S)" && chmod 600 "$ROOT"/backups/env-*
      if git -C "$ROOT" show-ref -q --verify "refs/remotes/origin/$target"; then
        git -C "$ROOT" checkout -q "$target" && git -C "$ROOT" merge -q --ff-only "origin/$target"
      else
        git -C "$ROOT" checkout -q "$target"
      fi
      ok "代码已更新到 $(git -C "$ROOT" rev-parse --short HEAD)"
    fi
    # 用新版本的脚本完成剩下的步骤
    local args=(upgrade --dir "$ROOT" --after-pull "$old")
    (( ASSUME_YES )) && args+=(--yes)
    exec bash "$ROOT/scripts/deploy.sh" "${args[@]}"
  fi
  step "更新 .env（补上新版本新增的变量并检查）"
  (cd "$ROOT" && python3 scripts/init_env.py) || die ".env 检查没有通过"
  BASE_URL="$(env_get PUBLIC_BASE_URL)"
  start_services
  migrate
  smoke
  [[ $BASE_URL == https://* ]] && check_https
  step "升级完成：$(git -C "$ROOT" log -1 --format='%h %cs %s')"
  if [[ $AFTER_PULL != "$(git -C "$ROOT" rev-parse HEAD)" ]]; then
    info "回滚：没有新迁移时 git -C $ROOT checkout $(git -C "$ROOT" rev-parse --short "$AFTER_PULL") 后再 make up；有新迁移见 docs/deployment.md 第 5.3 节"
  fi
}

project_exists() {
  [[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$1" 2>/dev/null)" ]] \
    || [[ -n "$(docker volume ls -q --filter "label=com.docker.compose.project=$1" 2>/dev/null)" ]]
}

do_reset() {
  ensure_docker
  step "清空项目"
  local projects=() name
  for name in "$PROJECT" "${OPTIONAL_PROJECTS[@]}"; do project_exists "$name" && projects+=("$name"); done
  if ((${#projects[@]} == 0)); then ok "没有找到本项目的容器与数据卷"; else
    warn "将删除下面这些 Compose 项目的全部容器、网络与数据卷（数据库、证书等，无法恢复）："
    for name in "${projects[@]}"; do
      info "$name：$(docker volume ls -q --filter "label=com.docker.compose.project=$name" | tr '\n' ' ')"
    done
    info "Caddy 的证书也会删除；重新部署会重新申请，Let's Encrypt 对同一域名每周最多签发 5 次"
  fi
  if ((${#projects[@]})) && compose ps --status running --services 2>/dev/null | grep -qx postgres && [[ -f $ROOT/.env ]]; then
    confirm "清空前先备份数据库？（make backup；备份在 BACKUP_REPOSITORY，不会被清空）" y && { run_backup || true; }
  fi
  local delete_env=0
  case "$ENV_CHOICE" in
    --delete-env) delete_env=1 ;;
    --keep-env) delete_env=0 ;;
    *) [[ -f $ROOT/.env ]] && confirm "同时删除 .env（所有密钥）？保留的话重新部署会沿用原来的密钥" n && delete_env=1 ;;
  esac
  if (( ASSUME_YES )) || ! interactive; then
    [[ $CONFIRM == "$PROJECT" ]] || die "无人值守清空必须加 --confirm $PROJECT"
  else
    local typed
    read -r -p "确认清空请输入项目名 $PROJECT: " typed || true
    [[ $typed == "$PROJECT" ]] || die "输入不一致，已取消"
  fi
  for name in "${projects[@]}"; do
    # 只给项目名：按标签找到该项目的全部容器（包括 --profile 启动的 agent-gateway、监控等）、网络与数据卷
    docker compose -p "$name" down -v --remove-orphans
    ok "已删除 $name"
  done
  if (( delete_env )) && [[ -f $ROOT/.env ]]; then
    local keep
    keep="$ROOT/backups/env-deleted-$(date +%Y%m%d%H%M%S)"
    mkdir -p "$ROOT/backups" && mv "$ROOT/.env" "$keep" && chmod 600 "$keep"
    ok "已移走 .env（留了一份在 $keep，确认不需要后自己删除）"
  fi
  step "清空完成"
  info "代码目录 $ROOT 与 backups/ 保留；重新部署：sudo bash $ROOT/scripts/deploy.sh install"
}

do_status() {
  ensure_docker
  step "状态"
  info "代码：$(git -C "$ROOT" log -1 --format='%h %cs %s' 2>/dev/null || echo 未知)"
  [[ -f $ROOT/.env ]] || { warn "还没有 .env（未部署）"; return 0; }
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
  show_setup
}

menu() {
  interactive || die "没有终端可以显示菜单：请指定动作（install / upgrade / reset / status）"
  cat >&2 <<EOF

${C_B}My Workspace 部署脚本${C_0}（代码目录：$ROOT）
  1) 部署（首次安装，或重新检查并修复）
  2) 升级到最新版本（自动备份、迁移、检查）
  3) 清空项目（删除全部容器与数据）
  4) 查看状态
  0) 退出
EOF
  local choice
  read -r -p "请选择 [1]: " choice || true
  case "${choice:-1}" in
    1) ACTION=install ;; 2) ACTION=upgrade ;; 3) ACTION=reset ;; 4) ACTION=status ;; 0) exit 0 ;;
    *) die "无效的选择：$choice" ;;
  esac
}

parse_args() {
  while (($#)); do
    case "$1" in
      install|upgrade|reset|status) ACTION="$1" ;;
      --dir) DIR="${2:?--dir 需要目录}"; shift ;;
      --ref) REF="${2:?--ref 需要分支或 tag}"; shift ;;
      --domain) DOMAIN="${2:?}"; shift ;;
      --email) EMAIL="${2:?}"; shift ;;
      --base-url) BASE_URL="${2:?}"; shift ;;
      --yes|-y) ASSUME_YES=1 ;;
      --confirm) CONFIRM="${2:?}"; shift ;;
      --keep-env|--delete-env) ENV_CHOICE="$1" ;;
      --after-pull) AFTER_PULL="${2:?}"; shift ;;
      -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || echo "用法见 docs/deploy-guide.md"; exit 0 ;;
      *) die "未知参数：$1（--help 查看用法）" ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"
  if [[ -n $DIR ]]; then
    [[ -f $DIR/compose.yaml && -f $DIR/scripts/init_env.py ]] && ROOT="$(cd "$DIR" && pwd)"
  else
    ROOT="$(find_checkout || true)"
  fi
  [[ -n $ROOT ]] || bootstrap
  [[ -n $ACTION ]] || menu
  case "$ACTION" in
    install) ensure_tools; do_install ;;
    upgrade) ensure_tools; do_upgrade ;;
    reset) do_reset ;;
    status) do_status ;;
  esac
}

# 整个脚本读完再执行：curl | bash 时下载中断不会只执行一半
main "$@"
