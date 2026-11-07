import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { agentError } from './queries'

// SSH automatic deployment of an OpenClaw instance (T-0089 backend, ADR 2026-11-03-openclaw-ssh-provisioning).
// The key starts with 'agents' so writes and the SSE topic refresh it; while a job runs, poll its progress.
export function sshKey(user: string | undefined, scope: string, instanceId: string) {
  return ['agents', user, scope, 'ssh', instanceId] as const
}

export function useAgentSsh(scope: string, instanceId: () => string, enabled: () => boolean) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return useQuery({
    queryKey: computed(() => sshKey(user, scope, instanceId())),
    enabled: computed(() => active() && auth.isOwner && enabled()),
    queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/agents/instances/{instance_id}/ssh', { params: { header: { 'X-Workspace-Id': scope }, path: { instance_id: instanceId() } }, signal })),
    refetchInterval: query => query.state.data?.job?.status === 'running' ? 2000 : false,
  })
}

export const SSH_STEP: Record<Schema['AgentSshJobStep']['key'], string> = {
  connect: '连接服务器',
  inspect: '读取服务器信息',
  authorize_key: '安装工作台的专用公钥',
  docker: '检查 / 安装 Docker',
  reconnect: '改用专用密钥登录',
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
export const SSH_JOB_KIND: Record<Schema['AgentSshJobOut']['kind'], string> = { install: '部署', uninstall: '卸载' }

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
  if (error.status === 429) return error.retryAfter === null ? '读取主机公钥太频繁（每分钟最多 10 次），请稍后重试。' : `读取主机公钥太频繁（每分钟最多 10 次），请 ${error.retryAfter} 秒后重试。`
  if (error.code === 'AGENT_SSH_UNREACHABLE') {
    const reason = String((error.details as Record<string, unknown> | undefined)?.reason ?? '')
    return UNREACHABLE[reason] ?? '连接不上这台服务器。'
  }
  const messages: Record<string, string> = {
    AGENT_SSH_ADDRESS_NOT_ALLOWED: '不能连接这个地址：回环、链路本地、组播等地址不允许，请填写服务器的 IP 或域名。',
    AGENT_SSH_PUBLIC_ONLY: 'SSH 自动部署只支持外网模式的实例。',
    AGENT_SSH_KEY_INSTALLED: '这台服务器上装着工作台的专用公钥：先吊销专用密钥，再更换或删除服务器。',
    AGENT_SSH_FINGERPRINT_MISMATCH: '指纹与读到的不一致：请重新读取主机公钥后再核对。',
    AGENT_SSH_HOST_KEY_UNCONFIRMED: '先核对并确认服务器的主机公钥指纹。',
    AGENT_SSH_JOB_RUNNING: '这台服务器正在部署或卸载，结束后再操作。',
    AGENT_SSH_KEY_MISSING: '这台服务器上没有工作台的专用公钥（还没部署过，或者已经吊销）。',
    AGENT_SSH_KEY_INVALID: '工作台保存的专用私钥无法解密（加密密钥可能换过）。',
    AGENT_INGRESS_NOT_CONFIGURED: '外网接入入口还没有配置：在工作台服务器上运行 sudo bash scripts/deploy.sh connect，问到“配置接入入口？”时回答 y。',
    AGENT_DISABLED: '实例已停用，启用后才能部署。',
    EXTERNAL_UNAVAILABLE: '暂时不能执行：密钥库未配置加密密钥（CREDENTIAL_ENCRYPTION_KEY），或者部署任务暂时不可用，请稍后重试。',
    VALIDATION_FAILED: '输入不符合要求：检查地址、端口、账号；第一次部署要填写登录密码。',
    WRITE_UNKNOWN: '连接中断，结果未知：已刷新状态，确认后再决定是否重试。',
  }
  return messages[error.code] ?? agentError(error)
}
