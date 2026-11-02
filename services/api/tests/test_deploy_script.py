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
