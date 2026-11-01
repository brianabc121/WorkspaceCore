"""OC-0 ADR F9：`@openim/openclaw-channel` 发布包审查（T-0058，OC-6）。

这个插件只在 npm 上发布，GitHub 没有对应源码，所以每次升级前都要审查发布包本身。

用法（需要 npm，经平台代理访问 registry）：

    # 核对锁定版本：tarball 的 sha256 与 docs/versions.md 一致，静态扫描通过（ci-security 每次都跑）
    python3 tests/e2e/openclaw_plugin_review.py

    # 升级前：下载候选版本，静态扫描，并列出与锁定版本的差异（文件增删、改动内容、依赖、安装脚本）
    python3 tests/e2e/openclaw_plugin_review.py --candidate 0.3.4

    # 离线核对本地 tarball
    python3 tests/e2e/openclaw_plugin_review.py --tarball openim-openclaw-channel-0.3.3.tgz

静态扫描（OC-0 第 3 节 F9 的审查项）：
- 不能有子进程、eval / new Function、vm、worker_threads、process.binding；
- package.json 不能有安装期脚本（preinstall / install / postinstall / prepare）；
- 代码里写死的外部地址只允许白名单（目前为空；本机回环地址不算）；
- 只读 `OPENIM_*` 环境变量，不能动态读取 process.env。

候选版本通过扫描后，人工阅读差异；确认后同时更新 docs/versions.md、
infra/openclaw/README.md、接入包里的 CHECKLIST.md 与 `app/agent_bundle` 的 PLUGIN_VERSION
（services/api/tests/test_oc6_config.py 会检查这几处一致），并在 ADR 里记一笔审查结论。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "@openim/openclaw-channel"
VERSIONS = ROOT / "docs" / "versions.md"
PIN = re.compile(
    r"`@openim/openclaw-channel@([0-9][0-9A-Za-z.\-]*)`.*?tarball sha256 `([0-9a-f]{64})`"
)

FORBIDDEN = {
    "子进程": re.compile(r"child_process"),
    "eval": re.compile(r"\beval\s*\("),
    "new Function": re.compile(r"\bnew\s+Function\s*\("),
    "vm 模块": re.compile(r"""(?:require\(|from\s+|import\(\s*)["'](?:node:)?vm["']"""),
    "worker_threads": re.compile(r"worker_threads"),
    "process.binding": re.compile(r"process\.binding"),
}
INSTALL_SCRIPTS = ("preinstall", "install", "postinstall", "prepare", "preprepare", "postprepare")
URL = re.compile(r"""["'`]((?:https?|wss?)://[^"'`\s]+)""")
# 本机回环地址不是外部地址（setup 向导的默认值是 ws://127.0.0.1:10001、http://127.0.0.1:10002）
LOOPBACK = re.compile(r"^(?:https?|wss?)://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?(?:/|$)")
ALLOWED_URLS: frozenset[str] = frozenset()
ENV_STATIC = re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)")
ENV_DYNAMIC = re.compile(r"process\.env\s*\[")
ENV_DESTRUCTURE = re.compile(r"\{[^}]*\}\s*=\s*process\.env\b")


@dataclass
class Report:
    version: str
    sha256: str
    problems: list[str] = field(default_factory=list)
    env: set[str] = field(default_factory=set)
    files: dict[str, bytes] = field(default_factory=dict)

    @property
    def manifest(self) -> dict[str, object]:
        raw = self.files.get("package.json", b"{}")
        data: dict[str, object] = json.loads(raw)
        return data


def pinned() -> tuple[str, str]:
    match = PIN.search(VERSIONS.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"{VERSIONS} 里找不到 {PACKAGE} 的版本与 sha256")
    return match.group(1), match.group(2)


def fetch(version: str, dest: Path) -> Path:
    """npm pack 下载发布包（不执行包里的任何脚本）。"""
    # 用 PATH 里的 npm（CI 由 setup-node 提供）；参数固定，版本号已校验格式
    command = ["npm", "pack", f"{PACKAGE}@{version}", "--json", "--pack-destination", str(dest)]
    out = subprocess.run(  # noqa: S603
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    filename = json.loads(out.stdout)[0]["filename"]
    return dest / filename


def read(tarball: Path) -> dict[str, bytes]:
    """读出包里的文件（不解压到磁盘），路径去掉 npm 的 package/ 前缀。"""
    files: dict[str, bytes] = {}
    with tarfile.open(tarball, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            name = member.name.removeprefix("package/")
            handle = archive.extractfile(member)
            if handle is not None:
                files[name] = handle.read()
    return files


def scan(tarball: Path, version: str) -> Report:
    report = Report(version=version, sha256=hashlib.sha256(tarball.read_bytes()).hexdigest())
    report.files = read(tarball)
    scripts = report.manifest.get("scripts") or {}
    if isinstance(scripts, dict):
        for name in INSTALL_SCRIPTS:
            if name in scripts:
                report.problems.append(f"package.json 有安装期脚本 {name}: {scripts[name]}")
    for name, content in sorted(report.files.items()):
        if not name.endswith((".js", ".mjs", ".cjs")):
            continue
        text = content.decode("utf-8", errors="replace")
        for label, pattern in FORBIDDEN.items():
            if pattern.search(text):
                report.problems.append(f"{name}: 出现{label}")
        for url in URL.findall(text):
            if url not in ALLOWED_URLS and not LOOPBACK.match(url):
                report.problems.append(f"{name}: 写死的外部地址 {url}")
        for variable in ENV_STATIC.findall(text):
            report.env.add(variable)
            if not variable.startswith("OPENIM_"):
                report.problems.append(f"{name}: 读取环境变量 {variable}")
        if ENV_DYNAMIC.search(text) or ENV_DESTRUCTURE.search(text):
            report.problems.append(f"{name}: 动态读取 process.env")
    return report


def diff(old: Report, new: Report, limit: int = 400) -> list[str]:
    lines: list[str] = []
    added = sorted(set(new.files) - set(old.files))
    removed = sorted(set(old.files) - set(new.files))
    changed = sorted(n for n in set(old.files) & set(new.files) if old.files[n] != new.files[n])
    lines += [f"新增文件：{n}" for n in added] + [f"删除文件：{n}" for n in removed]
    for key in ("dependencies", "peerDependencies", "optionalDependencies", "bin", "scripts"):
        before, after = old.manifest.get(key), new.manifest.get(key)
        if before != after:
            lines.append(f"package.json {key}: {before!r} → {after!r}")
    for name in changed:
        a = old.files[name].decode("utf-8", errors="replace").splitlines()
        b = new.files[name].decode("utf-8", errors="replace").splitlines()
        before, after = f"{old.version}/{name}", f"{new.version}/{name}"
        lines += list(difflib.unified_diff(a, b, before, after, lineterm=""))
    if len(lines) > limit:
        lines = [*lines[:limit], f"……（还有 {len(lines) - limit} 行，完整差异请自行比对）"]
    return lines


def print_report(report: Report) -> None:
    print(f"{PACKAGE}@{report.version}  sha256 {report.sha256}  文件 {len(report.files)} 个")
    print(f"  读取的环境变量：{', '.join(sorted(report.env)) or '无'}")
    for problem in report.problems:
        print(f"  ✗ {problem}")
    if not report.problems:
        print("  ✓ 静态扫描通过")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--candidate", help="升级候选版本，与锁定版本比对")
    parser.add_argument("--tarball", type=Path, help="离线核对本地的锁定版本 tarball")
    args = parser.parse_args(argv)
    version, expected = pinned()
    if args.candidate and not re.fullmatch(r"[0-9][0-9A-Za-z.\-]*", args.candidate):
        parser.error("版本号格式不对")
    with tempfile.TemporaryDirectory() as tmp:
        tarball = args.tarball or fetch(version, Path(tmp))
        current = scan(tarball, version)
        print_report(current)
        failed = bool(current.problems)
        if current.sha256 != expected:
            print(f"  ✗ sha256 与 docs/versions.md 锁定的 {expected} 不一致")
            failed = True
        if not args.candidate:
            return 1 if failed else 0
        candidate = scan(fetch(args.candidate, Path(tmp)), args.candidate)
        print()
        print_report(candidate)
        print(f"\n与锁定版本 {version} 的差异：")
        for line in diff(current, candidate):
            print(line)
        print("\n人工确认差异无误后，把这几处更新为新版本与上面的 sha256：")
        print("  docs/versions.md、infra/openclaw/README.md、")
        print("  services/api/app/agent_bundle/files/CHECKLIST.md、")
        print("  services/api/app/agent_bundle/__init__.py 的 PLUGIN_VERSION；")
        print("  并在 ADR 里记录审查结论。")
        return 1 if candidate.problems else 0


if __name__ == "__main__":
    sys.exit(main())
