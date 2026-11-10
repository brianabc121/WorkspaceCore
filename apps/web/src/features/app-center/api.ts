import { computed, type MaybeRefOrGetter, toValue } from 'vue'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { useModulesStore } from '../../stores/modules'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'

// 应用中心（T-0211，方案 docs/v2/app-center/ 契约第 2、3 节）的数据层：类型取自后端 OpenAPI 生成的 schema.d.ts。
// 查询键 ['app-center', user, workspace, ...]：换身份或工作空间就是另一组查询，迟到的回包落在旧键上、不会显示出来；
// 实时事件的 modules 主题会刷新这一组（useWorkspaceEvents）。管理信息只给 owner / admin，成员不发请求。
// 列表与详情只读健康缓存（后端不探测）；“检测连接”是显式操作。

export type Management = Schema['ModuleManagementOut']
export type ManagementList = Schema['ModuleManagementList']
export type HealthState = Schema['ModuleHealthOut']['state']
export type SyncState = Schema['ModuleSyncOut']['state']
export type SyncReason = NonNullable<Schema['ModuleSyncOut']['reason']>
export type ActivityItem = Schema['AuditLogView']

export const appCenterKey = (user: string | undefined, scope: string, ...rest: unknown[]) => ['app-center', user, scope, ...rest] as const
// 同步在执行中时短暂轮询：每 2 秒一次，最多 1 分钟（后端执行期限 15 秒，超过 60 秒没结果显示为中断）
const POLL_MS = 2000, POLL_LIMIT_MS = 60_000

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, active, enabled: computed(() => active() && auth.canManage) }
}

const inFlight = (items: readonly Management[] | undefined) => !!items?.some(item => item.sync.state === 'pending' || item.sync.state === 'running')

export function useManagement(scope: string) {
  const c = context(scope)
  let pollingSince = 0
  return useQuery({
    queryKey: appCenterKey(c.user, scope, 'management'),
    enabled: c.enabled,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/modules/management', { params: { header: c.header }, signal })),
    refetchInterval: query => {
      if (!inFlight(query.state.data?.items)) { pollingSince = 0; return false }
      pollingSince ||= Date.now()
      return Date.now() - pollingSince < POLL_LIMIT_MS ? POLL_MS : false
    },
  })
}

/** 模块的操作记录：key 为 null 时是所有模块（含已移除模块的历史） */
export function useActivity(scope: string, key: MaybeRefOrGetter<string | null>, limit = 20) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => appCenterKey(c.user, scope, 'activity', toValue(key) ?? '*')),
    enabled: c.enabled,
    initialPageParam: null as string | null,
    getNextPageParam: (page: Schema['Page_AuditLogView_']) => page.next_cursor ?? null,
    queryFn: async ({ pageParam, signal }) => {
      const query = { limit, ...(pageParam ? { cursor: pageParam } : {}) }
      const module = toValue(key)
      return dataOf(module
        ? await c.auth.api.GET('/api/v1/modules/{key}/activity', { params: { header: c.header, path: { key: module }, query }, signal })
        : await c.auth.api.GET('/api/v1/modules/activity', { params: { header: c.header, query }, signal }))
    },
  })
}

/** 一次只做一个写操作；完成后刷新管理信息、操作记录和侧栏用的模块列表 */
export function useAppCenterActions(scope: string) {
  const c = context(scope), cache = useQueryClient(), modules = useModulesStore()
  const refresh = async () => {
    if (!c.active()) return
    await Promise.all([
      cache.invalidateQueries({ queryKey: appCenterKey(c.user, scope) }),
      modules.refresh(),
    ])
  }
  async function toggle(item: Management, enabled: boolean): Promise<Schema['ModuleOut']> {
    const result = dataOf(await c.auth.api.PATCH('/api/v1/modules/{key}', {
      params: { header: c.header, path: { key: item.module.key } },
      body: { enabled, expected_state_version: item.module.state_version },
    }))
    if (c.active()) modules.apply(result)
    return result
  }
  async function reconcile(item: Management): Promise<Management> {
    return dataOf(await c.auth.api.POST('/api/v1/modules/{key}/reconcile', {
      params: { header: c.header, path: { key: item.module.key } },
      body: { expected_state_version: item.module.state_version },
    }))
  }
  async function checkHealth(item: Management): Promise<Schema['ModuleHealthOut']> {
    return dataOf(await c.auth.api.POST('/api/v1/modules/{key}/health-check', { params: { header: c.header, path: { key: item.module.key } } }))
  }
  return { toggle, reconcile, checkHealth, refresh, active: c.active }
}

/** 写操作失败的说明：业务错误用后端写好的说明；网络断开、超时等不知道结果的情况单独说明 */
export function actionError(cause: unknown): { message: string; unknown: boolean } {
  if (cause instanceof ApiError) {
    if (cause.code === 'VERSION_CONFLICT') return { message: '模块状态已被其他操作修改，已刷新为最新状态，请确认后再操作。', unknown: false }
    if (cause.code === 'MODULE_BUSY') return { message: '这个工作空间正在启用 / 停用模块或同步配置，请稍后再试。', unknown: false }
    if (cause.code === 'MODULE_DISABLED_FOR_CHECK') return { message: '模块已停用，启用后才能检测连接。', unknown: false }
    if (cause.code === 'RATE_LIMITED') return { message: `检测太频繁，请${retryAfter(cause)}再试。`, unknown: false }
    if (cause.status === 409 || cause.status === 404) return { message: cause.message, unknown: false }
    if (cause.status >= 500 || cause.status === 0) return { message: '请求没有完成，结果未知：已重新读取真实状态，请看列表里的当前开关后再决定。', unknown: true }
    return { message: errorMessage(cause), unknown: false }
  }
  return { message: '网络中断，结果未知：已重新读取真实状态，请看列表里的当前开关后再决定。', unknown: true }
}

function retryAfter(cause: ApiError) {
  const seconds = cause.retryAfter ?? Number((cause.details as { retry_after_seconds?: unknown } | undefined)?.retry_after_seconds)
  return Number.isFinite(seconds) && seconds > 0 ? ` ${Math.ceil(seconds)} 秒后` : '稍后'
}
