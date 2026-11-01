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

export function useSecrets(scope: string) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: ['agents', c.user, scope, 'secrets'], enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/secrets', { params: { header: c.header, query: { limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined })
}

export function useSecretAccesses(scope: string, filter: () => { instance_id?: string; result?: Schema['SecretAccessOut']['result'] }) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: computed(() => ['agents', c.user, scope, 'accesses', filter()]), enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/secrets/accesses', { params: { header: c.header, query: { limit: 50, ...filter(), ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined })
}

export function unique<T extends { id: string }>(pages: { items: T[] }[] | undefined): T[] {
  return [...new Map((pages ?? []).flatMap(p => p.items).map(item => [item.id, item])).values()]
}

export const STATUS = { pending: '等待接入', online: '在线', offline: '离线', disabled: '已停用' } as const
export const NETWORK = { intranet: '内网', public: '外网' } as const
export const SCOPE = { read: '只读', read_write: '读写（写操作要审批）' } as const
export const ROLE = { admin: 'admin', member: 'member', viewer: 'viewer' } as const
export const SECRET_KIND = { model_api_key: '模型服务密钥', api_token: 'API 令牌', generic: '其他' } as const

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
