"""OC-6 配置检查（T-0058）：不需要数据库，读仓库里的部署配置与版本记录。

- OC-0 ADR F4：实例可以用 OpenClaw 的 `message` 工具主动私聊其他实例，拦截靠 OpenIM 的“单聊发送前”
  回调（工作台判断，实例之间一律拒收，见 test_im_accounts_callbacks.py）。这里检查部署配置确实打开了
  发送前回调，而且回调失败时消息不发出（上游默认 failedContinue: true）。
- OC-0 ADR F9：`@openim/openclaw-channel` 锁定的版本与 tarball sha256 在各处记录一致；升级前用
  tests/e2e/openclaw_plugin_review.py 审查发布包（ci-security 每次核对锁定版本）。
"""

import re
from pathlib import Path

from app.agent_bundle import PLUGIN_VERSION

ROOT = Path(__file__).resolve().parents[3]
PIN = re.compile(
    r"`@openim/openclaw-channel@([0-9][0-9A-Za-z.\-]*)`.*?tarball sha256 `([0-9a-f]{64})`"
)


def _openim_env() -> dict[str, str]:
    """openim-server 的 IMENV_* 环境变量（只在这个服务里出现；按文本读，不依赖 YAML 库）。"""
    text = (ROOT / "infra/openim/compose.yaml").read_text(encoding="utf-8")
    found = re.findall(r"^\s+(IMENV_[A-Z0-9_]+):\s*(.+?)\s*$", text, flags=re.MULTILINE)
    env = {key: value.strip('"') for key, value in found}
    assert len(env) == len(found), "IMENV_* 重复出现"
    return env


def test_before_send_callbacks_are_on_and_fail_closed() -> None:
    env = _openim_env()
    for kind in ("SINGLE", "GROUP"):
        assert env[f"IMENV_WEBHOOKS_BEFORESEND{kind}MSG_ENABLE"] == "true"
        assert env[f"IMENV_WEBHOOKS_BEFORESEND{kind}MSG_FAILEDCONTINUE"] == "false"
    # 回调发给工作台 API 的内部地址（随机路径），不经过公网
    assert "/internal/openim/" in env["IMENV_WEBHOOKS_URL"]
    assert env["IMENV_WEBHOOKS_URL"].split("${OPENIM_WEBHOOK_URL:-", 1)[1].startswith("http://api:")
    # 删好友后的回调用于自动补回（方案第 7 节“删好友”）
    assert env["IMENV_WEBHOOKS_AFTERDELETEFRIEND_ENABLE"] == "true"


def test_openclaw_channel_pin_is_consistent() -> None:
    match = PIN.search((ROOT / "docs/versions.md").read_text(encoding="utf-8"))
    assert match is not None, "docs/versions.md 里找不到插件的版本与 sha256"
    version, sha256 = match.groups()
    assert version == PLUGIN_VERSION
    for path in (
        "services/api/app/agent_bundle/files/CHECKLIST.md",
        "infra/openclaw/README.md",
        "docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md",
    ):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert sha256 in text, path
        assert version in text, path
    # 安装命令锁定版本（不能装 latest）
    for path in ("services/api/app/agent_bundle/files/CHECKLIST.md", "infra/openclaw/README.md"):
        installs = re.findall(r"plugins install (\S+)", (ROOT / path).read_text(encoding="utf-8"))
        assert installs and set(installs) == {f"@openim/openclaw-channel@{version}"}, path
    # 审查脚本读的是同一处锁定值
    script = (ROOT / "tests/e2e/openclaw_plugin_review.py").read_text(encoding="utf-8")
    assert 'VERSIONS = ROOT / "docs" / "versions.md"' in script


def test_im_tokens_last_30_days() -> None:
    """方案第 4.3 节：IM 令牌有效期 30 天。

    上游默认 90 天；T-0063 在锁定的补丁镜像上实测这个环境变量生效。
    """
    assert _openim_env()["IMENV_OPENIM_RPC_AUTH_TOKENPOLICY_EXPIRE"] == "30"


def test_long_running_services_have_healthchecks() -> None:
    """规范第 14 节：服务要有就绪检查（一次性的 *-init 除外）；make openim-up 用 --wait 等就绪。"""
    for path in ("infra/openim/compose.yaml", "compose.yaml"):
        text = (ROOT / path).read_text(encoding="utf-8")
        services = text.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        blocks = re.split(r"\n(?=  [a-z][a-z0-9-]*:\n)", services)
        for block in blocks:
            name = block.strip().split(":", 1)[0]
            if not name or name.endswith("-init") or 'restart: "no"' in block:
                continue
            assert "healthcheck:" in block, f"{path}: {name} 没有健康检查"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "$(OPENIM_COMPOSE) up -d --wait" in makefile
