"""一键部署脚本在服务器上安装的 Python 3.12（T-0074）。

应用跑在 Docker 镜像里；服务器上的项目脚本（make env、改写 .env、备份）
用 deploy.sh 安装的 Python 3.12，与 api 镜像是同一个小版本。
版本与校验和的来源见 docs/decisions/2026-11-02-host-python-3-12.md。
"""

import contextlib
import os
import pty
import re
import select
import signal
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
    prelude: str = "",
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
            + prelude
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


# ---------------------------------------------------------------- 真正的终端设备（T-0093）


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


@pytest.mark.parametrize(
    "message",
    [
        "toomanyrequests: You have reached your unauthenticated pull rate limit",
        # docker compose pull 有时只给出 HTTP 状态（T-0094 本机实测）
        "unexpected status from HEAD request to https://registry-1.docker.io/v2/library/"
        "postgres/manifests/sha256:0c: 429 Too Many Requests",
    ],
)
def test_pull_does_not_retry_a_docker_hub_rate_limit(tmp_path: Path, message: str) -> None:
    code, stderr, calls = _pull(tmp_path, fails=5, message=message)
    assert code == 1
    assert len(calls) == 1
    assert "429" in stderr


def test_deploy_pulls_before_starting() -> None:
    for target, start in (
        ("pull", "make up"),
        ("openim-pull", "make -s openim-up"),
    ):
        assert f"pull_images {target} " in DEPLOY, target
        assert DEPLOY.index(f"pull_images {target} ") < DEPLOY.index(
            start, DEPLOY.index(f"pull_images {target} ")
        ), target
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("pull", "openim-pull"):
        assert re.search(rf"^{target}: env ##", makefile, re.MULTILINE), target
    assert "grep -vx openim-server" in makefile


# ---------------------------------------------------------------- 先问完，再后台运行（T-0094）

FUNCTIONS = DEPLOY.rstrip().rsplit("\n", 1)[0]


