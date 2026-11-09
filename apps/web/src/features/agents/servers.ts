import { computed } from 'vue'
import { keepPreviousData, useQuery, type QueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import type { HostRange } from './host'

// 智能体服务器（T-0145、T-0147；独立页面 T-0181，docs/v2/agent-servers-redesign）：一台服务器上可以有多个 OpenClaw。
// 键以 'agents' 开头，写操作和 SSE 的 agents 主题会一起刷新；有 SSH 任务在跑时每 2 秒取一次进度，
// 其余时候每分钟刷新（实例每分钟上报一次）。
export type Server = Schema['AgentServerOut']
export type ServerSsh = Schema['ServerSshOut']
export type ServerDetail = Schema['AgentServerDetail']
export type ServerList = Schema['AgentServerList']
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

/** 写操作返回的最新详情：马上放进已缓存的详情（各个 range），下一步就用最新的 version，不等刷新 */
export function putServerDetail(cache: QueryClient, user: string | undefined, scope: string, detail: ServerDetail) {
  cache.setQueriesData<ServerDetail>({ queryKey: serverKey(user, scope, detail.server.id) }, (old) => old && { ...old, server: detail.server, job: detail.job, warning: detail.warning ?? null, agents: detail.agents })
  void cache.invalidateQueries({ queryKey: serversKey(user, scope), exact: true })
}

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

export function useDeployment(scope: string, instanceId: () => string, enabled: () => boolean = () => true) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => deploymentKey(c.user, scope, instanceId())),
    enabled: computed(() => c.active() && c.auth.isOwner && !!instanceId() && enabled()),
    refetchInterval: (query: { state: { data?: Schema['AgentDeploymentState'] } }) => query.state.data?.job?.status === 'running' ? 2000 : false,
    queryFn: async ({ signal }): Promise<Schema['AgentDeploymentState']> => dataOf(await c.auth.api.GET('/api/v1/agents/instances/{instance_id}/deployment', { params: { header: c.header, path: { instance_id: instanceId() } }, signal })),
  })
}

export const SERVER_KIND: Record<Server['kind'], string> = { ssh: '账号密码添加', auto: '自动识别' }
export const AGENT_ALERT: Record<AgentRow['alerts'][number], string> = {
  mem_near_limit: '内存接近上限',
  restarting: '最近一小时反复重启',
  cpu_hog: '长时间占满 CPU',
}

/** 状态的语气：决定标签的颜色；文字始终同时显示 */
export type Tone = 'green' | 'blue' | 'amber' | 'red' | 'muted'

// 初始化状态（只看本地记录：最近一次初始化任务与 prepared_at）。kind=auto 为 null：不适用
export const INIT_STATUS: Record<NonNullable<Server['initialization_status']>, { label: string; tone: Tone }> = {
  not_initialized: { label: '待初始化', tone: 'amber' },
  initializing: { label: '初始化中', tone: 'blue' },
  ready: { label: '已就绪', tone: 'green' },
  failed: { label: '初始化失败', tone: 'red' },
  interrupted: { label: '初始化中断', tone: 'red' },
}
export function initStatus(server: Server): { label: string; tone: Tone } {
  return server.kind === 'auto' || !server.initialization_status ? { label: '不适用', tone: 'muted' } : INIT_STATUS[server.initialization_status]
}

