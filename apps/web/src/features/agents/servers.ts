import { computed } from 'vue'
import { keepPreviousData, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import type { HostRange } from './host'

// OpenClaw 服务器（T-0145、T-0147，docs/v2/MULTI_OPENCLAW_PLAN.md 第 7 节）：一台服务器上可以有多个实例。
// 键以 'agents' 开头，写操作和 SSE 的 agents 主题会一起刷新；有 SSH 任务在跑时每 2 秒取一次进度，
// 其余时候每分钟刷新（实例每分钟上报一次）。
export type Server = Schema['AgentServerOut']
export type ServerDetail = Schema['AgentServerDetail']
export type AgentRow = Schema['ServerAgentRow']
const REFRESH_MS = 60_000

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, active }
}

export const serversKey = (user: string | undefined, scope: string) => ['agents', user, scope, 'servers'] as const
export const serverKey = (user: string | undefined, scope: string, serverId: string) => ['agents', user, scope, 'servers', serverId] as const
export const deploymentKey = (user: string | undefined, scope: string, instanceId: string) => ['agents', user, scope, 'deployment', instanceId] as const

export function useServers(scope: string, enabled: () => boolean = () => true) {
  const c = context(scope)
  return useQuery({
    queryKey: serversKey(c.user, scope), enabled: computed(() => c.active() && c.auth.isOwner && enabled()), refetchInterval: REFRESH_MS,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/servers', { params: { header: c.header }, signal })),
  })
}

export function useServer(scope: string, serverId: () => string, range: () => HostRange) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => [...serverKey(c.user, scope, serverId()), range()]),
    enabled: computed(() => c.active() && c.auth.isOwner && !!serverId()), placeholderData: keepPreviousData,
    refetchInterval: (query: { state: { data?: ServerDetail } }) => query.state.data?.job?.status === 'running' ? 2000 : REFRESH_MS,
    queryFn: async ({ signal }): Promise<ServerDetail> => dataOf(await c.auth.api.GET('/api/v1/agents/servers/{server_id}', { params: { header: c.header, path: { server_id: serverId() }, query: { range: range() } }, signal })),
  })
}

export function useDeployment(scope: string, instanceId: () => string) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => deploymentKey(c.user, scope, instanceId())),
    enabled: computed(() => c.active() && c.auth.isOwner),
    refetchInterval: (query: { state: { data?: Schema['AgentDeploymentState'] } }) => query.state.data?.job?.status === 'running' ? 2000 : false,
    queryFn: async ({ signal }): Promise<Schema['AgentDeploymentState']> => dataOf(await c.auth.api.GET('/api/v1/agents/instances/{instance_id}/deployment', { params: { header: c.header, path: { instance_id: instanceId() } }, signal })),
  })
}

export const SERVER_KIND: Record<Server['kind'], string> = { ssh: 'SSH 部署', auto: '自动识别' }
export const AGENT_ALERT: Record<AgentRow['alerts'][number], string> = {
  mem_near_limit: '内存接近上限',
  restarting: '最近一小时反复重启',
  cpu_hog: '长时间占满 CPU',
}

// 服务器的状态一句话：列表和详情的标题旁边用
export function serverState(server: Server): string {
  if (server.kind === 'ssh' && server.ssh) {
    if (!server.ssh.host_key_confirmed) return '待核对指纹'
    if (!server.ssh.client_key_installed) return server.ssh.prepared_at ? '专用密钥已吊销' : '待准备'
  }
  if (!server.reported_at) return '还没有上报'
  return server.stale ? '数据过期' : '在线'
}

// 能部署实例的服务器：经 SSH 添加、确认过指纹（准备好的不用密码，没准备的部署时带一次密码）
export const deployable = (server: Server) => server.kind === 'ssh' && !!server.ssh?.host_key_confirmed
export const prepared = (server: Server) => !!server.ssh?.client_key_installed

// 智能体在服务器上占的份额：CPU 是占整机的百分比；内存按整机总量算
export function agentMemPercent(row: AgentRow, memTotal: number | null | undefined): number | null {
  const used = row.load?.mem_bytes
  return used === null || used === undefined || !memTotal ? null : (100 * used) / memTotal
}
