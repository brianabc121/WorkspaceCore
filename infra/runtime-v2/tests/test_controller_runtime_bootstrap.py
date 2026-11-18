"""Offline bootstrap boundary tests; run with stdlib unittest on Python3.9+."""

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "bootstrap", Path(__file__).parents[1] / "bootstrap_controller_runtime.py"
)
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOTSTRAP)
CACHE = Path("/workspace/.cache/t0224-completion/controller-runtime")
REPOSITORY = Path(__file__).parents[3]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def archive(self, members):
        path = self.root / "archive.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, link, data in members:
                info = tarfile.TarInfo(name)
                if link is not None:
                    info.type = tarfile.SYMTYPE
                    info.linkname = link
                    archive.addfile(info)
                else:
                    info.size = len(data)
                    info.mode = 0o6777
                    archive.addfile(info, io.BytesIO(data))
        return path

    def test_hash_failure_creates_no_installation(self):
        archive = self.archive([("python/bin/python", None, b"code")])
        destination = self.root / "output"
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            BOOTSTRAP.extract_verified(archive, "0" * 64, destination, "python")
        self.assertFalse(destination.exists())

    def test_parent_path_refused(self):
        archive = self.archive([("python/../../escape", None, b"bad")])
        with self.assertRaisesRegex(ValueError, "unsupported archive"):
            BOOTSTRAP.extract_verified(
                archive, BOOTSTRAP.sha256(archive), self.root / "output", "python"
            )
        self.assertFalse((self.root / "escape").exists())

    def test_absolute_symlink_refused(self):
        archive = self.archive([("python/lib/link", "/etc", b"")])
        with self.assertRaisesRegex(ValueError, "symlink escapes"):
            BOOTSTRAP.extract_verified(
                archive, BOOTSTRAP.sha256(archive), self.root / "output", "python"
            )

    def test_archive_write_through_symlink_refused(self):
        archive = self.archive(
            [
                ("python/lib/link", "../bin", b""),
                ("python/lib/link/overwrite", None, b"bad"),
            ]
        )
        with self.assertRaisesRegex(ValueError, "traverses symlink"):
            BOOTSTRAP.extract_verified(
                archive, BOOTSTRAP.sha256(archive), self.root / "output", "python"
            )

    def test_relative_symlink_and_permission_normalization(self):
        archive = self.archive(
            [
                ("python/bin/python3.12", None, b"code"),
                ("python/bin/python", "python3.12", b""),
            ]
        )
        destination = self.root / "output"
        BOOTSTRAP.extract_verified(
            archive, BOOTSTRAP.sha256(archive), destination, "python"
        )
        self.assertEqual((destination / "python/bin/python").read_bytes(), b"code")
        self.assertEqual(
            (destination / "python/bin/python3.12").stat().st_mode & 0o7777, 0o755
        )

    def test_chained_symlink_escape_rejected_before_any_writes(self):
        archive = self.archive(
            [
                ("python/d1/d2/link", "../../bin", b""),
                ("python/attack", "d1/d2/link/../../../escape", b""),
            ]
        )
        destination = self.root / "output"
        with self.assertRaisesRegex(ValueError, "symlink"):
            BOOTSTRAP.extract_verified(
                archive, BOOTSTRAP.sha256(archive), destination, "python"
            )
        self.assertFalse(destination.exists())

    def test_symlink_cycle_rejected_before_any_writes(self):
        archive = self.archive(
            [
                ("python/a", "b", b""),
                ("python/b", "a", b""),
            ]
        )
        destination = self.root / "output"
        with self.assertRaisesRegex(ValueError, "symlink"):
            BOOTSTRAP.extract_verified(
                archive, BOOTSTRAP.sha256(archive), destination, "python"
            )
        self.assertFalse(destination.exists())

    @unittest.skipUnless(
        (CACHE / "local-runtime/uv-x86_64-unknown-linux-gnu/uv").is_file(),
        "requires the SHA-verified pinned offline artifact cache",
    )
    def test_real_pinned_uv_build_ignores_ambient_config(self):
        config = self.root / "config/uv"
        config.mkdir(parents=True)
        (config / "uv.toml").write_text("this-is-not-a-valid-setting = true\n")

        def reuse_verified_archive(archive, digest, destination, top):
            BOOTSTRAP.verify(archive, digest)
            (destination / top).symlink_to(
                CACHE / "local-runtime" / top, target_is_directory=True
            )

        with (
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")}),
            mock.patch.object(
                BOOTSTRAP, "extract_verified", side_effect=reuse_verified_archive
            ),
        ):
            BOOTSTRAP.build_runtime(
                self.root / "built", REPOSITORY / "services/api", CACHE, offline=True
            )
        self.assertTrue((self.root / "built/bootstrap-manifest.json").is_file())

    def prepare_bundle_inputs(self):
        source, artifacts = self.root / "source", self.root / "artifacts"
        (source / "app").mkdir(parents=True)
        (source / "app/__init__.py").write_text("")
        (source / "pyproject.toml").write_text(
            "[project]\nname='test'\nversion='0.1'\n"
        )
        (source / "uv.lock").write_text("test source identity only\n")
        (artifacts / "uv-cache").mkdir(parents=True)
        for name, _, _, _ in BOOTSTRAP.ARTIFACTS:
            shutil.copyfile(CACHE / name, artifacts / name)
        return source, artifacts

    def run_bundler(self, source, artifacts, output):
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parents[1] / "package_controller_runtime.py"),
                "--source",
                str(source),
                "--artifacts",
                str(artifacts),
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    @unittest.skipUnless(
        (CACHE / BOOTSTRAP.ARTIFACTS[0][0]).is_file(),
        "requires the SHA-verified pinned offline artifact cache",
    )
    def test_bundler_rejects_absolute_cache_symlink_before_creating_archive(self):
        source, artifacts = self.prepare_bundle_inputs()
        cache = artifacts / "uv-cache"
        (cache / "target").write_text("contained dependency bytes")
        (cache / "link").symlink_to(cache / "target")
        output = self.root / "bundle.tar.gz"
        result = self.run_bundler(source, artifacts, output)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("absolute cache symlink", result.stderr)
        self.assertFalse(output.exists())

    @unittest.skipUnless(
        (CACHE / BOOTSTRAP.ARTIFACTS[0][0]).is_file(),
        "requires the SHA-verified pinned offline artifact cache",
    )
    def test_bundler_relative_cache_chain_survives_relocation(self):
        source, artifacts = self.prepare_bundle_inputs()
        cache = artifacts / "uv-cache"
        (cache / "nested").mkdir()
        (cache / "target").write_text("contained dependency bytes")
        (cache / "nested/first").symlink_to("../target")
        (cache / "second").symlink_to("nested/first")
        output = self.root / "bundle.tar.gz"
        result = self.run_bundler(source, artifacts, output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        relocated = self.root / "relocated"
        with tarfile.open(output) as archive:
            for member in archive:
                if member.issym():
                    self.assertFalse(member.linkname.startswith("/"))
            archive.extractall(relocated, filter="data")
        shutil.rmtree(artifacts)
        link = relocated / "artifacts/uv-cache/second"
        self.assertEqual(link.read_text(), "contained dependency bytes")
        self.assertTrue(link.resolve().is_relative_to(relocated))

    def test_source_directory_symlink_refused(self):
        (self.root / "app").mkdir()
        (self.root / "app/link").symlink_to(self.root, target_is_directory=True)
        (self.root / "pyproject.toml").write_text("project")
        (self.root / "uv.lock").write_text("lock")
        with self.assertRaisesRegex(ValueError, "directory symlink"):
            BOOTSTRAP.source_files(self.root)

    def test_inherited_uv_and_python_configuration_removed(self):
        with mock.patch.dict(
            os.environ,
            {
                "UV_INDEX_URL": "https://untrusted.invalid",
                "PYTHONPATH": "/tmp/untrusted",
                "VIRTUAL_ENV": "/tmp/venv",
                "HTTPS_PROXY": "https://proxy.invalid",
            },
            clear=True,
        ):
            environment = BOOTSTRAP.clean_environment()
        self.assertNotIn("UV_INDEX_URL", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("VIRTUAL_ENV", environment)
        self.assertEqual(environment["HTTPS_PROXY"], "https://proxy.invalid")

    def test_uv_lock_and_directories_become_readable_without_write_access(self):
        venv = self.root / ".venv"
        venv.mkdir(mode=0o700)
        lock = venv / ".lock"
        lock.write_bytes(b"")
        lock.chmod(0o666)
        executable = venv / "python"
        executable.write_bytes(b"executable")
        executable.chmod(0o700)
        BOOTSTRAP.readable_permissions(self.root)
        self.assertEqual(venv.stat().st_mode & 0o7777, 0o755)
        self.assertEqual(lock.stat().st_mode & 0o7777, 0o644)
        self.assertEqual(executable.stat().st_mode & 0o7777, 0o755)

    def test_private_installation_parent_refused_without_chmod(self):
        self.root.chmod(0o700)
        with self.assertRaisesRegex(ValueError, "cannot traverse"):
            BOOTSTRAP.controller_traversable(self.root)
        self.assertEqual(self.root.stat().st_mode & 0o7777, 0o700)

    def test_disk_floor_fails_before_build_or_account_mutation(self):
        source, artifacts = self.root / "source", self.root / "artifacts"
        artifacts.mkdir()
        with (
            mock.patch.object(BOOTSTRAP.os, "geteuid", return_value=0),
            mock.patch.object(BOOTSTRAP.platform, "system", return_value="Linux"),
            mock.patch.object(BOOTSTRAP.platform, "machine", return_value="x86_64"),
            mock.patch.object(
                BOOTSTRAP.platform, "libc_ver", return_value=("glibc", "2.31")
            ),
            mock.patch.object(BOOTSTRAP, "trusted_source"),
            mock.patch.object(BOOTSTRAP, "trusted_path"),
            mock.patch.object(BOOTSTRAP, "controller_traversable"),
            mock.patch.object(BOOTSTRAP, "source_digest", return_value="reviewed"),
            mock.patch.object(BOOTSTRAP, "SERVICE_API", self.root / "absent"),
            mock.patch.object(
                BOOTSTRAP.shutil,
                "disk_usage",
                return_value=mock.Mock(free=5 * 1024**3 - 1),
            ),
            mock.patch.object(BOOTSTRAP, "build_runtime") as build,
            mock.patch.object(BOOTSTRAP, "ensure_account") as account,
            self.assertRaisesRegex(ValueError, "4GiB emergency"),
        ):
            BOOTSTRAP.install(source, artifacts, "reviewed", offline=True)
        build.assert_not_called()
        account.assert_not_called()


if __name__ == "__main__":
    unittest.main()
