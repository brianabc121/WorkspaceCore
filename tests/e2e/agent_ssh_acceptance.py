#!/usr/bin/env python3
"""OpenClaw 服务器与部署的验收（T-0089 B3；一台服务器多个实例，T-0148）：真实 sshd + 两个真实 OpenClaw 上线。

前提（CI 的 agents job 在「接入入口自动配置验收」之后）：
- Compose 全栈、OpenIM 在运行；外网接入入口已由 deploy.sh connect 自动配置（内部 CA、AGENT_PUBLIC_ADDRESS）；
- 本机另起一个 sshd（SSH_TARGET_HOST:SSH_TARGET_PORT），账号 SSH_TARGET_USER：
  密码登录、在 sudo 组（sudo 要密码）、**不在 docker 组**——这样 Docker 一步真的走 sudo -S；
- 以 root 运行：要读目标账号的 authorized_keys、crontab 与接入包目录。

检查（docs/v2/MULTI_OPENCLAW_PLAN.md 第 9 节）：
1. 添加服务器：主机公钥指纹与 sshd 的一致，确认之前不能准备、不能部署；
2. 准备服务器（一次密码）：sudo 把账号加入 docker 组，装上这台服务器的专用公钥（一台一把，D7），专用密钥重新登录；
   这时还没有任何实例目录；
3. 部署实例 A、实例 B（都不用密码）：各自的目录、Compose 项目、网络命名空间、定时任务，**两个真实的 OpenClaw 都连上工作台**；
   A 的资源上限进了它自己的容器；authorized_keys 里仍然只有一行；
4. 密码不出现在任务、服务器、审计记录里；
5. 照 crontab 那几行分别跑一次两个实例的用量与服务器状态上报：两个实例都归到这台服务器，各自有自己的用量；
6. 远程运维（T-0114、T-0145）：A 的查看状态、日志、立即上报、重启、重新拉起只作用于 A；
   服务器级的查看全部容器能看到两个项目、清理镜像；二次认证、输出脱敏、审计只记退出码；
7. 重新部署 A（只用专用密钥）：B 的容器不受影响；
8. 卸载 A（删除目录）：A 的容器、目录、定时任务都没有了，B 照常运行，定时任务各自独立；
9. 还有部署的实例时不能移除服务器；卸载 B 之后吊销专用密钥、移除服务器。

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
# 每个实例一个目录、一个 Compose 项目、一组带自己标记的定时任务（docs/v2/MULTI_OPENCLAW_PLAN.md 第 3 节），
# 按实例短号（实例 ID 去掉连字符的前 12 位）区分
INSTALL_ROOT = "myworkspace-openclaw"
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
    """远程运维动作（T-0114）：执行一个动作并等它结束。path 是实例级或服务器级的 /ops。"""

    def __init__(self, owner: Client, headers: dict[str, str], path: str) -> None:
        self.owner = owner
        self.headers = headers
        self.path = path

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


def wait_job(read: Any, timeout: float = INSTALL_TIMEOUT) -> dict[str, Any]:
    """等 SSH 任务（准备、部署、卸载）结束；read() 返回带 job 的状态。"""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        state: dict[str, Any] = read()
        job = state.get("job") or {}
        current = next((s["key"] for s in job.get("steps", []) if s["status"] == "running"), "")
        if current != last:
            print(f"  … {job.get('kind')}：{current}", flush=True)
            last = current
        if job and job["status"] != "running":
            return state
        time.sleep(5)
    sys.exit("任务超时")


class Server:
    """一台 SSH 服务器（T-0145）：添加、确认指纹、准备、吊销、移除。"""

    def __init__(self, owner: Client, headers: dict[str, str], server_id: str) -> None:
        self.owner = owner
        self.headers = headers
        self.path = f"/api/v1/agents/servers/{server_id}"

    def detail(self) -> dict[str, Any]:
        response = self.owner.request("GET", self.path, headers=self.headers)
        if response.status != 200:
            sys.exit(f"读取服务器失败：{response.summary}")
        data: dict[str, Any] = response.json()
        return data

    def version(self) -> int:
        return int(self.detail()["server"]["version"])

    def post(self, action: str, body: dict[str, Any]) -> Any:
        return self.owner.request("POST", f"{self.path}/{action}", body, headers=self.headers)


class Agent:
    """一个实例：部署到服务器、卸载（T-0147）。"""

    def __init__(self, owner: Client, headers: dict[str, str], instance: dict[str, Any], home: Path) -> None:
        self.owner = owner
        self.headers = headers
        self.id = instance["id"]
        self.path = f"/api/v1/agents/instances/{self.id}"
        self.short = self.id.replace("-", "")[:12]
        self.project = f"myworkspace-openclaw-{self.short}"
        self.marker = f"# mw-openclaw:{self.short}"
        self.dir = home / INSTALL_ROOT / self.short

    def version(self) -> int:
        return int(self.owner.request("GET", self.path, headers=self.headers).json()["version"])

    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = self.owner.request("GET", f"{self.path}/deployment", headers=self.headers).json()
        return data

    def deploy(self, server_id: str, password: str | None = None) -> Any:
        body: dict[str, Any] = {"server_id": server_id, "version": self.version()}
        if password:
            body["password"] = password
        return self.owner.request("POST", f"{self.path}/deploy", body, headers=self.headers)

    def undeploy(self, remove_files: bool) -> Any:
        body = {"version": self.version(), "remove_files": remove_files}
        return self.owner.request("POST", f"{self.path}/undeploy", body, headers=self.headers)

    def containers(self, service: str | None = None, *, all_states: bool = False) -> list[str]:
        cmd = ["docker", "ps", *(["-a"] if all_states else []), "--filter", f"label=com.docker.compose.project={self.project}"]
        if service:
            cmd += ["--filter", f"label=com.docker.compose.service={service}"]
        return run(*cmd, "--format", "{{.ID}}").stdout.split()

    def cron_lines(self, user: str) -> list[str]:
        return [x for x in run("crontab", "-l", "-u", user).stdout.splitlines() if x.endswith(self.marker)]


def reflection_jobs(project: str) -> list[dict[str, Any]]:
    """这个实例的 OpenClaw 里每晚复盘的定时任务（T-0100；按 declarationKey 找）。"""
    names = run("docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
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




def main() -> int:  # noqa: PLR0915 -- 一条完整的验收流程，按页面上的顺序走
    if os.geteuid() != 0:
        sys.exit("要以 root 运行：要读目标账号的 authorized_keys、crontab 与实例目录")
    host, port = env("SSH_TARGET_HOST"), int(env("SSH_TARGET_PORT"))
    user, password = env("SSH_TARGET_USER"), env("SSH_TARGET_PASSWORD")
    host_key = Path(env("SSH_HOST_KEY_PUB")).read_text(encoding="utf-8").strip()
    home = Path(pwd.getpwnam(user).pw_dir)
    keys_file = home / ".ssh" / "authorized_keys"
    report = Report()

    # ---------------------------------------------------------------- 工作台：两个实例
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
    agents: list[Agent] = []
    for name in ("SSH 部署甲", "SSH 部署乙"):
        instance = owner.request(
            "POST", "/api/v1/agents/instances", {"name": name, "network_mode": "public"}, headers=headers
        ).json()
        owner.request(
            "PUT",
            f"/api/v1/agents/instances/{instance['id']}/secret-grants",
            {"version": instance["version"], "grants": [{"secret_id": secret["id"], "ref_id": "openai_api_key"}]},
            headers=headers,
        )
        agents.append(Agent(owner, headers, instance, home))
    a, b = agents
    # 资源上限（T-0145，D2）：只给甲设，写进甲的 compose.yaml；乙不限制
    limited = owner.request(
        "PUT", f"{a.path}/limits", {"version": a.version(), "mem_limit_mb": 3072, "cpus": 1.5}, headers=headers
    )
    report.check("给甲设资源上限（内存 3072 MiB、CPU 1.5 核）", limited.status == 200, limited.summary)

    # ---------------------------------------------------------------- 添加服务器、核对指纹
    response = owner.request(
        "POST", "/api/v1/agents/servers", {"name": "验收服务器", "host": host, "port": port, "username": user},
        headers=headers,
    )
    if response.status != 201:
        sys.exit(f"添加服务器失败：{response.summary}")
    created = response.json()["server"]
    server = Server(owner, headers, created["id"])
    report.check(
        "添加服务器：探测到的主机公钥指纹与 sshd 的一致（不登录）",
        created["ssh"]["host_key_fingerprint"] == fingerprint(host_key),
        created["ssh"]["host_key_fingerprint"],
    )
    early = server.post("prepare", {"version": created["version"], "password": password})
    early_deploy = a.deploy(created["id"], password)
    report.check(
        "指纹确认之前不能准备、不能部署",
        early.status == 409 and early_deploy.status == 409,
        f"{early.summary} / {early_deploy.summary}",
    )
    confirmed = server.post(
        "confirm", {"version": created["version"], "fingerprint": created["ssh"]["host_key_fingerprint"]}
    )
    report.check("确认指纹", confirmed.status == 200, confirmed.summary)

    # ---------------------------------------------------------------- 准备服务器（一次密码）
    groups_before = run("id", "-nG", user).stdout.split()
    report.check("准备之前：账号不在 docker 组", "docker" not in groups_before, " ".join(groups_before))
    response = server.post("prepare", {"version": server.version(), "password": password})
    report.check("开始准备服务器（202）", response.status == 202, response.summary)
    state = wait_job(server.detail, timeout=15 * 60)
    if not report.check("准备成功", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    done = steps(state)
    report.check(
        "准备只跑前五步（连接、读取信息、装专用公钥、Docker、改用专用密钥）",
        list(done) == ["connect", "inspect", "authorize_key", "docker", "reconnect"]
        and all(v == "done" for v in done.values()),
        json.dumps(done, ensure_ascii=False),
    )
    report.check("sudo 把账号加入了 docker 组", "docker" in run("id", "-nG", user).stdout.split())
    keys = keys_file.read_text(encoding="utf-8").splitlines()
    ssh_info = state["server"]["ssh"]
    report.check(
        "authorized_keys 里是这台服务器的专用公钥（一台一把，指纹一致），记下了准备好的时间",
        len(keys) == 1 and fingerprint(keys[0]) == ssh_info["client_key_fingerprint"] and bool(ssh_info["prepared_at"]),
    )
    report.check("准备不建任何实例目录", not (home / INSTALL_ROOT).exists())

    # ---------------------------------------------------------------- 部署甲、乙（都不用密码）
    for agent, label in ((a, "甲"), (b, "乙")):
        response = agent.deploy(created["id"])
        report.check(f"部署{label}：不用密码（202）", response.status == 202, response.summary)
        state = wait_job(agent.state)
        if not report.check(f"部署{label}成功：真实的 OpenClaw 连上了工作台", state["job"]["status"] == "succeeded"):
            show_job(state)
            logs = run("docker", "compose", "-p", agent.project, "logs", "--no-color", "--tail=120")
            print(logs.stdout[-6000:], logs.stderr[-1000:], file=sys.stderr)
            return report.finish()
        done = steps(state)
        report.check(
            f"部署{label}：只用专用密钥（跳过装公钥与 Docker），其余每一步完成",
            done["authorize_key"] == "skipped" and done["docker"] == "skipped"
            and all(done[k] == "done" for k in done if k not in {"authorize_key", "docker", "schedule"})
            and done["schedule"] in {"done", "skipped"},
            json.dumps(done, ensure_ascii=False),
        )
        jobs = reflection_jobs(agent.project)
        report.check(
            f"{label}建好了自己的每晚复盘定时任务（1 个，0 22 * * *，Asia/Shanghai）",
            done["reflection"] == "done" and len(jobs) == 1 and jobs[0]["schedule"].get("expr") == "0 22 * * *"
            and jobs[0]["schedule"].get("tz") == "Asia/Shanghai",
            f"{step_detail(state, 'reflection')}；{len(jobs)} 个",
        )
        deployment = state.get("deployment") or {}
        report.check(
            f"{label}的部署记录：这台服务器、自己的目录",
            deployment.get("server_id") == created["id"] and deployment.get("install_dir") == str(agent.dir),
            json.dumps(deployment, ensure_ascii=False),
        )
    for agent, label in ((a, "甲"), (b, "乙")):
        seen = owner.request("GET", agent.path, headers=headers).json()
        report.check(
            f"{label}有心跳，所在服务器是这台（以部署为准）",
            bool(seen.get("last_seen_at")) and seen.get("server_id") == created["id"] and seen.get("server_manual") is True,
            json.dumps({k: seen.get(k) for k in ("last_seen_at", "server_id", "server_manual")}),
        )
        report.check(f"{label}的 net、OpenClaw 与本机隧道在运行（Compose 项目带它的短号）", len(agent.containers()) >= 3)
        report.check(f"{label}的目录只给账号自己（700），state/ 交给了 uid 1000",
                     (agent.dir.stat().st_mode & 0o777) == 0o700 and (agent.dir / "state").stat().st_uid == 1000)
    # 核验记录第 3 节：这台机器的 cgroup 版本（cgroup2fs 是 v2 统一层级，tmpfs 是 v1），只记录不判断
    print(f"信息  /sys/fs/cgroup 的文件系统：{run('stat', '-fc', '%T', '/sys/fs/cgroup').stdout.strip()}", flush=True)
    # 每个实例自己的网络命名空间（D1 A）：各自的 net 在各自项目的网络上，OpenClaw 与隧道加入自己的 net
    layout: dict[str, dict[str, list[str]]] = {}
    for agent in agents:
        layout[agent.short] = {}
        for service in ("net", "openclaw", "tunnel"):
            ids = agent.containers(service)
            layout[agent.short][service] = run(
                "docker", "inspect", "-f",
                "{{.HostConfig.NetworkMode}} {{range $k, $v := .NetworkSettings.Networks}}{{$k}},{{end}}"
                " {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}", *ids[:1],
            ).stdout.split()
    nets = {agent.short: run("docker", "ps", "-q", "--no-trunc", "--filter", f"label=com.docker.compose.project={agent.project}",
                             "--filter", "label=com.docker.compose.service=net").stdout.strip() for agent in agents}
    report.check(
        "两个实例各有自己的网络命名空间：net 在各自项目的网络上（不是宿主网络），OpenClaw 与隧道加入自己的 net",
        all(
            len(layout[x.short]["net"]) >= 2 and f"{x.project}_default," in layout[x.short]["net"][1]
            and layout[x.short]["net"][0] != "host"
            and all(layout[x.short][s][:1] == [f"container:{nets[x.short]}"] for s in ("openclaw", "tunnel"))
            for x in agents
        ) and nets[a.short] != nets[b.short],
        json.dumps(layout),
    )
    report.check(
        "甲的 OpenClaw 容器带上了资源上限（3 GiB、1.5 核），乙不限制",
        layout[a.short]["openclaw"][-2:] == [str(3072 << 20), "1500000000"]
        and layout[b.short]["openclaw"][-2:] == ["0", "0"],
        json.dumps({x.short: layout[x.short]["openclaw"] for x in agents}),
    )
    report.check("authorized_keys 里仍然只有一行（两个实例共用这台服务器的专用密钥）",
                 len(keys_file.read_text(encoding="utf-8").splitlines()) == 1)
    dump = sql_rows(
        "SELECT row_to_json(j)::text FROM workspace.agent_ssh_jobs j"
        " UNION ALL SELECT row_to_json(t)::text FROM workspace.agent_ssh_targets t"
        " UNION ALL SELECT row_to_json(s)::text FROM workspace.agent_servers s"
        " UNION ALL SELECT row_to_json(d)::text FROM workspace.agent_deployments d"
        " UNION ALL SELECT row_to_json(a)::text FROM workspace.audit_logs a"
    )
    report.check("登录密码不在任务、服务器、部署记录、审计记录里", password not in dump and len(dump) > 0)

    # ---------------------------------------------------------------- 定时任务与上报：两个实例各自的
    scheduled = all(len(x.cron_lines(user)) == 6 for x in agents)
    report.check("crontab 里每个实例各六行带自己标记的定时任务（含用量与服务器状态上报）", scheduled,
                 run("crontab", "-l", "-u", user).stdout)
    if scheduled:
        for agent, label in ((a, "甲"), (b, "乙")):
            cron = "\n".join(agent.cron_lines(user))
            # 照 crontab 那一行以部署账号跑一次（T-0106 / T-0113）：真实 OpenClaw 的用量、宿主机与自己容器的 cgroup
            usage = run("sh", "-c", cron_command(cron, "usage-report.mjs"), user=user)
            hostr = run("sh", "-c", cron_command(cron, "host-report.mjs"), user=user)
            report.check(
                f"照 crontab 那几行运行一次{label}的用量与服务器状态上报",
                usage.returncode == 0 and hostr.returncode == 0,
                f"{usage.stderr.strip()[-300:]}；{hostr.stderr.strip()[-300:]}",
            )
        listed = owner.request("GET", "/api/v1/agents/servers", headers=headers).json()
        report.check(
            "两个实例都归到这台服务器：服务器列表只有它一台，没有未归属的实例",
            [s["id"] for s in listed["items"]] == [created["id"]] and listed["unassigned"] == []
            and listed["items"][0]["instances_total"] == 2,
            json.dumps(listed, ensure_ascii=False)[:1500],
        )
        view = server.detail()
        rows = {r["instance_id"]: r for r in view["agents"]}
        current = view.get("current") or {}
        report.check(
            "服务器详情：整机有 CPU 核数、内存、两个分区与开机时长",
            (current.get("cores") or 0) >= 1 and (current.get("mem_total") or 0) > 0
            and (current.get("data_disk") or {}).get("total", 0) > 0 and (current.get("uptime_seconds") or 0) > 0,
            json.dumps({k: current.get(k) for k in ("cores", "mem_total", "uptime_seconds")}),
        )
        loads = {x.short: (rows.get(x.id) or {}).get("load") or {} for x in agents}
        # 每个智能体自己容器的 cgroup（T-0146）：CI 的 Ubuntu 是 cgroup v2，核验记录第 3 节
        report.check(
            "服务器详情里两个智能体都是部署的，各有自己的用量（读得到 cgroup，内存、进程数有值）；甲的内存上限是 3 GiB",
            set(rows) == {a.id, b.id} and all(r["deployed"] for r in rows.values())
            and all(x.get("available") is True and (x.get("mem_bytes") or 0) > 0 and (x.get("pids") or 0) > 0 for x in loads.values())
            and loads[a.short].get("mem_limit") == 3072 << 20,
            json.dumps(loads, ensure_ascii=False),
        )
        report.check(
            "两个智能体的内存用量不同（各读各的容器，不是整机数）",
            loads[a.short].get("mem_bytes") != loads[b.short].get("mem_bytes")
            or loads[a.short].get("pids") != loads[b.short].get("pids"),
            json.dumps(loads, ensure_ascii=False),
        )

    # ---------------------------------------------------------------- 远程运维：甲自己的与整台服务器的
    ops = Ops(owner, headers, f"{a.path}/ops")
    server_ops = Ops(owner, headers, f"{server.path}/ops")
    report.check("甲的运维动作可用（部署到了 SSH 服务器）", ops.state().get("available") is True)
    status = ops.run({"action": "status"})
    output = status.get("output") or ""
    report.check(
        "查看甲的状态：docker compose ps 只有甲的服务（SERVICE 列与 openclaw）、docker stats 有 CPU % 列、df 与 uptime",
        status["status"] == "succeeded" and status["exit_code"] == 0 and "SERVICE" in output
        and "openclaw" in output and "CPU %" in output and "Filesystem" in output
        and "load average" in output and b.project not in output,
        output[-1500:],
    )
    token = next(
        (x.split("=", 1)[1] for x in (a.dir / "myworkspace.env").read_text().splitlines()
         if x.startswith("MYWS_INSTANCE_TOKEN=")),
        "",
    )
    logs = ops.run({"action": "logs"})
    output = logs.get("output") or ""
    report.check(
        "查看甲的日志：OpenClaw 最近 200 行；输出里没有实例凭证与未脱敏的 mwa_ 令牌",
        logs["status"] == "succeeded" and len(output) > 100 and bool(token) and token not in output
        and re.search(r"mwa_(?!\*\*\*)[A-Za-z0-9]", output) is None,
        output[-600:],
    )
    tunnel = ops.run({"action": "logs", "service": "tunnel"})
    report.check("查看甲的本机隧道日志", tunnel["status"] == "succeeded", (tunnel.get("output") or "")[-300:])
    upload = ops.run({"action": "report"})
    report.check("立即上报一次甲：两个脚本都成功", upload["status"] == "succeeded" and upload["exit_code"] == 0,
                 (upload.get("output") or "")[-600:])
    refused = ops.start({"action": "restart"})
    report.check("重启甲不带密码被拒（STEP_UP_REQUIRED）",
                 refused.status == 403 and "STEP_UP_REQUIRED" in refused.summary, refused.summary)
    b_before = b.containers("openclaw")
    for body, name in (
        ({"action": "restart", "password": PASSWORD}, "重启甲的 OpenClaw"),
        ({"action": "up", "password": PASSWORD}, "重新拉起甲的全部服务"),
    ):
        result = ops.run(body)
        report.check(f"{name}：输入登录密码后执行成功（退出码 0）",
                     result["status"] == "succeeded" and result["exit_code"] == 0,
                     (result.get("output") or result.get("error_message") or "")[-600:])
    report.check("实例级的清理镜像不再提供（在服务器上做）", ops.start({"action": "prune", "password": PASSWORD}).status == 422)
    containers = server_ops.run({"action": "containers"})
    output = containers.get("output") or ""
    report.check(
        "服务器运维：查看全部容器能看到两个实例的 Compose 项目",
        containers["status"] == "succeeded" and a.project in output and b.project in output,
        output[-1500:],
    )
    prune = server_ops.run({"action": "prune", "password": PASSWORD})
    report.check("服务器运维：清理无用镜像（输入登录密码）", prune["status"] == "succeeded" and prune["exit_code"] == 0,
                 (prune.get("output") or prune.get("error_message") or "")[-600:])
    report.check("甲的重启与重新拉起没有碰到乙的容器", b.containers("openclaw") == b_before and len(b_before) == 1)
    audits = sql_rows(
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'agents.ops.finished'"
        f" AND workspace_id = '{workspace}' AND metadata_redacted_json->>'exit_code' = '0'"
    ).strip()
    leaked = sql_rows(
        "SELECT count(*) FROM workspace.audit_logs WHERE action LIKE 'agents.ops.%'"
        f" AND workspace_id = '{workspace}' AND metadata_redacted_json::text LIKE '%Filesystem%'"
    ).strip()
    report.check("审计记下 8 个动作与退出码，不含输出", audits == "8" and leaked == "0", f"{audits} / {leaked}")

    # ---------------------------------------------------------------- 重新部署甲：乙不受影响
    response = a.deploy(created["id"])
    report.check("不填密码重新部署甲（202）", response.status == 202, response.summary)
    state = wait_job(a.state)
    if not report.check("重新部署甲成功（只用专用密钥）", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    again = steps(state)
    report.check(
        "重新部署跳过装公钥、Docker 与已经装好的插件；复盘任务只更新、不重复",
        again["authorize_key"] == "skipped" and again["docker"] == "skipped" and again["plugin"] == "skipped"
        and "已有" in step_detail(state, "reflection") and len(reflection_jobs(a.project)) == 1,
        json.dumps(again, ensure_ascii=False),
    )
    report.check("重新部署甲没有碰到乙的容器", b.containers("openclaw") == b_before)
    if scheduled:
        report.check("定时任务没有重复（各六行）", all(len(x.cron_lines(user)) == 6 for x in agents))

    # ---------------------------------------------------------------- 卸载甲（删除目录）：乙照常运行
    response = a.undeploy(remove_files=True)
    report.check("开始卸载甲（202）", response.status == 202, response.summary)
    state = wait_job(a.state, timeout=10 * 60)
    if not report.check("卸载甲成功", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    report.check("卸载后甲没有部署记录、改为未归属",
                 state.get("deployment") is None and state.get("server") is None, json.dumps(state, ensure_ascii=False)[:600])
    report.check("甲的容器都删除了", a.containers(all_states=True) == [])
    report.check("甲的目录删除了，乙的目录还在", not a.dir.exists() and b.dir.exists())
    report.check("甲的定时任务删除了，乙的还在", a.cron_lines(user) == [] and (not scheduled or len(b.cron_lines(user)) == 6))
    report.check("乙的 OpenClaw 照常运行", b.containers("openclaw") == b_before)
    report.check("卸载后甲的运维动作不再可用", ops.state().get("reason") == "not_deployed")

    # ---------------------------------------------------------------- 还有部署的实例：不能移除服务器
    blocked = owner.request("DELETE", f"{server.path}?version={server.version()}", headers=headers)
    report.check("乙还部署在上面：不能移除服务器（409）", blocked.status == 409, blocked.summary)
    response = b.undeploy(remove_files=True)
    state = wait_job(b.state, timeout=10 * 60)
    if not report.check("卸载乙成功", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    report.check("实例目录都删除了（上一级 myworkspace-openclaw/ 空了也一起删掉），定时任务都删除了",
                 not (home / INSTALL_ROOT).exists() and all(x.cron_lines(user) == [] for x in agents))

    # ---------------------------------------------------------------- 吊销专用密钥、移除服务器
    response = server.post("revoke", {"version": server.version(), "force": False})
    report.check("吊销专用密钥（200）", response.status == 200, response.summary)
    report.check("authorized_keys 里没有专用公钥了", keys_file.read_text(encoding="utf-8").strip() == "")
    report.check("工作台不再保存专用私钥", response.json()["server"]["ssh"]["client_key_installed"] is False)
    response = owner.request("DELETE", f"{server.path}?version={server.version()}", headers=headers)
    report.check("移除服务器（204）", response.status == 204, response.summary)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
