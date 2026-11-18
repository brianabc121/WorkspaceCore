#!/usr/bin/env python3
"""Create a root-owned offline input bundle, never a relocated virtualenv."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import tarfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="services/api directory"
    )
    parser.add_argument(
        "--artifacts", type=Path, required=True, help="verified archives + uv-cache"
    )
    parser.add_argument("--output", type=Path, required=True, help="new .tar.gz output")
    args = parser.parse_args()
    helper = Path(__file__).with_name("bootstrap_controller_runtime.py")
    spec = importlib.util.spec_from_file_location("bootstrap", helper)
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    source = args.source.absolute()
    artifacts = args.artifacts.absolute()
    if args.output.exists():
        raise ValueError("output already exists; refusing replacement")
    entries = [(helper, "bootstrap_controller_runtime.py")]
    entries.extend(
        (p, "source/" + p.relative_to(source).as_posix())
        for p in bootstrap.source_files(source)
    )
    for name, _, digest, _ in bootstrap.ARTIFACTS:
        bootstrap.verify(artifacts / name, digest)
        entries.append((artifacts / name, "artifacts/" + name))
    cache = artifacts / "uv-cache"
    if not cache.is_dir() or cache.is_symlink():
        raise ValueError("prepared uv-cache is required")
    cache_links = {}
    for directory, dirs, names in os.walk(cache, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(directory) / name
            if path.is_symlink():
                target = os.readlink(path)
                if target.startswith("/"):
                    raise ValueError("absolute cache symlink is not portable")
                cache_links["cache/" + path.relative_to(cache).as_posix()] = target
            elif not path.is_dir() and not path.is_file():
                raise ValueError("special cache entry refused")
            entries.append(
                (path, "artifacts/uv-cache/" + path.relative_to(cache).as_posix())
            )
    bootstrap.validate_symlink_graph(cache_links, "cache")
    for name in cache_links:
        path = cache / name.removeprefix("cache/")
        if not path.resolve(strict=True).is_relative_to(cache):
            raise ValueError("cache symlink escapes bundle")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Archive headers normalize ownership/modes. Extract only into a new, root-owned directory.
    with (
        args.output.open("xb") as raw,
        tarfile.open(fileobj=raw, mode="w:gz") as archive,
    ):
        for path, name in sorted(entries, key=lambda item: item[1]):
            info = archive.gettarinfo(str(path), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = 0
            info.mode = (
                0o755 if info.isdir() or path.stat().st_mode & stat.S_IXUSR else 0o644
            )
            if info.issym():
                info.mode = (
                    0o777  # Symlink mode is ignored by Linux; targets remain immutable.
                )
            if info.isfile():
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
    manifest = {
        "bundle": args.output.name,
        "bundle_sha256": bootstrap.sha256(args.output),
        "source_sha256": bootstrap.source_digest(source),
        "lock_sha256": bootstrap.sha256(source / "uv.lock"),
        "python_platform": bootstrap.PYTHON_PLATFORM,
    }
    args.output.with_suffix(args.output.suffix + ".json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
