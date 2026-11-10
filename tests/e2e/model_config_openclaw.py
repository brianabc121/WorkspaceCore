"""model-config.mjs 的真实 OpenClaw 验证（T-0205）：锁定的 OpenClaw 2026.9.36 网关 + 接入包的 model-config.mjs
与取密钥程序 + 假的工作台实例接口（fake_workspace_agent_api.py）+ 假模型服务（fake_model_server.py）。

不需要整套工作台：几分钟跑完“没有绑定 / 第一次应用 / 目标不变 / 切换 / 换 Key / 无效配置 / 取不到 Key / 解除”，
核对写进 openclaw.json 的内容、真实请求带的 Key 与模型、回报的结果与原因。整套工作台下的同一流程见 agents 场景 27。
用法（要 root：把目录交给容器里的 uid 1000）：sudo python3 tests/e2e/model_config_openclaw.py [--keep]
只用本机 Docker；容器与网络名都以 mcv- 开头，结束时删除。"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(tempfile.mkdtemp(prefix="mcv-"))
B6 = ROOT
IMAGE = "openclaw/openclaw:2026.9.36@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f"
NET = "mcv-net"
results = []


def sh(*cmd, check=True, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


def check(name, ok, detail=""):
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def ctl(path, body=None):
    ip = sh("docker", "inspect", "-f", f'{{{{(index .NetworkSettings.Networks "{NET}").IPAddress}}}}', "mcv-fakews").stdout.strip()
    req = urllib.request.Request(f"http://{ip}:8000{path}", data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET", headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def target(rev, attempt, key_hex, model, api="openai-completions", state="active"):
    cid = uuid.UUID(key_hex * 32 if len(key_hex) == 1 else key_hex)
    pk = "mwc" + cid.hex[:20]
    common = {"binding_version": rev, "desired_revision": rev, "apply_key": f"{rev}.{attempt}", "job_state": "queued", "managed_prefix": "mwc"}
    if state == "revoked":
        return {**common, "state": "revoked", "connection_id": None, "provider_key": None, "api": None, "protocol": None,
                "base_url": None, "model_id": None, "model_name": None, "primary": None, "secret_ref_id": None,
                "config_revision": None, "fingerprint": f"revoked-{rev}"}
    t = {**common, "state": "active", "connection_id": str(cid), "provider_key": pk, "api": api, "protocol": "openai_compatible",
         "base_url": "http://mcv-model:18080/v1", "model_id": model, "model_name": model, "primary": f"{pk}/{model}",
         "secret_ref_id": "mc_" + cid.hex, "config_revision": 1}
    t["fingerprint"] = hashlib.sha256(json.dumps(t, sort_keys=True).encode()).hexdigest()
    return t


def apply():
    out = sh("docker", "exec", "mcv-oc", "node", "/opt/myworkspace/bin/model-config.mjs", check=False, timeout=300)
    return (out.stdout.strip().splitlines() or [""])[-1], out.stderr.strip()[-300:]


def records():
    log = HERE / "work" / "fake_model.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines() if x.strip()]


def turn(text):
    since = time.time()
    out = sh("docker", "exec", "mcv-oc", "node", "openclaw.mjs", "agent", "--agent", "main", "-m", text, "--json", check=False, timeout=300)
    got = {(r.get("auth"), r.get("model")) for r in records() if r["at"] >= since and text in (r.get("last_user") or "")}
    if not got:
        print("   turn output:", out.stdout[-500:], out.stderr[-500:])
    return got


def config():
    return json.loads((HERE / "state" / "openclaw.json").read_text())


def main() -> int:
    for name in ("mcv-oc", "mcv-fakews", "mcv-model"):
        sh("docker", "rm", "-f", name, check=False)
    sh("docker", "network", "rm", NET, check=False)
    shutil.rmtree(HERE / "state", ignore_errors=True)
    shutil.rmtree(HERE / "bin", ignore_errors=True)
    shutil.rmtree(HERE / "work", ignore_errors=True)
    (HERE / "work").mkdir()
    shutil.copy(B6 / "tests/e2e/fake_model_server.py", HERE / "work")
    shutil.copy(ROOT / "tests/e2e/fake_workspace_agent_api.py", HERE / "work" / "fakews.py")
    (HERE / "work" / "fake_model.jsonl").touch()
    (HERE / "work" / "fake_model.jsonl").chmod(0o666)
    shutil.copytree(B6 / "services/api/app/agent_bundle/files/bin", HERE / "bin")
    for f in (HERE / "bin").iterdir():
        f.chmod(0o755)
    (HERE / "state").mkdir()
    sys.path.insert(0, str(B6 / "services/api"))
    from app import agent_bundle
    # 新实例还没有主模型时接入包不带模型段（T-0209）
    cfg = json.loads(agent_bundle._openclaw_json([]))
    for k in ("channels", "plugins", "mcp"):
        cfg.pop(k)
    cfg["gateway"]["auth"]["token"] = "local-test-token"
    (HERE / "state" / "openclaw.json").write_text(json.dumps(cfg, indent=2))
    sh("chown", "-R", "1000:1000", str(HERE / "state"), str(HERE / "bin"))
    sh("docker", "network", "create", NET)
    sh("docker", "run", "-d", "--name", "mcv-model", "--network", NET, "-e", "FAKE_MODEL_LOG=/work/fake_model.jsonl",
       "-v", f"{HERE / 'work'}:/work", "--entrypoint", "python", "my-workspace-api:local", "-I", "/work/fake_model_server.py", "18080")
    sh("docker", "run", "-d", "--name", "mcv-fakews", "--network", NET, "-v", f"{HERE / 'work'}:/work",
       "--entrypoint", "python", "my-workspace-api:local", "-I", "/work/fakews.py", "8000")
    sh("docker", "run", "-d", "--name", "mcv-oc", "--network", NET, "--user", "node",
       "-e", "MYWS_AGENT_API=http://mcv-fakews:8000/agent-api", "-e", "MYWS_INSTANCE_TOKEN=t-local",
       "-e", "MYWS_LOCAL_TUNNEL_HOST=mcv-fakews", "-e", "OPENCLAW_GATEWAY_TOKEN=local-test-token",
       "-v", f"{HERE / 'bin'}:/opt/myworkspace/bin:ro", "-v", f"{HERE / 'state'}:/home/node/.openclaw",
       "--entrypoint", "tini", IMAGE, "-s", "--", "node", "/app/docker-entrypoint.mjs", "node", "openclaw.mjs", "gateway")
    for _ in range(120):
        if "[gateway] ready" in sh("docker", "logs", "mcv-oc", check=False).stdout + sh("docker", "logs", "mcv-oc", check=False).stderr:
            break
        time.sleep(3)
    else:
        print(sh("docker", "logs", "--tail", "60", "mcv-oc", check=False).stderr)
        raise SystemExit("gateway not ready")
    print("gateway ready", flush=True)

    ka, kb, kb2 = "sk-local-a-1", "sk-local-b-1", "sk-local-b-2"
    A, Bc = "a" * 32, "b" * 32
    ref_a, ref_b = "mc_" + A, "mc_" + Bc
    status, err = apply()
    check("没有绑定时什么也不做", status == "no_binding", status + err)

    ctl("/control/set", {"target": target(1, 1, A, "m-a1"), "keys": {ref_a: ka}})
    status, err = apply()
    c = config()
    check("第一次应用：applied", status == "applied", status + " " + err)
    check("受管 provider 写入，主模型指向它",
          ("mwc" + A[:20]) in c["models"]["providers"]
          and c["agents"]["defaults"]["model"]["primary"] == f"mwc{A[:20]}/m-a1", json.dumps(sorted(c["models"]["providers"])))
    check("配置里没有 Key", ka not in json.dumps(c))
    check("工具限制保留", c["tools"]["exec"]["security"] == "deny")
    got = turn("本机验证一")
    check("一轮对话用连接 A 的 Key 和模型", got == {(f"Bearer {ka}", "m-a1")}, str(got))
    status, _ = apply()
    check("目标没变：unchanged", status == "unchanged", status)

    ctl("/control/set", {"target": target(2, 1, Bc, "m-b"), "keys": {ref_a: ka, ref_b: kb}})
    status, err = apply()
    c = config()
    check("切换到 B：applied", status == "applied", status + " " + err)
    check("旧受管 provider 删除", ("mwc" + A[:20]) not in c["models"]["providers"] and ("mwc" + Bc[:20]) in c["models"]["providers"])
    got = turn("本机验证二")
    check("切换后用 B 的 Key 与模型", got == {(f"Bearer {kb}", "m-b")}, str(got))

    ctl("/control/set", {"target": target(3, 1, Bc, "m-b"), "keys": {ref_b: kb2}})
    status, err = apply()
    got = turn("本机验证三")
    check("换 Key 后重新应用（secrets reload），下一轮用新 Key", status == "applied" and got == {(f"Bearer {kb2}", "m-b")}, f"{status} {got} {err}")

    bad = target(4, 1, Bc, "m-b", api="openai-chat")
    ctl("/control/set", {"target": bad})
    before = (HERE / "state" / "openclaw.json").read_text()
    status, err = apply()
    check("无效的 api：failed:invalid_config，配置不变", status == "failed:invalid_config" and before == (HERE / "state" / "openclaw.json").read_text(), status + err)

    ctl("/control/set", {"target": target(5, 1, A, "m-a1"), "keys": {ref_b: kb2}})
    status, err = apply()
    check("取不到 Key：failed:credential_unavailable，不写入", status == "failed:credential_unavailable", status + err)

    ctl("/control/set", {"target": target(6, 1, A, "m-a1", state="revoked")})
    status, err = apply()
    c = config()
    check("解除：applied，受管 provider 与主模型移除",
          status == "applied" and not any(k.startswith("mwc") for k in c.get("models", {}).get("providers", {}))
          and not c.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"), status + err)
    reports = ctl("/control/state")["reports"]
    check("每次应用都回报一次（含失败原因）",
          [(r["desired_revision"], r["result"], r.get("reason")) for r in reports] ==
          [(1, "applied", None), (2, "applied", None), (3, "applied", None), (4, "failed", "invalid_config"),
           (5, "failed", "credential_unavailable"), (6, "applied", None)], json.dumps(reports))
    print(json.dumps({"passed": sum(ok for _, ok in results), "failed": sum(not ok for _, ok in results)}))
    return 0 if all(ok for _, ok in results) else 1


try:
    code = main()
finally:
    if "--keep" not in sys.argv:
        for name in ("mcv-oc", "mcv-fakews", "mcv-model"):
            sh("docker", "rm", "-f", name, check=False)
        sh("docker", "network", "rm", NET, check=False)
        shutil.rmtree(HERE, ignore_errors=True)
sys.exit(code)