def _shell(
    tmp_path: Path,
    body: str,
    *,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """加载 deploy.sh 的函数后运行 body；ROOT 是临时目录。"""
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    return subprocess.run(  # noqa: S603 - 运行仓库里的脚本
        ["/bin/bash", "-c", FUNCTIONS + f'\nROOT="{root}"\nPY=python3\n' + body],
        input=stdin,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


def test_answers_round_trip_without_executing_anything(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    _shell(
        tmp_path,
        "deploy_dir_init\n"
        "DOMAIN=ws.example.com EMAIL=ops@example.com BASE_URL=https://ws.example.com\n"
        f"ANS[INGRESS_HOST]='$(touch {marker})'\nANS[OPENIM]=n\nANS[STORAGE_REGION]=''\n"
        "answers_save\n",
    )
    file = tmp_path / "root/.deploy/answers.env"
    assert file.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "root/.deploy").stat().st_mode & 0o777 == 0o700
    file.write_text(file.read_text() + "lower_case=ignored\nnot a line\n")
    out = _shell(
        tmp_path,
        'answers_load "$(answers_file)"\n'
        'for k in $(printf "%s\\n" "${!ANS[@]}" | sort); do\n'
        '  printf "%s=[%s]\\n" "$k" "${ANS[$k]}"\ndone\n'
        'echo "DOMAIN=$DOMAIN EMAIL=$EMAIL BASE_URL=$BASE_URL"\n',
    ).stdout
    assert f"INGRESS_HOST=[$(touch {marker})]" in out
    assert "STORAGE_REGION=[]" in out and "OPENIM=[n]" in out
    assert "lower_case" not in out
    assert "DOMAIN=ws.example.com EMAIL=ops@example.com BASE_URL=https://ws.example.com" in out
    assert not marker.exists()


def test_questions_are_asked_up_front_and_never_in_the_background(tmp_path: Path) -> None:
    # 前台：问并记下
    plan = _shell(
        tmp_path,
        "interactive() { return 0; }\nPHASE=plan\n"
        'decide OPENIM "部署 OpenIM？" y && echo OPENIM=yes || echo OPENIM=no\n'
        'choose host INGRESS_HOST "OpenClaw 服务器访问工作台用的域名" "ws.example.com"\n'
        'echo "host=$host"\n'
        'decide OPENIM "部署 OpenIM？" y && echo again=yes || echo again=no\n'
        'echo "recorded=${ANS[OPENIM]}/${ANS[INGRESS_HOST]}"\n',
        stdin="n\nagent.example.com\n",
    )
    assert "OPENIM=no" in plan.stdout and "host=agent.example.com" in plan.stdout
    assert "again=no" in plan.stdout  # 同一个问题只问一次
    assert "recorded=n/agent.example.com" in plan.stdout
    # 后台：有回答就用回答，没有就按默认值并写进日志；任何时候都不调用 confirm / ask
    run = _shell(
        tmp_path,
        'confirm() { echo "ASKED $1"; return 0; }\nask() { echo "ASKED $1"; }\nPHASE=run\n'
        "ANS[OPENIM]=n\nANS[INGRESS_HOST]=-\n"
        'decide OPENIM "部署 OpenIM？" y && echo OPENIM=yes || echo OPENIM=no\n'
        'decide STORAGE "配置图片存储？" y && echo STORAGE=yes || echo STORAGE=no\n'
        'decide INGRESS "配置接入入口？" n && echo INGRESS=yes || echo INGRESS=no\n'
        'choose host INGRESS_HOST "域名" "ws.example.com"; echo "host=$host"\n'
        'choose region STORAGE_REGION "地域" "test-region"; echo "region=$region"\n',
    )
    assert "ASKED" not in run.stdout
    assert "OPENIM=no" in run.stdout and "STORAGE=yes" in run.stdout and "INGRESS=no" in run.stdout
    assert "host=-" in run.stdout and "region=test-region" in run.stdout
    assert "没有事先回答「配置图片存储？」，按默认：是" in run.stderr
    assert "没有事先回答「地域」，按默认：test-region" in run.stderr


def test_plan_components_records_every_choice(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "docker").chmod(0o755)
    (tmp_path / "root/infra/openim").mkdir(parents=True)
    (tmp_path / "root/infra/openim/compose.yaml").write_text(
        "    image: my-workspace/openim-server:test\n"
    )
    out = _shell(
        tmp_path,
        "interactive() { return 0; }\napi_running() { return 1; }\nPHASE=plan ACTION=install\n"
        "DOMAIN=ws.example.com EMAIL=ops@example.com\n"
        "plan_components\n"
        'for k in $(printf "%s\\n" "${!ANS[@]}" | sort); do\n'
        '  printf "%s=%s\\n" "$k" "${ANS[$k]}"\ndone\n',
        # OpenIM：否；接入入口：是、没有域名、IP；新闻早报：是
        stdin="n\ny\n-\n203.0.113.9\ny\n",
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    ).stdout
    answers = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    assert answers == {
        "OPENIM": "n",
        "INGRESS": "y",
        "INGRESS_HOST": "-",
        "INGRESS_ADDRESS": "203.0.113.9",
        "NEWS": "y",
    }


def test_ingress_in_the_background_uses_the_recorded_answers(tmp_path: Path) -> None:
    prelude = "\nPHASE=run\nANS[INGRESS]=y\nANS[INGRESS_HOST]=-\nANS[INGRESS_ADDRESS]=203.0.113.8\n"
    _, calls, env = _ingress(tmp_path, "SITE_DOMAIN=\nAGENT_PUBLIC_URL=\n", prelude=prelude)
    assert "confirm" not in calls  # 后台一个问题都不问
    assert env["AGENT_PUBLIC_ADDRESS"] == "203.0.113.8"
    assert env["AGENT_PUBLIC_HOST"] == "agent-gateway.myworkspace.internal"


# 后台运行的端到端：用真的进程走一遍 前台提问 → 启动后台 → 跟随日志 → 等网页首次安装 → 完成。
# 只换掉真正部署的那部分（do_install 等），其余都是 deploy.sh 本身的代码；
# 没有 systemd 时用 setsid nohup。
STUB = r"""
have_systemd() { return 1; }
ensure_root() { :; }
ensure_tools() { :; }
legacy_tmux_session() { return 1; }
SETUP_POLL=1
connect_status() {
  if [[ -f $ROOT/setup-done ]]; then echo setup=done; else echo setup=required; fi
}
plan_install() {
  DOMAIN=ws.example.com EMAIL=ops@example.com BASE_URL=https://ws.example.com
  decide OPENIM "部署 OpenIM？" y
  plan_summary
}
do_install() {
  step "第一步"
  info "hello from background"
  [[ -f $ROOT/fail ]] && die "模拟失败：端口被占用"
  show_setup_stub
  wait_for_setup_run
  step "接入组件"
  todo_add "示例待办"
}
show_setup_stub() { info "下一步：创建管理员"; }
"""


def _deploy_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    (root / "compose.yaml").touch()
    (root / "Makefile").touch()
    (root / "scripts/init_env.py").touch()
    (root / ".env").write_text(
        "PUBLIC_BASE_URL=https://ws.example.com\nSETUP_TOKEN=setup-token-123456\n"
    )
    (root / "scripts/deploy.sh").write_text(FUNCTIONS + "\n" + STUB + '\nmain "$@"\n')
    return root


def _state(root: Path) -> dict[str, str]:
    path = root / ".deploy/state"
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def _wait(predicate: object, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return
        time.sleep(0.2)
    raise AssertionError("超时")


def _start(root: Path, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - 运行临时目录里的脚本
        ["/bin/bash", str(root / "scripts/deploy.sh"), *args, "--dir", str(root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "HOME": str(root.parent)},
    )


def _status(tmp_path: Path, root: Path) -> subprocess.CompletedProcess[str]:
    """deploy.sh status；docker 换成总是失败的假命令（只看后台任务的部分）。"""
    bin_dir = tmp_path / "nodocker"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "docker").chmod(0o755)
    return subprocess.run(  # noqa: S603 - 运行临时目录里的脚本
        ["/bin/bash", str(root / "scripts/deploy.sh"), "status", "--dir", str(root)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )


def _stop_background(root: Path) -> None:
    pid = _state(root).get("pid", "")
    if pid.isdigit():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid), 9)


def test_deploy_runs_in_the_background_and_waits_for_the_web_setup(tmp_path: Path) -> None:
    root = _deploy_root(tmp_path)
    follower = _start(root, "install")
    try:
        _wait(lambda: _state(root).get("status") == "waiting_setup")
        log = root / ".deploy/deploy.log"
        assert "hello from background" in log.read_text()
        # 后台进程独立于前台（自己的会话），前台退出也不受影响
        pid = int(_state(root)["pid"])
        assert os.getsid(pid) != os.getsid(follower.pid)
        time.sleep(4)  # 前台每 2 秒看一次状态：等它显示出安装凭据
        (root / "setup-done").touch()
        out, err = follower.communicate(timeout=60)
    finally:
        _stop_background(root)
    assert follower.returncode == 0, err
    state = _state(root)
    assert state["status"] == "done" and state["action"] == "install"
    text = log.read_text()
    # 前台显示了日志、安装凭据与待办；安装凭据只在终端上，不写进日志
    assert "hello from background" in out and "示例待办" in out
    assert "安装凭据：setup-token-123456" in err
    assert "setup-token-123456" not in text
    assert "已经创建管理员" in text and "全部完成" in text
    answers = (root / ".deploy/answers.env").read_text()
    assert "OPENIM=y" in answers and "SITE_DOMAIN=ws.example.com" in answers
    assert (root / ".deploy/answers.env").stat().st_mode & 0o777 == 0o600
    assert log.stat().st_mode & 0o777 == 0o600


def test_ctrl_c_only_stops_following_and_running_again_follows_the_same_task(
    tmp_path: Path,
) -> None:
    root = _deploy_root(tmp_path)
    follower = _start(root, "install")
    try:
        _wait(lambda: _state(root).get("status") == "waiting_setup")
        follower.send_signal(signal.SIGINT)
        _, err = follower.communicate(timeout=30)
        assert follower.returncode == 0
        assert "已停止显示，部署在后台继续" in err
        assert _state(root)["status"] == "waiting_setup"  # 后台还在等
        # 重新运行（没有动作，像 curl | bash 那样）：不显示菜单、不开始第二次，接着显示这一次的进度
        again = _start(root)
        time.sleep(3)  # 让它先发现正在运行的任务，再让后台任务结束
        (root / "setup-done").touch()
        _, err2 = again.communicate(timeout=60)
    finally:
        _stop_background(root)
    assert again.returncode == 0, err2
    assert "已经有一个后台任务在运行" in err2
    assert (root / ".deploy/deploy.log").read_text().count("后台任务开始") == 1
    status = _status(tmp_path, root)
    assert "已完成" in status.stderr and "示例待办" in status.stderr


def test_a_failed_task_reports_why_and_running_again_reuses_the_answers(tmp_path: Path) -> None:
    root = _deploy_root(tmp_path)
    (root / "fail").touch()
    follower = _start(root, "install")
    try:
        _, err = follower.communicate(timeout=60)
    finally:
        _stop_background(root)
    assert follower.returncode == 1
    assert "后台任务失败：模拟失败：端口被占用" in err
    state = _state(root)
    assert state["status"] == "failed" and state["error"] == "模拟失败：端口被占用"
    status = _status(tmp_path, root)
    assert "失败" in status.stderr and "模拟失败：端口被占用" in status.stderr
    assert "hello from background" in status.stderr  # 日志的最后几行
    # 处理后重新运行：沿用上次的回答（无人值守时按默认“是”）
    (root / "fail").unlink()
    (root / "setup-done").touch()
    again = _start(root, "install")
    try:
        _, err2 = again.communicate(timeout=60)
    finally:
        _stop_background(root)
    assert again.returncode == 0, err2
    assert "上次的任务没有完成" in err2 and "已沿用上次的回答" in err2


def test_systemd_runs_the_task_as_a_transient_service(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("systemd-run", "systemctl"):
        (bin_dir / name).write_text(f'#!/bin/sh\nprintf "{name} %s\\n" "$*" >> "$LOG"\n')
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "calls.log"
    _shell(
        tmp_path,
        "have_systemd() { return 0; }\nfollow_run() { :; }\n"
        "ACTION=install ASSUME_YES=1 DOMAIN=ws.example.com\nlaunch_run\n",
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "LOG": str(log), "HOME": "/root"},
    )
    calls = log.read_text()
    root = tmp_path / "root"
    assert "systemctl reset-failed my-workspace-deploy" in calls
    run = next(line for line in calls.splitlines() if line.startswith("systemd-run"))
    assert "--unit my-workspace-deploy" in run and "--collect" in run
    assert f"StandardOutput=append:{root}/.deploy/deploy.log" in run
    assert f"StandardError=append:{root}/.deploy/deploy.log" in run
    assert "--setenv HOME=/root" in run
    assert run.endswith(
        f"/bin/bash {root}/scripts/deploy.sh install --dir {root} --run "
        f"--answers {root}/.deploy/answers.env --yes"
    )
    assert _state(root)["runner"] == "systemd:my-workspace-deploy"


def _git(*args: str) -> str:
    """测试里的本地 git 仓库。"""
    command = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args]
    result = subprocess.run(command, capture_output=True, text=True, check=True)  # noqa: S603
    return result.stdout.strip()


def test_upgrade_in_the_background_goes_to_the_confirmed_version(tmp_path: Path) -> None:
    """后台升级到前台确认过的提交（即使远端又有了新提交），并在同一个后台任务里交给新版本的脚本。"""
    origin, root = tmp_path / "origin.git", tmp_path / "root"
    _git("init", "-q", "--bare", "-b", "main", str(origin))
    _git("clone", "-q", str(origin), str(root))
    (root / "compose.yaml").touch()
    _git("-C", str(root), "add", ".")
    _git("-C", str(root), "commit", "-q", "-m", "v1")
    _git("-C", str(root), "push", "-q", "origin", "HEAD:main")
    old = _git("-C", str(root), "rev-parse", "HEAD")

    def push(name: str) -> str:
        work = tmp_path / f"work-{name}"
        _git("clone", "-q", str(origin), str(work))
        (work / name).touch()
        _git("-C", str(work), "add", ".")
        _git("-C", str(work), "commit", "-q", "-m", name)
        _git("-C", str(work), "push", "-q", "origin", "HEAD:main")
        return _git("-C", str(work), "rev-parse", "HEAD")

    confirmed = push("v2")
    push("v3")  # 确认之后远端又有了新提交
    (root / ".env").write_text("A=1\n")
    out = _shell(
        tmp_path,
        "ensure_docker() { :; }\ncompose() { return 1; }\n"
        'exec() { echo "EXEC $*"; exit 0; }\n'
        f"PHASE=run ACTION=upgrade\nANS[UPGRADE]=y\nANS[UPGRADE_TO]={confirmed}\n"
        "deploy_dir_init\ndo_upgrade\n",
    ).stdout
    assert _git("-C", str(root), "rev-parse", "HEAD") == confirmed
    assert (
        f"EXEC bash {root}/scripts/deploy.sh upgrade --dir {root} --after-pull {old} "
        f"--run --answers {root}/.deploy/answers.env"
    ) in out


def test_an_old_tmux_deploy_must_be_finished_first(tmp_path: Path) -> None:
    root = _deploy_root(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tmux").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "tmux").chmod(0o755)
    script = (root / "scripts/deploy.sh").read_text()
    (root / "scripts/deploy.sh").write_text(
        script.replace("legacy_tmux_session() { return 1; }\n", "")
    )
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", str(root / "scripts/deploy.sh"), "install", "--dir", str(root)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    assert result.returncode == 1
    assert "以前版本的部署还在 tmux 会话里" in result.stderr
    assert "tmux -L my-workspace attach -t deploy" in result.stderr
    assert not (root / ".deploy/state").exists()


def test_state_writes_from_both_sides_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """前台与后台同时写状态文件：任何一方写下的键都不能被另一方用旧内容覆盖掉。"""
    out = _shell(
        tmp_path,
        "deploy_dir_init\n"
        '( for i in $(seq 1 60); do state_set "k$i" "$i"; done ) &\n'
        '( for i in $(seq 1 300); do state_set step "$i"; done ) &\n'
        "wait\n"
        'grep -c "^k" "$(state_file)"; state_get step\n',
    ).stdout.split()
    assert out == ["60", "300"]


# ---------------------------------------------------------------- 知识库图片存储（T-0103）


def test_storage_is_configured_on_the_web_not_in_the_deploy_script() -> None:
    # T-0223：主存储、副本与 R2 / OSS 的连接信息在网页「设置 → 图片存储」里填，
    # 部署脚本不再问、不写 .env
    assert re.search(r"connect_components\(\) \{.*?connect_storage\n", DEPLOY, re.DOTALL)
    assert "plan_storage" not in DEPLOY and "storage-check" not in DEPLOY
    for name in ("FILE_STORAGE", "R2_ACCOUNT_ID", "R2_SECRET_ACCESS_KEY", "OSS_ACCESS_KEY_SECRET"):
        assert name not in DEPLOY
    assert "设置 → 图片存储" in DEPLOY


# ---------------------------------------------------------------- 停用旧的 n8n（T-0149）
# 测试只调用假的 Docker；站点内容来自旧 write_app_site 的完整生成模板。

LEGACY_SITE = (
    "# 由 scripts/deploy.sh connect 生成（T-0078）：n8n 的对外入口。"
    "删除本文件并重启 caddy 即取消对外\n"
    "automation.example.com {\n"
    "\tencode zstd gzip\n"
    "\treverse_proxy n8n:5678\n"
    "\theader {\n"
    '\t\tStrict-Transport-Security "max-age=31536000"\n'
    "\t\tX-Content-Type-Options nosniff\n"
    "\t\tReferrer-Policy strict-origin-when-cross-origin\n"
    "\t\t-Server\n"
    "\t}\n"
    "}\n"
)


def _retire_n8n(
    tmp_path: Path,
    *,
    site: str | None = LEGACY_SITE,
    project: str = "my-workspace",
    exported_project: str = "",
    fail: str = "",
    symlink: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    root = tmp_path / "root"
    sites = root / "infra/caddy/apps-enabled"
    sites.mkdir(parents=True)
    (root / ".env").write_text(f"COMPOSE_PROJECT_NAME={project}\nKEEP=unchanged\n")
    (root / ".deploy").mkdir()
    (root / ".deploy/n8n-credential.txt").write_text("old credential\n")
    (root / "legacy-volume").write_text("old workflow data\n")
    if site is not None:
        if symlink:
            (root / "custom-site").write_text(site)
            (sites / "n8n.caddy").symlink_to(root / "custom-site")
        else:
            (sites / "n8n.caddy").write_text(site)
    (sites / "custom.caddy").write_text("custom.example.com {}\n")
    log = tmp_path / "docker.log"
    result = _shell(
        tmp_path,
        r"""
docker() {
  printf '%s\n' "$*" >> "$LOG"
  local query="ps -aq --filter label=com.docker.compose.project=$EXPECTED-n8n"
  query+=" --filter label=com.docker.compose.service="
  case "$*" in
    "${query}n8n")
      [[ $FAIL == discover ]] && return 1
      echo legacy-service ;;
    "${query}volume-init") echo legacy-init ;;
    "rm -f legacy-service" | "rm -f legacy-init") [[ $FAIL != remove ]] ;;
    *) return 90 ;;
  esac
}
retire_n8n
""",
        env={
            "LOG": str(log),
            "EXPECTED": exported_project or project or "my-workspace",
            "COMPOSE_PROJECT_NAME": exported_project,
            "FAIL": fail,
        },
        check=False,
    )
    return result, log.read_text().splitlines()


@pytest.mark.parametrize(
    ("project", "exported_project"),
    [("", ""), ("my-workspace", ""), ("custom", ""), ("custom", "exported")],
)
def test_retire_n8n_only_removes_known_service_containers_and_generated_site(
    tmp_path: Path, project: str, exported_project: str
) -> None:
    result, calls = _retire_n8n(tmp_path, project=project, exported_project=exported_project)
    assert result.returncode == 0, result.stderr
    name = exported_project or project or "my-workspace"
    assert calls == [
        f"ps -aq --filter label=com.docker.compose.project={name}-n8n "
        "--filter label=com.docker.compose.service=n8n",
        "rm -f legacy-service",
        f"ps -aq --filter label=com.docker.compose.project={name}-n8n "
        "--filter label=com.docker.compose.service=volume-init",
        "rm -f legacy-init",
    ]
    root = tmp_path / "root"
    assert not (root / "infra/caddy/apps-enabled/n8n.caddy").exists()
    assert (root / "infra/caddy/apps-enabled/custom.caddy").read_text() == "custom.example.com {}\n"
    assert (root / "legacy-volume").read_text() == "old workflow data\n"
    assert (root / ".deploy/n8n-credential.txt").read_text() == "old credential\n"
    assert (root / ".env").read_text() == f"COMPOSE_PROJECT_NAME={project}\nKEEP=unchanged\n"


@pytest.mark.parametrize(
    ("site", "symlink"),
    [
        ("custom.example.com { reverse_proxy custom:80 }\n", False),
        (LEGACY_SITE.replace("n8n:5678", "custom:80"), False),
        (LEGACY_SITE, True),
        (LEGACY_SITE + "custom.example.com {\n\trespond custom\n}\n", False),
        (LEGACY_SITE.replace("\tencode zstd gzip\n", "\tencode zstd gzip\n\tlog\n"), False),
    ],
)
def test_retire_n8n_preserves_unknown_site_files_and_symlinks(
    tmp_path: Path, site: str, symlink: bool
) -> None:
    result, _ = _retire_n8n(tmp_path, site=site, symlink=symlink)
    assert result.returncode == 0, result.stderr
    path = tmp_path / "root/infra/caddy/apps-enabled/n8n.caddy"
    assert path.read_text() == site
    assert path.is_symlink() == symlink


@pytest.mark.parametrize("fail", ["discover", "remove"])
def test_retire_n8n_reports_docker_failure_and_preserves_data(tmp_path: Path, fail: str) -> None:
    result, calls = _retire_n8n(tmp_path, fail=fail)
    assert result.returncode == 1
    assert "请检查 Docker 后重试" in result.stderr
    assert (tmp_path / "root/legacy-volume").read_text() == "old workflow data\n"
    assert (tmp_path / "root/infra/caddy/apps-enabled/n8n.caddy").read_text() == LEGACY_SITE
    assert all("volume" not in call and "network" not in call for call in calls)


def test_retire_n8n_without_a_legacy_deployment_is_a_noop(tmp_path: Path) -> None:
    result = _shell(
        tmp_path,
        "docker() { [[ $1 == ps ]]; }\nretire_n8n\n",
        env={"COMPOSE_PROJECT_NAME": ""},
    )
    assert result.stdout == result.stderr == ""
