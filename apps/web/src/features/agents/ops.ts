import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { agentError } from './queries'

// 远程运维动作（T-0114，AGENT_OPS_PLAN 6.1）：固定的几个动作，经工作台的 SSH 通道执行。
// 键以 'agents' 开头，写操作和 SSE 的 agents 主题会一起刷新；有动作在跑时轮询进度。
export type OpsAction = Schema['AgentOpsActionOut']['action']
export type OpsService = NonNullable<Schema['AgentOpsRun']['service']>
// 实例级动作只作用于这个实例的目录与 Compose 项目；服务器级动作作用于整台服务器（T-0145）
export type OpsKind = 'instance' | 'server'

export function opsKey(user: string | undefined, scope: string, kind: OpsKind, id: string) {
  return ['agents', user, scope, 'ops', kind, id] as const
}

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, active }
}

export function useAgentOps(scope: string, kind: OpsKind, id: () => string) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => opsKey(c.user, scope, kind, id())),
    enabled: computed(() => c.active() && c.auth.isOwner),
    queryFn: async ({ signal }) => kind === 'server'
      ? dataOf(await c.auth.api.GET('/api/v1/agents/servers/{server_id}/ops', { params: { header: c.header, path: { server_id: id() } }, signal }))
      : dataOf(await c.auth.api.GET('/api/v1/agents/instances/{instance_id}/ops', { params: { header: c.header, path: { instance_id: id() } }, signal })),
    refetchInterval: query => query.state.data?.running ? 2000 : false,
  })
}

export function useOpsAction(scope: string, kind: OpsKind, id: () => string, actionId: () => string | null) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => [...opsKey(c.user, scope, kind, id()), actionId()]),
    enabled: computed(() => c.active() && c.auth.isOwner && !!actionId()),
    queryFn: async ({ signal }) => kind === 'server'
      ? dataOf(await c.auth.api.GET('/api/v1/agents/servers/{server_id}/ops/{action_id}', { params: { header: c.header, path: { server_id: id(), action_id: actionId()! } }, signal }))
      : dataOf(await c.auth.api.GET('/api/v1/agents/instances/{instance_id}/ops/{action_id}', { params: { header: c.header, path: { instance_id: id(), action_id: actionId()! } }, signal })),
    refetchInterval: query => query.state.data?.status === 'running' ? 1500 : false,
  })
}

export const OPS_ACTION: Record<OpsAction, string> = {
  status: '查看状态',
  logs: '查看日志',
  report: '立即上报一次',
  restart: '重启 OpenClaw',
  up: '重新拉起全部服务',
  prune: '清理无用镜像',
  containers: '查看全部容器',
}
export const OPS_HELP: Record<OpsAction, string> = {
  status: '这个实例的容器状态、CPU / 内存、磁盘、开机时长与负载',
  logs: '最近 200 行；日志可能含对话片段，查看会写审计',
  report: '在容器里运行用量与服务器状态两个上报脚本',
  restart: 'docker compose restart openclaw',
  up: 'docker compose up -d，最多等 3 分钟',
  prune: '只删没在用的镜像（整台服务器），回收磁盘',
  containers: '这台服务器上所有工作台项目的容器与资源占用（docker compose ls、docker stats）',
}
export const OPS_DIRECT: Record<OpsKind, OpsAction[]> = { instance: ['status', 'logs', 'report'], server: ['containers'] }
export const OPS_STEP_UP: Record<OpsKind, OpsAction[]> = { instance: ['restart', 'up'], server: ['prune'] }
export const OPS_STATUS: Record<Schema['AgentOpsActionOut']['status'], string> = {
  running: '进行中', succeeded: '成功', failed: '失败', interrupted: '已中断',
}
export const OPS_SERVICE: Record<OpsService, string> = { openclaw: 'OpenClaw', tunnel: '本机隧道', 'image-proxy': '取图代理' }

const UNAVAILABLE: Record<NonNullable<Schema['AgentOpsState']['reason']>, string> = {
  not_deployed: '只适用于部署到 SSH 服务器的实例。手动接入的，先在上面“部署到服务器”（会原样重装接入包，会话和记忆在 state/ 里保留）。',
  credential_missing: '这台服务器还没有保存登录密码：到“服务器”页补充登录密码后才能执行运维动作。',
  credential_unreadable: '这台服务器保存的登录密码无法解密：到“服务器”页重新填写登录密码。',
  host_key_unconfirmed: '这台服务器的主机公钥还没有确认：到“服务器”页核对指纹。',
  key_missing: '这台服务器的专用密钥已吊销：到“服务器”页重新准备这台服务器后才能执行运维动作。',
}
export const opsUnavailable = (reason: Schema['AgentOpsState']['reason']) => reason ? UNAVAILABLE[reason] : ''

export function opsError(error: unknown): string {
  if (!(error instanceof ApiError)) return agentError(error)
  if (error.status === 429) return error.retryAfter === null ? '密码错误次数过多，请稍后再试。' : `密码错误次数过多，请 ${error.retryAfter} 秒后再试。`
  const messages: Record<string, string> = {
    STEP_UP_REQUIRED: '这个动作要再输一次你的登录密码。',
    STEP_UP_FAILED: '密码错误。',
    AGENT_OPS_RUNNING: '这台服务器正在执行另一个运维动作（同一台服务器同时只跑一个），结束后再操作。',
    AGENT_SSH_JOB_RUNNING: '这台服务器正在准备、部署或卸载，结束后再操作。',
    AGENT_OPS_UNAVAILABLE: '只适用于部署到 SSH 服务器的实例，或这台服务器的专用密钥已吊销。',
    EXTERNAL_UNAVAILABLE: '暂时不能执行：密钥库未配置加密密钥，或运维任务暂时不可用，请稍后重试。',
    WRITE_UNKNOWN: '连接中断，结果未知：已刷新记录，确认后再决定是否重试。',
  }
  return messages[error.code] ?? agentError(error)
}
