import { ApiError, type Schema } from '../../services/api'
import { agentError } from './queries'

// SSH 部署的步骤、状态与错误文字（T-0089；一台服务器多个实例后由 servers.ts 使用，T-0147；密码登录 T-0181）。
// authorize_key 只会出现在旧方案留下的任务记录里；新的初始化不再安装任何公钥。
export const SSH_STEP: Record<Schema['AgentSshJobStep']['key'], string> = {
  connect: '用保存的密码登录服务器',
  inspect: '检测系统与权限',
  authorize_key: '安装专用公钥（旧方案的记录）',
  docker: '检测 / 安装 Docker 与 Compose',
  reconnect: '重新登录，确认 Docker 权限生效',
  workspace: '准备部署目录',
  verify: '验证 Docker、目录与平台连通',
  upload: '生成并上传接入包',
  pull: '拉取镜像',
  plugin: '安装 IM 插件',
  start: '启动 OpenClaw',
  schedule: '写入定时任务',
  reflection: '建每晚复盘任务',
  online: '等待实例连上工作台',
  stop: '停止并删除容器',
  unschedule: '删除定时任务',
  remove_files: '删除接入包目录',
  install: '运行安装脚本（Docker、gVisor、runner-agent）',
}
export const SSH_STEP_STATUS: Record<Schema['AgentSshJobStep']['status'], string> = {
  pending: '等待', running: '进行中', done: '完成', skipped: '跳过', failed: '失败',
}
export const SSH_JOB_STATUS: Record<Schema['AgentSshJobOut']['status'], string> = {
  running: '进行中', succeeded: '成功', failed: '失败', interrupted: '已中断',
}
export const SSH_JOB_KIND: Record<Schema['AgentSshJobOut']['kind'], string> = { prepare: '初始化服务器', install: '部署', uninstall: '卸载' }

// The command that prints the same SHA256 fingerprint on the server itself (ssh-keygen -lf).
export function hostKeyCommand(algorithm: string): string {
  const type = algorithm === 'ssh-ed25519' ? 'ed25519'
    : algorithm.startsWith('ecdsa-') ? 'ecdsa'
      : /^(ssh-rsa|rsa-sha2-)/.test(algorithm) ? 'rsa' : null
  return type ? `ssh-keygen -lf /etc/ssh/ssh_host_${type}_key.pub` : 'for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done'
}

export const HOST_PATTERN = /^[A-Za-z0-9.:-]{1,253}$/
export const USERNAME_PATTERN = /^[A-Za-z_][A-Za-z0-9._-]{0,31}$/

const UNREACHABLE: Record<string, string> = {
  dns: '解析不了这个域名：检查地址是否正确。',
  timeout: '连接超时：检查 IP 与端口，以及服务器的防火墙、云安全组是否允许工作台服务器访问这个端口。',
  refused: '连接被拒绝：这个端口上没有 SSH 服务。',
  handshake: 'SSH 握手失败：服务器的主机公钥可能变了（重装过系统，或者连到了别的机器），或者不支持工作台使用的算法。',
  unreachable: '连接不上这台服务器：检查网络。',
}

export function sshError(error: unknown): string {
  if (!(error instanceof ApiError)) return agentError(error)
  if (error.status === 429) return error.retryAfter === null ? '连接服务器太频繁（读取主机公钥与测试连接每分钟最多 10 次），请稍后重试。' : `连接服务器太频繁（读取主机公钥与测试连接每分钟最多 10 次），请 ${error.retryAfter} 秒后重试。`
  if (error.code === 'AGENT_SSH_UNREACHABLE') {
    const reason = String((error.details as Record<string, unknown> | undefined)?.reason ?? '')
    return UNREACHABLE[reason] ?? '连接不上这台服务器。'
  }
  const messages: Record<string, string> = {
    AGENT_SSH_ADDRESS_NOT_ALLOWED: '不能连接这个地址：回环、链路本地、组播等地址不允许，请填写服务器的 IP 或域名。',
    AGENT_SSH_PUBLIC_ONLY: '部署到服务器只支持外网模式的实例；内网模式请生成接入包手动部署。',
    AGENT_SSH_KEY_INSTALLED: '这台服务器上还留有旧方案的专用公钥：先在服务器详情里完成“旧公钥退役”，再更换地址或移除服务器。',
    AGENT_SSH_FINGERPRINT_MISMATCH: '指纹与读到的不一致：请重新读取主机公钥后再核对。',
    AGENT_SSH_HOST_KEY_UNCONFIRMED: '先核对并确认服务器的主机公钥指纹。',
    AGENT_SSH_JOB_RUNNING: '这台服务器正在初始化、部署、卸载或运维，结束后再操作。',
    AGENT_SSH_KEY_MISSING: '这台服务器还不能登录：先补充登录密码并完成初始化。',
    AGENT_SSH_KEY_INVALID: '工作台保存的旧专用私钥无法解密（加密密钥可能换过）。',
    AGENT_SSH_KEY_AUTH_RETIRED: '专用密钥登录已经停用：请补充服务器登录密码，再用“旧公钥退役”清理服务器上的旧公钥。',
    AGENT_SERVER_CREDENTIAL_REQUIRED: '这台服务器还没有保存登录密码：先补充登录密码。',
    AGENT_SERVER_CREDENTIAL_UNREADABLE: '保存的登录密码无法解密（工作台的加密密钥可能换过）：请重新填写登录密码。',
    AGENT_SERVER_CREDENTIAL_CHANGED: '登录密码刚被修改过，这次操作已停止：刷新后重新开始。',
    AGENT_SERVER_NOT_INITIALIZED: '这台服务器还没初始化完成：先在智能体服务器页初始化。',
    AGENT_OPS_RUNNING: '这台服务器正在执行运维动作，结束后再操作。',
    AGENT_SERVER_EXISTS: '这台服务器（同一个地址、端口和账号）已经添加过了。',
    AGENT_SERVER_HAS_DEPLOYMENTS: '这台服务器上还有经平台部署的智能体：先卸载它们，再更换地址或移除服务器（不会把原来的智能体挂到新机器上）。',
    AGENT_SERVER_NOT_SSH: '这台服务器是按上报自动识别的，不能经工作台部署：先用 IP、账号和密码添加一台服务器。',
    AGENT_DEPLOYED_ELSEWHERE: '这个实例已经部署在另一台服务器上：先在那里卸载，再部署到这台。',
    AGENT_DEPLOYED: '这个实例是经 SSH 部署的，所在服务器以部署为准；要换服务器先卸载。',
    AGENT_INGRESS_NOT_CONFIGURED: '外网接入入口还没有配置：在工作台服务器上运行 sudo bash scripts/deploy.sh connect，问到“配置接入入口？”时回答 y。',
    AGENT_DISABLED: '实例已停用，启用后才能部署。',
    EXTERNAL_UNAVAILABLE: '暂时不能执行：密钥库未配置加密密钥（CREDENTIAL_ENCRYPTION_KEY），或者部署任务暂时不可用，请稍后重试。',
    VALIDATION_FAILED: '输入不符合要求：检查名称、地址、端口、账号与登录密码。',
    IDEMPOTENCY_KEY_REUSED: '这次提交与之前的请求内容不一致：刷新后重新提交。',
    IDEMPOTENCY_IN_PROGRESS: '上一次提交还在处理中：稍后刷新查看结果。',
    WRITE_UNKNOWN: '连接中断，结果未知：已刷新状态，确认后再决定是否重试。',
  }
  return messages[error.code] ?? agentError(error)
}
