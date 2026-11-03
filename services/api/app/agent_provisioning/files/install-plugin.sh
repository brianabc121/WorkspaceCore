# 在锁定的 OpenClaw 镜像里以 node 用户运行（T-0089）：脚本从标准输入读入（docker compose run ... sh -s）。
# 与接入包 CHECKLIST.md 相同：下载锁定版本的 openclaw-channel、核对发布包 sha256，
# 再用一份临时的空配置安装（state/openclaw.json 引用了插件提供的 openim 渠道，插件装好之前这份配置无效）。
# @PACKAGE@、@PLUGIN_NAME@、@PLUGIN_VERSION@ 与 @SHA256@ 由工作台填入（app/agent_provisioning 的锁定值，与 docs/versions.md 一致）。
set -eu
# 重新部署：已经装了锁定的版本就不再装（OpenClaw 运行过之后 state/ 里已有插件设置，
# 带 --force 重装会被拒绝：Plugin settings are not ready for activation，T-0089 B3 的 CI 端到端里遇到）
installed=/home/node/.openclaw/extensions/openclaw-channel/package.json
if [ -f "$installed" ] && node -e '
  const p = require(process.argv[1]);
  process.exit(p.name === process.argv[2] && p.version === process.argv[3] ? 0 : 1);
' "$installed" "@PLUGIN_NAME@" "@PLUGIN_VERSION@"; then
  echo "plugin_already_installed @PLUGIN_VERSION@"
  exit 0
fi
package="@PACKAGE@"
expected="@SHA256@"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
npm pack "$package" --ignore-scripts --loglevel=error --pack-destination "$work" >/dev/null
set -- "$work"/*.tgz
tarball="$1"
actual="$(sha256sum "$tarball" | cut -d ' ' -f 1)"
if [ "$actual" != "$expected" ]; then
  echo "plugin_sha256_mismatch ${actual}" >&2
  exit 6
fi
echo '{}' > /home/node/.openclaw/plugin-install.json
OPENCLAW_CONFIG_PATH=/home/node/.openclaw/plugin-install.json \
  node openclaw.mjs plugins install "$tarball" --force --accept-capabilities
