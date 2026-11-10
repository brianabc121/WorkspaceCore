#!/usr/bin/env python3
"""智能体服务器与 OpenClaw 部署的验收（T-0089 B3；一台多实例 T-0148；保存的登录密码 T-0183）：
真实 sshd + 真实 Docker + 两个真实 OpenClaw 上线。

前提（CI 的 agents job 在「接入入口自动配置验收」之后）：
- Compose 全栈、OpenIM 在运行；外网接入入口已由 deploy.sh connect 自动配置（内部 CA、AGENT_PUBLIC_ADDRESS）；
- 本机另起一个 sshd（SSH_TARGET_HOST:SSH_TARGET_PORT），账号 SSH_TARGET_USER / SSH_TARGET_PASSWORD：
  密码登录、在 sudo 组（sudo 要密码）、**不在 docker 组**——这样初始化的 Docker 一步真的走 sudo -S；
  SSH_HOST_KEY_PUB 是 sshd 的主机公钥文件；
- 以 root 运行：要读写目标账号的 authorized_keys、读 crontab 与实例目录。

认证方案见 docs/decisions/2026-11-09-agent-server-password-auth.md：OpenClaw 服务器全程用保存的（加密的）登录密码，
不再生成、安装或使用 SSH 客户端密钥。检查：
1. 添加服务器只填 host / port / username / login_password：名称是 host:port，响应 auth_method=password、
   password_set、没有密码；指纹与 sshd 的一致；数据库里只有 enc: 密文；
2. 确认指纹之前：测试连接、初始化 409，一次点击部署被拒且实例数不变；
3. 确认指纹 → 测试连接 connected；authorized_keys 与测试前完全相同；
4. 换成错误密码：测试连接 auth_failed（200），部署被拒且不建实例；改回正确密码后 connected；
5. 初始化（connect / inspect / docker / reconnect / workspace / verify）：sudo 把账号加入 docker 组，
   建好 ~/myworkspace-openclaw 但没有实例子目录，authorized_keys 不变，服务器 ready、可以部署；再初始化一次也成功、不建实例；
6. 一次点击部署甲（3 GiB、1.5 核）：202，同一个 request_id 立即重放得到同一个实例，收据 accepted；
   甲部署中用另一个 request_id 部署乙到同一台 → 409、实例数不变；部署步骤里没有 authorize_key；
7. 部署乙（不限资源）：两个实例各自的目录、Compose 项目、网络命名空间、定时任务、资源上限，**两个真实的 OpenClaw 都连上工作台**；
   上报、远程运维（二次认证用工作台账号密码，填 SSH 密码被拒）；密码不在任务、服务器、部署、审计、幂等记录里；
8. 总览 total=2；服务器列表 summary 与这台服务器的部署记录 2、成功 2，逐行 deployed；
9. 用旧接口重新部署甲（不带密码）：乙的容器不变；
10. 卸载甲保留数据（runtime_installed=false、retained，乙照常），再卸载甲删除目录（乙照常）；
11. 乙还有部署记录时不能删除服务器；卸载乙；
12. 旧公钥退役：authorized_keys 里加一把“工作台旧公钥”和一把“其他用户的公钥”，数据库标记 pending；
    pending 时不能删除服务器；旧的吊销入口 410；legacy-key-cleanup 用密码登录只删旧公钥那一行；然后删除服务器。
    结束时 authorized_keys 恢复成测试前的样子（之后的 SSH 浏览器验收与运行服务器验收用同一个账号）。

只依赖 Python 标准库、docker 与 ssh-keygen；用法：
  sudo -E python3 tests/e2e/agent_ssh_acceptance.py
"""

import contextlib
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from e2e_support import (
    Client,
    Report,
    Response,
    compose_exec,
    default_base_url,
    parse_base_url,
)
from seed import create_user

PASSWORD = "e2e ssh provisioning passphrase"  # 只用于本脚本临时创建的工作台账号
SECRET_VALUE = "sk-ssh-acceptance-" + uuid.uuid4().hex
WRONG_PASSWORD = "wrong-ssh-password-" + uuid.uuid4().hex
# 每个实例一个目录、一个 Compose 项目、一组带自己标记的定时任务（docs/v2/MULTI_OPENCLAW_PLAN.md 第 3 节），
# 按实例短号（实例 ID 去掉连字符的前 12 位）区分；部署根目录由初始化建好
INSTALL_ROOT = "myworkspace-openclaw"
INSTALL_TIMEOUT = 25 * 60
PREPARE_STEPS = ["connect", "inspect", "docker", "reconnect", "workspace", "verify"]
DEPLOY_STEPS = ["connect", "inspect", "upload", "pull", "plugin", "start", "schedule", "online", "reflection"]
LEGACY_REF = "enc:v1:e2e-legacy:placeholder"  # 只占位：新逻辑从不读取旧私钥


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"缺少环境变量 {name}")
    return value


def run(*cmd: str, user: str | None = None) -> subprocess.CompletedProcess[str]:
    command = ["sudo", "-u", user, "-H", *cmd] if user else list(cmd)
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)


def fingerprint(public_key: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".pub", delete=False) as handle:
        handle.write(public_key + "\n")
    try:
        return run("ssh-keygen", "-lf", handle.name).stdout.split()[1]
    finally:
        os.unlink(handle.name)


def key_id(line: str) -> tuple[str, ...]:
    """authorized_keys 一行里的“类型 公钥数据”（不管行首选项与注释）。"""
    fields = line.split()
    for i, field in enumerate(fields[:-1]):
        if field.startswith(("ssh-", "ecdsa-", "sk-")):
            return (field, fields[i + 1])
    return ()


