import { computed } from 'vue'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'

// OpenClaw instances and the secret vault (V2 OC-4; OC-1 contract §3, §4). The whole agents API is owner-only.
// Keys start with 'agents' so the SSE topic `agents` refreshes them.
function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, active, enabled: computed(() => active() && auth.isOwner) }
}

export function useAgentSettings(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: ['agents', c.user, scope, 'settings'], enabled: c.enabled,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/settings', { params: { header: c.header }, signal })) })
}

export function useInstances(scope: string) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: ['agents', c.user, scope, 'instances'], enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/instances', { params: { header: c.header, query: { limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined })
}

// 密钥库（T-0196，契约第 4 节）：q / kind 在服务端筛选、分页之前生效，筛选条件进查询键——换条件就是另一条查询、从第一页开始，
// 旧条件的游标不会拼进新条件。queryFn 从查询键取条件，游标与条件总是同一组。只留元数据字段，值永远不进缓存。
export type SecretFilters = { q?: string; kind?: Schema['AgentSecretOut']['kind'] }
export const secretMeta = (s: Schema['AgentSecretOut']): Schema['AgentSecretOut'] => ({
  id: s.id, name: s.name, kind: s.kind, description: s.description, value_version: s.value_version, last_rotated_at: s.last_rotated_at,
  grants: s.grants.map(g => ({ instance_id: g.instance_id, instance_name: g.instance_name, ref_id: g.ref_id })),
  version: s.version, created_at: s.created_at, updated_at: s.updated_at,
})
export function useSecrets(scope: string, filters: () => SecretFilters = () => ({})) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: computed(() => ['agents', c.user, scope, 'secrets', filters()] as const), enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ queryKey, pageParam, signal }) => {
      const page = dataOf(await c.auth.api.GET('/api/v1/agents/secrets', { params: { header: c.header, query: { limit: 50, ...queryKey[4], ...(pageParam ? { cursor: pageParam } : {}) } }, signal }))
      return { ...page, items: page.items.map(secretMeta) }
    },
    getNextPageParam: p => p.next_cursor ?? undefined })
}

/** 单个密钥的元数据（选中项不在已取回的列表页里也能读；不存在或跨空间 404） */
export function useSecret(scope: string, secretId: () => string | null) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => ['agents', c.user, scope, 'secret', secretId()] as const), enabled: computed(() => c.enabled.value && !!secretId()), retry: false,
    queryFn: async ({ queryKey, signal }) => secretMeta(dataOf(await c.auth.api.GET('/api/v1/agents/secrets/{secret_id}', { params: { header: c.header, path: { secret_id: queryKey[4]! } }, signal }))) })
}

export type AccessFilters = { secret_id?: string; instance_id?: string; result?: Schema['SecretAccessOut']['result']; limit?: number }
export function useSecretAccesses(scope: string, filter: () => AccessFilters) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: computed(() => ['agents', c.user, scope, 'accesses', filter()] as const), enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ queryKey, pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/secrets/accesses', { params: { header: c.header, query: { limit: 50, ...queryKey[4], ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined })
}

// OpenClaw 智能体总览（T-0182，改造契约第 6 节）：只读本地记录；summary 按 server_id / q 统计全部实例（不套用 status、不受分页影响）。
// 筛选条件进查询键：切换筛选时旧条件迟到的响应不会顶替新条件的结果
export type OverviewFilters = { server_id?: string; q?: string; status?: Schema['AgentInstanceOut']['status'] }
export const overviewKey = (user: string | undefined, scope: string) => ['agents', user, scope, 'overview'] as const
export function useAgentOverview(scope: string, filters: () => OverviewFilters) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: computed(() => [...overviewKey(c.user, scope), filters()]), enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/overview', { params: { header: c.header, query: { limit: 50, ...filters(), ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined })
}

/** 单个实例（详情不在已取回的列表页里时、部署抽屉继续部署前取最新 version） */
export function useInstance(scope: string, instanceId: () => string | null, enabled: () => boolean = () => true) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => ['agents', c.user, scope, 'instance', instanceId()]), enabled: computed(() => c.enabled.value && !!instanceId() && enabled()), retry: false,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/instances/{instance_id}', { params: { header: c.header, path: { instance_id: instanceId()! } }, signal })) })
}

