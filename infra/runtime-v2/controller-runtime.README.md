# Native runtime-v2 controller prerequisite

This package supplies an isolated Python 3.12 interpreter and the complete production
dependency set from `services/api/uv.lock` for the reviewed native controller service.
It does not install or start a service, write credentials, change feature flags, or
replace the operating system's Python. Tenant Pods receive neither this host tree nor
a host socket. Existing service hardening, including `PrivateTmp=true`, stays intact.

The installer runs on Python 3.9 or newer. It is intended for the dedicated T-0224
nonproduction Debian 11/12 amd64 hosts. The application itself still requires
`>=3.12,<3.13`; Debian's Python 3.9/3.11 only executes the bootstrap.

## Verified pinned inputs

These assets were downloaded from the official Astral releases and their actual
bytes checked against the release API `digest` and published checksum files.

| Input | Official release | SHA256 |
| --- | --- | --- |
| `cpython-3.12.16+20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz` | [python-build-standalone 20261110](https://github.com/astral-sh/python-build-standalone/releases/tag/20261110) | `6a9e62dad033790aef722dbd370e0ce76ce17b73627ba3ae679f537049350b39` |
| `uv-x86_64-unknown-linux-gnu.tar.gz` | [uv 0.13.8](https://github.com/astral-sh/uv/releases/tag/0.13.8) | `5acfd9177cfecf0bf87578f059728215ccc884388988f55f7a65041b57387d91` |

Checksum sources are the Python release's
[SHA256SUMS](https://github.com/astral-sh/python-build-standalone/releases/download/20261110/SHA256SUMS)
and uv's
[asset checksum](https://github.com/astral-sh/uv/releases/download/0.13.8/uv-x86_64-unknown-linux-gnu.tar.gz.sha256).
The installer embeds the verified digests; it never resolves `latest`.

Python and uv's ELF references require glibc at most 2.17. The full locked dependency
closure requires glibc 2.28: in particular, asyncpg has a CPython 3.12 manylinux 2.28
wheel. Installation explicitly selects `--python-platform x86_64-manylinux_2_28`.
This also selects cryptography's 2.28 wheel instead of the 2.34 wheel that a newer
build host would otherwise cache. Debian 11's glibc 2.31 and Debian 12's glibc 2.36
satisfy that floor. CPython-specific wheels are for 3.12; `abi3` dependencies can
target an older CPython ABI while running under the isolated 3.12 interpreter.
This does not make the application compatible with the hosts' older Python versions.

## Prepare the offline input bundle

Freeze the approved `services/api/app`, `pyproject.toml`, and `uv.lock` before
packaging. Changes to any source file require a new bundle and reviewed source
digest. The bundle contains source and dependency inputs; a prebuilt virtualenv
must not be moved between paths or machines.

Fetch only the two pinned archives:

```sh
python3 infra/runtime-v2/bootstrap_controller_runtime.py fetch \
  --artifacts /absolute/controller-artifacts
```

Populate and validate the dependency cache locally with the same build function
used by installation. Use a new absolute output path on each attempt:

```python
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "bootstrap", "infra/runtime-v2/bootstrap_controller_runtime.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
bootstrap.build_runtime(
    Path("/absolute/controller-local-validation"),
    Path("/absolute/approved-checkout/services/api"),
    Path("/absolute/controller-artifacts"),
    offline=False,
)
```

The underlying command is the pinned uv binary running:

```text
uv --no-config sync --project API --python ISOLATED_PYTHON --locked --no-dev --no-build
  --no-install-project
  --no-python-downloads --python-platform x86_64-manylinux_2_28
  --link-mode copy --cache-dir ARTIFACTS/uv-cache
```

`--locked` checks the project/lock relationship; all package versions and wheel
hashes come from the existing lock. `--no-build` prevents an unlocked source-build
toolchain. The verified current lock installs 55 distributions and excludes the
development group. No dependency versions are rewritten or independently resolved.
`--no-config` disables ambient user/system/ancestor uv configuration discovery;
`--no-install-project` explicitly preserves this application's `package = false`
installation semantics even while uv configuration discovery is disabled.

Create a new bundle, preserving the uv cache's contained relative symlinks:

```sh
python3 infra/runtime-v2/package_controller_runtime.py \
  --source /absolute/approved-checkout/services/api \
  --artifacts /absolute/controller-artifacts \
  --output /absolute/controller-runtime-offline.tar.gz
```

The accompanying `.tar.gz.json` records the bundle, deterministic source, and lock
SHA256 values. Archive entries have root ownership and normalized permissions.
Cache symlinks must be relative and remain inside the cache after complete chained
resolution; absolute links are rejected before an output archive is created.
Verify the bundle SHA256 after transfer through the approved deployment channel.
Extract into a new root-owned staging directory with traversable parents and no
group/other write permissions. Do not extract over an existing application tree.

## Install after the source review gate

Run the system Python as root against the transferred, verified bundle's helper:

```sh
python3 /absolute/root-owned-staging/bootstrap_controller_runtime.py install \
  --source /absolute/root-owned-staging/source \
  --artifacts /absolute/root-owned-staging/artifacts \
  --expect-source-sha256 REVIEWED_SOURCE_SHA256 \
  --offline
```

The installer verifies source ownership, ancestor permissions, source digest,
archive digests, cache ownership, architecture, and glibc before executing the
downloaded tools. It refuses existing runtime or API paths and account ID conflicts.
Existing installation parents must be traversable by UID10001; the installer does
not widen private directories. Before creating the service compatibility link it
runs the default-disabled controller as UID/GID10001 with no supplementary groups.
Installations deliberately do not overwrite or upgrade an existing deployment.
A failed installation remains inactive for inspection; do not rerun against it
without reviewing its partial tree.

The resulting paths are:

| Path | Purpose |
| --- | --- |
| `/opt/workspace-runtime-t0224/python` | Verified standalone Python 3.12.16 |
| `/opt/workspace-runtime-t0224/api` | Root-owned approved application source and `.venv` |
| `/opt/workspace-runtime-t0224/bootstrap-manifest.json` | Installed source/lock/artifact identities, `enabled: false` |
| `/opt/workspace/services/api` | New compatibility symlink to the isolated API directory |

The symlink satisfies the existing reviewed service's `WorkingDirectory` and
`ExecStart=/opt/workspace/services/api/.venv/bin/python -m app.runtime_v2`.
The helper creates only the `workspace-runtime` account with UID/GID 10001 and a
non-login shell. Source, interpreter, and virtualenv directories are root-owned
0755; files are 0644 or 0755 for executables. The controller can read/execute them
and cannot modify them. Permissions are normalized after uv completes, including
uv's otherwise writable `.venv/.lock`. Runtime state belongs in the reviewed
service's `StateDirectory`, never alongside the source or virtualenv.

The fixed prefix `/opt/workspace-runtime-t0224` is a new sibling of the existing
deployment tree. The actual `/opt/workspace-t0224/completion` parent is private
0700 and remains private; do not chmod it to make a controller path traversable.
Unknown preexisting native-prefix or compatibility paths are refused.

The offline install requires at least **5 GiB free in `/opt` after staging**:
1 GiB installation allowance plus a **4 GiB emergency floor**. Online installation
requires 6 GiB. A second check before exposing the service path enforces the 4 GiB
floor. The tested installed tree is about 250 MiB. Coordinate with other disk
writers and recheck the actual host immediately before installation; the earlier
inventory of A (6.79 GiB free) is not a current free-space guarantee.

## Credentials and activation remain operator steps

The existing service reads `/etc/workspace/runtime-v2/controller.env` as root before
dropping to UID10001. Keep that environment file **root-owned 0600**; UID10001 does
not need direct access to it. Do not print its contents in verification logs.

Files opened by the controller require different permissions:

- Copy the **public CA certificate only** to a dedicated accessible path, owned by
  root, either 0644 or 0640 with group `workspace-runtime` (GID10001). A CA file below
  a root-only 0700 directory remains inaccessible even when the file is 0644.
- Install the dedicated, least-privilege kubeconfig as root:workspace-runtime 0640,
  with parent directories traversable by GID10001 (for example root:workspace-runtime
  0750). Apply the same rule to separately referenced client certificate/key files.
- Keep CA signing keys and unrelated credentials in their existing private paths.
  Do not widen those parent directories to make a public certificate readable.
- Use absolute paths in the controller environment and kubeconfig. Have UID10001
  validate readability and the scoped Kubernetes TLS connection without logging
  credential contents. Supply a separately verified kubectl binary on the service
  PATH; kubectl is outside this Python prerequisite package.

Only after the code, scoped credentials, resource policy, and host checks pass
should the parent deployment install/start the reviewed service and explicitly
enable the nonproduction controller. The installer never performs those steps.

The real service's `PrivateTmp=true` creates a mount namespace. A container import
smoke does not prove host root retirement, mount references, namespace cleanup, or
live Kubernetes access. Keep the sandbox and perform the retirement/mount/process
inventory against the actual running service PID and namespace before acceptance.

## Verification

```sh
python3 -m unittest discover -s infra/runtime-v2/tests \
  -p 'test_controller_runtime_bootstrap.py' -v
```

The boundary tests cover artifact corruption, archive traversal and symlink
escapes, source symlinks, inherited Python/uv overrides, permission normalization,
and rejecting insufficient disk space before installation/account mutation.
Private existing installation parents are also rejected without changing their mode.
The pinned-uv regression performs an actual offline locked build with a malformed
ambient uv configuration. Additional tests reject chained symlink escapes/cycles
before extraction and exercise relative cache-link relocation through the bundler.
Actual offline root installation and UID/GID10001 execution were also tested in
isolated Debian 11/Python3.9 and Debian 12/Python3.11 containers with networking
disabled. These local tests do not substitute for activating and observing the
native service on the two hosts.