def cron_command(cron: str, script: str) -> str:
    """crontab 里带这个脚本的那一行的命令部分（去掉前 5 个时间字段与重定向）。"""
    line = next((x for x in cron.splitlines() if script in x), "")
    return line.split(None, 5)[5].split(" >/dev/null")[0] if line.count(" ") >= 5 else "false"


def sql_rows(query: str) -> str:
    return compose_exec(
        "postgres", "sh", "-c", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "{query}"'
    )


class AuthorizedKeys:
    """目标账号的 ~/.ssh/authorized_keys：记下测试前的内容，结束时原样恢复。

    之后的 SSH 浏览器验收与运行服务器验收用同一个账号（运行服务器仍用专用密钥），这里不能留下任何改动。
    """

    def __init__(self, user: str) -> None:
        info = pwd.getpwnam(user)
        self.uid, self.gid = info.pw_uid, info.pw_gid
        self.dir = Path(info.pw_dir) / ".ssh"
        self.file = self.dir / "authorized_keys"
        self.dir_existed = self.dir.exists()
        self.baseline = self.read()

    def read(self) -> bytes | None:
        return self.file.read_bytes() if self.file.exists() else None

    def unchanged(self) -> bool:
        return self.read() == self.baseline

    def lines(self) -> list[str]:
        return [x for x in (self.read() or b"").decode().splitlines() if x.strip()]

    def describe(self) -> str:
        data = self.read()
        return "不存在" if data is None else f"{len(self.lines())} 行"

    def _write(self, data: bytes) -> None:
        if not self.dir.exists():
            self.dir.mkdir(mode=0o700)
            os.chown(self.dir, self.uid, self.gid)
        self.file.write_bytes(data)
        os.chmod(self.file, 0o600)
        os.chown(self.file, self.uid, self.gid)

    def append(self, *lines: str) -> None:
        current = self.read() or b""
        if current and not current.endswith(b"\n"):
            current += b"\n"
        self._write(current + "".join(f"{x}\n" for x in lines).encode())

    def restore(self) -> None:
        if self.baseline is not None:
            self._write(self.baseline)
            return
        self.file.unlink(missing_ok=True)
        if not self.dir_existed:
            with contextlib.suppress(OSError):  # 里面还有别的文件就保留
                self.dir.rmdir()


class Ops:
    """远程运维动作（T-0114）：执行一个动作并等它结束。path 是实例级或服务器级的 /ops。"""

    def __init__(self, owner: Client, headers: dict[str, str], path: str) -> None:
        self.owner = owner
        self.headers = headers
        self.path = path

    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = self.owner.request("GET", self.path, headers=self.headers).json()
        return data

    def start(self, body: dict[str, Any]) -> Response:
        return self.owner.request("POST", self.path, body, headers=self.headers)

    def run(self, body: dict[str, Any]) -> dict[str, Any]:
        response = self.start(body)
        if response.status != 202:
            return {"status": f"HTTP {response.status}", "output": response.summary, "exit_code": None}
        action_id = response.json()["id"]
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            data: dict[str, Any] = self.owner.request("GET", f"{self.path}/{action_id}", headers=self.headers).json()
            if data["status"] != "running":
                return data
            time.sleep(2)
        return {"status": "timeout", "output": "", "exit_code": None}


def wait_job(read: Callable[[], dict[str, Any]], timeout: float = INSTALL_TIMEOUT) -> dict[str, Any]:
    """等 SSH 任务（初始化、部署、卸载）结束；read() 返回带 job 的状态。"""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        state = read()
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
    """一台智能体服务器（T-0181）：添加、确认指纹、测试连接、换密码、初始化、退役旧公钥、删除。"""

    def __init__(self, owner: Client, headers: dict[str, str], server_id: str) -> None:
        self.owner = owner
        self.headers = headers
        self.id = server_id
        self.path = f"/api/v1/agents/servers/{server_id}"

    def detail(self) -> dict[str, Any]:
        response = self.owner.request("GET", self.path, headers=self.headers)
        if response.status != 200:
            sys.exit(f"读取服务器失败：{response.summary}")
        data: dict[str, Any] = response.json()
        return data

    def version(self) -> int:
        return int(self.detail()["server"]["version"])

    def post(self, action: str, body: dict[str, Any]) -> Response:
        return self.owner.request("POST", f"{self.path}/{action}", body, headers=self.headers)

    def test(self) -> Response:
        return self.post("test-connection", {"version": self.version()})

    def set_password(self, password: str) -> Response:
        body = {"version": self.version(), "login_password": password}
        return self.owner.request("PUT", f"{self.path}/credentials", body, headers=self.headers)

    def delete(self) -> Response:
        return self.owner.request("DELETE", f"{self.path}?version={self.version()}", headers=self.headers)

    def target_column(self, column: str) -> str:
        return sql_rows(f"SELECT {column} FROM workspace.agent_ssh_targets WHERE server_id = '{self.id}'").strip()


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

    def deploy(self, server_id: str) -> Response:
        """旧接口（重新部署、失败后继续）：只有 server_id 与实例 version，不带密码。"""
        body: dict[str, Any] = {"server_id": server_id, "version": self.version()}
        return self.owner.request("POST", f"{self.path}/deploy", body, headers=self.headers)

    def undeploy(self, remove_files: bool) -> Response:
        body = {"version": self.version(), "remove_files": remove_files}
        return self.owner.request("POST", f"{self.path}/undeploy", body, headers=self.headers)

    def grant(self, secret_id: str, version: int) -> Response:
        body = {"version": version, "grants": [{"secret_id": secret_id, "ref_id": "openai_api_key"}]}
        response = self.owner.request("PUT", f"{self.path}/secret-grants", body, headers=self.headers)
        if response.code == "VERSION_CONFLICT":  # 部署任务刚好改了实例：用最新的 version 再试一次
            body["version"] = self.version()
            response = self.owner.request("PUT", f"{self.path}/secret-grants", body, headers=self.headers)
        return response

    def containers(self, service: str | None = None, *, all_states: bool = False) -> list[str]:
        cmd = ["docker", "ps", *(["-a"] if all_states else []), "--filter", f"label=com.docker.compose.project={self.project}"]
        if service:
            cmd += ["--filter", f"label=com.docker.compose.service={service}"]
        return run(*cmd, "--format", "{{.ID}}").stdout.split()

    def cron_lines(self, user: str) -> list[str]:
        return [x for x in run("crontab", "-l", "-u", user).stdout.splitlines() if x.endswith(self.marker)]


