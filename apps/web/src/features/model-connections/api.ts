import { computed } from 'vue'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'

// 中转站管理（T-0206 / MC-6，契约第 1、2 节）的数据层：类型取自后端 OpenAPI 生成的 schema.d.ts，不在这里另造字段。
// 查询键以 'agents' 开头、带 user + workspace：切换身份或工作空间就是另一组查询，不会显示上一身份的数据；
// 写操作后 useAgentAction 一并刷新（智能体的主模型、连接选择器也在 'agents' 下）。整个接口只给 owner。
// API Key 只在表单组件的内存里，从不进查询缓存、地址或浏览器存储；这里的查询只读元数据。

export type Connection = Schema['ModelConnectionOut']
export type ConnectionCheck = Schema['ConnectionCheckOut']
export type ConnectionModel = Schema['ConnectionModelOut']
export type Capabilities = Schema['ModelConnectionCapabilities']
export type Protocol = Schema['ModelConnectionCapabilities']['supported_protocols'][number]
export type Provider = Schema['OfficialPreset']['provider']
export type MigrationEntry = Schema['MigrationEntryOut']
export type MigrationState = NonNullable<NonNullable<NonNullable<import('../../services/schema').operations['list_migration_entries']['parameters']['query']>['state']>>
export type SyncResult = Schema['ModelSyncResult']
export type CheckReason = NonNullable<ConnectionCheck['reason']>
export type Assignment = Schema['ModelAssignmentRow']
export type DrawerTab = 'config' | 'models' | 'grants' | 'activity'
/** “复制为新连接”的预填：只有非敏感字段，Key 必须重新输入 */
export type CreatePrefill = Partial<Omit<Schema['ModelConnectionCreate'], 'api_key' | 'models'>> & { models?: string[] }

export type ConnectionFilters = {
  q?: string
  type?: 'official' | 'relay'
  configuration_state?: Connection['configuration_state']
  enabled?: boolean
  check_status?: ConnectionCheck['status'] | 'never'
}

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, active, enabled: computed(() => active() && auth.isOwner) }
}

/** 本页所有查询键的前缀：['agents', user, workspace, 'model-connections', ...] */
export const mcKey = (user: string | undefined, scope: string, ...rest: unknown[]) => ['agents', user, scope, 'model-connections', ...rest] as const

const cursor = (pageParam: string | null) => (pageParam ? { cursor: pageParam } : {})
// 授权、实例与完整模型集合要整体替换，必须读全：逐页读到底（服务端每页最多 100，最多读 20 页）
async function everyPage<T>(read: (cursor: string | null) => Promise<{ items: T[]; next_cursor?: string | null }>): Promise<T[]> {
  const items: T[] = []
  let next: string | null = null
  for (let page = 0; page < 20; page++) {
    const result = await read(next)
    items.push(...result.items)
    next = result.next_cursor ?? null
    if (!next) return items
  }
  throw new ApiError(502, 'INVALID_RESPONSE', '分页过多')
}

export function useCapabilities(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: mcKey(c.user, scope, 'capabilities'), enabled: c.enabled, staleTime: 5 * 60_000,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/model-connections/capabilities', { params: { header: c.header }, signal })) })
}

/** 整个工作空间的统计（不受筛选与分页影响） */
export function useSummary(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: mcKey(c.user, scope, 'summary'), enabled: c.enabled,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/model-connections/summary', { params: { header: c.header }, signal })) })
}

/** 连接列表：筛选条件进查询键，换条件就是另一条查询、从第一页开始（游标绑定条件） */
export function useConnections(scope: string, filters: () => ConnectionFilters) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => mcKey(c.user, scope, 'list', filters())),
    enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ queryKey, pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/model-connections', { params: { header: c.header, query: { limit: 50, ...(queryKey[5] as ConnectionFilters), ...cursor(pageParam) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

/** 一个连接（不在当前页、当前筛选里也能打开；不存在或跨空间 404） */
export function useConnection(scope: string, id: () => string | null) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => mcKey(c.user, scope, 'connection', id())), enabled: computed(() => c.enabled.value && !!id()), retry: false,
    queryFn: async ({ queryKey, signal }) => dataOf(await c.auth.api.GET('/api/v1/model-connections/{connection_id}', { params: { header: c.header, path: { connection_id: queryKey[5] as string } }, signal })) })
}

