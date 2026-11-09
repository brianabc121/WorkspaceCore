import { computed, type Ref } from 'vue'
import type { LocationQuery, LocationQueryRaw } from 'vue-router'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'
import type { operations } from '../../services/schema'

// 审计日志（T-0198，方案 docs/v2/ai-admin-redesign 计划 C2 / 契约第 5 节）：服务端筛选、按 ID 读详情、导出当前筛选。
// 查询键：['audit', 用户, 工作空间, 'events' | 'detail', 条件 / ID]。切换工作空间时路由会清掉 'audit' 开头的缓存；
// 换用户时整个缓存清空。分页游标只存在同一组条件的查询里，换了条件就是新的查询、从第一页开始。
export type AuditEntry = Schema['AuditLogView']
export type ServerFilters = Omit<NonNullable<operations['list_audit_logs']['parameters']['query']>, 'limit' | 'cursor'>

const HOUR = 3600_000, DAY = 24 * HOUR
export const PAGE_SIZE = 30
// 时间范围按“现在”算出 after / before（UTC）。before 多留几分钟，免得本机时钟比服务器慢时看不到刚发生的事件；
// 不超过服务端导出的 31 天上限（30 天 + 5 分钟）
export const CLOCK_ALLOWANCE_MS = 5 * 60_000
export const RANGES = [
  { key: '24h', label: '最近 24 小时', ms: DAY },
  { key: '7d', label: '最近 7 天', ms: 7 * DAY },
  { key: '30d', label: '最近 30 天', ms: 30 * DAY },
  { key: 'all', label: '全部时间', ms: null },
] as const
export type RangeKey = (typeof RANGES)[number]['key']
export const DEFAULT_RANGE: RangeKey = '7d'

// 事件类型：按事件名前缀筛（服务端 action_prefix，按字面匹配）。只列内置事件的分组，链接里别的值不认
export const EVENT_TYPES = [
  { prefix: 'auth.', label: '登录与账户安全' },
  { prefix: 'user.', label: '账号资料' },
  { prefix: 'workspace.', label: '工作空间与成员' },
  { prefix: 'module.', label: '模块启用' },
  { prefix: 'integration.', label: '集成' },
  { prefix: 'project.', label: '项目' },
  { prefix: 'task.', label: '任务' },
  { prefix: 'issues.', label: 'Issue' },
  { prefix: 'knowledge_', label: '知识库' },
  { prefix: 'calendar_event.', label: '日程' },
  { prefix: 'agents.instance.', label: '智能体实例' },
  { prefix: 'agents.secret.', label: '密钥库' },
  { prefix: 'agents.server.', label: 'OpenClaw 服务器' },
  { prefix: 'agents.ssh.', label: '智能体部署与卸载' },
  { prefix: 'agents.', label: '全部智能体事件' },
  { prefix: 'mail.', label: '邮件' },
  { prefix: 'news.', label: '新闻早报' },
  { prefix: 'runner.', label: '容器服务器与运行环境' },
  { prefix: 'im.', label: '群聊与会话' },
  { prefix: 'audit.', label: '审计导出' },
] as const

/** 页面上的筛选（都不涉密，写在地址里：?range= / ?actor= / ?type= / ?q=）。actor：'' 全部、'system' 系统、成员 ID */
export type AuditFilterState = { range: RangeKey; actor: string; type: string; q: string }
export const DEFAULT_FILTERS: AuditFilterState = { range: DEFAULT_RANGE, actor: '', type: '', q: '' }
export const Q_MAX = 100

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export const isUuid = (value: string) => UUID.test(value)
const one = (value: unknown) => (typeof value === 'string' ? value : '')

export function filtersFromQuery(query: LocationQuery): AuditFilterState {
  const range = one(query.range), actor = one(query.actor), type = one(query.type)
  return {
    range: RANGES.some((r) => r.key === range) ? (range as RangeKey) : DEFAULT_RANGE,
    actor: actor === 'system' || isUuid(actor) ? actor : '',
    type: EVENT_TYPES.some((t) => t.prefix === type) ? type : '',
    q: one(query.q).trim().slice(0, Q_MAX),
  }
}

/** 把筛选写回地址；默认值不写。其余参数（例如 ?id=）保留 */
export function filtersToQuery(state: AuditFilterState, rest: LocationQuery): LocationQueryRaw {
  const query: LocationQueryRaw = { ...rest }
  delete query.range; delete query.actor; delete query.type; delete query.q
  if (state.range !== DEFAULT_RANGE) query.range = state.range
  if (state.actor) query.actor = state.actor
  if (state.type) query.type = state.type
  if (state.q) query.q = state.q
  return query
}

export const sameFilters = (a: AuditFilterState, b: AuditFilterState) => a.range === b.range && a.actor === b.actor && a.type === b.type && a.q === b.q

