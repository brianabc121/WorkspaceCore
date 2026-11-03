"""一键部署脚本在服务器上安装的 Python 3.12（T-0074）。

应用跑在 Docker 镜像里；服务器上的项目脚本（make env、改写 .env、备份）
用 deploy.sh 安装的 Python 3.12，与 api 镜像是同一个小版本。
版本与校验和的来源见 docs/decisions/2026-11-02-host-python-3-12.md。
"""

import os
import pty
import re
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = (ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")
# 直接调用系统 python3（或其他版本）运行脚本、-c 或 heredoc 的写法
BARE_CALL = re.compile(r"(^|[\s(@])python3(\.\d+)? (-c |- |scripts/|tests/)")


def _value(name: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"$', DEPLOY, re.MULTILINE)
    assert match, f"deploy.sh 里没有 {name}"
    return match.group(1)


def test_the_pinned_python_matches_the_api_image() -> None:
    version = _value("PY_VERSION")
    assert re.fullmatch(r"3\.12\.\d+", version)
    dockerfile = (ROOT / "services/api/Dockerfile").read_text(encoding="utf-8")
    minors = set(re.findall(r"^FROM python:(\d+\.\d+)\.\d+", dockerfile, re.MULTILINE))
    assert minors == {version.rsplit(".", 1)[0]}


def test_the_download_is_pinned_by_build_and_checksum() -> None:
    assert re.fullmatch(r"\d{8}", _value("PY_BUILD"))
    sums = [_value("PY_SHA256_X86_64"), _value("PY_SHA256_AARCH64")]
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in sums)
    assert len(set(sums)) == 2
    assert "sha256sum -c" in DEPLOY


def test_scripts_run_with_the_resolved_interpreter() -> None:
    # 系统的 python3 可能是 3.9、3.11，也可能没有：deploy.sh 只用 ensure_python 选出的 $PY
    assert [line for line in DEPLOY.splitlines() if BARE_CALL.search(line)] == []
    assert '"$PY" scripts/init_env.py' in DEPLOY
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "python3.12" in makefile
    assert [line for line in makefile.splitlines() if BARE_CALL.search(line)] == []


# ---------------------------------------------------------------- 停用旧的 Vikunja（T-0082）

PROJECT_LABEL = "label=com.docker.compose.project=my-workspace-vikunja"
FAKE_DOCKER = f"""#!/usr/bin/env bash
# 假的 docker：记下调用；按 Compose 项目标签返回预先放好的容器、网络、数据卷
echo "$*" >> "$LOG"
case "$*" in
  "ps -aq --filter {PROJECT_LABEL}") cat "$STATE/containers" 2>/dev/null ;;
  "network ls -q --filter {PROJECT_LABEL}") echo net1 ;;
  "volume ls -q --filter {PROJECT_LABEL}")
    printf 'my-workspace-vikunja_files\\nmy-workspace-vikunja_db\\n' ;;
esac
exit 0
"""


def _retire(tmp_path: Path, *, containers: str, site: bool) -> tuple[str, str]:
    """只加载 deploy.sh 的函数（去掉最后一行的 main），在临时目录里运行 retire_vikunja。"""
    import os
    import subprocess

    root, state, bin_dir = tmp_path / "root", tmp_path / "state", tmp_path / "bin"
    for path in (root / "infra/caddy/apps-enabled", state, bin_dir):
        path.mkdir(parents=True)
    (root / ".env").write_text("VIKUNJA_DOMAIN=vikunja.example.com\nOTHER=1\n", encoding="utf-8")
    if site:
        (root / "infra/caddy/apps-enabled/vikunja.caddy").write_text("vikunja.example.com {}\n")
    (state / "containers").write_text(containers, encoding="utf-8")
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    functions = DEPLOY.rstrip().rsplit("\n", 1)[0]  # 最后一行是 main "$@"
    log = tmp_path / "docker.log"
    result = subprocess.run(  # noqa: S603 - 运行仓库里的脚本
        [
            "/bin/bash",
            "-c",
            functions + '\nPY=python3\nROOT="$1"\nretire_vikunja',
            "deploy",
            str(root),
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LOG": str(log),
            "STATE": str(state),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return result.stderr, calls


def test_upgrade_retires_old_vikunja_and_keeps_its_volumes(tmp_path: Path) -> None:
    out, calls = _retire(tmp_path, containers="c1\nc2\n", site=True)
    assert "rm -f c1 c2" in calls
    assert "network rm net1" in calls
    assert "volume rm" not in calls  # 数据卷保留，只打印删除命令
    assert "docker volume rm my-workspace-vikunja_files my-workspace-vikunja_db" in out
    assert not (tmp_path / "root/infra/caddy/apps-enabled/vikunja.caddy").exists()
    assert "caddy validate" in calls  # 删除站点后校验并重启 caddy
    env = (tmp_path / "root/.env").read_text(encoding="utf-8")
    assert "VIKUNJA_DOMAIN=\n" in env and "OTHER=1" in env


def test_upgrade_without_old_vikunja_does_nothing(tmp_path: Path) -> None:
    out, calls = _retire(tmp_path, containers="", site=False)
    assert out == ""
    assert calls.strip() == f"ps -aq --filter {PROJECT_LABEL}"
    assert "VIKUNJA_DOMAIN=vikunja.example.com" in (tmp_path / "root/.env").read_text()


# ---------------------------------------------------------------- OpenClaw 接入入口（T-0088）

FAKE_COMPOSE_DOCKER = """#!/usr/bin/env bash
# 假的 docker：记下调用；agent_ca 子命令返回预先放好的结果（假证书，不是真的 PEM）
echo "$*" >> "$LOG"
case "$*" in
  *"agent_ca check-server --host "*) cat >/dev/null; cat "$STATE/check" ;;
  *"agent_ca issue-server --host "*)
    args="$*"; host="${args##* }"
    printf '{"cert": "CERT %s", "key": "KEY %s", "ca": "CA", ' "$host" "$host"
    printf '"not_after": "2028-11-02T00:00:00+00:00"}' ;;
esac
exit 0
"""
FAKE_MAKE = '#!/usr/bin/env bash\necho "make $*" >> "$LOG"\n'
# 测试里替换掉交互、握手检查与 root 操作
OVERRIDES = r"""
confirm() { echo "confirm $1" >> "$LOG"; return 0; }
ask() {
  case $1 in
    *域名*) printf '%s\n' "${ANSWER_HOST:-$2}" ;;
    *IP*) printf '%s\n' "${ANSWER_IP:-$2}" ;;
    *) printf '%s\n' "$2" ;;
  esac
}
check_agent_ingress() { echo "handshake $*" >> "$LOG"; }
as_root() { echo "as_root $*" >> "$LOG"; }
outbound_ip() { echo 10.0.0.9; }
"""


def _ingress(
    tmp_path: Path,
    env: str,
    *,
    check: str = "renew",
    answers: dict[str, str] | None = None,
    umask: str = "022",
) -> tuple[str, str, dict[str, str]]:
    """在临时目录里运行 connect_agent_ingress；返回 (输出, docker/make 调用, .env)。"""
    import os
    import subprocess

    root, state, bin_dir = tmp_path / "root", tmp_path / "state", tmp_path / "bin"
    for path in (root, state, bin_dir):
        path.mkdir(parents=True, exist_ok=True)
    if not (root / ".env").exists():
        (root / ".env").write_text(env, encoding="utf-8")
    (state / "check").write_text(check + "\n", encoding="utf-8")
    for name, body in (("docker", FAKE_COMPOSE_DOCKER), ("make", FAKE_MAKE)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "calls.log"
    log.unlink(missing_ok=True)
    functions = DEPLOY.rstrip().rsplit("\n", 1)[0]
    result = subprocess.run(  # noqa: S603 - 运行仓库里的脚本
        [
            "/bin/bash",
            "-c",
            functions
            + OVERRIDES
            + f'\numask {umask}\nPY=python3\nROOT="$1"\nconnect_agent_ingress',
            "deploy",
            str(root),
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "LOG": str(log),
            "STATE": str(state),
            **(answers or {}),
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    values = dict(
        line.split("=", 1)
        for line in (root / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    return result.stderr, calls, values


def test_ingress_without_a_domain_uses_the_internal_name_and_an_ip(tmp_path: Path) -> None:
    out, calls, env = _ingress(
        tmp_path,
        "SITE_DOMAIN=\nAGENT_PUBLIC_URL=\n",
        answers={"ANSWER_HOST": "-", "ANSWER_IP": "203.0.113.7"},
    )
    host = "agent-gateway.myworkspace.internal"
    assert {key: env.get(key) for key in env if key.startswith("AGENT_")} == {
        "AGENT_PUBLIC_URL": f"https://{host}:9443",
        "AGENT_PUBLIC_HOST": host,
        "AGENT_PUBLIC_BIND": "0.0.0.0",  # noqa: S104 - 外网入口要对公网开放
        "AGENT_PUBLIC_PORT": "9443",
        "AGENT_PUBLIC_ADDRESS": "203.0.113.7",
        "AGENT_PUBLIC_INTERNAL_CA": "true",
    }
    tls = tmp_path / "root/infra/caddy/agent-tls"
    assert (tls / "public.crt").read_text() == f"CERT {host}"
    assert (tls / "public.key").read_text() == f"KEY {host}"
    assert (tls / "public.key").stat().st_mode & 0o777 == 0o600
    assert (tls / "public.crt").stat().st_mode & 0o777 == 0o644
    # 没有内网证书时 Caddy 起不来：签一张占位证书
    assert (tls / "intranet.crt").read_text() == "CERT intranet.invalid"
    assert "check-server" not in calls  # 还没有证书，直接签发
    assert f"agent_ca issue-server --host {host}" in calls
    assert "compose up -d api worker" in calls
    assert "make -s agents-up" in calls
    assert "compose --profile agents restart agent-gateway" in calls
    assert f"handshake {host} 9443 {tmp_path}/root/infra/caddy/agent-ca/agent-ca.pem" in calls
    assert "有效期到 2028-11-02" in out and "KEY" not in out


def test_ingress_with_a_domain_and_a_still_valid_certificate(tmp_path: Path) -> None:
    _, calls, env = _ingress(tmp_path, "SITE_DOMAIN=ws.example.com\nAGENT_PUBLIC_URL=\n")
    assert env["AGENT_PUBLIC_HOST"] == "ws.example.com"
    assert env["AGENT_PUBLIC_URL"] == "https://ws.example.com:9443"
    assert env.get("AGENT_PUBLIC_ADDRESS", "") == ""  # 域名在公网 DNS 里，不需要映射
    assert "issue-server --host ws.example.com" in calls

    # 再运行一次：证书仍然有效、变量没变 → 不重签、不重启
    out, calls, again = _ingress(tmp_path, "", check="ok")
    assert again == env
    assert "check-server --host ws.example.com" in calls
    assert "issue-server" not in calls and "up -d api worker" not in calls
    assert "restart agent-gateway" not in calls
    assert "make -s agents-up" in calls  # 入口没有运行时会启动它
    assert "入口证书仍然有效" in out


def test_ingress_reissues_when_the_address_changes(tmp_path: Path) -> None:
    answers = {"ANSWER_HOST": "-", "ANSWER_IP": "203.0.113.7"}
    _ingress(tmp_path, "AGENT_PUBLIC_URL=\n", answers=answers)
    _, calls, env = _ingress(
        tmp_path, "", check="ok", answers={**answers, "ANSWER_IP": "198.51.100.2"}
    )
    assert env["AGENT_PUBLIC_ADDRESS"] == "198.51.100.2"
    assert "issue-server" not in calls  # 证书里是主机名，地址变了不用重签
    assert "compose up -d api worker" in calls  # 接入包里的地址变了，api 要读到新值


def test_ingress_keeps_a_manually_configured_public_entry(tmp_path: Path) -> None:
    out, calls, env = _ingress(tmp_path, "AGENT_PUBLIC_URL=https://agent.example.com:9443\n")
    assert calls == "" and "保持不变" in out
    assert env == {"AGENT_PUBLIC_URL": "https://agent.example.com:9443"}


def test_ingress_refuses_an_invalid_address(tmp_path: Path) -> None:
    out, calls, env = _ingress(
        tmp_path, "AGENT_PUBLIC_URL=\n", answers={"ANSWER_HOST": "-", "ANSWER_IP": "not-an-ip"}
    )
    assert "不是 IP 地址" in out and "agent_ca" not in calls
    assert env == {"AGENT_PUBLIC_URL": ""}


def test_ingress_creates_a_custom_tls_dir_the_gateway_can_enter(tmp_path: Path) -> None:
    # agent-gateway 去掉了全部 capability：新建的证书目录不能是 700（umask 077 时也一样）
    custom = tmp_path / "custom-tls"
    _ingress(
        tmp_path,
        f"SITE_DOMAIN=ws.example.com\nAGENT_PUBLIC_URL=\nAGENT_TLS_DIR={custom}\n",
        umask="077",
    )
    assert custom.stat().st_mode & 0o777 == 0o755
    assert (custom / "public.key").stat().st_mode & 0o777 == 0o600
    assert (custom / "public.crt").read_text() == "CERT ws.example.com"


# ---------------------------------------------------------------- SSH 断线后继续运行（T-0092）

FAKE_TMUX = """#!/usr/bin/env bash
# 假的 tmux：每次调用记一行（参数用 \\x1f 分隔，保留空格）
# has-session 按 $STATE/session 是否存在返回；
# 设置 ATTACH_FAILS 时 attach 失败（像真的 tmux 那样报错）
{ printf '%s\\x1f' "$@"; printf '\\n'; } >> "$LOG"
for arg in "$@"; do [[ $arg == has-session ]] && { [[ -f $STATE/session ]]; exit; }; done
for arg in "$@"; do
  [[ $arg == attach && -n ${ATTACH_FAILS:-} ]] \\
    && { echo "open terminal failed: can't use /dev/tty" >&2; exit 1; }
done
exit 0
"""


def _keep_running(
    tmp_path: Path,
    action: str = "install",
    *,
    terminal: bool = True,
    multiplexer: bool = False,
    session: bool = False,
    tmux: bool = True,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    """只加载 deploy.sh 的函数，在假的终端环境里运行 keep_running。"""
    root, state, bin_dir = tmp_path / "root", tmp_path / "state", tmp_path / "bin"
    for path in (root / "scripts", state, bin_dir):
        path.mkdir(parents=True, exist_ok=True)
    if session:
        (state / "session").touch()
    if tmux:
        (bin_dir / "tmux").write_text(FAKE_TMUX, encoding="utf-8")
        (bin_dir / "tmux").chmod(0o755)
    log = tmp_path / "tmux.log"
    functions = DEPLOY.rstrip().rsplit("\n", 1)[0]
    fake_tmux = 'tmux() { "$BIN/tmux" "$@"; }' if tmux else "tmux() { return 127; }"
    overrides = "\n".join(
        [
            f"has_terminal() {{ {'return 0' if terminal else 'return 1'}; }}",
            f"in_multiplexer() {{ {'return 0' if multiplexer else 'return 1'}; }}",
            # 只用假的 tmux：本机装了真的 tmux 时也不会真的建会话
            f"install_tmux() {{ {'return 0' if tmux else 'return 1'}; }}",
            fake_tmux,
            "",
        ]
    )
    script = (
        functions
        + "\n"
        + overrides
        + f'ROOT="$1" ACTION="{action}" ASSUME_YES=1 DOMAIN=ws.example.com\n'
        + 'keep_running\necho "keep_running returned"\n'
    )
    result = subprocess.run(  # noqa: S603 - 参数固定
        ["/bin/bash", "-c", script, "deploy", str(root)],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "LOG": str(log),
            "STATE": str(state),
            "BIN": str(bin_dir),
            **(env or {}),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    calls = [
        line.split("\x1f")[:-1] for line in (log.read_text().splitlines() if log.exists() else [])
    ]
    return result, calls


@pytest.mark.parametrize("action", ["install", "upgrade", "connect"])
def test_long_actions_move_into_a_tmux_session(tmp_path: Path, action: str) -> None:
    result, calls = _keep_running(tmp_path, action)
    assert result.returncode == 0, result.stderr
    # exec tmux attach 替换了脚本：后面的 echo 不会运行
    assert "keep_running returned" not in result.stdout
    assert [c[:4] for c in calls] == [
        ["-L", "my-workspace", "has-session", "-t"],
        ["-L", "my-workspace", "new-session", "-d"],
        ["-L", "my-workspace", "set-option", "-t"],
        ["-L", "my-workspace", "attach", "-t"],
    ]
    new = calls[1]
    assert new[new.index("-s") + 1] == "deploy"
    # 会话里重新运行同一个动作，参数原样转发；结束后等回车，最后的输出（安装凭据等）不会一闪而过
    script = str(tmp_path / "root/scripts/deploy.sh")
    tail = new[new.index(script) :]
    assert tail == [
        script,
        action,
        "--dir",
        str(tmp_path / "root"),
        "--domain",
        "ws.example.com",
        "--yes",
    ]
    assert "WORKSPACE_IN_TMUX=1" in new
    wrapper = new[new.index("-c") + 1]
    assert "read -r _" in wrapper and 'exit "$code"' in wrapper
    assert "SSH 断开后部署会继续" in result.stderr


def test_running_again_reattaches_instead_of_starting_twice(tmp_path: Path) -> None:
    result, calls = _keep_running(tmp_path, session=True)
    assert result.returncode == 0, result.stderr
    assert [c[2] for c in calls] == ["has-session", "attach"]
    assert "接回去" in result.stderr


@pytest.mark.parametrize(
    "case",
    [
        {"action": "status"},
        {"action": "reset"},
        {"terminal": False},
        {"multiplexer": True},
        {"env": {"WORKSPACE_NO_TMUX": "1"}},
    ],
)
def test_no_tmux_when_not_needed(tmp_path: Path, case: dict[str, object]) -> None:
    result, calls = _keep_running(tmp_path, **case)  # type: ignore[arg-type]
    assert result.returncode == 0, result.stderr
    assert "keep_running returned" in result.stdout
    assert calls == []


def test_without_tmux_the_script_continues_with_a_warning(tmp_path: Path) -> None:
    result, calls = _keep_running(tmp_path, tmux=False)
    assert result.returncode == 0, result.stderr
    assert "keep_running returned" in result.stdout
    assert "没有 tmux" in result.stderr
    assert calls == []


def test_the_session_waits_for_enter_and_keeps_the_exit_code(tmp_path: Path) -> None:
    """按假 tmux 记下的命令真正运行一次：脚本结束后显示退出码，按回车才关闭。"""
    _, calls = _keep_running(tmp_path)
    new = calls[1]
    command = new[new.index("env") :]
    script = Path(command[command.index("-c") + 2])
    script.write_text('echo "deploy ran with: $*"; exit 3\n', encoding="utf-8")
    result = subprocess.run(  # noqa: S603 - 参数来自上面的假 tmux 记录
        command, input="\n", capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode == 3
    assert "deploy ran with: install --dir" in result.stdout
    assert "退出码 3" in result.stdout


def test_when_attach_fails_the_deploy_keeps_running(tmp_path: Path) -> None:
    """T-0093：接回失败（例如 tmux 拒绝终端）时不当作部署失败：会话已经在跑，告诉用户怎么接回。"""
    result, calls = _keep_running(tmp_path, env={"ATTACH_FAILS": "1"})
    assert result.returncode == 0, result.stderr
    assert "keep_running returned" not in result.stdout
    assert [c[2] for c in calls] == ["has-session", "new-session", "set-option", "attach"]
    assert "部署在后台的会话里继续运行" in result.stderr
    assert "tmux -L my-workspace attach -t deploy" in result.stderr


def _in_pty(script: str, env: dict[str, str], timeout: float = 30) -> str:
    """在新的伪终端里运行 bash（它是终端的控制进程，/dev/tty 能打开），返回终端上的输出。"""
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - 子进程
        os.execve("/bin/bash", ["/bin/bash", "-c", script], env)  # noqa: S606 - 参数固定
    output = b""
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # 子进程退出后 Linux 上读主端返回 EIO
                break
            if not chunk:
                break
            output += chunk
    finally:
        os.close(fd)
        if os.waitpid(pid, os.WNOHANG) == (0, 0):
            os.kill(pid, 9)
            os.waitpid(pid, 0)
    return output.decode(errors="replace")


def test_real_tty_is_the_terminal_device_not_dev_tty(tmp_path: Path) -> None:
    """curl | bash 时标准输入换成了 /dev/tty：real_tty 找到同一个终端真正的设备（/dev/pts/N）。"""
    functions = DEPLOY.rstrip().rsplit("\n", 1)[0]
    script = (
        functions
        + "\nset +eE; trap - ERR\nexec </dev/tty\n"
        + 'echo "stdin=$(readlink /proc/$$/fd/0) stdout=$(readlink /proc/$$/fd/1)'
        + ' real=$(real_tty)"\n'
    )
    out = _in_pty(script, {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    line = next(item for item in out.splitlines() if item.startswith("stdin="))
    fields = dict(part.split("=", 1) for part in line.split())
    assert fields["stdin"] == "/dev/tty"
    assert fields["stdout"].startswith("/dev/pts/")
    assert fields["real"] == fields["stdout"]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="需要真的 tmux")
def test_attach_works_when_stdin_is_dev_tty(tmp_path: Path) -> None:
    """真的 tmux：标准输入是 /dev/tty 时直接 attach 会报 can't use /dev/tty；tmux_attach 能接上。"""
    functions = DEPLOY.rstrip().rsplit("\n", 1)[0]
    script = (
        functions
        + "\nset +eE; trap - ERR\nTMUX_SOCKET=t0093-test\n"
        + "tmux_cmd new-session -d -s deploy 'sleep 1'\n"
        + "exec </dev/tty\n"
        + "tmux_attach\n"
        + "echo not-reached\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TERM": "xterm",
        "TMUX_TMPDIR": str(tmp_path),
    }
    try:
        out = _in_pty(script, env)
    finally:
        subprocess.run(
            ["tmux", "-L", "t0093-test", "kill-server"],  # noqa: S607
            env=env,
            capture_output=True,
            check=False,
        )
    assert "can't use /dev/tty" not in out
    assert "没能接回" not in out
    assert "[exited]" in out
    assert "not-reached" not in out


def test_in_multiplexer_sees_tmux_and_screen(tmp_path: Path) -> None:
    functions = DEPLOY.rstrip().rsplit("\n", 1)[0]
    for var in ("TMUX", "STY", "WORKSPACE_IN_TMUX"):
        result = subprocess.run(  # noqa: S603 - 参数固定
            ["/bin/bash", "-c", functions + "\nin_multiplexer"],
            env={"PATH": "/usr/bin:/bin", var: "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (var, result.stderr)


FAKE_MAKE = """#!/usr/bin/env bash
# 假的 make：前 $FAILS 次失败（输出 $MESSAGE），之后成功
n=$(cat "$STATE/count" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$STATE/count"
echo "make $*" >> "$LOG"
if (( n <= FAILS )); then echo "$MESSAGE" >&2; exit 1; fi
echo "pulled"
"""


def _pull(
    tmp_path: Path, fails: int, message: str = "net/http: TLS handshake timeout"
) -> tuple[int, str, list[str]]:
    root, state, bin_dir = tmp_path / "root", tmp_path / "state", tmp_path / "bin"
    for path in (root, state, bin_dir):
        path.mkdir(parents=True, exist_ok=True)
    (bin_dir / "make").write_text(FAKE_MAKE, encoding="utf-8")
    (bin_dir / "make").chmod(0o755)
    log = tmp_path / "make.log"
    functions = DEPLOY.rstrip().rsplit("\n", 1)[0]
    result = subprocess.run(  # noqa: S603 - 参数固定
        [
            "/bin/bash",
            "-c",
            functions + '\nROOT="$1" PULL_PAUSE=0\npull_images openim-pull OpenIM',
            "deploy",
            str(root),
        ],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LOG": str(log),
            "STATE": str(state),
            "FAILS": str(fails),
            "MESSAGE": message,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stderr, log.read_text().splitlines()


def test_pull_retries_until_it_works(tmp_path: Path) -> None:
    code, stderr, calls = _pull(tmp_path, fails=2)
    assert code == 0
    assert calls == ["make -s openim-pull"] * 3
    assert "第 1 次" in stderr and "第 2 次" in stderr


def test_pull_gives_up_after_three_attempts(tmp_path: Path) -> None:
    code, stderr, calls = _pull(tmp_path, fails=5)
    assert code == 1
    assert len(calls) == 3
    assert "失败了 3 次" in stderr


def test_pull_does_not_retry_a_docker_hub_rate_limit(tmp_path: Path) -> None:
    code, stderr, calls = _pull(
        tmp_path,
        fails=5,
        message="toomanyrequests: You have reached your unauthenticated pull rate limit",
    )
    assert code == 1
    assert len(calls) == 1
    assert "429" in stderr


def test_deploy_pulls_before_starting() -> None:
    for target, start in (
        ("pull", "make up"),
        ("openim-pull", "make -s openim-up"),
        ("n8n-pull", "make -s n8n-up"),
    ):
        assert f"pull_images {target} " in DEPLOY, target
        assert DEPLOY.index(f"pull_images {target} ") < DEPLOY.index(
            start, DEPLOY.index(f"pull_images {target} ")
        ), target
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("pull", "openim-pull", "n8n-pull"):
        assert re.search(rf"^{target}: env ##", makefile, re.MULTILINE), target
    assert "grep -vx openim-server" in makefile


def test_running_again_without_an_action_reattaches_before_the_menu(tmp_path: Path) -> None:
    """curl | bash 重新运行时（没有动作）：已经有部署在运行就直接接回，不显示菜单。"""
    root, state, bin_dir = tmp_path / "root", tmp_path / "state", tmp_path / "bin"
    for path in (root / "scripts", state, bin_dir):
        path.mkdir(parents=True)
    (root / "compose.yaml").touch()
    (root / "scripts/init_env.py").touch()
    (state / "session").touch()
    (bin_dir / "tmux").write_text(FAKE_TMUX, encoding="utf-8")
    (bin_dir / "tmux").chmod(0o755)
    log = tmp_path / "tmux.log"
    functions = DEPLOY.rstrip().rsplit("\n", 1)[0]
    script = (
        functions
        + "\nhas_terminal() { return 0; }\nin_multiplexer() { return 1; }\n"
        + 'tmux() { "$BIN/tmux" "$@"; }\nmenu() { echo "MENU SHOWN"; exit 9; }\n'
        + 'main --dir "$1"\n'
    )
    result = subprocess.run(  # noqa: S603 - 参数固定
        ["/bin/bash", "-c", script, "deploy", str(root)],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LOG": str(log),
            "STATE": str(state),
            "BIN": str(bin_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MENU SHOWN" not in result.stdout
    calls = [line.split("\x1f")[:-1] for line in log.read_text().splitlines()]
    assert [c[2] for c in calls] == ["has-session", "attach"]