/** 模型管理页签：独立分页 */
export function useModels(scope: string, id: () => string | null) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => mcKey(c.user, scope, 'models', id())),
    enabled: computed(() => c.enabled.value && !!id()), initialPageParam: null as string | null,
    queryFn: async ({ queryKey, pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/model-connections/{connection_id}/models', { params: { header: c.header, path: { connection_id: queryKey[5] as string }, query: { limit: 50, ...cursor(pageParam) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

/** 完整的模型集合与同步快照（编辑模型范围时要整体替换，必须读全） */
export function useAllModels(scope: string, id: () => string | null) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => mcKey(c.user, scope, 'models-all', id())),
    enabled: computed(() => c.enabled.value && !!id()), retry: false,
    queryFn: async ({ queryKey, signal }) => {
      const pages: Schema['ConnectionModelPage'][] = []
      const items = await everyPage(async next => {
        const page = dataOf(await c.auth.api.GET('/api/v1/model-connections/{connection_id}/models', { params: { header: c.header, path: { connection_id: queryKey[5] as string }, query: { limit: 100, ...cursor(next) } }, signal }))
        pages.push(page)
        return page
      })
      const first = pages[0]
      return { items, discovered: first?.discovered ?? [], sync_state: first?.sync_state ?? null, synced_at: first?.synced_at ?? null }
    },
  })
}

/** 连接的全部授权 */
export function useGrants(scope: string, id: () => string | null) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => mcKey(c.user, scope, 'grants', id())),
    enabled: computed(() => c.enabled.value && !!id()), retry: false,
    queryFn: async ({ queryKey, signal }) => everyPage(async next => dataOf(await c.auth.api.GET('/api/v1/model-connections/{connection_id}/grants', { params: { header: c.header, path: { connection_id: queryKey[5] as string }, query: { limit: 100, ...cursor(next) } }, signal }))),
  })
}

/** 本工作空间的全部 OpenClaw 实例（授权勾选用；只取名称与状态） */
export function useAllInstances(scope: string, enabled: () => boolean = () => true) {
  const c = context(scope)
  return useQuery({
    queryKey: mcKey(c.user, scope, 'instances'),
    enabled: computed(() => c.enabled.value && enabled()),
    queryFn: async ({ signal }) => (await everyPage(async next => dataOf(await c.auth.api.GET('/api/v1/agents/instances', { params: { header: c.header, query: { limit: 100, ...cursor(next) } }, signal })))).map(i => ({ id: i.id, name: i.name, status: i.status })),
  })
}

export function useActivity(scope: string, id: () => string | null) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => mcKey(c.user, scope, 'activity', id())),
    enabled: computed(() => c.enabled.value && !!id()), initialPageParam: null as string | null,
    queryFn: async ({ queryKey, pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/model-connections/{connection_id}/activity', { params: { header: c.header, path: { connection_id: queryKey[5] as string }, query: { limit: 30, ...cursor(pageParam) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

/** 迁移项（按状态筛选、分页）；counts 是各状态的全量计数 */
export function useMigrationEntries(scope: string, state: () => MigrationState | null, limit = 50) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => mcKey(c.user, scope, 'migration', state(), limit)),
    enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ queryKey, pageParam, signal }) => {
      const s = queryKey[5] as MigrationState | null
      return dataOf(await c.auth.api.GET('/api/v1/model-connections/migration/entries', { params: { header: c.header, query: { limit, ...(s ? { state: s } : {}), ...cursor(pageParam) } }, signal }))
    },
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

/** OpenClaw 连接分配表 */
export function useAssignments(scope: string) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: mcKey(c.user, scope, 'assignments'),
    enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/model-assignments', { params: { header: c.header, query: { limit: 50, ...cursor(pageParam) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

export function unique<T extends { id: string }>(pages: { items: T[] }[] | undefined): T[] {
  return [...new Map((pages ?? []).flatMap(p => p.items).map(item => [item.id, item])).values()]
}

// ---- 显示文字 ----

