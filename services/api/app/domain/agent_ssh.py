"""通过 SSH 部署的公共部分（T-0089）：错误、任务与目标的输出、专用私钥的加密用途。

一台服务器多个实例后（T-0145），OpenClaw 服务器的添加、核对指纹、准备、部署、卸载、吊销
在 app/domain/agent_servers.py；运行服务器（T-0136）在 app/domain/runner_ssh.py，
两边共用这里的函数。

以下是 T-0089 的原始说明（流程不变，只是从“每个实例一台目标服务器”改成“每台服务器一条”）：

设计见 docs/decisions/2026-11-03-openclaw-ssh-provisioning.md
（人类的选择：先显示指纹、确认后才安装；密码只用一次，换成专用密钥）。本模块（B1）只做：

1. 填写目标服务器（主机、端口、账号）：解析地址并检查，读取主机公钥（不登录），记下 SHA256 指纹；
2. owner 核对指纹后确认：之后的连接只接受这把公钥（B2 用 `known_hosts` 固定）；
3. 删除目标服务器；
4. 开始自动安装（B2）：新建一条安装任务，交给进程内的 `SshJobRunner`
   （app/domain/agent_ssh_install.py）。

只有 owner 可以使用（与实例接口一致）；每个实例最多一台目标服务器；每一步写审计。
探测会对外发起连接，按用户限流（每分钟 10 次），避免被用来扫描端口。
"""

import uuid
from typing import TYPE_CHECKING

import asyncssh

from app.core.errors import ApiError
from app.integrations import ssh
from app.models import (
    AgentSshJob,
    RunnerSshJob,
    RunnerSshTarget,
)
from app.schemas.agents import (
    AgentSshJobOut,
    AgentSshJobStep,
    AgentSshTargetOut,
)

if TYPE_CHECKING:
    pass

PROBES_PER_MINUTE = 10


def agent_ssh_context(target_id: uuid.UUID) -> str:
    """专用私钥的加密用途（B2）：绑定到目标服务器这条记录。"""
    return f"agent_ssh:{target_id}"


def _version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current},
    )


def _unreachable(error: ssh.SshError) -> ApiError:
    if error.reason == "not_allowed":
        return ApiError(422, "AGENT_SSH_ADDRESS_NOT_ALLOWED", error.message)
    return ApiError(503, "AGENT_SSH_UNREACHABLE", error.message, details={"reason": error.reason})


def _key_missing() -> ApiError:
    return ApiError(409, "AGENT_SSH_KEY_MISSING", "这台服务器上还没有装工作台的专用公钥")


def _job_running() -> ApiError:
    return ApiError(409, "AGENT_SSH_JOB_RUNNING", "这台服务器正在安装，结束后再操作")


def _client_fingerprint(public_key: str | None) -> str | None:
    if not public_key:
        return None
    try:
        return asyncssh.import_public_key(public_key).get_fingerprint("sha256")
    except (asyncssh.KeyImportError, ValueError):
        return None


def _job_out(job: AgentSshJob | RunnerSshJob) -> AgentSshJobOut:
    return AgentSshJobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        steps=[AgentSshJobStep.model_validate(step) for step in job.steps],
        log=job.log,
        error_code=job.error_code,
        error_message=job.error_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
        updated_at=job.updated_at,
    )


def _out(target: RunnerSshTarget) -> AgentSshTargetOut:
    """运行服务器的 SSH 目标（T-0136）。OpenClaw 服务器的见 app/domain/agent_servers.py。"""
    return AgentSshTargetOut(
        host=target.host,
        address=target.address,
        port=target.port,
        username=target.username,
        host_key_algorithm=target.host_key_algorithm,
        host_key_fingerprint=target.host_key_fingerprint,
        host_key_confirmed=target.host_key_confirmed_at is not None,
        host_key_confirmed_at=target.host_key_confirmed_at,
        client_key_installed=target.client_key_ref is not None,
        client_key_fingerprint=_client_fingerprint(target.client_public_key),
        install_dir=target.install_dir,
        installed_at=target.installed_at,
        version=target.version,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )
