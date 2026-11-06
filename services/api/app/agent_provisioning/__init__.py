"""通过 SSH 自动部署 OpenClaw（T-0089 B2）：远端脚本、锁定值与远端命令。

设计见 docs/decisions/2026-11-03-openclaw-ssh-provisioning.md 第 5 节。脚本放在 `files/`：

- `inspect.sh`：目标服务器上以登录账号运行（`sh -s`）。只读检查发行版、架构、账号、
  Docker / Compose / sudo / crontab；
- `install-docker.sh`：目标服务器上以 root 运行（上传后 `sudo -S bash`）。
  从 Docker 官方软件源安装锁定版本，把账号加入 docker 组；
- `install-plugin.sh`：锁定的 OpenClaw 镜像里以 node 用户运行（`sh -s`）。
  下载并核对 openclaw-channel，用临时的空配置安装。

远端命令里的路径都经过 `shlex.quote`；密码只从标准输入传给 `sudo -S`，不出现在命令行里。
"""

import shlex
from importlib import resources

from app.agent_bundle import OPENCLAW_VERSION, PLUGIN_VERSION

# 与接入包 compose.yaml 锁定的镜像相同（测试核对）
OPENCLAW_IMAGE = (
    f"openclaw/openclaw:{OPENCLAW_VERSION}"
    "@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f"
)
PLUGIN_NAME = "@openim/openclaw-channel"
PLUGIN_PACKAGE = f"{PLUGIN_NAME}@{PLUGIN_VERSION}"
# 发布包 sha256：与 docs/versions.md、接入包 CHECKLIST.md 一致（测试核对）
PLUGIN_SHA256 = "3d10dca0edcba0ecdb0dc915e7cf1ddf5a8453165de13dec76bbe5ef85607643"
# 官方镜像里运行 OpenClaw 的用户（node）
OPENCLAW_UID = 1000
# 接入包在登录账号主目录下的位置；安装用的临时文件在另一个目录，用完删除
INSTALL_DIR_NAME = "myworkspace-openclaw"
STAGING_DIR_NAME = ".myworkspace-install"
# crontab 里本工作台写入的行都带这个标记；重装时先删掉带标记的行再写
CRON_MARKER = "# myworkspace-openclaw"
# 交给 uid 1000 的子目录（OpenClaw 的状态、取密钥程序、隧道证书）
OWNED_DIRS = ("state", "bin", "tls")

# 安装任务的步骤（ADR 第 5.2 节），顺序即执行顺序
STEPS = (
    "connect",
    "inspect",
    "authorize_key",
    "docker",
    "reconnect",
    "upload",
    "pull",
    "plugin",
    "start",
    "schedule",
    "online",
    "reflection",
)

# 卸载任务的步骤（ADR 第 6 节）
UNINSTALL_STEPS = ("connect", "inspect", "stop", "unschedule", "remove_files")

_FILES = resources.files("app.agent_provisioning") / "files"


def script(name: str) -> str:
    return (_FILES / name).read_text(encoding="utf-8")


def plugin_script() -> str:
    return (
        script("install-plugin.sh")
        .replace("@PACKAGE@", PLUGIN_PACKAGE)
        .replace("@PLUGIN_NAME@", PLUGIN_NAME)
        .replace("@PLUGIN_VERSION@", PLUGIN_VERSION)
        .replace("@SHA256@", PLUGIN_SHA256)
    )


def q(value: str) -> str:
    return shlex.quote(value)


def authorize_key_command(public_key: str) -> str:
    """把专用公钥追加到 ~/.ssh/authorized_keys；已经有同一行就不重复。

    已有文件的最后一行没有换行符时先补一个，免得专用公钥接在原来那行的末尾。
    """
    key = q(public_key)
    path = "~/.ssh/authorized_keys"
    return (
        f"umask 077 && mkdir -p ~/.ssh && touch {path}"
        f" && (grep -qxF {key} {path}"
        f' || {{ if [ -s {path} ] && [ -n "$(tail -c 1 {path})" ]; then echo >> {path}; fi'
        f" && printf '%s\\n' {key} >> {path}; }})"
        f" && chmod 700 ~/.ssh && chmod 600 {path}"
    )


def _chown_with_image(directory: str, owner: str) -> str:
    """登录账号既不是 root 也不是 uid 1000 时：用锁定镜像里的 chown（以 root 运行，不联网）。

    只处理已经存在的子目录。
    """
    paths = " ".join(f"{d}" for d in OWNED_DIRS)
    return (
        f"cd {q(directory)} && set -- && for d in {paths}; do"
        ' [ -e "$d" ] && set -- "$@" "/target/$d"; done;'
        ' [ "$#" -eq 0 ] || docker run --rm --network none --user 0 --entrypoint chown'
        f' -v {q(directory)}:/target {OPENCLAW_IMAGE} -R {owner} "$@"'
    )


def reclaim_command(directory: str, *, uid: int, gid: int) -> str | None:
    """重装前把 state/、bin/、tls/ 收回给登录账号，才能用 SFTP 覆盖里面的文件。"""
    if uid in (0, OPENCLAW_UID):
        return None
    return _chown_with_image(directory, f"{uid}:{gid}")


