#!/usr/bin/env python3
"""页面上一键部署 MCP 管理器的验收（T-0233，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md）：
真实 sshd（OpenSSH）+ 真实 Docker，真的构建并启动一个会话桥容器。

前提（CI 的 agents job，在「智能体服务器 SSH 验收」之后；那一步结束时已经把服务器从工作台删除、
authorized_keys 恢复原样）：
- Compose 全栈在运行，工作台镜像里带着会话桥的构建文件；
- 本机另起的 sshd（SSH_TARGET_HOST:SSH_TARGET_PORT），账号 SSH_TARGET_USER / SSH_TARGET_PASSWORD；
- 以 root 运行：要读目标账号的 authorized_keys 和部署目录、查看 Docker。

检查：
1. 启用模块后 `available=true`（工作台镜像里真的带着构建文件）；把这台服务器添加成智能体服务器、
   确认指纹、初始化；它出现在可部署的服务器里；
2. 部署一个管理器，指定走 SSH 通道（这台机器就是工作台所在的机器，不指定会走内部网络）：
   八步都完成；容器在运行、只监听 127.0.0.1:<端口>；容器、数据卷、网络都带 `myworkspace.mcp-manager` 标签；
   authorized_keys 多出一行只能转发这一个端口、带固定命令的密钥；
3. 工作台经这条通道连得上管理器（状态接口、控制的平台已写入）；从服务器页面的入口也看得到它、在线；
4. 智能体服务器页面的“清理无用镜像”用的那条命令（docker image prune -f）不影响它；
5. 重启、重新部署（同一个目录、同一个端口、同一把密钥）；
6. 卸载：容器、目录、转发密钥都没有了，登记也没有了；
7. 再部署一个（不指定：和工作台同机，加入内部网络，不装密钥）；把服务器从工作台移除后它照常能用；
   这时普通卸载被拒（工作台登录不上去了），“只删除登记”可以，并写明服务器上还留着什么；
8. 管理令牌和登录密码不在任务记录里。

结束时清掉第 7 步留在服务器上的容器和目录，authorized_keys 恢复成测试前的样子（之后的浏览器验收用同一个账号）。

只依赖 Python 标准库和 docker；用法：
  sudo -E python3 tests/e2e/mcp_manager_deploy_acceptance.py
"""

import json
import os
import pwd
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from agent_ssh_acceptance import AuthorizedKeys, Server, env, sql_rows, wait_job
from e2e_support import Client, Report, default_base_url, parse_base_url
from seed import create_user

PASSWORD = "e2e mcp manager deploy passphrase"  # 只用于本脚本临时创建的工作台账号
API = "/api/v1/llm_bridge"
LABEL = "myworkspace.mcp-manager"
INSTALL_ROOT = "myworkspace-llm-bridge"
DEPLOY_TIMEOUT = 25 * 60
DEPLOY_STEPS = ["connect", "inspect", "upload", "build", "start", "tunnel", "verify", "register"]


def docker(*args: str) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, check=False, timeout=180)
    return done.stdout.strip()


