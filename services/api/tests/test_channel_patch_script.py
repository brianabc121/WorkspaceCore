"""接入包的 bin/channel-patch.mjs：给 openclaw-channel 0.3.3 打补丁。
T-0120：自动回复被拒收时告诉智能体；T-0221：给 resolveAgentRoute 传 peer，各对话各用一个会话。

用 Node 直接运行脚本。真实的 0.3.3 文件不在仓库里：
这里用含同样的 catch 块与路由调用的合成文件测变换和补上的代码，
用改过的文件测“不是锁定版本就不打”；在真实文件上打补丁、核对结果 sha256 由 agents 端到端验收做
（tests/e2e/agents_acceptance.py 的 patch_plugin）。没有 node 时跳过。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.agent_bundle import PLUGIN_PATCH, PLUGIN_VERSION
from app.domain.agent_outbox import REJECT_REASONS

BIN = Path(__file__).resolve().parents[1] / "app/agent_bundle/files/bin"
SCRIPT = BIN / "channel-patch.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="没有 node")


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
    )
    return json.loads(result.stdout)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    return subprocess.run(  # noqa: S603 - 测试里运行固定的脚本
        [NODE, str(SCRIPT), *args], capture_output=True, text=True, timeout=30, check=False
    )


def _fake_inbound(anchor: str, route_anchor: str) -> str:
    """和 0.3.3 dist/inbound.js 同样结构的一小段：给收到的消息定路由；deliver 失败时进 catch 块。"""
    return (
        'import { formatSdkError } from "./utils.mjs";\n'
        "export function routeFor(runtime, client, msg, group, cfg) {\n"
        "    const baseSessionKey = group ? `openim:group:${msg.groupID}`.toLowerCase()"
        " : `openim:${msg.sendID}`.toLowerCase();\n"
        "    const route = runtime.channel.routing?.resolveAgentRoute?.({\n"
        "        cfg,\n"
        "        sessionKey: baseSessionKey,\n"
        f"{route_anchor}\n"
        "    return route;\n"
        "}\n"
        "export async function deliverFails(api, route, sessionKey, group, error) {\n"
        "    const deliver = async () => {\n"
        "                    try {\n"
        "                        throw error;\n"
        "                    }\n"
        f"{anchor}\n"
        "    };\n"
        "    await deliver();\n"
        "}\n"
    )


def _anchors() -> tuple[str, str]:
    anchors = _node("console.log(JSON.stringify([m.ANCHOR, m.ROUTE_ANCHOR]))")
    return anchors[0], anchors[1]


def test_reasons_match_the_workspace() -> None:
    """插件里的原因表与工作台的拒收码一致：码相同，前半句与工作台的原因相同。"""
    reasons = _node("console.log(JSON.stringify(m.REASONS))")
    assert {int(k) for k in reasons} == set(REJECT_REASONS)
    for code, (reason, advice) in reasons.items():
        assert reason == REJECT_REASONS[int(code)][0]
        assert advice


def test_transform_and_helper_tell_the_agent(tmp_path: Path) -> None:
    anchor, route_anchor = _anchors()
    (tmp_path / "utils.mjs").write_text(
        "export const formatSdkError = (e) => `errCode=${e.errCode}`;\n"
    )
    source = _fake_inbound(anchor, route_anchor)
    patched = _node(f"console.log(JSON.stringify(m.transform({json.dumps(source)})))")
    assert patched.count("myworkspace-patch:T-0120") == 2
    assert patched.count("myworkspace-patch:T-0221") == 1
    (tmp_path / "inbound.mjs").write_text(patched)
    probe = f"""