def handover_command(directory: str, *, uid: int) -> str | None:
    """state/、bin/、tls/ 交给运行 OpenClaw 的 uid 1000（接入包 CHECKLIST.md 的要求）。"""
    if uid == OPENCLAW_UID:
        return None
    owner = f"{OPENCLAW_UID}:{OPENCLAW_UID}"
    if uid == 0:
        paths = " ".join(q(f"{directory}/{d}") for d in OWNED_DIRS)
        return f'for p in {paths}; do [ ! -e "$p" ] || chown -R {owner} "$p"; done'
    return _chown_with_image(directory, owner)


def compose_command(directory: str, *args: str) -> str:
    return f"cd {q(directory)} && docker compose " + " ".join(args)


def cron_lines(directory: str) -> list[str]:
    """每 10 分钟取一次轮换后的模型密钥；每周一重启换上新的 IM 令牌；
    每分钟上报一次 token 用量（时间轴，T-0104）与服务器状态（T-0113）、
    领取到期的唤醒（T-0121）与终止令（T-0126）。见接入包 CHECKLIST.md。"""
    cd = f"cd {q(directory)}"
    return [
        f"*/10 * * * * {cd} && docker compose exec -T openclaw node openclaw.mjs secrets reload"
        f" --json >/dev/null 2>&1 {CRON_MARKER}",
        f"* * * * * {cd} && docker compose exec -T openclaw node"
        f" /opt/myworkspace/bin/usage-report.mjs >/dev/null 2>&1 {CRON_MARKER}",
        f"* * * * * {cd} && docker compose exec -T openclaw node"
        f" /opt/myworkspace/bin/host-report.mjs >/dev/null 2>&1 {CRON_MARKER}",
        f"* * * * * {cd} && docker compose exec -T openclaw node"
        f" /opt/myworkspace/bin/wakeup.mjs >/dev/null 2>&1 {CRON_MARKER}",
        f"* * * * * {cd} && docker compose exec -T openclaw node"
        f" /opt/myworkspace/bin/stop-runs.mjs >/dev/null 2>&1 {CRON_MARKER}",
        f"17 4 * * 1 {cd} && docker compose restart openclaw >/dev/null 2>&1 {CRON_MARKER}",
    ]


def schedule_command(directory: str) -> str:
    """替换当前账号 crontab 里带标记的行（没有 crontab 时新建）。"""
    lines = " ".join(q(line) for line in cron_lines(directory))
    return (
        f"{{ crontab -l 2>/dev/null | grep -vF {q(CRON_MARKER)}; printf '%s\\n' {lines}; }}"
        " | crontab -"
    )


def reflection_command(directory: str, tries: int, wait: int) -> str:
    """建（或更新）每晚复盘的 OpenClaw 定时任务：运行接入包里的 setup-reflection.sh（T-0100）。

    刚启动的 OpenClaw 网关可能还没就绪，脚本按 tries / wait 重试。
    """
    return (
        f"cd {q(directory)} && MYWS_REFLECTION_TRIES={int(tries)} "
        f"MYWS_REFLECTION_WAIT={int(wait)} sh setup-reflection.sh"
    )


def unschedule_command() -> str:
    """删掉当前账号 crontab 里带标记的行，其他行保留。"""
    return f"crontab -l 2>/dev/null | grep -vF {q(CRON_MARKER)} | crontab -"


# 按字段找这把公钥：awk 默认按连续的空格 / 制表符分字段，行首带选项、两字段之间是制表符或多个空格、
# 行尾注释不同的都算同一把（只比较相邻的“类型 公钥数据”两个字段）
_AWK_MATCH = "{for (i = 1; i < NF; i++) if ($i == kt && $(i + 1) == kd) next; print}"
_AWK_FOUND = "{for (i = 1; i < NF; i++) if ($i == kt && $(i + 1) == kd) f = 1} END {exit !f}"


def revoke_key_command(public_key: str) -> str:
    """从 ~/.ssh/authorized_keys 删掉这把公钥所在的行，其他行原样保留（先写临时文件再替换）。

    最后再按同样的字段规则检查一遍：还能找到这把公钥就返回失败，不会误报已吊销。
    """
    kind, data = public_key.split()[:2]
    awk_vars = f"-v kt={q(kind)} -v kd={q(data)}"
    path = "~/.ssh/authorized_keys"
    return (
        f"[ -f {path} ] || exit 0; t=$(mktemp {path}.XXXXXX) || exit 1;"
        f' awk {awk_vars} {q(_AWK_MATCH)} {path} > "$t" || {{ rm -f "$t"; exit 1; }};'
        f' chmod 600 "$t" && mv "$t" {path} && ! awk {awk_vars} {q(_AWK_FOUND)} {path}'
    )


def remove_dir_command(directory: str) -> str:
    """删除接入包目录（只接受 <主目录>/myworkspace-openclaw 这一种路径）。"""
    if not directory.endswith("/" + INSTALL_DIR_NAME):
        raise ValueError("只能删除接入包目录")
    return f"rm -rf -- {q(directory)}"