export const TYPE_LABEL = { official: '官方直连', relay: '第三方中转' } as const
export const PROTOCOL_LABEL: Record<Protocol, string> = { openai_compatible: 'OpenAI 兼容', anthropic_native: 'Anthropic 原生', gemini_native: 'Gemini 原生' }
export const SCOPE_LABEL = { selected: '指定模型', all_available: '服务商全部可用模型' } as const
export const MIGRATION_LABEL: Record<Connection['migration_state'], string> = { native: '新建', needs_configuration: '待完善', needs_classification: '待归类', managed: '已纳管', non_model: '非模型凭据' }
export const LEGACY_KIND = { model_api_key: '模型服务密钥', api_token: 'API 令牌', generic: '其他' } as const

type Tone = 'green' | 'amber' | 'red' | 'muted' | 'blue'

/** 配置完整度与启停（与检测结果分开显示；检测失败不等于停用） */
export function configStatus(c: Pick<Connection, 'configuration_state' | 'enabled' | 'migration_state' | 'credential_configured'>): { label: string; tone: Tone } {
  if (c.migration_state === 'non_model') return { label: '非模型凭据', tone: 'muted' }
  if (c.migration_state === 'needs_classification') return { label: '待归类', tone: 'amber' }
  if (c.configuration_state === 'needs_review') return { label: '需复核', tone: 'red' }
  if (c.configuration_state === 'draft') return { label: c.migration_state === 'needs_configuration' ? '待完善' : '草稿', tone: 'amber' }
  if (!c.enabled) return { label: '已停用', tone: 'muted' }
  return { label: '已启用', tone: 'green' }
}

export const REASON_TEXT: Record<CheckReason, string> = {
  unauthorized: '凭据失效或权限不足：服务商拒绝了这个 Key（401），请更换 Key',
  forbidden: '凭据失效或权限不足：这个 Key 没有权限（403），请检查服务商的权限或更换 Key',
  timeout: '连接服务商超时',
  unreachable: '连不上服务商的地址',
  tls_error: 'TLS 证书校验失败',
  invalid_response: '服务商返回的内容不符合协议',
  model_not_available: '这个模型不可用（服务商没有提供或这个 Key 无权使用）',
  protocol_unsupported: '这个协议还没有开放',
  models_endpoint_unsupported: '服务商不支持列出模型：需手动配置模型',
  destination_not_allowed: '目标地址不被允许（内网、回环或受限地址）',
  credential_unreadable: '保存的 Key 无法解密，请更换 Key',
  stale_revision: '检测期间连接配置变了，这次结果作废',
}

export const FRESH_SECONDS = 15 * 60
/** 检测结果是否已超过有效期（默认 15 分钟；不是实时健康承诺） */
export function isStale(check: Pick<ConnectionCheck, 'checked_at'>, freshSeconds = FRESH_SECONDS, now = Date.now()) {
  const at = Date.parse(check.checked_at)
  return Number.isFinite(at) && now - at > freshSeconds * 1000
}

/** 检测结果的文字与色调：元数据与推理分开说，限流、凭据、不支持都不当成“空成功” */
export function checkStatus(check: ConnectionCheck | null | undefined): { label: string; tone: Tone; detail: string } {
  if (!check) return { label: '未检测', tone: 'muted', detail: '还没有检测过当前配置。' }
  const reason = check.reason ? REASON_TEXT[check.reason] ?? check.reason : ''
  const mode = check.mode === 'inference' ? '推理检测' : '元数据检测'
  switch (check.status) {
    case 'passed':
      return check.mode === 'inference'
        ? { label: '推理检测通过', tone: 'green', detail: `模型 ${check.model_id ?? '—'} 接受了一次真实的推理请求。` }
        : { label: '元数据检测通过', tone: 'green', detail: '只证明 Key 与模型列表接口可用，不代表任何模型能推理。' }
    case 'rate_limited':
      return { label: '服务商限流中', tone: 'amber', detail: `${mode}被服务商限流（429），连接与模型数据保留不变。` }
    case 'unsupported':
      return { label: check.reason === 'models_endpoint_unsupported' ? '需手动配置模型' : '不支持', tone: 'amber', detail: check.reason === 'models_endpoint_unsupported' ? '服务商不支持列出模型：请用“指定模型”手工填写模型 ID，再做推理检测。' : reason || '服务商不支持这项检测。' }
    case 'failed':
      return { label: check.reason === 'unauthorized' || check.reason === 'forbidden' ? '凭据失效或权限不足' : `${mode}失败`, tone: 'red', detail: reason || '检测没有通过。' }
    default:
      return { label: '结果未知', tone: 'muted', detail: reason || '没有得到明确结果，请稍后重试。' }
  }
}

