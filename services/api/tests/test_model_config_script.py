"""接入包的 bin/model-config.mjs（T-0205）：算补丁、校验后写入、secrets reload、失败原因。

用 Node 直接导入脚本，换掉“读配置”“跑 OpenClaw 命令”“等待”三个函数。没有 node 时跳过。
真实 OpenClaw 里的 `config patch` / 热加载 / `secrets reload` 由 agents 端到端验收跑（场景 26）。
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "app/agent_bundle/files/bin/model-config.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="没有 node")

TARGET = {
    "state": "active",
    "managed_prefix": "mwc",
    "provider_key": "mwc0123456789abcdef0123",
    "api": "openai-completions",
    "base_url": "https://relay.example.com/v1",
    "model_id": "org/model-x",
    "model_name": "Model X",
    "primary": "mwc0123456789abcdef0123/org/model-x",
    "secret_ref_id": "mc_" + "a" * 32,
}
LEGACY_OPENAI = {"apiKey": {"source": "exec", "provider": "myworkspace", "id": "openai_api_key"}}


def _node(code: str) -> Any:
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - 测试里运行固定的脚本
        [
            NODE,
            "--input-type=module",
            "-e",
            f"import * as m from {json.dumps(SCRIPT.as_uri())};\n{code}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    return json.loads(result.stdout)


def _patch(current: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = _node(
        f"console.log(JSON.stringify(m.buildPatch({json.dumps(current)}, {json.dumps(target)})))"
    )
    return out


def test_patch_only_touches_managed_providers_and_primary() -> None:
    current = {
        "models": {
            "providers": {
                "openai": LEGACY_OPENAI,
                "mwcffffffffffffffffffff": {"baseUrl": "https://old.example.com/v1"},
                "mine": {"baseUrl": "https://mine.example.com/v1", "models": []},
            }
        },
        "agents": {"defaults": {"model": {"primary": "openai/gpt-x", "fallbacks": ["mine/a"]}}},
        "mcp": {"servers": {"myworkspace": {"url": "${MYWS_MCP_URL}"}}},
        "tools": {"exec": {"security": "deny"}},
    }
    patch = _patch(current, TARGET)
    assert patch == {
        "models": {
            "providers": {
                # 旧模板的 openai 段与旧的受管 provider 删除；用户自己的 provider 不动
                "openai": None,
                "mwcffffffffffffffffffff": None,
                TARGET["provider_key"]: {
                    "baseUrl": "https://relay.example.com/v1",
                    "api": "openai-completions",
                    "apiKey": {
                        "source": "exec",
                        "provider": "myworkspace",
                        "id": TARGET["secret_ref_id"],
                    },
                    "models": [{"id": "org/model-x", "name": "Model X"}],
                },
            }
        },
        # 只改 primary：fallbacks、MCP、工具限制都不在补丁里
        "agents": {"defaults": {"model": {"primary": TARGET["primary"]}}},
    }


def test_patch_keeps_a_customised_openai_provider_and_same_primary() -> None:
    custom = {**LEGACY_OPENAI, "baseUrl": "https://api.openai.com/v1"}
    current = {
        "models": {"providers": {"openai": custom}},
        "agents": {"defaults": {"model": {"primary": TARGET["primary"]}}},
    }
    patch = _patch(current, TARGET)
    assert "openai" not in patch["models"]["providers"]
    assert "agents" not in patch


def test_revoked_removes_the_managed_provider_and_its_primary() -> None:
    current: dict[str, Any] = {
        "models": {"providers": {TARGET["provider_key"]: {}, "mine": {}}},
        "agents": {"defaults": {"model": {"primary": TARGET["primary"]}}},
    }
    revoked = {"state": "revoked", "managed_prefix": "mwc"}
    assert _patch(current, revoked) == {
        "models": {"providers": {TARGET["provider_key"]: None}},
        "agents": {"defaults": {"model": {"primary": None}}},
    }
    # 主模型不是受管的：只删 provider
    current["agents"]["defaults"]["model"] = "mine/a"
    assert "agents" not in _patch(current, revoked)


HARNESS = """
const calls = [];
const runner = (results) => async (args, input) => {
  calls.push({ args, input: input === undefined ? null : JSON.parse(input) });
  const key = args.slice(0, 2).join(" ") + (args.includes("--dry-run") ? " dry" : "");
  return results[key] ?? { ok: true, stdout: "{}" };
};
const run = async (current, results) => {
  const deps = { run: runner(results), read: () => current, wait: async () => {} };
  const outcome = await m.apply(TARGET, deps);
  console.log(JSON.stringify({ outcome, calls }));
};
"""


def _apply(current: str, results: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = _node(
        f"const TARGET = {json.dumps(TARGET)};\n{HARNESS}\n"
        f"await run({json.dumps(current)}, {json.dumps(results)});"
    )
    return out


def test_apply_validates_then_writes_then_reloads_secrets() -> None:
    ok = {"ok": True, "stdout": '{"ok": true, "checks": {"schema": true}}'}
    got = _apply(json.dumps({}), {"config patch dry": ok, "secrets reload": ok})
    assert got["outcome"] == {"result": "applied", "reason": None}
    args = [call["args"] for call in got["calls"]]
    assert args == [
        ["config", "patch", "--stdin", "--dry-run", "--allow-exec", "--json"],
        ["config", "patch", "--stdin"],
        ["secrets", "reload", "--json"],
    ]
    # 写入的补丁与校验的是同一份，里面没有 Key
    assert got["calls"][0]["input"] == got["calls"][1]["input"]
    assert "sk-" not in json.dumps(got["calls"])


@pytest.mark.parametrize(
    ("results", "reason"),
    [
        (
            {"config patch dry": {"ok": False, "stdout": '{"ok":false,"checks":{"schema":false}}'}},
            "invalid_config",
        ),
        (
            {
                "config patch dry": {
                    "ok": False,
                    "stdout": '{"ok":false,"checks":{"schema":true,"resolvability":false}}',
                }
            },
            "credential_unavailable",
        ),
        # OpenClaw 2026.9.36 实测：失败时 checks 仍是 true，类别在 errors[].kind 里
        (
            {
                "config patch dry": {
                    "ok": False,
                    "stdout": '{"ok":false,"checks":{"schema":true,"resolvability":true},'
                    '"errors":[{"kind":"schema","message":"x"},{"kind":"resolvability"}]}',
                }
            },
            "invalid_config",
        ),
        (
            {
                "config patch dry": {
                    "ok": False,
                    "stdout": '{"ok":false,"checks":{"schema":true,"resolvability":true},'
                    '"errors":[{"kind":"resolvability","message":"NOT_FOUND"}]}',
                }
            },
            "credential_unavailable",
        ),
        ({"config patch": {"ok": False, "stdout": ""}}, "patch_failed"),
        ({"secrets reload": {"ok": False, "stdout": ""}}, "reload_failed"),
    ],
)
def test_apply_reports_fixed_failure_reasons(results: dict[str, Any], reason: str) -> None:
    ok = {"ok": True, "stdout": '{"ok": true}'}
    got = _apply(json.dumps({}), {"config patch dry": ok, **results})
    assert got["outcome"] == {"result": "failed", "reason": reason}
    if reason in ("invalid_config", "credential_unavailable"):
        # 校验没过：不写入
        assert [c["args"][:2] for c in got["calls"]] == [["config", "patch"]]


def test_unreadable_config_needs_manual_handling() -> None:
    got = _apply("{not json", {})
    assert got["outcome"] == {"result": "manual_required", "reason": "config_unreadable"}
    assert got["calls"] == []
