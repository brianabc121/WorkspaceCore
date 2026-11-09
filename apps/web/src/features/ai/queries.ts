import { computed } from 'vue'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'

// AI tool calls and approvals (V2 OC-5; OC-1 contract §6; 审批中心改版 T-0191，契约 docs/v2/ai-admin-redesign §2).
// Owner / admin see the whole workspace and decide; members and viewers only see records acting as themselves.
// Keys start with 'ai-tool-executions' so the SSE topic `ai` refreshes them (list, summary and single records).
export type Execution = Schema['AiToolExecutionOut']
export type ExecutionStatus = Execution['status']
export type RiskLevel = Execution['risk_level']
export type ExecutionSummary = Schema['AiExecutionSummaryOut']
export type ExecutionPeriod = ExecutionSummary['period']
export type ExecutionSort = 'created_desc' | 'expires_asc'

/** 汇总与列表共用的非状态条件：时间范围、类型、智能体、工具（契约 §2.1：汇总不接收 status） */
export interface ScopeFilter { period: ExecutionPeriod; risk_level: RiskLevel[]; instance_id: string | null; tool_name: string | null }
/** 列表条件：再加状态与排序。任何一项变了都是新的查询键，分页从第一页重新开始（旧游标对新条件无效，服务端会 422） */
export interface ExecutionFilter extends ScopeFilter { status: ExecutionStatus[]; sort: ExecutionSort; limit?: number }

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, enabled: computed(() => active()) }
}

function scopeQuery(f: ScopeFilter) {
  return { period: f.period, ...(f.risk_level.length ? { risk_level: f.risk_level } : {}), ...(f.instance_id ? { instance_id: f.instance_id } : {}), ...(f.tool_name ? { tool_name: f.tool_name } : {}) }
}

export const executionKey = (user: string | undefined, scope: string, id: string | null) => ['ai-tool-executions', user, scope, 'one', id]
/** 只匹配某个用户在某个工作空间的审批数据（换用户 / 空间的迟到响应不串） */
export const ownKeys = (user: string | undefined, scope: string) =>
  (q: { queryKey: readonly unknown[] }) => ['ai-tool-executions', 'im'].includes(String(q.queryKey[0])) && q.queryKey[1] === user && q.queryKey[2] === scope

export function useExecutions(scope: string, filter: () => ExecutionFilter, enabled: () => boolean = () => true) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => ['ai-tool-executions', c.user, scope, 'list', filter()]),
    enabled: computed(() => c.enabled.value && enabled()),
    initialPageParam: null as string | null,
    // signal 交给请求：条件变了旧查询没人用，TanStack 会取消它，迟到的旧页不会拼进新条件
    queryFn: async ({ pageParam, signal }) => {
      const f = filter()
      const query = { limit: f.limit ?? 30, ...scopeQuery(f), sort: f.sort, ...(f.status.length ? { status: f.status } : {}), ...(pageParam ? { cursor: pageParam } : {}) }
      return dataOf(await c.auth.api.GET('/api/v1/ai/tool-executions', { params: { header: c.header, query }, signal }))
    },
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

/** 汇总卡与筛选项（契约 §2.1）。offset_ms = 服务端 as_of − 收到响应时的本机时间，用来校正倒计时 */
export function useApprovalSummary(scope: string, filter: () => ScopeFilter) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => ['ai-tool-executions', c.user, scope, 'summary', filter()]),
    enabled: c.enabled,
    queryFn: async ({ signal }) => {
      const data = dataOf(await c.auth.api.GET('/api/v1/ai/tool-executions/summary', { params: { header: c.header, query: scopeQuery(filter()) }, signal }))
      const asOf = Date.parse(data.as_of)
      return { ...data, offset_ms: Number.isNaN(asOf) ? 0 : asOf - Date.now() }
    },
  })
}

export function useExecution(scope: string, id: () => string | null) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => executionKey(c.user, scope, id())), enabled: computed(() => c.enabled.value && !!id()), retry: false,
    // 执行中的记录过一会再读一次（SSE 也会刷新）；只读，不会触发执行
    refetchInterval: (q: { state: { data?: Execution } }) => (q.state.data?.status === 'executing' ? 5000 : false),
    queryFn: async ({ signal }): Promise<Execution> => dataOf(await c.auth.api.GET('/api/v1/ai/tool-executions/{execution_id}', { params: { header: c.header, path: { execution_id: id()! } }, signal })),
  })
}

/** 成员显示名（owner / admin 才能读成员列表；与设置页共用同一个查询键），用来显示执行身份与审批人 */
export function useMemberNames(scope: string) {
  const c = context(scope)
  const members = useQuery({
    queryKey: computed(() => ['members', c.user, scope]),
    enabled: computed(() => c.enabled.value && c.auth.canManage),
    staleTime: 60_000,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/workspaces/{workspace_id}/members', { params: { path: { workspace_id: scope } }, signal })),
  })
  return computed(() => new Map((members.data.value?.items ?? []).map(m => [m.user_id, m.display_name || m.email])))
}

export const STATUS_LABELS: Record<ExecutionStatus, string> = {
  pending_approval: '待审批', rejected: '已拒绝', expired: '已过期（自动拒绝）', executing: '执行中',
  succeeded: '执行成功', failed: '执行失败', unknown: '结果未知', denied: '权限不够，已拒绝',
}
// knowledge：知识库工具，直接执行、不审批（T-0098）
export const RISK_LABELS: Record<RiskLevel, string> = { read: '读取', write: '写操作', critical: '高危', knowledge: '知识库', direct: '直接执行' }
export const PERIOD_LABELS: Record<ExecutionPeriod, string> = { '24h': '最近 24 小时', '7d': '最近 7 天', all: '全部时间' }

export function aiError(error: unknown): string {
  if (!(error instanceof ApiError)) return '请求失败，请稍后重试。'
  const messages: Record<string, string> = {
    STEP_UP_REQUIRED: '高危操作需要输入你的登录密码。',
    STEP_UP_FAILED: '密码不正确。',
    RATE_LIMITED: '密码错误次数过多，请稍后再试。',
    VERSION_CONFLICT: '这条请求刚刚有变化，已刷新，请确认后再操作。',
    EXECUTION_NOT_PENDING: '这条请求已经处理过或已过期。',
    FORBIDDEN: '只有 owner 和 admin 可以审批。',
    FORBIDDEN_ROLE: '只有 owner 和 admin 可以审批。',
    NOT_FOUND: '记录不存在，或你没有权限查看。',
  }
  return messages[error.code] ?? (error.status >= 500 ? '服务暂时不可用，请稍后重试。' : error.message || '请求失败，请稍后重试。')
}
