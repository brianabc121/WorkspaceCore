#!/usr/bin/env python3
"""Pinned, opt-in native controller prerequisite; bootstrap runs on Python 3.9+."""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import platform
import pwd
import shutil
import stat
import subprocess
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

RUNTIME = Path("/opt/workspace-runtime-t0224")
SERVICE_API = Path("/opt/workspace/services/api")
ACCOUNT = "workspace-runtime"
CONTROLLER_UID = 10001
PYTHON_VERSION = "3.12.16"
UV_VERSION = "0.13.8"
PYTHON_PLATFORM = "x86_64-manylinux_2_28"
EMERGENCY_FLOOR = 4 * 1024**3
ARTIFACTS = (
    (
        "cpython-3.12.16+20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        (
            "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/"
            "cpython-3.12.16%2B20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
        ),
        "6a9e62dad033790aef722dbd370e0ce76ce17b73627ba3ae679f537049350b39",
        "python",
    ),
    (
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        (
            "https://github.com/astral-sh/uv/releases/download/0.13.8/"
            "uv-x86_64-unknown-linux-gnu.tar.gz"
        ),
        "5acfd9177cfecf0bf87578f059728215ccc884388988f55f7a65041b57387d91",
        "uv-x86_64-unknown-linux-gnu",
    ),
)


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def verify(path: Path, expected: str) -> None:
    if path.is_symlink() or not path.is_file() or sha256(path) != expected:
        raise ValueError("artifact SHA256 mismatch: " + path.name)


def fetch(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, url, digest, _ in ARTIFACTS:
        path = directory / name
        if not path.exists():
            partial = directory / (name + ".partial")
            # O_EXCL prevents following an existing symlink or overwriting a partial download.
            with (
                partial.open("xb") as output,
                urllib.request.urlopen(url, timeout=60) as response,
            ):
                shutil.copyfileobj(response, output, length=1024 * 1024)
            verify(partial, digest)
            partial.rename(path)
        verify(path, digest)
        print(name + " sha256:" + digest, flush=True)


def validate_symlink_graph(links: dict[str, str], top: str) -> None:
    """Resolve components in filesystem order, expanding links before processing '..'."""
    for name in links:
        resolved = []
        pending = list(PurePosixPath(name).parts)
        expansions = 0
        while pending:
            component = pending.pop(0)
            if component in ("", "."):
                continue
            if component == "..":
                if len(resolved) <= 1:
                    raise ValueError("archive symlink escapes installation")
                resolved.pop()
                continue
            resolved.append(component)
            if resolved[0] != top:
                raise ValueError("archive symlink escapes installation")
            target = links.get("/".join(resolved))
            if target is not None:
                expansions += 1
                if expansions > 40:
                    raise ValueError("archive symlink cycle or excessive chain")
                if not target or target.startswith("/"):
                    raise ValueError("archive symlink escapes installation")
                resolved.pop()
                pending = list(PurePosixPath(target).parts) + pending


def extract_verified(archive: Path, digest: str, destination: Path, top: str) -> None:
    """Validate the whole archive, write regular files first, then contained symlinks.

    Avoid tar extraction defaults that differ between Debian's Python 3.9 and 3.11.
    Official PBS archives contain relative symlinks (including ../ terminfo links).
    """
    verify(archive, digest)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        seen = set()
        total = 0
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] != top
                or str(path) in seen
                or not (member.isdir() or member.isfile() or member.issym())
            ):
                raise ValueError("unsupported archive member")
            seen.add(str(path))
            total += member.size
            if total > 2 * 1024**3 or len(members) > 100000:
                raise ValueError("archive exceeds bootstrap bounds")
        graph = {str(PurePosixPath(m.name)): m.linkname for m in members if m.issym()}
        validate_symlink_graph(graph, top)
        # No archive member may be under a symlink, independent of member ordering.
        for member in members:
            path = PurePosixPath(member.name)
            if any(str(parent) in graph for parent in path.parents):
                raise ValueError("archive member traverses symlink")
        destination.mkdir(parents=True, exist_ok=True)
        for member in members:
            path = PurePosixPath(member.name)
            output = destination.joinpath(*path.parts)
            if member.issym():
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                output.mkdir(exist_ok=True)
                output.chmod(0o755)
            else:
                with source.extractfile(member) as stream, output.open("xb") as target:
                    shutil.copyfileobj(stream, target)
                output.chmod(0o755 if member.mode & 0o111 else 0o644)
        for member in members:
            if member.issym():
                output = destination.joinpath(*PurePosixPath(member.name).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.symlink_to(member.linkname)


def source_files(source: Path) -> list[Path]:
    files = [source / "pyproject.toml", source / "uv.lock"]
    if (source / "app").is_symlink() or not (source / "app").is_dir():
        raise ValueError("source must contain a regular app directory")
    for directory, dirs, names in os.walk(source / "app", followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        if any((Path(directory) / name).is_symlink() for name in dirs):
            raise ValueError("source directory symlink refused")
        files.extend(
            Path(directory) / name
            for name in sorted(names)
            if not name.endswith(".pyc")
        )
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("source must contain only regular files")
    return sorted(files)


def source_digest(source: Path) -> str:
    result = hashlib.sha256()
    for path in source_files(source):
        name = path.relative_to(source).as_posix().encode()
        result.update(len(name).to_bytes(8, "big"))
        result.update(name)
        result.update(bytes.fromhex(sha256(path)))
    return result.hexdigest()


def clean_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "PYTHON"))
        and key not in ("VIRTUAL_ENV", "CONDA_PREFIX")
    }
    environment.update(UV_PYTHON_DOWNLOADS="never", PYTHONNOUSERSITE="1")
    return environment