# 新部署必须选主模型（T-0204）：main() 开头建一条中转站连接，所有部署请求都带上它
MODEL_CHOICE: dict[str, str] = {}


def create_deployment(owner: Client, headers: dict[str, str], request_id: str, body: dict[str, Any]) -> Response:
    """一次点击新建并部署（T-0180）：request_id 同时是 Idempotency-Key。"""
    return owner.request(
        "POST",
        "/api/v1/agents/deployments",
        {"request_id": request_id, "model": dict(MODEL_CHOICE), **body},
        headers={**headers, "Idempotency-Key": request_id},
    )


def instance_total(owner: Client, headers: dict[str, str]) -> int:
    """工作空间里的实例总数（总览的 summary，不受分页影响）。"""
    response = owner.request("GET", "/api/v1/agents/overview", headers=headers)
    if response.status != 200:
        sys.exit(f"读取智能体总览失败：{response.summary}")
    return int(response.json()["summary"]["total"])


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


def no_password_in_db(password: str) -> bool:
    """SSH 登录密码（正确的与错误的）不在任务、服务器、部署、运维、审计、幂等记录里（库里只有 enc: 密文）。"""
    dump = sql_rows(
        "SELECT row_to_json(j)::text FROM workspace.agent_ssh_jobs j"
        " UNION ALL SELECT row_to_json(t)::text FROM workspace.agent_ssh_targets t"
        " UNION ALL SELECT row_to_json(s)::text FROM workspace.agent_servers s"
        " UNION ALL SELECT row_to_json(d)::text FROM workspace.agent_deployments d"
        " UNION ALL SELECT row_to_json(o)::text FROM workspace.agent_ops_actions o"
        " UNION ALL SELECT row_to_json(a)::text FROM workspace.audit_logs a"
        " UNION ALL SELECT row_to_json(i)::text FROM workspace.idempotency_requests i"
    )
    return len(dump) > 0 and password not in dump and WRONG_PASSWORD not in dump


def instance_dirs(home: Path) -> list[str]:
    root = home / INSTALL_ROOT
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def finish_deploy(report: Report, agent: Agent, label: str, server_id: str) -> bool:
    """等一次部署结束并检查它的步骤、复盘任务与部署记录；失败时打出日志并返回 False。"""
    state = wait_job(agent.state)
    if not report.check(f"部署{label}成功：真实的 OpenClaw 连上了工作台", state["job"]["status"] == "succeeded"):
        show_job(state)
        logs = run("docker", "compose", "-p", agent.project, "logs", "--no-color", "--tail=120")
        print(logs.stdout[-6000:], logs.stderr[-1000:], file=sys.stderr)
        return False
    done = steps(state)
    report.check(
        f"部署{label}：步骤是环境预检到上线（没有装公钥、装 Docker 与重新登录），每一步完成",
        list(done) == DEPLOY_STEPS
        and all(done[k] == "done" for k in done if k != "schedule") and done["schedule"] in {"done", "skipped"},
        json.dumps(done, ensure_ascii=False),
    )
    report.check(f"部署{label}：第一步用保存的登录密码登录", "保存的登录密码" in step_detail(state, "connect"),
                 step_detail(state, "connect"))
    jobs = reflection_jobs(agent.project)
    report.check(
        f"{label}建好了自己的每晚复盘定时任务（1 个，0 22 * * *，Asia/Shanghai）",
        done["reflection"] == "done" and len(jobs) == 1 and jobs[0]["schedule"].get("expr") == "0 22 * * *"
        and jobs[0]["schedule"].get("tz") == "Asia/Shanghai",
        f"{step_detail(state, 'reflection')}；{len(jobs)} 个",
    )
    deployment = state.get("deployment") or {}
    report.check(
        f"{label}的部署记录：这台服务器、自己的目录、runtime_installed=true",
        deployment.get("server_id") == server_id and deployment.get("install_dir") == str(agent.dir)
        and deployment.get("runtime_installed") is True and bool(deployment.get("installed_at")),
        json.dumps(deployment, ensure_ascii=False),
    )
    return True


def main() -> int:
    if os.geteuid() != 0:
        sys.exit("要以 root 运行：要读写目标账号的 authorized_keys、读 crontab 与实例目录")
    host, port = env("SSH_TARGET_HOST"), int(env("SSH_TARGET_PORT"))
    user, password = env("SSH_TARGET_USER"), env("SSH_TARGET_PASSWORD")
    host_key = Path(env("SSH_HOST_KEY_PUB")).read_text(encoding="utf-8").strip()
    home = Path(pwd.getpwnam(user).pw_dir)
    keys = AuthorizedKeys(user)
    print(f"信息  测试前 authorized_keys：{keys.describe()}", flush=True)
    report = Report()
    try:
        return flow(report, host, port, user, password, host_key, home, keys)
    finally:
        keys.restore()
        print(f"信息  authorized_keys 已恢复成测试前的样子（{keys.describe()}）", flush=True)