/** 发给服务端的条件；anchor 是这组条件开始查询的时刻（换条件、刷新时更新） */
export function serverFilters(state: AuditFilterState, anchor: number): ServerFilters {
  const filters: ServerFilters = {}
  const span = RANGES.find((r) => r.key === state.range)?.ms ?? null
  if (span !== null) {
    filters.after = new Date(anchor - span).toISOString()
    filters.before = new Date(anchor + CLOCK_ALLOWANCE_MS).toISOString()
  }
  if (state.actor === 'system') filters.system_only = true
  else if (state.actor) filters.actor_id = state.actor
  if (state.type) filters.action_prefix = state.type
  if (state.q) filters.q = state.q
  return filters
}

/** 导出只接受 31 天以内、给出了起止时间的范围（服务端同样校验） */
export const canExport = (filters: ServerFilters) => !!filters.after && !!filters.before

export function useAuditEvents(scope: string, user: string, filters: Readonly<Ref<ServerFilters>>, anchor: Readonly<Ref<number>>) {
  const auth = useAuthStore()
  return useInfiniteQuery({
    queryKey: computed(() => ['audit', user, scope, 'events', filters.value, anchor.value] as const),
    enabled: computed(() => auth.canManage && auth.workspaceId === scope && auth.user?.id === user),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, queryKey, signal }) => dataOf(await auth.api.GET('/api/v1/audit-logs', {
      params: { header: { 'X-Workspace-Id': scope }, query: { ...queryKey[4], limit: PAGE_SIZE, ...(pageParam ? { cursor: pageParam } : {}) } },
      signal,
    })),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })
}

/** 单条详情：总是按 ID 读（不在已加载列表、不在当前筛选里也能打开）。读的过程中先显示列表里的同一行；
 *  读失败（404、失权）时不保留它。 */
export function useAuditDetail(scope: string, user: string, id: Readonly<Ref<string>>, loaded: (id: string) => AuditEntry | undefined) {
  const auth = useAuthStore()
  return useQuery({
    queryKey: computed(() => ['audit', user, scope, 'detail', id.value] as const),
    enabled: computed(() => isUuid(id.value) && auth.canManage && auth.workspaceId === scope && auth.user?.id === user),
    placeholderData: () => loaded(id.value),
    queryFn: async ({ queryKey, signal }) => dataOf(await auth.api.GET('/api/v1/audit-logs/{entry_id}', {
      params: { header: { 'X-Workspace-Id': scope }, path: { entry_id: queryKey[4] } },
      signal,
    })),
  })
}

export type MemberOption = { id: string; name: string }
/** 操作者筛选用的成员表（与成员管理同一个查询键）；读不到时只有“全部 / 系统” */
export function useAuditMembers(scope: string, user: string) {
  const auth = useAuthStore()
  return useQuery({
    queryKey: ['members', user, scope],
    enabled: computed(() => auth.canManage && auth.workspaceId === scope && auth.user?.id === user),
    queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/workspaces/{workspace_id}/members', { params: { path: { workspace_id: scope } }, signal })),
    select: (page) => page.items.map((m): MemberOption => ({ id: m.user_id, name: m.display_name || m.email })),
  })
}

/** 按当前服务端条件生成 CSV。共用客户端（同一会话、超时、换用户作废）按文本读取响应，会去掉 UTF-8 BOM，这里补回，
 *  Excel 才会按 UTF-8 打开。返回的 Blob 只交给这一次下载，调用方用完即释放，不缓存。 */
export async function fetchAuditCsv(scope: string, filters: ServerFilters): Promise<{ blob: Blob; filename: string }> {
  const auth = useAuthStore()
  const result = await auth.slowApi.GET('/api/v1/audit-logs/export', { params: { header: { 'X-Workspace-Id': scope }, query: filters }, parseAs: 'text' })
  if (typeof result.data !== 'string') throw new ApiError(502, 'INVALID_RESPONSE', '缺少响应数据')
  const text = result.data.startsWith('\uFEFF') ? result.data : `\uFEFF${result.data}`
  const named = /filename="([A-Za-z0-9._-]{1,120})"/.exec(result.response.headers.get('Content-Disposition') ?? '')?.[1]
  return { blob: new Blob([text], { type: 'text/csv;charset=utf-8' }), filename: named ?? 'audit-logs.csv' }
}

export function exportError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'AUDIT_EXPORT_TOO_LARGE') {
      const limit = typeof error.details?.limit === 'number' ? error.details.limit : 10000
      return `符合条件的记录超过 ${limit} 条，没有生成文件。请缩小时间范围或增加筛选条件后再导出。`
    }
    if (error.status === 403) return '你没有导出审计日志的权限（只有所有者和管理员可以导出）。'
    if (error.status === 422) return '导出需要 31 天以内的时间范围，请缩小时间范围后再试。'
    if (error.status === 404) return '工作空间不存在，或你已无权访问。'
  }
  return `没有生成文件：${errorMessage(error)}`
}

export function auditError(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return '你没有查看审计日志的权限。'
  return errorMessage(error)
}
