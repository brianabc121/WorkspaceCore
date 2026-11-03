"""一键部署脚本在服务器上安装的 Python 3.12（T-0074）。

应用跑在 Docker 镜像里；服务器上的项目脚本（make env、改写 .env、备份）
用 deploy.sh 安装的 Python 3.12，与 api 镜像是同一个小版本。
版本与校验和的来源见 docs/decisions/2026-11-02-host-python-3-12.md。
"""

import re
from pathlib import Path

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
    tmp_path: Path, env: str, *, check: str = "renew", answers: dict[str, str] | None = None
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
            functions + OVERRIDES + '\nPY=python3\nROOT="$1"\nconnect_agent_ingress',
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