def flow(  # 一条完整的验收流程，按页面上的顺序走
    report: Report, host: str, port: int, user: str, password: str, host_key: str, home: Path, keys: AuthorizedKeys
) -> int:
    # ---------------------------------------------------------------- 工作台：owner 与密钥
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
    # 主模型（T-0204）：这个验收不调模型，连接只要配置完整（地址不会被访问）
    connection = owner.request(
        "POST", "/api/v1/model-connections",
        {"name": "SSH 验收中转", "connection_type": "relay", "protocol": "openai_compatible",
         "base_url": "https://relay.e2e.invalid/v1", "api_key": "sk-ssh-e2e-" + uuid.uuid4().hex,
         "models": [{"model_id": "e2e-model"}]},
        headers=headers,
    )
    if connection.status != 201:
        sys.exit(f"建中转站连接失败：{connection.summary}")
    MODEL_CHOICE.update(connection_id=connection.json()["id"], model_id="e2e-model")

    # ---------------------------------------------------------------- 1. 添加服务器：只填四项
    response = owner.request(
        "POST", "/api/v1/agents/servers",
        {"host": host, "port": port, "username": user, "login_password": password},
        headers=headers,
    )
    if response.status != 201:
        sys.exit(f"添加服务器失败：{response.summary}")
    created = response.json()["server"]
    server = Server(owner, headers, created["id"])
    ssh_info = created["ssh"]
    expected_name = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    report.check("添加服务器只填 IP / 端口 / 账号 / 密码：名称是 host:port", created["name"] == expected_name, created["name"])
    report.check(
        "添加服务器：登录方式是密码、已保存（第 1 版）、还没检测，响应正文里没有密码",
        ssh_info["auth_method"] == "password" and ssh_info["password_set"] is True
        and ssh_info["credential_revision"] == 1 and ssh_info["connection_status"] == "unknown"
        and ssh_info["legacy_key_cleanup_state"] == "not_needed" and password not in response.text,
        json.dumps({k: ssh_info.get(k) for k in ("auth_method", "password_set", "credential_revision", "connection_status")}),
    )
    report.check(
        "添加服务器：探测到的主机公钥指纹与 sshd 的一致（不登录）",
        ssh_info["host_key_fingerprint"] == fingerprint(host_key),
        ssh_info["host_key_fingerprint"],
    )
    stored = server.target_column("login_password_ref")
    report.check("数据库里的 login_password_ref 是 enc: 密文、不含明文", stored.startswith("enc:") and password not in stored,
                 stored[:7] + "…")
    report.check(
        "添加后还没初始化、不能部署（第一个原因：指纹没确认）",
        created["initialization_status"] == "not_initialized" and created["deployable"] is False
        and created["deploy_block_reason"] == "host_key_unconfirmed",
        f"{created['initialization_status']} / {created['deploy_block_reason']}",
    )

    # ---------------------------------------------------------------- 2. 确认指纹之前：不能测试、初始化、部署
    early_test = server.post("test-connection", {"version": created["version"]})
    early_prepare = server.post("prepare", {"version": created["version"]})
    early_deploy = create_deployment(
        owner, headers, str(uuid.uuid4()),
        {"server_id": server.id, "server_version": created["version"], "name": "确认前的部署"},
    )
    report.check(
        "指纹确认之前：测试连接、初始化 409（AGENT_SSH_HOST_KEY_UNCONFIRMED），一次点击部署也被拒",
        early_test.status == 409 and early_prepare.status == 409 and early_deploy.status == 409
        and early_test.code == early_prepare.code == early_deploy.code == "AGENT_SSH_HOST_KEY_UNCONFIRMED",
        f"{early_test.summary} / {early_prepare.summary} / {early_deploy.summary}",
    )
    report.check("确认前的部署没有建实例", instance_total(owner, headers) == 0)

    # ---------------------------------------------------------------- 3. 确认指纹、测试连接
    confirmed = server.post("confirm", {"version": created["version"], "fingerprint": ssh_info["host_key_fingerprint"]})
    report.check("确认指纹", confirmed.status == 200, confirmed.summary)
    tested = server.test()
    detail = tested.json() if tested.status == 200 else {}
    status = ((detail.get("server") or {}).get("ssh") or {}).get("connection_status")
    report.check("测试连接：用保存的密码登录成功（200，connected）", tested.status == 200 and status == "connected",
                 f"{tested.summary} {status} {detail.get('warning')}")
    report.check("测试连接：账号能用同一个密码 sudo，没有需要处理的环境问题", tested.status == 200 and not detail.get("warning"),
                 str(detail.get("warning")))
    report.check("测试连接没有改 authorized_keys（与测试前完全相同）", keys.unchanged(), keys.describe())

    # ---------------------------------------------------------------- 4. 错误的密码
    replaced = server.set_password(WRONG_PASSWORD)
    replaced_ssh = ((replaced.json().get("server") or {}).get("ssh") or {}) if replaced.status == 200 else {}
    report.check(
        "换成错误的密码：凭证第 2 版、连接状态回到未检测，响应里没有密码",
        replaced.status == 200 and replaced_ssh.get("credential_revision") == 2
        and replaced_ssh.get("connection_status") == "unknown" and WRONG_PASSWORD not in replaced.text,
        replaced.summary,
    )
    wrong = server.test()
    wrong_server = (wrong.json().get("server") or {}) if wrong.status == 200 else {}
    report.check(
        "错误的密码：测试连接返回 200、连接状态 auth_failed，服务器不能部署（auth_failed）",
        wrong.status == 200 and (wrong_server.get("ssh") or {}).get("connection_status") == "auth_failed"
        and wrong_server.get("deploy_block_reason") == "auth_failed" and WRONG_PASSWORD not in wrong.text,
        f"{wrong.summary} {json.dumps({k: wrong_server.get(k) for k in ('deployable', 'deploy_block_reason')})}",
    )
    refused = create_deployment(
        owner, headers, str(uuid.uuid4()),
        {"server_id": server.id, "server_version": server.version(), "name": "错误密码时的部署"},
    )
    report.check("错误的密码：一次点击部署被拒（409 AGENT_SERVER_CONNECTION_FAILED）且没有建实例",
                 refused.status == 409 and refused.code == "AGENT_SERVER_CONNECTION_FAILED"
                 and instance_total(owner, headers) == 0, refused.summary)
    restored = server.set_password(password)
    report.check("改回正确的密码（凭证第 3 版）", restored.status == 200
                 and restored.json()["server"]["ssh"]["credential_revision"] == 3, restored.summary)
    tested = server.test()
    status = (tested.json()["server"]["ssh"]["connection_status"]) if tested.status == 200 else None
    report.check("改回后测试连接 connected", tested.status == 200 and status == "connected", f"{tested.summary} {status}")

    # ---------------------------------------------------------------- 5. 初始化
    groups_before = run("id", "-nG", user).stdout.split()
    report.check("初始化之前：账号不在 docker 组", "docker" not in groups_before, " ".join(groups_before))
    response = server.post("prepare", {"version": server.version()})
    report.check("开始初始化（202，请求体只有 version）", response.status == 202, response.summary)
    state = wait_job(server.detail, timeout=15 * 60)
    if not report.check("初始化成功", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    done = steps(state)
    report.check(
        "初始化的步骤：connect / inspect / docker / reconnect / workspace / verify；Docker 一步经 sudo 配置，"
        "reconnect 重新用密码登录（或跳过）",
        list(done) == PREPARE_STEPS and done["docker"] == "done" and done["reconnect"] in {"done", "skipped"}
        and all(done[k] == "done" for k in ("connect", "inspect", "workspace", "verify")),
        json.dumps(done, ensure_ascii=False),
    )
    print(f"信息  最终检查：{step_detail(state, 'verify')}", flush=True)
    report.check("sudo 把账号加入了 docker 组", "docker" in run("id", "-nG", user).stdout.split())
    report.check(
        f"初始化建好了 ~/{INSTALL_ROOT}（属于这个账号），里面没有任何实例子目录",
        (home / INSTALL_ROOT).is_dir() and (home / INSTALL_ROOT).stat().st_uid == keys.uid and instance_dirs(home) == [],
        str(instance_dirs(home)),
    )
    report.check("初始化没有改 authorized_keys（与测试前完全相同，没有平台公钥）", keys.unchanged(), keys.describe())
    view = server.detail()["server"]
    report.check(
        "初始化后服务器 ready、可以部署，记下了初始化成功的时间",
        view["initialization_status"] == "ready" and view["deployable"] is True and view["deploy_block_reason"] is None
        and bool(view["ssh"]["prepared_at"]),
        json.dumps({k: view.get(k) for k in ("initialization_status", "deployable", "deploy_block_reason")}),
    )
    response = server.post("prepare", {"version": server.version()})
    state = wait_job(server.detail, timeout=15 * 60) if response.status == 202 else {"job": {"status": response.summary, "steps": []}}
    again = steps(state)
    report.check(
        "再初始化一次也成功：Docker 已可用（跳过安装、不重新登录），不建实例",
        state["job"]["status"] == "succeeded" and again.get("docker") == "skipped" and again.get("reconnect") == "skipped"
        and instance_dirs(home) == [] and instance_total(owner, headers) == 0,
        json.dumps(again, ensure_ascii=False),
    )

    # ---------------------------------------------------------------- 6. 一次点击部署甲（资源上限 3 GiB、1.5 核）
    request_a = str(uuid.uuid4())
    body_a = {"server_id": server.id, "server_version": server.version(), "name": "SSH 部署甲", "mem_limit_mb": 3072, "cpus": 1.5}
    response = create_deployment(owner, headers, request_a, body_a)
    if not report.check("一次点击部署甲（202）", response.status == 202, response.summary):
        return report.finish()
    out = response.json()
    instance_a = out["instance"]
    a = Agent(owner, headers, instance_a, home)
    job = (out.get("deployment") or {}).get("job") or {}
    report.check(
        "部署甲：外网模式、只读、member，资源上限进了实例；任务是部署、步骤里没有 authorize_key",
        instance_a["network_mode"] == "public" and instance_a["scope"] == "read" and instance_a["max_role"] == "member"
        and instance_a["limits"] == {"mem_limit_mb": 3072, "cpus": 1.5} and out["request_id"] == request_a
        and job.get("kind") == "install" and [s["key"] for s in job.get("steps", [])] == DEPLOY_STEPS,
        json.dumps({"limits": instance_a.get("limits"), "steps": [s["key"] for s in job.get("steps", [])]}, ensure_ascii=False),
    )
    replay = create_deployment(owner, headers, request_a, body_a)
    report.check(
        "同一个 request_id、同样的内容立即重放：202，同一个实例（Idempotent-Replayed）",
        replay.status == 202 and replay.json()["instance"]["id"] == a.id
        and replay.headers.get("Idempotent-Replayed") == "true",
        replay.summary,
    )
    receipt = owner.request("GET", f"/api/v1/agents/deployments/requests/{request_a}", headers=headers)
    receipt_data = receipt.json() if receipt.status == 200 else {}
    report.check(
        "部署请求的收据：accepted，实例与任务相同",
        receipt.status == 200 and receipt_data.get("state") == "accepted"
        and (receipt_data.get("instance") or {}).get("id") == a.id
        and (((receipt_data.get("deployment") or {}).get("job")) or {}).get("id") == job.get("id"),
        receipt.summary,
    )
    request_busy = str(uuid.uuid4())
    busy = create_deployment(
        owner, headers, request_busy, {"server_id": server.id, "server_version": server.version(), "name": "SSH 部署乙"}
    )
    report.check(
        "甲部署中用另一个 request_id 部署乙到同一台：409（同机互斥），实例数仍是 1",
        busy.status == 409 and busy.code == "AGENT_SSH_JOB_RUNNING" and instance_total(owner, headers) == 1,
        busy.summary,
    )
    busy_receipt = owner.request("GET", f"/api/v1/agents/deployments/requests/{request_busy}", headers=headers)
    report.check(
        "被拒的部署请求收据：failed、没有实例",
        busy_receipt.status == 200 and busy_receipt.json()["state"] == "failed" and busy_receipt.json()["instance"] is None
        and busy_receipt.json()["error_code"] == "AGENT_SSH_JOB_RUNNING",
        busy_receipt.text[:300],
    )
    granted = a.grant(secret["id"], int(instance_a["version"]))
    report.check("部署开始后给甲授权密钥", granted.status == 200, granted.summary)
    if not finish_deploy(report, a, "甲", server.id):
        return report.finish()

    # ---------------------------------------------------------------- 7. 部署乙（不限资源）
    response = create_deployment(
        owner, headers, str(uuid.uuid4()), {"server_id": server.id, "server_version": server.version(), "name": "SSH 部署乙"}
    )
    if not report.check("一次点击部署乙（新的 request_id，不限资源，202）", response.status == 202, response.summary):
        return report.finish()
    instance_b = response.json()["instance"]
    b = Agent(owner, headers, instance_b, home)
    agents = [a, b]
    report.check("乙不限资源", instance_b["limits"] == {"mem_limit_mb": None, "cpus": None}, json.dumps(instance_b["limits"]))
    granted = b.grant(secret["id"], int(instance_b["version"]))
    report.check("部署开始后给乙授权密钥", granted.status == 200, granted.summary)
    if not finish_deploy(report, b, "乙", server.id):
        return report.finish()
    for agent, label in ((a, "甲"), (b, "乙")):
        seen = owner.request("GET", agent.path, headers=headers).json()
        report.check(
            f"{label}有心跳，所在服务器是这台（以部署为准）",
            bool(seen.get("last_seen_at")) and seen.get("server_id") == server.id and seen.get("server_manual") is True,
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
    report.check("authorized_keys 与测试前完全相同（两次部署都没有装平台公钥）", keys.unchanged(), keys.describe())
    report.check("登录密码（正确的与错误的）不在任务、服务器、部署记录、审计、幂等记录里", no_password_in_db(password))

    # ---------------------------------------------------------------- 8. 总览与服务器统计
    overview = owner.request("GET", "/api/v1/agents/overview", headers=headers).json()
    rows_by_id = {r["instance"]["id"]: r for r in overview["items"]}
    report.check(
        "智能体总览：summary.total=2，两行都归到这台服务器、带各自的 Compose 项目名",
        overview["summary"]["total"] == 2 and set(rows_by_id) == {a.id, b.id}
        and all((rows_by_id[x.id]["server"] or {}).get("id") == server.id
                and rows_by_id[x.id]["deployment_project"] == x.project for x in agents),
        json.dumps(overview["summary"]),
    )
    listed = owner.request("GET", "/api/v1/agents/servers", headers=headers).json()
    summary = listed["summary"]
    item = next((s for s in listed["items"] if s["id"] == server.id), {})
    report.check(
        "服务器列表 summary：1 台、1 台就绪、部署成功 2（不是部分统计）、没有待初始化与待补密码",
        summary["server_count"] == 1 and summary["ready_count"] == 1 and summary["successful_deployments"] == 2
        and summary["deployment_counts_partial"] is False and summary["not_initialized_count"] == 0
        and summary["needs_credentials_count"] == 0,
        json.dumps(summary),
    )
    report.check(
        "这台服务器：部署记录 2、成功 2；乙不限资源，所以上限之和不完整",
        item.get("managed_deployments") == 2 and item.get("successful_deployments") == 2
        and item.get("has_unlimited_limits") is True and item.get("allocated_mem_limit_mb") is None,
        json.dumps({k: item.get(k) for k in ("managed_deployments", "successful_deployments", "has_unlimited_limits")}),
    )
    rows = {r["instance_id"]: r for r in server.detail()["agents"]}
    report.check(
        "服务器详情：两行都是 deployed，部署项目是各自的 Compose 项目名",
        set(rows) == {a.id, b.id} and all(rows[x.id]["deployment_state"] == "deployed"
                                          and rows[x.id]["deployment_project"] == x.project for x in agents),
        json.dumps({k: v["deployment_state"] for k, v in rows.items()}),
    )

    # ---------------------------------------------------------------- 定时任务与上报：两个实例各自的
    scheduled = all(len(x.cron_lines(user)) == 7 for x in agents)
    report.check("crontab 里每个实例各七行带自己标记的定时任务（含用量与服务器状态上报、应用主模型）", scheduled,
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
            [s["id"] for s in listed["items"]] == [server.id] and listed["unassigned"] == []
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
    report.check("甲的运维动作可用（部署到了 SSH 服务器、保存了能用的密码）", ops.state().get("available") is True)
    status_run = ops.run({"action": "status"})
    output = status_run.get("output") or ""
    report.check(
        "查看甲的状态：docker compose ps 只有甲的服务（SERVICE 列与 openclaw）、docker stats 有 CPU % 列、df 与 uptime",
        status_run["status"] == "succeeded" and status_run["exit_code"] == 0 and "SERVICE" in output
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
    refused_ops = ops.start({"action": "restart"})
    report.check("重启甲不带密码被拒（STEP_UP_REQUIRED）",
                 refused_ops.status == 403 and refused_ops.code == "STEP_UP_REQUIRED", refused_ops.summary)
    ssh_step_up = ops.start({"action": "restart", "password": password})
    report.check("二次认证要工作台账号的密码：填服务器的 SSH 密码被拒（STEP_UP_FAILED）",
                 ssh_step_up.status == 403 and ssh_step_up.code == "STEP_UP_FAILED", ssh_step_up.summary)
    b_before = b.containers("openclaw")
    for body, name in (
        ({"action": "restart", "password": PASSWORD}, "重启甲的 OpenClaw"),
        ({"action": "up", "password": PASSWORD}, "重新拉起甲的全部服务"),
    ):
        result = ops.run(body)
        report.check(f"{name}：输入工作台登录密码后执行成功（退出码 0）",
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
    report.check("服务器运维：清理无用镜像（输入工作台登录密码）", prune["status"] == "succeeded" and prune["exit_code"] == 0,
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

    # ---------------------------------------------------------------- 9. 用旧接口重新部署甲：乙不受影响
    response = a.deploy(server.id)
    report.check("用旧接口重新部署甲（只有 server_id 与 version，不带密码，202）", response.status == 202, response.summary)
    state = wait_job(a.state)
    if not report.check("重新部署甲成功（用保存的密码）", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    again = steps(state)
    if not report.check(
        "重新部署跳过已经装好的插件；没有 authorize_key；复盘任务只更新、不重复",
        list(again) == DEPLOY_STEPS and again["plugin"] == "skipped"
        and "已有" in step_detail(state, "reflection") and len(reflection_jobs(a.project)) == 1,
        json.dumps(again, ensure_ascii=False),
    ):
        # 复盘任务没建成时把任务日志与 OpenClaw 的日志打出来，看网关为什么没就绪
        show_job(state)
        logs_out = run("docker", "compose", "-p", a.project, "logs", "--no-color", "--tail=80", "openclaw")
        print(logs_out.stdout[-6000:], logs_out.stderr[-1000:], file=sys.stderr)
    report.check("重新部署甲没有碰到乙的容器", b.containers("openclaw") == b_before)
    if scheduled:
        report.check("定时任务没有重复（各七行）", all(len(x.cron_lines(user)) == 7 for x in agents))

    # ---------------------------------------------------------------- 10. 卸载甲：先保留数据，再删除目录
    response = a.undeploy(remove_files=False)
    report.check("卸载甲、保留数据（202）", response.status == 202, response.summary)
    state = wait_job(a.state, timeout=10 * 60)
    if not report.check("卸载甲（保留数据）成功", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    deployment = state.get("deployment") or {}
    report.check(
        "保留数据：部署记录还在，runtime_installed=false、installed_at 清空",
        deployment.get("runtime_installed") is False and deployment.get("installed_at") is None
        and deployment.get("install_dir") == str(a.dir),
        json.dumps(deployment, ensure_ascii=False),
    )
    report.check("保留数据：甲的容器都删除了、目录还在、定时任务删除了",
                 a.containers(all_states=True) == [] and a.dir.is_dir() and a.cron_lines(user) == [])
    report.check("乙的 OpenClaw 照常运行，定时任务还在", b.containers("openclaw") == b_before
                 and (not scheduled or len(b.cron_lines(user)) == 7))
    view = server.detail()
    rows = {r["instance_id"]: r for r in view["agents"]}
    report.check(
        "服务器详情：甲是 retained、乙仍是 deployed；部署记录 2、成功 1",
        (rows.get(a.id) or {}).get("deployment_state") == "retained"
        and (rows.get(b.id) or {}).get("deployment_state") == "deployed"
        and view["server"]["managed_deployments"] == 2 and view["server"]["successful_deployments"] == 1,
        json.dumps({k: v["deployment_state"] for k, v in rows.items()}),
    )
    response = a.undeploy(remove_files=True)
    report.check("再卸载甲、删除目录（202）", response.status == 202, response.summary)
    state = wait_job(a.state, timeout=10 * 60)
    if not report.check("卸载甲（删除目录）成功", state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    report.check("卸载后甲没有部署记录、改为未归属",
                 state.get("deployment") is None and state.get("server") is None, json.dumps(state, ensure_ascii=False)[:600])
    report.check("甲的容器都删除了", a.containers(all_states=True) == [])
    report.check("甲的目录删除了，乙的目录还在", not a.dir.exists() and b.dir.exists())
    report.check("甲的定时任务删除了，乙的还在", a.cron_lines(user) == [] and (not scheduled or len(b.cron_lines(user)) == 7))
    report.check("乙的 OpenClaw 照常运行", b.containers("openclaw") == b_before)
    report.check("卸载后甲的运维动作不再可用", ops.state().get("reason") == "not_deployed")

    # ---------------------------------------------------------------- 11. 还有部署的实例：不能删除服务器
    blocked = server.delete()
    report.check("乙还部署在上面：不能删除服务器（409 AGENT_SERVER_HAS_DEPLOYMENTS）",
                 blocked.status == 409 and blocked.code == "AGENT_SERVER_HAS_DEPLOYMENTS", blocked.summary)
    response = b.undeploy(remove_files=True)
    state = wait_job(b.state, timeout=10 * 60)
    if not report.check("卸载乙成功", response.status == 202 and state["job"]["status"] == "succeeded"):
        show_job(state)
        return report.finish()
    report.check("实例目录都删除了（上一级 myworkspace-openclaw/ 空了也一起删掉），定时任务都删除了",
                 not (home / INSTALL_ROOT).exists() and all(x.cron_lines(user) == [] for x in agents))
    report.check("全程没有改 authorized_keys（与测试前完全相同）", keys.unchanged(), keys.describe())

    # ---------------------------------------------------------------- 12. 旧公钥退役（T-0179）
    with tempfile.TemporaryDirectory() as scratch:
        legacy, other = (
            new_key(Path(scratch), "legacy", "myworkspace-server-e2e-legacy"),
            new_key(Path(scratch), "other", "someone-else@e2e"),
        )
    keys.append(legacy, other)
    planted = keys.lines()
    sql_rows(
        f"UPDATE workspace.agent_ssh_targets SET client_public_key = '{legacy}', client_key_ref = '{LEGACY_REF}',"
        f" legacy_key_cleanup_state = 'pending' WHERE server_id = '{server.id}' RETURNING id"
    )
    ssh_now = server.detail()["server"]["ssh"]
    report.check(
        "模拟旧方案留下的专用公钥：服务器显示待退役（pending）与旧公钥指纹",
        ssh_now["legacy_key_cleanup_state"] == "pending" and ssh_now["client_key_fingerprint"] == fingerprint(legacy)
        and ssh_now["client_key_installed"] is True,
        json.dumps({k: ssh_now.get(k) for k in ("legacy_key_cleanup_state", "client_key_fingerprint")}),
    )
    pending = server.delete()
    report.check("旧公钥还没退役：不能删除服务器（409 AGENT_SERVER_LEGACY_KEY_PENDING）",
                 pending.status == 409 and pending.code == "AGENT_SERVER_LEGACY_KEY_PENDING", pending.summary)
    retired = server.post("revoke", {"version": server.version(), "force": False})
    report.check("旧的吊销专用密钥入口已退役（410 AGENT_SSH_KEY_AUTH_RETIRED），authorized_keys 没动",
                 retired.status == 410 and retired.code == "AGENT_SSH_KEY_AUTH_RETIRED" and keys.lines() == planted,
                 retired.summary)
    cleaned = server.post("legacy-key-cleanup", {"version": server.version()})
    cleaned_data = cleaned.json() if cleaned.status == 200 else {}
    cleaned_ssh = (cleaned_data.get("server") or {}).get("ssh") or {}
    report.check(
        "旧公钥退役：用保存的密码登录（200），状态 done，工作台不再记着旧公钥",
        cleaned.status == 200 and cleaned_ssh.get("legacy_key_cleanup_state") == "done"
        and cleaned_ssh.get("client_key_installed") is False and cleaned_ssh.get("client_key_fingerprint") is None
        and cleaned_ssh.get("connection_status") == "connected" and not cleaned_data.get("warning"),
        f"{cleaned.summary} {cleaned_data.get('warning')}",
    )
    remaining = {key_id(x) for x in keys.lines()}
    baseline_ids = {key_id(x) for x in (keys.baseline or b"").decode().splitlines() if x.strip()}
    report.check(
        "authorized_keys：只删了工作台旧公钥那一行，其他用户的公钥与原有的行都还在",
        key_id(legacy) not in remaining and key_id(other) in remaining and baseline_ids <= remaining,
        f"{len(remaining)} 行",
    )
    refs = server.target_column("coalesce(client_key_ref, '') || '|' || coalesce(client_public_key, '')")
    report.check("数据库里的旧私钥引用与旧公钥都清空了", refs == "|", refs[:40])
    cleaned_audit = sql_rows(
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'agents.server.legacy_key_cleaned'"
        f" AND resource_id = '{server.id}'"
    ).strip()
    report.check("审计记下旧公钥退役（agents.server.legacy_key_cleaned）", cleaned_audit == "1", cleaned_audit)
    report.check("全程结束：登录密码仍不在任务、服务器、部署、运维、审计、幂等记录里", no_password_in_db(password))

    # ---------------------------------------------------------------- 删除服务器
    response = server.delete()
    report.check("删除服务器（204）", response.status == 204, response.summary)
    gone = owner.request("GET", server.path, headers=headers)
    report.check("删除后服务器不存在（404）", gone.status == 404, gone.summary)
    return report.finish()


def new_key(directory: Path, name: str, comment: str) -> str:
    """生成一把临时 ed25519 公钥（只用来写进 authorized_keys，私钥随临时目录删除）。"""
    path = directory / name
    result = run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path))
    if result.returncode != 0:
        sys.exit(f"ssh-keygen 失败：{result.stderr.strip()}")
    return (directory / f"{name}.pub").read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    sys.exit(main())