export function syncText(result: Pick<SyncResult, 'sync_state' | 'total' | 'added' | 'removed' | 'reason' | 'kept_previous' | 'kept_in_use'>): { text: string; tone: Tone } {
  const reason = result.reason ? `：${REASON_TEXT[result.reason] ?? result.reason}` : ''
  const kept = result.kept_in_use.length ? `；${result.kept_in_use.join('、')} 正被智能体使用，已保留` : ''
  switch (result.sync_state) {
    case 'complete': return { text: `已同步 ${result.total} 个模型（新增 ${result.added}、移除 ${result.removed}）${kept}。列出的模型不代表都能推理。`, tone: 'green' }
    case 'partial': return { text: `服务商返回的列表不完整，已保留上一次的完整列表${reason}。`, tone: 'amber' }
    case 'unsupported': return { text: '服务商不支持列出模型：请用“指定模型”手工填写模型 ID。已保留现有模型。', tone: 'amber' }
    default: return { text: `同步失败${reason}${result.kept_previous ? '；已保留上一次的模型列表' : ''}。`, tone: 'red' }
  }
}

const ACTIONS: Record<string, string> = {
  'model_connection.created': '创建连接',
  'model_connection.updated': '修改配置',
  'model_connection.key_rotated': '更换 Key',
  'model_connection.models_replaced': '修改模型范围',
  'model_connection.models_synced': '同步模型',
  'model_connection.checked': '检测连接',
  'model_connection.grants_replaced': '修改智能体授权',
  'model_connection.classified': '迁移归类',
  'model_connection.deleted': '删除连接',
  'credential.resolved': '智能体取用 Key',
  'agent.model_binding.updated': '设置主模型',
  'agent.model_binding.revoked': '解除主模型',
  'agent.model_binding.apply_requested': '重新应用主模型',
  'agent.model_binding.applied': '智能体确认已应用',
  'agent.model_binding.apply_failed': '智能体应用失败',
}
export const actionLabel = (action: string) => ACTIONS[action] ?? action
export const ACCESS_RESULT: Record<string, { label: string; tone: Tone }> = { ok: { label: '成功', tone: 'green' }, not_found: { label: '未授权或不存在', tone: 'muted' }, rate_limited: { label: '过于频繁', tone: 'amber' } }

// ---- 错误 ----

export type Impact = { instances: { instance_id: string; instance_name: string; bound: boolean }[]; bound_instances: number; pending_jobs: number }
/** 409 CONNECTION_IN_USE 的 details.impact（停用 / 删除会影响到的实例）；details 在 OpenAPI 里是任意对象，这里按后端 ImpactOut 读取并校验 */
export function impactOf(error: unknown): Impact | null {
  if (!(error instanceof ApiError)) return null
  const raw = (error.details as { impact?: unknown } | undefined)?.impact
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Partial<Impact>
  const instances = Array.isArray(value.instances) ? value.instances.filter(i => i && typeof i.instance_id === 'string').map(i => ({ instance_id: i.instance_id, instance_name: typeof i.instance_name === 'string' ? i.instance_name : i.instance_id, bound: !!i.bound })) : []
  return { instances, bound_instances: Number(value.bound_instances) || 0, pending_jobs: Number(value.pending_jobs) || 0 }
}
/** details 里的数字字段（例如 legacy_grants） */
export function detailNumber(error: unknown, key: string): number {
  if (!(error instanceof ApiError)) return 0
  return Number((error.details as Record<string, unknown> | undefined)?.[key]) || 0
}
/** 409 MODEL_IN_USE 的 details.models：{model_id: [instance_id]} */
export function modelsInUse(error: unknown): Record<string, string[]> {
  if (!(error instanceof ApiError)) return {}
  const raw = (error.details as { models?: unknown } | undefined)?.models
  if (!raw || typeof raw !== 'object') return {}
  return Object.fromEntries(Object.entries(raw as Record<string, unknown>).map(([k, v]) => [k, Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []]))
}
/** 409 GRANT_IN_USE 的 details.instance_ids */
export function instanceIdsOf(error: unknown): string[] {
  if (!(error instanceof ApiError)) return []
  const raw = (error.details as { instance_ids?: unknown } | undefined)?.instance_ids
  return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : []
}
export const isCode = (error: unknown, code: string) => error instanceof ApiError && error.code === code