class Deploys:
    def __init__(self, owner: Client, headers: dict[str, str]) -> None:
        self.owner = owner
        self.headers = headers

    def get(self, path: str) -> Any:
        response = self.owner.request("GET", f"{API}{path}", headers=self.headers)
        if response.status != 200:
            sys.exit(f"GET {path} 失败：{response.summary}")
        return response.json()

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self.owner.request("POST", f"{API}{path}", body, headers=self.headers)

    def wait(self, job_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + DEPLOY_TIMEOUT
        last = ""
        while time.monotonic() < deadline:
            job: dict[str, Any] = self.get(f"/deployments/{job_id}")
            current = next((s["key"] for s in job["steps"] if s["status"] == "running"), "")
            if current != last:
                print(f"  … {job['kind']}：{current}", flush=True)
                last = current
            if job["status"] != "running":
                return job
            time.sleep(5)
        sys.exit("部署任务超时")

    def run(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        started = self.post(path, {"request_id": f"e2e-{uuid.uuid4().hex[:10]}", **body})
        if started.status != 202:
            sys.exit(f"POST {path} 失败：{started.summary}")
        job = self.wait(started.json()["id"])
        if job["status"] != "succeeded":
            print(job.get("log") or "", flush=True)
        return job

    def bridges(self) -> list[dict[str, Any]]:
        listed: list[dict[str, Any]] = self.get("/bridges")["bridges"]
        return listed


def steps(job: dict[str, Any]) -> dict[str, str]:
    return {s["key"]: s["status"] for s in job["steps"]}


def managers() -> list[dict[str, Any]]:
    """这台机器上带 MCP 管理器标签的容器。"""
    ids = docker("ps", "-aq", "--filter", f"label={LABEL}").split()
    return [json.loads(docker("inspect", i))[0] for i in ids]


def cleanup(home: Path) -> None:
    for item in managers():
        project = item["Config"]["Labels"].get("com.docker.compose.project", "")
        if project.startswith("mw-mcp-"):
            subprocess.run(
                ["docker", "compose", "-p", project, "down", "-v", "--remove-orphans"],
                capture_output=True, check=False, timeout=300,
            )
    shutil.rmtree(home / INSTALL_ROOT, ignore_errors=True)


def flow(report: Report, host: str, port: int, user: str, password: str, home: Path, keys: AuthorizedKeys) -> int:
    email = f"mcp-{uuid.uuid4().hex[:8]}@e2e.example.com"
    _, workspace, _ = create_user(email, "MCP 管理器部署验收", PASSWORD)
    owner = Client(parse_base_url(default_base_url()))
    login = owner.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if login.status != 200:
        sys.exit(f"登录失败：{login.summary}")
    headers = {"X-Workspace-Id": workspace}
    api = Deploys(owner, headers)

    # ---------------------------------------------------------------- 1. 模块、服务器
    enabled = owner.request("PATCH", "/api/v1/modules/llm_bridge", {"enabled": True}, headers=headers)
    report.check("启用“大模型客户端”模块", enabled.status == 200, enabled.summary)
    listed = api.get("/deployments")
    if not report.check("工作台镜像里带着 MCP 管理器的构建文件（available=true）", listed["available"] is True):
        return report.finish()
    response = owner.request(
        "POST", "/api/v1/agents/servers",
        {"host": host, "port": port, "username": user, "login_password": password}, headers=headers,
    )
    if response.status != 201:
        sys.exit(f"添加服务器失败：{response.summary}")
    created = response.json()["server"]
    server = Server(owner, headers, created["id"])
    blocked = next(t for t in api.get("/deployments/targets")["targets"] if t["id"] == server.id)
    report.check(
        "确认指纹之前这台服务器不能部署，原因写明",
        blocked["deployable"] is False and bool(blocked["block_message"]),
        json.dumps(blocked, ensure_ascii=False),
    )
    confirmed = server.post(
        "confirm", {"version": created["version"], "fingerprint": created["ssh"]["host_key_fingerprint"]}
    )
    report.check("确认指纹", confirmed.status == 200, confirmed.summary)
    response = server.post("prepare", {"version": server.version()})
    report.check("开始初始化（202）", response.status == 202, response.summary)
    state = wait_job(server.detail, timeout=15 * 60)
    if not report.check("初始化成功", state["job"]["status"] == "succeeded"):
        return report.finish()
    target = next(t for t in api.get("/deployments/targets")["targets"] if t["id"] == server.id)
    report.check(
        "这台服务器出现在可以部署的服务器里（智能体服务器，0 个管理器）",
        target["kind"] == "agent_server" and target["deployable"] is True and target["managers"] == 0,
        json.dumps(target, ensure_ascii=False),
    )
    before = set(keys.lines())

    # ---------------------------------------------------------------- 2. 部署（走 SSH 通道）
    body = {"target_kind": "agent_server", "target_id": server.id, "name": "验收一号",
            "platforms": ["claude", "workbuddy"], "network": "tunnel"}
    job = api.run("/deployments", body)
    if not report.check("部署成功（真实构建并启动）", job["status"] == "succeeded", job.get("error_message") or ""):
        return report.finish()
    report.check(
        "八步都完成，网络方式是 SSH 通道",
        list(steps(job)) == DEPLOY_STEPS and set(steps(job).values()) == {"done"} and job["mode"] == "tunnel",
        json.dumps(steps(job)),
    )
    (bridge,) = api.bridges()
    install = bridge["deployment"]
    port_on_host = install["port"]
    (container,) = managers()
    slot = container["Config"]["Labels"][LABEL]
    project = f"mw-mcp-{slot}"
    bindings = container["HostConfig"]["PortBindings"].get("8787/tcp") or []
    report.check(
        "容器在运行，只监听服务器的本机地址",
        container["State"]["Running"] is True
        and bindings == [{"HostIp": "127.0.0.1", "HostPort": str(port_on_host)}],
        json.dumps(bindings),
    )
    host_config = container["HostConfig"]
    report.check(
        "容器没有特权、不挂 Docker socket 和宿主目录、重启策略 unless-stopped",
        host_config["Privileged"] is False and host_config["NetworkMode"] != "host"
        and not any("docker.sock" in b or b.startswith("/") for b in (host_config.get("Binds") or []))
        and host_config["RestartPolicy"]["Name"] == "unless-stopped"
        and (host_config.get("CapDrop") or []) == ["ALL"],
        json.dumps({k: host_config.get(k) for k in ("Privileged", "NetworkMode", "Binds", "RestartPolicy", "CapDrop")}),
    )
    volume = docker("volume", "ls", "-q", "--filter", f"label={LABEL}={slot}").split()
    network = docker("network", "ls", "-q", "--filter", f"label={LABEL}={slot}").split()
    report.check(
        "容器、数据卷、网络都带保护标签，Compose 项目是 mw-mcp-<编号>",
        container["Config"]["Labels"].get("com.docker.compose.project") == project
        and len(volume) == 1 and len(network) == 1,
        f"{project} volumes={volume} networks={len(network)}",
    )
    added = [line for line in keys.lines() if line not in before]
    expected = f'restrict,port-forwarding,permitopen="127.0.0.1:{port_on_host}",command="/bin/false" ssh-ed25519 '
    report.check(
        "authorized_keys 多出一行：只能转发这一个端口、带固定命令的密钥",
        len(added) == 1 and added[0].startswith(expected) and added[0].endswith(f" myworkspace-mcp-{slot}"),
        added[0][:90] if added else "没有新增",
    )
    directory = home / INSTALL_ROOT / slot
    report.check(
        "部署目录权限 700，令牌文件权限 600",
        directory.is_dir() and (directory.stat().st_mode & 0o777) == 0o700
        and ((directory / "bridge.env").stat().st_mode & 0o777) == 0o600,
    )

    # ---------------------------------------------------------------- 3. 工作台经通道访问
    status = api.get(f"/bridges/{bridge['id']}/status")
    report.check(
        "工作台经 SSH 通道连得上管理器，控制的平台已写入",
        status["reachable"] is True and status["platforms"] == ["claude", "workbuddy"],
        json.dumps({k: status.get(k) for k in ("reachable", "platforms", "error")}, ensure_ascii=False),
    )
    report.check(
        "登记里写着部署位置：智能体服务器、SSH 通道、不需要 sudo 密码",
        install["target_kind"] == "agent_server" and install["target_id"] == server.id
        and install["mode"] == "tunnel" and install["needs_sudo"] is False and install["outdated"] is False,
        json.dumps(install, ensure_ascii=False),
    )
    on_server = api.get(f"/installs?target_kind=agent_server&target_id={server.id}")["managers"]
    report.check(
        "服务器页面的入口看得到它，在线",
        len(on_server) == 1 and on_server[0]["bridge_id"] == bridge["id"] and on_server[0]["reachable"] is True,
        json.dumps([(m["name"], m["reachable"]) for m in on_server], ensure_ascii=False),
    )

    # ---------------------------------------------------------------- 4. 页面的清理不影响它
    image = container["Config"]["Image"]
    docker("image", "prune", "-f")  # 智能体服务器页面“清理无用镜像”执行的就是这一条
    (after_prune,) = managers()
    report.check(
        "“清理无用镜像”（docker image prune -f）之后容器照常运行、镜像还在",
        after_prune["State"]["Running"] is True and after_prune["State"]["StartedAt"] == container["State"]["StartedAt"]
        and bool(docker("image", "inspect", "-f", "{{.Id}}", image)),
        image,
    )

    # ---------------------------------------------------------------- 5. 重启、重新部署
    restarted = api.run(f"/bridges/{bridge['id']}/restart", {})
    (after_restart,) = managers()
    report.check(
        "重启：容器重新启动过，工作台经通道确认连得上",
        restarted["status"] == "succeeded" and set(steps(restarted).values()) == {"done"}
        and after_restart["State"]["StartedAt"] != container["State"]["StartedAt"],
        restarted.get("error_message") or "",
    )
    redeployed = api.run(f"/bridges/{bridge['id']}/redeploy", {})
    (current,) = api.bridges()
    report.check(
        "重新部署：同一个登记、同一个端口、同一把转发密钥",
        redeployed["status"] == "succeeded" and current["id"] == bridge["id"]
        and current["deployment"]["port"] == port_on_host
        and [line for line in keys.lines() if line not in before] == added,
        redeployed.get("error_message") or "",
    )
    report.check(
        "重新部署之后工作台仍然连得上",
        api.get(f"/bridges/{bridge['id']}/status")["reachable"] is True,
    )

    # ---------------------------------------------------------------- 6. 卸载
    gone = api.run(f"/bridges/{bridge['id']}/uninstall", {"remove_data": True})
    report.check(
        "卸载：容器、数据卷、目录、转发密钥、登记都没有了",
        gone["status"] == "succeeded" and managers() == [] and not directory.exists()
        and not docker("volume", "ls", "-q", "--filter", f"label={LABEL}={slot}")
        and set(keys.lines()) == before and api.bridges() == [],
        gone.get("error_message") or "",
    )

    # ---------------------------------------------------------------- 7. 同机部署；移除服务器后照常能用
    body = {"target_kind": "agent_server", "target_id": server.id, "name": "验收二号", "platforms": ["codex"]}
    job = api.run("/deployments", body)
    if not report.check("再部署一个（不指定网络方式）成功", job["status"] == "succeeded", job.get("error_message") or ""):
        return report.finish()
    (bridge,) = api.bridges()
    (container,) = managers()
    slot = container["Config"]["Labels"][LABEL]
    report.check(
        "和工作台同一台服务器：加入内部网络，不开端口，也不装转发密钥",
        job["mode"] == "same_host" and not container["HostConfig"]["PortBindings"]
        and any(name.endswith("_integrations") for name in container["NetworkSettings"]["Networks"])
        and set(keys.lines()) == before,
        json.dumps(list(container["NetworkSettings"]["Networks"])),
    )
    report.check("工作台经内部网络连得上", api.get(f"/bridges/{bridge['id']}/status")["reachable"] is True)
    response = server.delete()
    report.check("把服务器从工作台移除（204；上面有管理器也不拦）", response.status == 204, response.summary)
    (orphan,) = api.bridges()
    report.check(
        "移除服务器之后：管理器还登记着、照常连得上，部署位置里服务器为空、名字还在",
        orphan["deployment"]["target_id"] is None and bool(orphan["deployment"]["server_name"])
        and api.get(f"/bridges/{bridge['id']}/status")["reachable"] is True
        and managers()[0]["State"]["Running"] is True,
        json.dumps(orphan["deployment"], ensure_ascii=False),
    )
    refused = api.post(f"/bridges/{bridge['id']}/uninstall", {"request_id": "e2e-gone"})
    report.check(
        "工作台登录不上那台服务器了：普通卸载 409 SERVER_REMOVED",
        refused.status == 409 and refused.code == "SERVER_REMOVED", refused.summary,
    )
    forced = api.post(f"/bridges/{bridge['id']}/uninstall", {"request_id": "e2e-forget", "force": True})
    detail = forced.json()["steps"][0]["detail"] if forced.status == 202 else ""
    report.check(
        "只删除登记：登记没有了，容器还在运行，任务里写明服务器上还留着什么",
        forced.status == 202 and api.bridges() == [] and len(managers()) == 1
        and f"mw-mcp-{slot}" in detail and str(home / INSTALL_ROOT / slot) in detail,
        detail,
    )

    # ---------------------------------------------------------------- 8. 记录里没有令牌和密码
    token = (home / INSTALL_ROOT / slot / "bridge.env").read_text().strip().split("=", 1)[1]
    leaked = sql_rows(
        "SELECT count(*) FROM workspace.llm_bridge_deploy_jobs j WHERE j::text LIKE '%" + token + "%'"
        " OR j::text LIKE '%" + password + "%'"
    ).strip()
    audit = sql_rows(
        "SELECT count(*) FROM workspace.audit_logs a WHERE a.action LIKE 'llm_bridge.%' AND"
        " (a::text LIKE '%" + token + "%' OR a::text LIKE '%" + password + "%')"
    ).strip()
    report.check("管理令牌和登录密码不在任务记录和审计里", (leaked, audit) == ("0", "0"), f"{leaked} / {audit}")
    return report.finish()


def main() -> int:
    if os.geteuid() != 0:
        sys.exit("要以 root 运行：要读目标账号的 authorized_keys 和部署目录、查看 Docker")
    host, port = env("SSH_TARGET_HOST"), int(env("SSH_TARGET_PORT"))
    user, password = env("SSH_TARGET_USER"), env("SSH_TARGET_PASSWORD")
    home = Path(pwd.getpwnam(user).pw_dir)
    keys = AuthorizedKeys(user)
    print(f"信息  测试前 authorized_keys：{keys.describe()}", flush=True)
    report = Report()
    try:
        return flow(report, host, port, user, password, home, keys)
    finally:
        cleanup(home)
        keys.restore()
        print(f"信息  已清理验收留下的容器与目录，authorized_keys 已恢复（{keys.describe()}）", flush=True)


if __name__ == "__main__":
    sys.exit(main())