export function unique<T extends { id: string }>(pages: { items: T[] }[] | undefined): T[] {
  return [...new Map((pages ?? []).flatMap(p => p.items).map(item => [item.id, item])).values()]
}

export const STATUS = { pending: '等待接入', online: '在线', offline: '离线', disabled: '已停用' } as const
// 列表与服务器页的“连接状态”：消息在线以 OpenIM 上下线为准，不代表容器在运行（改造规范第 3.3 节）
export const IM_STATUS = { pending: '待接入', online: '消息在线', offline: '离线', disabled: '已停用' } as const
export const NETWORK = { intranet: '内网', public: '外网' } as const
export const SCOPE = { read: '只读', read_write: '读写（写操作要审批）' } as const
export const ROLE = { admin: 'admin', member: 'member', viewer: 'viewer' } as const
export const SECRET_KIND = { model_api_key: '模型服务密钥', api_token: 'API 令牌', generic: '其他' } as const
/** 密钥库里能新建、能筛选的类型：模型服务的 Key 只在中转站管理的连接里（T-0209） */
export const VAULT_KIND = { api_token: 'API 令牌', generic: '其他' } as const

export function agentError(error: unknown): string {
  if (!(error instanceof ApiError)) return '请求失败，请稍后重试。'
  const messages: Record<string, string> = {
    AGENT_DISABLED: '实例已停用，启用后才能生成接入包。',
    AGENT_INGRESS_NOT_CONFIGURED: '这个网络模式的接入入口还没有配置（AGENT_INTRANET_URL / AGENT_PUBLIC_URL），不能生成接入包。',
    AGENT_SECRET_EXISTS: '已有同名的密钥。',
    EXTERNAL_UNAVAILABLE: '密钥库未配置加密密钥（CREDENTIAL_ENCRYPTION_KEY），暂时不能保存密钥。',
    VERSION_CONFLICT: '已被其他人修改，请刷新后再操作。',
    VALIDATION_FAILED: '输入不符合要求，请检查后重试。',
    FORBIDDEN: '只有 owner 可以管理 OpenClaw 实例与密钥库。',
    NOT_FOUND: '记录不存在，或你已无权访问。',
    WRITE_UNKNOWN: '连接中断，结果未知。接入包可能已经签发：刷新后看凭证列表，需要时重新生成（旧凭证会按选择保留或吊销）。',
    NOT_IMPLEMENTED: '这项功能的服务端还没有就绪，请稍后再试。',
  }
  return messages[error.code] ?? (error.status === 0 ? '网络连接失败，请检查连接后重试。' : '请求失败，请稍后重试。')
}

function csrf() {
  const value = document.cookie.split('; ').find(item => item.startsWith('ws_csrf='))?.slice(8)
  try { return value ? decodeURIComponent(value) : '' } catch { return '' }
}

// The bundle is a zip (credentials appear only in it, once). The shared API client reads bodies as text,
// so download it with fetch and hand the bytes to the browser.
export async function downloadBundle(scope: string, instanceId: string, body: Schema['BundleRequest']): Promise<string> {
  let response: Response
  try {
    response = await fetch(new Request(`${window.location.origin}/api/v1/agents/instances/${encodeURIComponent(instanceId)}/bundle`, {
      method: 'POST', credentials: 'same-origin', body: JSON.stringify(body),
      headers: { 'Content-Type': 'application/json', 'X-Workspace-Id': scope, 'X-CSRF-Token': csrf() },
    }))
  } catch { throw new ApiError(0, 'WRITE_UNKNOWN', '请求未完成') }
  if (!response.ok) {
    let detail: Partial<Schema['ErrorBody']> = {}
    try { detail = ((await response.json()) as Schema['ErrorResponse']).error ?? {} } catch { /* gateway HTML */ }
    throw new ApiError(response.status, detail.code ?? 'HTTP_ERROR', detail.message ?? '请求失败', detail.request_id ?? null)
  }
  const blob = await response.blob()
  const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')?.[1] ?? 'myworkspace-openclaw.zip'
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = name; document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return name
}
