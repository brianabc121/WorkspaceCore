#!/usr/bin/env python3
"""OpenClaw SSH 自动部署的验收（T-0089 B3）：真实 sshd + 真实 OpenClaw 上线。

前提（CI 的 agents job 在「接入入口自动配置验收」之后）：
- Compose 全栈、OpenIM 在运行；外网接入入口已由 deploy.sh connect 自动配置（内部 CA、AGENT_PUBLIC_ADDRESS）；
- 本机另起一个 sshd（SSH_TARGET_HOST:SSH_TARGET_PORT），账号 SSH_TARGET_USER：
  密码登录、在 sudo 组（sudo 要密码）、**不在 docker 组**——这样 Docker 一步真的走 sudo -S；
- 以 root 运行：要读目标账号的 authorized_keys、crontab 与接入包目录。

检查：
1. 主机公钥指纹与 sshd 的一致，确认后才能安装；
2. 用密码安装：sudo 把账号加入 docker 组，专用密钥重新登录，接入包、插件、启动、定时任务，**真实的 OpenClaw 连上工作台**；
3. 密码不出现在任务、目标服务器、审计记录里；authorized_keys 里是专用公钥；
4. 照 crontab 那几行运行一次用量与服务器状态上报（T-0106 / T-0113）；
5. 远程运维动作（T-0114）：查看状态、查看日志、立即上报、重启、重新拉起、清理镜像逐个执行，
   核对 docker compose 的输出格式、输出脱敏、二次认证、审计只记退出码；
6. 不填密码重新部署（只用专用密钥）；
7. 卸载（删除目录）：容器、定时任务、目录都没有了，运维动作不再可用；
8. 吊销：authorized_keys 里没有专用公钥了；删除目标服务器。

只依赖 Python 标准库、docker 与 ssh-keygen；用法：
  sudo -E python3 tests/e2e/agent_ssh_acceptance.py
"""

import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from e2e_support import Client, Report, compose_exec, default_base_url, parse_base_url
from seed import create_user

PASSWORD = "e2e ssh provisioning passphrase"  # noqa: S105 -- 只用于本脚本临时创建的工作台账号
SECRET_VALUE = "sk-ssh-acceptance-" + uuid.uuid4().hex
PROJECT = "myworkspace-openclaw"
MARKER = "# myworkspace-openclaw"
INSTALL_TIMEOUT = 25 * 60


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"缺少环境变量 {name}")
    return value


def run(*cmd: str, user: str | None = None) -> subprocess.CompletedProcess[str]:
    command = ["sudo", "-u", user, "-H", *cmd] if user else list(cmd)
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)  # noqa: S603


def fingerprint(public_key: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".pub", delete=False) as handle:
        handle.write(public_key + "\n")
    try:
        return run("ssh-keygen", "-lf", handle.name).stdout.split()[1]
    finally:
        os.unlink(handle.name)


def cron_command(cron: str, script: str) -> str:
    """crontab 里带这个脚本的那一行的命令部分（去掉前 5 个时间字段与重定向）。"""
    line = next((x for x in cron.splitlines() if script in x), "")
    return line.split(None, 5)[5].split(" >/dev/null")[0] if line.count(" ") >= 5 else "false"


class Ops:
    """远程运维动作（T-0114）：执行一个动作并等它结束。"""

    def __init__(self, owner: Client, headers: dict[str, str], instance_id: str) -> None:
        self.owner = owner
        self.headers = headers
        self.path = f"/api/v1/agents/instances/{instance_id}/ops"

    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = self.owner.request("GET", self.path, headers=self.headers).json()
        return data

    def start(self, body: dict[str, Any]) -> Any:
        return self.owner.request("POST", self.path, body, headers=self.headers)

    def run(self, body: dict[str, Any]) -> dict[str, Any]:
        response = self.start(body)
        if response.status != 202:
            return {"status": f"HTTP {response.status}", "output": response.summary, "exit_code": None}
        action_id = response.json()["id"]
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            data: dict[str, Any] = self.owner.request(
                "GET", f"{self.path}/{action_id}", headers=self.headers
            ).json()
            if data["status"] != "running":
                return data
            time.sleep(2)
        return {"status": "timeout", "output": "", "exit_code": None}


