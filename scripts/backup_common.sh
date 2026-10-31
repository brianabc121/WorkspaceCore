# backup.sh 与 restore-verify.sh 共用的函数（由它们 source，不单独运行）。
# restic 版本与 digest 的核验见 docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md 第 3 节。

RESTIC_IMAGE="restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510"

# 读取配置：shell 里的同名变量优先，其次是 .env，最后是默认值（与 docker compose 的插值顺序一致）
env_value() {
  local name="$1" default="${2:-}" value
  if [[ -n "${!name+x}" ]]; then
    printf '%s' "${!name}"
    return
  fi
  value="$(sed -n "s/^${name}=//p" "$ROOT/.env" 2>/dev/null | tail -n 1)"
  printf '%s' "${value:-$default}"
}

# 准备临时目录、仓库密码文件与仓库地址；退出时删除临时目录
restic_prepare() {
  local password repository
  password="$(env_value BACKUP_PASSWORD)"
  if [[ -z "$password" || "$password" == GENERATE_* ]]; then
    echo "缺少 BACKUP_PASSWORD：运行 make env 生成，或在 .env 里设置（丢失就无法恢复，请单独保管）" >&2
    exit 1
  fi
  work="$(umask 077 && mktemp -d)"
  trap 'rm -rf -- "$work"' EXIT
  (umask 077 && printf '%s' "$password" > "$work/password")

  repository="$(env_value BACKUP_REPOSITORY ./backups/restic)"
  restic_mounts=(-v "$work/password:/run/restic-password:ro")
  if [[ "$repository" =~ ^[a-z0-9]+: ]]; then
    restic_repository="$repository"   # s3:… / sftp:… / rest:… 等远端仓库
  else
    mkdir -p "$repository"
    restic_mounts+=(-v "$(cd "$repository" && pwd):/repo")
    restic_repository="/repo"
    echo "提示：仓库在本机目录 $repository，不是异地备份；生产环境请配置 BACKUP_REPOSITORY 为异地仓库" >&2
  fi
  restic_env=(-e RESTIC_REPOSITORY="$restic_repository" -e RESTIC_PASSWORD_FILE=/run/restic-password)
  local name
  for name in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION AWS_SESSION_TOKEN; do
    [[ -n "${!name:-}" ]] && restic_env+=(-e "$name")
  done
  return 0
}

# restic_run [额外的 docker run 参数…] -- <restic 子命令与参数…>
restic_run() {
  local docker_args=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do docker_args+=("$1"); shift; done
  shift
  docker run --rm -i -u "$(id -u):$(id -g)" "${restic_env[@]}" "${restic_mounts[@]}" "${docker_args[@]}" \
    "$RESTIC_IMAGE" --no-cache --quiet "$@"
}

# 退出码见 restic doc/075_scripting.rst：10 = 仓库不存在，12 = 密码不对
restic_init_if_needed() {
  local code=0
  restic_run -- cat config >/dev/null 2>&1 || code=$?
  case "$code" in
    0) ;;
    10) echo "初始化 restic 仓库"; restic_run -- init ;;
    12) echo "BACKUP_PASSWORD 与仓库不匹配：请使用创建这个仓库时的密码" >&2; exit 1 ;;
    *) echo "无法打开 restic 仓库（restic 退出码 $code）" >&2; restic_run -- cat config >/dev/null || true; exit 1 ;;
  esac
}