import {{ deliverFails }} from {json.dumps((tmp_path / "inbound.mjs").as_uri())};
const events = [], logs = [];
const api = {{
  logger: {{ error: (t) => logs.push(t), warn: (t) => logs.push(t) }},
  runtime: {{ system: {{ enqueueSystemEvent: (text, options) => {{
    events.push([text, options]);
    return true;
  }} }} }},
}};
await deliverFails(api, {{ agentId: "main" }}, "openim:group:g1", true, {{ errCode: 5002 }});
await deliverFails(api, {{ agentId: "main" }}, "openim:ws_x", false, {{ errCode: 5005 }});
await deliverFails(api, {{ agentId: "main" }}, "openim:ws_x", false, new Error("socket closed"));
await deliverFails({{ logger: api.logger }}, {{}}, "openim:ws_x", false, {{ errCode: 5001 }});
console.log(JSON.stringify({{ events, logs }}));
"""
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - 测试里运行生成的模块
        [NODE, "--input-type=module", "-e", probe],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    out = json.loads(result.stdout)
    events = out["events"]
    # 只对工作台的拒收码放系统事件；网络错误、没有这个运行时接口时只记原来的日志
    assert len(events) == 2
    text, options = events[0]
    assert text.startswith("[工作台] 你刚才在这个群聊里的回复没有发出（5002）")
    assert "补发" in text and options == {"agentId": "main", "sessionKey": "openim:group:g1"}
    assert "私聊" in events[1][0] and "im_list_targets" in events[1][0]
    assert sum("deliver failed" in line for line in out["logs"]) == 4
    with pytest.raises(subprocess.CalledProcessError):
        _node(f"m.transform({json.dumps(source + anchor)})")  # 出现两次：不打
    with pytest.raises(subprocess.CalledProcessError):
        _node(f"m.transform({json.dumps(source.replace(route_anchor, ''))})")  # 没有路由调用：不打


def test_route_patch_gives_each_conversation_its_own_session(tmp_path: Path) -> None:
    """T-0221：补丁后插件把对方（peer）交给 resolveAgentRoute。

    群是 group + 群 ID，私聊是 direct + 发送者；升级只补这一处（routeTransform），
    从旧补丁升级与从原文件打的结果一致（真实文件上的核对见 agents 端到端验收）。"""
    anchor, route_anchor = _anchors()
    (tmp_path / "utils.mjs").write_text("export const formatSdkError = (e) => String(e);\n")
    source = _fake_inbound(anchor, route_anchor)
    patched = _node(f"console.log(JSON.stringify(m.transform({json.dumps(source)})))")
    (tmp_path / "inbound.mjs").write_text(patched)
    probe = f"""
import {{ routeFor }} from {json.dumps((tmp_path / "inbound.mjs").as_uri())};
const seen = [];
const runtime = {{ channel: {{ routing: {{ resolveAgentRoute: (input) => {{
  seen.push(input);
  const peer = input.peer;
  return {{ agentId: "main", sessionKey: `agent:main:openim:${{peer.kind}}:${{peer.id}}` }};
}} }} }} }};
const client = {{ config: {{ accountId: "default" }} }};
const group = routeFor(runtime, client, {{ groupID: "G1", sendID: "ws_admin" }}, true, {{}});
const direct = routeFor(runtime, client, {{ groupID: "", sendID: "ws_admin" }}, false, {{}});
console.log(JSON.stringify({{ seen, keys: [group.sessionKey, direct.sessionKey] }}));
"""
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - 测试里运行生成的模块
        [NODE, "--input-type=module", "-e", probe],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    out = json.loads(result.stdout)
    assert [s["peer"] for s in out["seen"]] == [
        {"kind": "group", "id": "G1"},
        {"kind": "direct", "id": "ws_admin"},
    ]
    assert [s["channel"] for s in out["seen"]] == ["openim", "openim"]
    assert out["keys"] == ["agent:main:openim:group:G1", "agent:main:openim:direct:ws_admin"]
    # 只打第一处的旧补丁 + routeTransform = 新补丁：去掉补上的那一行就回到只有第一处的样子
    route_line = next(line for line in patched.splitlines() if "myworkspace-patch:T-0221" in line)
    v1_like = patched.replace(route_line + "\n", "")
    upgraded = _node(f"console.log(JSON.stringify(m.routeTransform({json.dumps(v1_like)})))")
    assert upgraded == patched


def test_apply_refuses_files_that_are_not_the_locked_version(tmp_path: Path) -> None:
    target = tmp_path / "inbound.js"
    target.write_text("// 不是 0.3.3 的原文件\n")
    result = _run(str(target))
    assert result.returncode == 7 and "plugin_patch_source_mismatch" in result.stderr
    assert target.read_text() == "// 不是 0.3.3 的原文件\n"
    # 带着补丁标记、但内容和预期的结果不一样（被改过）：不认为已经打好
    target.write_text("// myworkspace-patch:T-0120\n")
    result = _run(str(target))
    assert result.returncode == 8 and "patched_file_changed" in result.stderr


def test_versions_and_marker_are_consistent() -> None:
    """启动包装按补丁标记上报插件版本，工作台据此判断实例的接入包是不是新的。"""
    markers = _node("console.log(JSON.stringify([m.MARKER, m.ROUTE_MARKER]))")
    start = (BIN / "myworkspace-openclaw-start").read_text()
    assert all(json.dumps(marker) in start for marker in markers)
    assert f"+{PLUGIN_PATCH}`" in start and PLUGIN_PATCH == "myworkspace.2"
    hashes = _node(
        "console.log(JSON.stringify([m.ORIGINAL_SHA256, m.PATCHED_SHA256, m.PATCHED_V1_SHA256]))"
    )
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes) and len(set(hashes)) == 3
    assert PLUGIN_VERSION == "0.3.3"