def sql_rows(query: str) -> str:
    return compose_exec(
        "postgres", "sh", "-c", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "{query}"'
    )


class Target:
    def __init__(self, owner: Client, headers: dict[str, str], instance_id: str) -> None:
        self.owner = owner
        self.headers = headers
        self.path = f"/api/v1/agents/instances/{instance_id}/ssh"

    def state(self) -> dict[str, Any]:
        response = self.owner.request("GET", self.path, headers=self.headers)
        if response.status != 200:
            sys.exit(f"读取目标服务器失败：{response.summary}")
        data: dict[str, Any] = response.json()
        return data

    def post(self, action: str, body: dict[str, Any]) -> Any:
        return self.owner.request("POST", f"{self.path}/{action}", body, headers=self.headers)

    def wait(self, timeout: float = INSTALL_TIMEOUT) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            state = self.state()
            job = state.get("job") or {}
            current = next((s["key"] for s in job.get("steps", []) if s["status"] == "running"), "")
            if current != last:
                print(f"  … {job.get('kind')}：{current}", flush=True)
                last = current
            if job and job["status"] != "running":
                return state
            time.sleep(5)
        sys.exit("任务超时")


def reflection_jobs() -> list[dict[str, Any]]:
    """目标服务器上 OpenClaw 里每晚复盘的定时任务（T-0100；按 declarationKey 找）。"""
    names = run("docker", "ps", "--filter", f"label=com.docker.compose.project={PROJECT}",
                "--filter", "label=com.docker.compose.service=openclaw", "--format", "{{.Names}}").stdout.split()
    if len(names) != 1:
        return []
    out = run("docker", "exec", names[0], "node", "openclaw.mjs", "automations", "list", "--json", "--all").stdout
    try:
        jobs: list[dict[str, Any]] = json.loads(out[out.index("{"):]).get("jobs", [])
    except ValueError:  # 没有 JSON（网关没就绪时输出的是错误说明）
        return []
    return [j for j in jobs if j.get("declarationKey") == "myworkspace-knowledge-reflection"]


def step_detail(state: dict[str, Any], key: str) -> str:
    return next((s.get("detail") or "" for s in state["job"]["steps"] if s["key"] == key), "")


def steps(state: dict[str, Any]) -> dict[str, str]:
    return {step["key"]: step["status"] for step in state["job"]["steps"]}


def show_job(state: dict[str, Any]) -> None:
    job = state.get("job") or {}
    print(json.dumps(job.get("steps"), ensure_ascii=False, indent=1), file=sys.stderr)
    print(job.get("error_code"), job.get("error_message"), file=sys.stderr)
    print((job.get("log") or "")[-6000:], file=sys.stderr)


