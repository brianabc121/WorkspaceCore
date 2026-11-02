"""make env（scripts/init_env.py）在构建前检查 .env（T-0068）。

部署时用 cp .env.example .env 复制出来的 .env 会留着占位符；
make env 以前对已存在的 .env 一律不检查，
要等镜像构建完，api 才在启动时以“CREDENTIAL_ENCRYPTION_KEY 必须是 64 位十六进制”拒绝启动。
"""

import ast
import importlib.util
import secrets
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / ".env.example"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("init_env", ROOT / "scripts/init_env.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def init_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load()
    monkeypatch.setattr(module, "EXAMPLE", EXAMPLE)
    monkeypatch.setattr(module, "TARGET", tmp_path / ".env")
    return module


def _set(path: Path, **values: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for key, value in values.items():
        lines = [f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_generated_env_passes_and_a_rerun_keeps_it(
    init_env: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert init_env.main() == 0
    target: Path = init_env.TARGET
    first = target.read_text(encoding="utf-8")
    assert init_env.problems(first) == []
    assert init_env.main() == 0
    assert target.read_text(encoding="utf-8") == first
    assert capsys.readouterr().err == ""


def test_a_copied_example_stops_before_the_build(
    init_env: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    target: Path = init_env.TARGET
    target.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    assert init_env.main() == 1
    err = capsys.readouterr().err
    assert "占位符" in err
    for key in ("POSTGRES_PASSWORD", "DATABASE_URL", "SESSION_SECRET", "CREDENTIAL_ENCRYPTION_KEY"):
        assert key in err
    # The placeholder is reported once, not again as a malformed key.
    assert "必须是 64 位十六进制" not in err
    assert "mv .env .env.old" in err and "docker compose down -v" in err
    # Existing values are never rewritten.
    assert target.read_text(encoding="utf-8") == EXAMPLE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "value", ["abc123", "g" * 64, secrets.token_hex(32)[:63], secrets.token_hex(33)]
)
def test_a_malformed_encryption_key_is_reported(init_env: ModuleType, value: str) -> None:
    assert init_env.main() == 0
    _set(init_env.TARGET, CREDENTIAL_ENCRYPTION_KEY=value)
    found = init_env.problems(init_env.TARGET.read_text(encoding="utf-8"))
    assert len(found) == 1 and "CREDENTIAL_ENCRYPTION_KEY 必须是 64 位十六进制" in found[0]
    assert value not in found[0]  # never echo the key


def test_quoted_values_and_previous_keys(init_env: ModuleType) -> None:
    assert init_env.main() == 0
    key, old = secrets.token_hex(32), secrets.token_hex(32)
    _set(
        init_env.TARGET,
        CREDENTIAL_ENCRYPTION_KEY=f'"{key}"',
        CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS=f"{old}, {old}",
    )
    assert init_env.problems(init_env.TARGET.read_text(encoding="utf-8")) == []
    _set(init_env.TARGET, CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS=f"{old},not-a-key")
    assert [
        "CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS" in item
        for item in init_env.problems(init_env.TARGET.read_text(encoding="utf-8"))
    ] == [True]


def test_production_needs_a_long_session_secret(init_env: ModuleType) -> None:
    assert init_env.main() == 0
    _set(init_env.TARGET, SESSION_SECRET="short")  # noqa: S106
    assert (
        init_env.problems(init_env.TARGET.read_text(encoding="utf-8")) == []
    )  # development accepts it
    _set(init_env.TARGET, APP_ENV="production")
    found = init_env.problems(init_env.TARGET.read_text(encoding="utf-8"))
    assert len(found) == 1 and "SESSION_SECRET 至少 32 个字符" in found[0]


def test_short_callback_token_and_windows_line_endings(init_env: ModuleType) -> None:
    assert init_env.main() == 0
    _set(init_env.TARGET, OPENIM_CALLBACK_PATH_TOKEN="tooshort")  # noqa: S106
    text = init_env.TARGET.read_text(encoding="utf-8")
    assert any("OPENIM_CALLBACK_PATH_TOKEN" in item for item in init_env.problems(text))
    _set(init_env.TARGET, OPENIM_CALLBACK_PATH_TOKEN=secrets.token_hex(24))
    crlf = init_env.TARGET.read_text(encoding="utf-8").replace("\n", "\r\n")
    found = init_env.problems(crlf)
    assert len(found) == 1 and "CRLF" in found[0]


def test_missing_variables_are_still_appended(init_env: ModuleType) -> None:
    assert init_env.main() == 0
    target: Path = init_env.TARGET
    target.write_text(
        "\n".join(
            line
            for line in target.read_text(encoding="utf-8").splitlines()
            if not line.startswith("BACKUP_PASSWORD=")
        )
        + "\n",
        encoding="utf-8",
    )
    assert init_env.main() == 0
    appended = [
        line
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.startswith("BACKUP_PASSWORD=")
    ]
    assert len(appended) == 1 and not appended[0].endswith("=")


def test_runs_on_python_3_8_and_3_9() -> None:
    """服务器上的 make env 用系统自带的 python3。

    Debian 11 是 3.9，Ubuntu 20.04 是 3.8；人类部署时在 3.9 上报过 TypeError。
    """
    source = (ROOT / "scripts/init_env.py").read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 8))  # 语法不超过 3.8
    # 标注里的 tuple[str, str] | None 在 3.9 及以下运行时会报错，必须推迟求值
    assert "from __future__ import annotations" in source
