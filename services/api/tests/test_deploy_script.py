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