def main() -> int:
    if os.geteuid() != 0:
        sys.exit("要以 root 运行：要读目标账号的 authorized_keys、crontab 与接入包目录")
    host, port = env("SSH_TARGET_HOST"), int(env("SSH_TARGET_PORT"))
    user, password = env("SSH_TARGET_USER"), env("SSH_TARGET_PASSWORD")
    host_key = Path(env("SSH_HOST_KEY_PUB")).read_text(encoding="utf-8").strip()
    home = Path(pwd.getpwnam(user).pw_dir)
    install_dir = home / PROJECT
    keys_file = home / ".ssh" / "authorized_keys"
    report = Report()

    # ---------------------------------------------------------------- 工作台：实例与目标服务器
    email = f"ssh-{uuid.uuid4().hex[:8]}@e2e.example.com"
    _, workspace, _ = create_user(email, "SSH 部署验收", PASSWORD)
    owner = Client(parse_base_url(default_base_url()))
    login = owner.request("POST", "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    if login.status != 200:
        sys.exit(f"登录失败：{login.summary}")
    headers = {"X-Workspace-Id": workspace}
    secret = owner.request(
        "POST", "/api/v1/agents/secrets", {"name": "OpenAI", "value": SECRET_VALUE}, headers=headers
    ).json()
    instance = owner.request(
        "POST", "/api/v1/agents/instances", {"name": "SSH 部署", "network_mode": "public"}, headers=headers
    ).json()
    owner.request(
        "PUT",
        f"/api/v1/agents/instances/{instance['id']}/secret-grants",
        {"version": instance["version"], "grants": [{"secret_id": secret["id"], "ref_id": "openai_api_key"}]},
        headers=headers,
    )
    target = Target(owner, headers, instance["id"])
    response = owner.request(
        "PUT", target.path, {"host": host, "port": port, "username": user}, headers=headers
    )
    if response.status != 200:
        sys.exit(f"填写目标服务器失败：{response.summary}")
    probed = response.json()["target"]
    report.check(
        "探测到的主机公钥指纹与 sshd 的一致（不登录）",
        probed["host_key_fingerprint"] == fingerprint(host_key),
        probed["host_key_fingerprint"],
    )
    early = target.post("install", {"version": probed["version"], "password": password})
    report.check("指纹确认之前不能安装", early.status == 409, early.summary)
    confirmed = owner.request(
        "POST",
        f"{target.path}/confirm",
        {"version": probed["version"], "fingerprint": probed["host_key_fingerprint"]},
        headers=headers,
    ).json()["target"]

    # ---------------------------------------------------------------- 用密码安装
    groups_before = run("id", "-nG", user).stdout.split()
    report.check("准备：账号不在 docker 组", "docker" not in groups_before, " ".join(groups_before))
    response = target.post("install", {"version": confirmed["version"], "password": password})
    report.check("开始安装（202）", response.status == 202, response.summary)
    state = target.wait()
    ok = report.check("安装成功：真实的 OpenClaw 连上了工作台", state["job"]["status"] == "succeeded")
    if not ok:
        show_job(state)
        logs = run("docker", "compose", "-p", PROJECT, "logs", "--no-color", "--tail=120")
        print(logs.stdout[-6000:], logs.stderr[-1000:], file=sys.stderr)
        return report.finish()
    done = steps(state)
    report.check(
        "每一步都完成（docker 一步经 sudo 加入 docker 组）",
        all(done[k] == "done" for k in done if k != "schedule") and done["schedule"] in {"done", "skipped"},
        json.dumps(done, ensure_ascii=False),
    )
    report.check("sudo 把账号加入了 docker 组", "docker" in run("id", "-nG", user).stdout.split())
    jobs = reflection_jobs()
    ok = report.check(
        "建好了每晚复盘的定时任务（OpenClaw 里 1 个，0 22 * * *，Asia/Shanghai）",
        done["reflection"] == "done" and len(jobs) == 1 and jobs[0]["schedule"].get("expr") == "0 22 * * *"
        and jobs[0]["schedule"].get("tz") == "Asia/Shanghai",
        f"{step_detail(state, 'reflection')}；{len(jobs)} 个",
    )
    if not ok:
        print(state["job"].get("log", "")[-3000:], file=sys.stderr)
    seen = owner.request("GET", f"/api/v1/agents/instances/{instance['id']}", headers=headers).json()
    report.check("实例有心跳（last_seen_at）", bool(seen.get("last_seen_at")), str(seen.get("last_seen_at")))
    running = run("docker", "ps", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format", "{{.Names}}")
    report.check("OpenClaw 与本机隧道在运行", running.stdout.count("\n") >= 2, running.stdout.strip())
    keys = keys_file.read_text(encoding="utf-8").splitlines()
    report.check(
        "authorized_keys 里是工作台的专用公钥（指纹一致）",
        len(keys) == 1 and fingerprint(keys[0]) == state["target"]["client_key_fingerprint"],
    )
    report.check("接入包目录只给账号自己（700）", (install_dir.stat().st_mode & 0o777) == 0o700)
    report.check("state/ 交给了 uid 1000", (install_dir / "state").stat().st_uid == 1000)
    dump = sql_rows(
        "SELECT row_to_json(j)::text FROM workspace.agent_ssh_jobs j"
        " UNION ALL SELECT row_to_json(t)::text FROM workspace.agent_ssh_targets t"
        " UNION ALL SELECT row_to_json(a)::text FROM workspace.audit_logs a"
    )
    report.check("登录密码不在任务、目标服务器、审计记录里", password not in dump and len(dump) > 0)
    cron = run("crontab", "-l", "-u", user).stdout
    if done["schedule"] == "done":
        # 每 10 分钟取密钥、每分钟上报 token 用量（T-0104）与服务器状态（T-0113）、每周一重启
        report.check("crontab 有六行带标记的定时任务（含每分钟领取唤醒与终止令）", cron.count(MARKER) == 6, cron)
        report.check("crontab 里有每分钟的用量上报", "/opt/myworkspace/bin/usage-report.mjs" in cron, cron)
        report.check("crontab 里有每分钟的服务器状态上报", "/opt/myworkspace/bin/host-report.mjs" in cron, cron)
        # 照 crontab 那一行以部署账号跑一次（T-0106）：真实 OpenClaw 的 sessions.usage 经接入网关送到工作台，
        # 实例记下第一次上报的时间（第一次只记基准，不计 token）
        command = cron_command(cron, "usage-report.mjs")
        uploaded = run("sh", "-c", command, user=user)
        reported = sql_rows(
            f"SELECT usage_reported_at IS NOT NULL FROM workspace.agent_instances WHERE id = '{instance['id']}'"
        ).strip()
        report.check(
            "照 crontab 那一行运行一次用量上报：成功，工作台记下实例上报过",
            uploaded.returncode == 0 and reported == "t",
            f"{command}；退出码 {uploaded.returncode}；{uploaded.stderr.strip()[-300:]}；{reported}",
        )

        # 服务器状态（T-0113）：容器里读宿主机的 /proc 与磁盘统计，经接入网关送到工作台
        command = cron_command(cron, "host-report.mjs")
        uploaded = run("sh", "-c", command, user=user)
        host_status = owner.request(
            "GET", f"/api/v1/agents/instances/{instance['id']}/host", headers=headers
        ).json()
        current = host_status.get("current") or {}
        report.check(
            "照 crontab 那一行运行一次服务器状态上报：工作台有 CPU、内存、两个分区与开机时长",
            uploaded.returncode == 0 and host_status.get("reported") is True
            and (current.get("cores") or 0) >= 1 and (current.get("mem_total") or 0) > 0
            and (current.get("data_disk") or {}).get("total", 0) > 0
            and (current.get("root_disk") or {}).get("total", 0) > 0
            and (current.get("uptime_seconds") or 0) > 0,
            f"{command}；退出码 {uploaded.returncode}；{uploaded.stderr.strip()[-300:]}；"
            + json.dumps({k: current.get(k) for k in ("cores", "mem_total", "load", "uptime_seconds")}),
        )

    # ---------------------------------------------------------------- 远程运维动作（T-0114）
    ops = Ops(owner, headers, instance["id"])
    report.check("运维动作可用（用 SSH 部署过）", ops.state().get("available") is True)
    status = ops.run({"action": "status"})
    output = status.get("output") or ""
    report.check(
        "查看状态：docker compose ps 有 SERVICE 列与 openclaw、docker stats 有 CPU % 列、df 与 uptime",
        status["status"] == "succeeded" and status["exit_code"] == 0 and "SERVICE" in output
        and "openclaw" in output and "CPU %" in output and "Filesystem" in output
        and "load average" in output,
        output[-1500:],
    )
    token = next(
        (x.split("=", 1)[1] for x in (install_dir / "myworkspace.env").read_text().splitlines()
         if x.startswith("MYWS_INSTANCE_TOKEN=")),
        "",
    )
    logs = ops.run({"action": "logs"})
    output = logs.get("output") or ""
    report.check(
        "查看日志：OpenClaw 最近 200 行；输出里没有实例凭证与未脱敏的 mwa_ 令牌",
        logs["status"] == "succeeded" and len(output) > 100 and bool(token) and token not in output
        and re.search(r"mwa_(?!\*\*\*)[A-Za-z0-9]", output) is None,
        output[-600:],
    )
    tunnel = ops.run({"action": "logs", "service": "tunnel"})
    report.check("查看本机隧道的日志", tunnel["status"] == "succeeded", (tunnel.get("output") or "")[-300:])
    upload = ops.run({"action": "report"})
    report.check(
        "立即上报一次：用量与服务器状态两个脚本都成功",
        upload["status"] == "succeeded" and upload["exit_code"] == 0,
        (upload.get("output") or "")[-600:],
    )
    refused = ops.start({"action": "restart"})
    report.check(
        "重启 OpenClaw 不带密码被拒（STEP_UP_REQUIRED）",
        refused.status == 403 and "STEP_UP_REQUIRED" in refused.summary,
        refused.summary,
    )
    for body, name in (
        ({"action": "restart", "password": PASSWORD}, "重启 OpenClaw"),
        ({"action": "up", "password": PASSWORD}, "重新拉起全部服务"),
        ({"action": "prune", "password": PASSWORD}, "清理无用镜像"),
    ):
        result = ops.run(body)
        report.check(
            f"{name}：输入登录密码后执行成功（退出码 0）",
            result["status"] == "succeeded" and result["exit_code"] == 0,
            (result.get("output") or result.get("error_message") or "")[-600:],
        )
    alive = run("docker", "ps", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format", "{{.Names}}")
    report.check("重启与重新拉起后 OpenClaw 与本机隧道仍在运行", alive.stdout.count("\n") >= 2, alive.stdout.strip())
    audits = sql_rows(
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'agents.ops.finished'"
        f" AND workspace_id = '{workspace}' AND metadata_redacted_json->>'exit_code' = '0'"
    ).strip()
    leaked = sql_rows(
        "SELECT count(*) FROM workspace.audit_logs WHERE action LIKE 'agents.ops.%'"
        f" AND workspace_id = '{workspace}' AND metadata_redacted_json::text LIKE '%Filesystem%'"
    ).strip()
    report.check("审计记下 7 个动作与退出码，不含输出", audits == "7" and leaked == "0", f"{audits} / {leaked}")

    # ---------------------------------------------------------------- 不填密码重新部署
    response = target.post("install", {"version": state["target"]["version"]})
    report.check("不填密码重新部署（202）", response.status == 202, response.summary)
    state = target.wait()
    if not report.check("重新部署成功（只用专用密钥）", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    again = steps(state)
    report.check(
        "重新部署跳过装公钥、Docker 与已经装好的插件",
        again["authorize_key"] == "skipped" and again["docker"] == "skipped" and again["plugin"] == "skipped",
        json.dumps(again, ensure_ascii=False),
    )
    report.check("authorized_keys 里仍然只有一行", len(keys_file.read_text().splitlines()) == 1)
    report.check(
        "重新部署时复盘任务只更新、不重复",
        again["reflection"] == "done" and "已有" in step_detail(state, "reflection") and len(reflection_jobs()) == 1,
        step_detail(state, "reflection"),
    )
    if done["schedule"] == "done":
        report.check("定时任务没有重复", run("crontab", "-l", "-u", user).stdout.count(MARKER) == 6)

    # ---------------------------------------------------------------- 卸载（删除目录）
    response = target.post("uninstall", {"version": state["target"]["version"], "remove_files": True})
    report.check("开始卸载（202）", response.status == 202, response.summary)
    state = target.wait(timeout=10 * 60)
    if not report.check("卸载成功", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    left = run("docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}", "-q").stdout.strip()
    report.check("容器都删除了", left == "", left)
    report.check("接入包目录删除了", not install_dir.exists())
    report.check("定时任务删除了", MARKER not in run("crontab", "-l", "-u", user).stdout)
    report.check("卸载后运维动作不再可用", ops.state().get("reason") == "not_deployed")

    # ---------------------------------------------------------------- 吊销专用密钥、删除目标服务器
    response = target.post("revoke", {"version": state["target"]["version"]})
    report.check("吊销专用密钥（200）", response.status == 200, response.summary)
    revoked = response.json()
    report.check("authorized_keys 里没有专用公钥了", keys_file.read_text(encoding="utf-8").strip() == "")
    report.check("工作台不再保存专用私钥", revoked["target"]["client_key_installed"] is False)
    response = owner.request(
        "DELETE", f"{target.path}?version={revoked['target']['version']}", headers=headers
    )
    report.check("删除目标服务器（204）", response.status == 204, response.summary)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