export function mcError(error: unknown): string {
  if (!(error instanceof ApiError)) return '请求失败，请稍后重试。'
  const messages: Record<string, string> = {
    MODEL_CONNECTION_EXISTS: '已有同名的连接，请换一个名称。',
    MODEL_PROTOCOL_UNSUPPORTED: '这个协议还没有开放。',
    MODEL_OFFICIAL_HOST_MISMATCH: '官方直连只能使用官方地址；用其他地址请选“第三方中转”。',
    MODEL_CONNECTION_NOT_READY: '连接还没有配置完整：需要类型、协议、URL、Key 与至少一个模型；也可以先保存为草稿。',
    CONNECTION_ENDPOINT_IN_USE: '这个连接已授权给智能体或正被使用，修改地址或协议会把 Key 发到新的目标：请复制为新连接再切换。',
    CONNECTION_IN_USE: '连接仍有授权、绑定或进行中的应用任务。',
    MODEL_IN_USE: '要移除的模型正被智能体使用，请先更换它们的主模型。',
    GRANT_IN_USE: '这些智能体正以这个连接为主模型，请先在下方“OpenClaw 连接分配”里更换或解除它们的主模型。',
    MODEL_SYNC_REQUIRED: '还没有一次完整的模型同步：先点“同步模型”，或改用“指定模型”手工填写。',
    CHECK_IN_PROGRESS: '这个连接正在同步或检测，请稍后再试。',
    MODEL_CONNECTION_NON_MODEL: '这是非模型凭据，请在“迁移与兼容凭据”里管理。',
    MODEL_CONFIGURATION_INCOMPLETE: '还没有填写协议或地址，不能同步或检测。',
    MODEL_NOT_ALLOWED: '这个模型不在连接的模型集合里。',
    MODEL_BINDING_CONFLICT: '有智能体正以它为主模型，或有进行中的应用任务：请先完成切换或解除主模型。',
    CREDENTIAL_UNREADABLE: '保存的 Key 无法解密，请更换 Key。',
    MODEL_CONNECTION_DISABLED: '这条连接已停用。',
    VERSION_CONFLICT: '连接已被其他操作修改，这次没有保存。已重新读取最新信息，请核对后再操作。',
    WRITE_UNKNOWN: '连接中断，结果未知：操作可能已经生效。请刷新后核对，再决定是否重试。',
    FORBIDDEN: '只有 owner 可以管理中转站连接。',
    NOT_FOUND: '连接不存在，或你已无权访问。',
    EXTERNAL_UNAVAILABLE: '凭据加密服务没有配置（CREDENTIAL_ENCRYPTION_KEY），暂时不能保存 Key。',
  }
  if (error.code === 'RATE_LIMITED' || error.status === 429) return error.retryAfter === null ? '同步与检测太频繁（每个连接每分钟最多 5 次），请稍后再试。' : `同步与检测太频繁，请在 ${error.retryAfter} 秒后再试。`
  if (error.code === 'VALIDATION_FAILED') {
    const first = (error.details as { errors?: { msg?: unknown }[] } | undefined)?.errors?.[0]?.msg
    return typeof first === 'string' && /[一-龥]/.test(first) ? `输入不符合要求：${first}` : '输入不符合要求，请检查后重试。'
  }
  return messages[error.code] ?? errorMessage(error)
}

/** 一条连接的全部模型 ID（“复制为新连接”预填用；模型 ID 不是敏感信息） */
export async function fetchModelIds(scope: string, id: string): Promise<string[]> {
  const auth = useAuthStore(), header = { 'X-Workspace-Id': scope }
  const items = await everyPage(async next => dataOf(await auth.api.GET('/api/v1/model-connections/{connection_id}/models', { params: { header, path: { connection_id: id }, query: { limit: 100, ...cursor(next) } } })))
  return items.map(m => m.model_id)
}