// 连接状态：最近一次显式检测的结果（GET 不登录服务器，没检测过就是“未检测”）
export const CONNECTION_STATUS: Record<ServerSsh['connection_status'], { label: string; tone: Tone }> = {
  unknown: { label: '未检测', tone: 'muted' },
  connected: { label: '连接正常', tone: 'green' },
  auth_failed: { label: '密码被拒绝', tone: 'red' },
  unreachable: { label: '连不上', tone: 'red' },
  host_key_changed: { label: '主机身份变化', tone: 'red' },
  credential_unreadable: { label: '密码无法解密', tone: 'red' },
  credentials_required: { label: '待补充密码', tone: 'amber' },
}
export function connectionStatus(server: Server): { label: string; tone: Tone } {
  if (server.kind === 'ssh' && server.ssh) return CONNECTION_STATUS[server.ssh.connection_status]
  // 自动识别的服务器工作台不能登录：只看实例的整机上报
  if (!server.reported_at) return { label: '没有上报', tone: 'muted' }
  return server.stale ? { label: '上报过期', tone: 'amber' } : { label: '上报正常', tone: 'green' }
}
export const CONNECTION_HELP: Record<ServerSsh['connection_status'], string> = {
  unknown: '还没有检测过（或刚换过密码 / 地址）。点“测试连接”用保存的密码登录检查一次。',
  connected: '最近一次检测用保存的密码登录成功。',
  auth_failed: '最近一次检测服务器拒绝了保存的密码：更新登录密码后再测试。',
  unreachable: '最近一次检测连不上服务器：检查 IP、端口、防火墙与云安全组。',
  host_key_changed: '服务器的主机公钥和确认过的不一致（重装过系统，或连到了别的机器）：工作台已停止连接，请核对后更换地址重新确认。',
  credential_unreadable: '保存的密码无法解密（工作台的加密密钥可能换过）：重新填写登录密码。',
  credentials_required: '这台服务器还没有保存登录密码（旧服务器）：补充密码后才能初始化、部署与运维；上面已有的智能体照常运行。',
}

export const BLOCK_REASON: Record<NonNullable<Server['deploy_block_reason']>, string> = {
  not_ssh: '自动识别的服务器，工作台不能登录部署',
  credentials_required: '还没保存登录密码',
  credential_unreadable: '保存的密码无法解密',
  host_key_unconfirmed: '还没核对主机指纹',
  host_key_changed: '主机身份与确认过的不一致',
  auth_failed: '最近一次检测密码被拒绝',
  not_initialized: '还没初始化',
  initializing: '正在初始化',
  initialization_failed: '初始化失败',
  initialization_interrupted: '初始化被中断',
  busy: '正在执行其他任务',
}
export const blockReason = (server: Server) => server.deploy_block_reason ? BLOCK_REASON[server.deploy_block_reason] : server.deployable ? '' : '暂不能部署'

// 这台服务器上每个智能体的部署状态（以部署记录与最近任务为准；没有真实容器检测，不写“运行中 / 已停止”）
export const DEPLOYMENT_STATE: Record<AgentRow['deployment_state'], { label: string; tone: Tone }> = {
  associated: { label: '归属（非平台部署）', tone: 'muted' },
  deploying: { label: '部署中', tone: 'blue' },
  deployed: { label: '已部署', tone: 'green' },
  failed: { label: '部署失败', tone: 'red' },
  interrupted: { label: '部署中断', tone: 'red' },
  retained: { label: '已卸载（保留数据）', tone: 'amber' },
}

/** 地址:端口；IPv6 写成 [地址]:端口 */
export const endpoint = (host: string, port: number) => `${host.includes(':') ? `[${host}]` : host}:${port}`

/** 内存（MiB）显示成 GB 概数，满 1 GB 才换算 */
export function mib(value: number): string {
  if (value < 1024) return `${value} MiB`
  const gb = value / 1024
  return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`
}
/** 资源上限：null 表示不限制 */
export function limitText(cpus: number | null | undefined, mem: number | null | undefined): string {
  if ((cpus === null || cpus === undefined) && (mem === null || mem === undefined)) return '不限制'
  return `${cpus === null || cpus === undefined ? 'CPU 不限' : `${cpus} 核`} · ${mem === null || mem === undefined ? '内存不限' : mib(mem)}`
}
/** 系统 / 资源：只用上报得到的核心数与内存；没有上报写 — */
export function resources(server: Server): string {
  const parts = [server.cores ? `${server.cores} 核` : '', server.mem_total ? `${Math.round(server.mem_total / 1024 ** 3)} GB` : ''].filter(Boolean)
  return parts.length ? parts.join(' / ') : '—'
}

// 能选来部署新智能体的服务器：以服务端的 deployable 为准（只是本地判断，实际部署前仍会真实检查）
export const deployable = (server: Server) => server.kind === 'ssh' && server.deployable

// 智能体在服务器上占的份额：CPU 是占整机的百分比；内存按整机总量算
export function agentMemPercent(row: AgentRow, memTotal: number | null | undefined): number | null {
  const used = row.load?.mem_bytes
  return used === null || used === undefined || !memTotal ? null : (100 * used) / memTotal
}