def readable_permissions(prefix: Path) -> None:
    """Code and dependency files contain no credentials; normalize uv's lock mode too."""
    for directory, dirs, names in os.walk(prefix, followlinks=False):
        Path(directory).chmod(0o755)
        for name in dirs + names:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            mode = path.stat().st_mode
            path.chmod(0o755 if stat.S_ISDIR(mode) or mode & 0o111 else 0o644)


def build_runtime(
    prefix: Path, source: Path, artifacts: Path, *, offline: bool
) -> None:
    """Build at its final path: venv interpreter links are absolute, not relocatable."""
    if prefix.exists() or prefix.is_symlink():
        raise ValueError("runtime already exists; refusing replacement")
    files = source_files(source)
    # Check hashes before creating the installation directory or executing an artifact.
    for name, _, digest, _ in ARTIFACTS:
        verify(artifacts / name, digest)
    prefix.mkdir(parents=True, mode=0o755)
    prefix.chmod(0o755)
    for name, _, digest, top in ARTIFACTS:
        extract_verified(artifacts / name, digest, prefix, top)
    api = prefix / "api"
    for path in files:
        output = api / path.relative_to(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
        output.chmod(0o644)
    python = prefix / "python/bin/python3.12"
    uv = prefix / "uv-x86_64-unknown-linux-gnu/uv"
    environment = clean_environment()
    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import sys; assert sys.version.split()[0] == " + repr(PYTHON_VERSION),
        ],
        check=True,
        env=environment,
    )
    result = subprocess.run(
        [str(uv), "--version"], capture_output=True, text=True, check=True
    )
    if not result.stdout.startswith("uv " + UV_VERSION + " "):
        raise ValueError("unexpected uv version")
    command = [
        str(uv),
        "--no-config",
        "sync",
        "--project",
        str(api),
        "--python",
        str(python),
        "--locked",
        "--no-dev",
        "--no-build",
        "--no-install-project",
        "--no-python-downloads",
        "--python-platform",
        PYTHON_PLATFORM,
        "--link-mode",
        "copy",
        "--cache-dir",
        str(artifacts / "uv-cache"),
    ]
    if offline:
        command.append("--offline")
    subprocess.run(command, check=True, env=environment)
    if sha256(api / "uv.lock") != sha256(source / "uv.lock"):
        raise ValueError("lock changed during install")
    smoke = subprocess.run(
        [str(api / ".venv/bin/python"), "-m", "app.runtime_v2"],
        cwd=api,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if smoke.returncode != 2 or "controller disabled" not in smoke.stderr:
        raise ValueError(
            "disabled controller import smoke failed: " + smoke.stderr[-1000:]
        )
    (prefix / "bootstrap-manifest.json").write_text(
        json.dumps(
            {
                "python": PYTHON_VERSION,
                "uv": UV_VERSION,
                "python_platform": PYTHON_PLATFORM,
                "source_sha256": source_digest(source),
                "lock_sha256": sha256(source / "uv.lock"),
                "artifacts": {name: digest for name, _, digest, _ in ARTIFACTS},
                "enabled": False,
            },
            indent=2,
        )
        + "\n"
    )
    readable_permissions(prefix)


def trusted_path(path: Path) -> None:
    """Reject symlink ancestors and non-root or writable deployment paths."""
    for part in [*reversed(path.parents), path]:
        if not part.exists() and not part.is_symlink():
            continue
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(
                "deployment path must be root owned and immutable: " + str(part)
            )


def trusted_source(source: Path) -> None:
    trusted_path(source)
    for path in source_files(source):
        trusted_path(path)


def ensure_account() -> None:
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        account = None
    try:
        uid_account = pwd.getpwuid(CONTROLLER_UID)
    except KeyError:
        uid_account = None
    if account is not None and account.pw_uid != CONTROLLER_UID:
        raise ValueError("workspace-runtime exists with unexpected UID")
    if uid_account is not None and uid_account.pw_name != ACCOUNT:
        raise ValueError("UID 10001 already belongs to another account")
    try:
        group = grp.getgrnam(ACCOUNT)
    except KeyError:
        group = None
    if group is not None and group.gr_gid != CONTROLLER_UID:
        raise ValueError("workspace-runtime exists with unexpected GID")
    if group is None:
        try:
            grp.getgrgid(CONTROLLER_UID)
        except KeyError:
            subprocess.run(
                ["/usr/sbin/groupadd", "--gid", str(CONTROLLER_UID), ACCOUNT],
                check=True,
            )
        else:
            raise ValueError("GID 10001 already belongs to another group")
    if account is None:
        subprocess.run(
            [
                "/usr/sbin/useradd",
                "--uid",
                str(CONTROLLER_UID),
                "--gid",
                ACCOUNT,
                "--no-create-home",
                "--home-dir",
                "/nonexistent",
                "--shell",
                "/usr/sbin/nologin",
                "--no-log-init",
                ACCOUNT,
            ],
            check=True,
        )
    elif account.pw_gid != CONTROLLER_UID or account.pw_shell not in (
        "/usr/sbin/nologin",
        "/sbin/nologin",
        "/bin/false",
    ):
        raise ValueError(
            "existing controller account must have GID10001 and no login shell"
        )


def controller_traversable(path: Path) -> None:
    """Reject private existing parents without widening access to neighboring material."""
    for part in [*reversed(path.parents), path]:
        if not part.exists():
            continue
        info = part.stat()
        if not info.st_mode & 0o001 and not (
            info.st_gid == CONTROLLER_UID and info.st_mode & 0o010
        ):
            raise ValueError(
                "UID10001 cannot traverse existing installation parent: " + str(part)
            )


def unprivileged_smoke(api: Path) -> None:
    smoke = subprocess.run(
        [str(api / ".venv/bin/python"), "-m", "app.runtime_v2"],
        cwd=api,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        user=CONTROLLER_UID,
        group=CONTROLLER_UID,
        extra_groups=(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if smoke.returncode != 2 or "controller disabled" not in smoke.stderr:
        raise ValueError("UID10001 disabled controller import smoke failed")


def install(
    source: Path, artifacts: Path, expected_source: str, *, offline: bool
) -> None:
    if os.geteuid() != 0:
        raise ValueError(
            "install requires root to establish immutable code and UID10001"
        )
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("only Linux x86_64 GNU hosts are supported")
    libc, version = platform.libc_ver()
    if libc != "glibc" or tuple(map(int, version.split(".")[:2])) < (2, 28):
        raise ValueError(
            "glibc >=2.28 is required by the locked CPython3.12 wheel closure"
        )
    trusted_source(source)
    trusted_path(artifacts)
    # Cache entries are executable dependency inputs: require the staged cache to be immutable too.
    for directory, dirs, names in os.walk(artifacts, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            if path.is_symlink():
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(artifacts):
                    raise ValueError("artifact cache symlink escapes staging directory")
                trusted_path(resolved)
                info = path.lstat()
                if info.st_uid != 0:
                    raise ValueError("artifact cache link must be root owned")
            else:
                trusted_path(path)
    if source_digest(source) != expected_source:
        raise ValueError("reviewed source digest mismatch")
    trusted_path(RUNTIME)
    trusted_path(SERVICE_API)
    controller_traversable(RUNTIME.parent)
    controller_traversable(SERVICE_API.parent)
    if SERVICE_API.exists() or SERVICE_API.is_symlink():
        raise ValueError("service API path already exists; refusing replacement")
    # The tested full runtime uses <300MiB. Reserve 1GiB for an offline install,
    # or 2GiB when downloads may also grow the cache; never consume the 4GiB floor.
    allowance = (1 if offline else 2) * 1024**3
    if shutil.disk_usage("/opt").free < EMERGENCY_FLOOR + allowance:
        raise ValueError(
            "installation allowance plus 4GiB emergency disk floor required"
        )
    old_mask = os.umask(0o022)
    try:
        build_runtime(RUNTIME, source, artifacts, offline=offline)
        if shutil.disk_usage("/opt").free < EMERGENCY_FLOOR:
            raise ValueError(
                "4GiB emergency disk floor breached; runtime remains inactive"
            )
        ensure_account()
        unprivileged_smoke(RUNTIME / "api")
        SERVICE_API.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        SERVICE_API.symlink_to(RUNTIME / "api", target_is_directory=True)
    finally:
        os.umask(old_mask)
    print("Native runtime installed; no service, credentials, or feature flag changed.")
    print(
        "Controller account: workspace-runtime UID/GID10001; run the reviewed service separately."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    download = sub.add_parser(
        "fetch", help="download and SHA256-verify pinned official artifacts"
    )
    download.add_argument("--artifacts", type=Path, required=True)
    digest = sub.add_parser(
        "source-digest", help="print deterministic app + pyproject + lock digest"
    )
    digest.add_argument("--source", type=Path, required=True)
    setup = sub.add_parser(
        "install", help="install only the isolated prerequisite; never start service"
    )
    setup.add_argument("--artifacts", type=Path, required=True)
    setup.add_argument("--source", type=Path, required=True)
    setup.add_argument("--expect-source-sha256", required=True)
    setup.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.mode == "fetch":
        fetch(args.artifacts.absolute())
    elif args.mode == "source-digest":
        print(source_digest(args.source.absolute()))
    else:
        install(
            args.source.absolute(),
            args.artifacts.absolute(),
            args.expect_source_sha256,
            offline=args.offline,
        )


if __name__ == "__main__":
    main()
