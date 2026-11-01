import { computed } from 'vue'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'

// AI tool calls and approvals (V2 OC-5; OC-1 contract §6). Owner / admin see the whole workspace and decide;
// members and viewers only see records acting as themselves. Keys start with 'ai-tool-executions' so the SSE
// topic `ai` refreshes them.
export type Execution = Schema['AiToolExecutionOut']
export type ExecutionStatus = Execution['status']
export type RiskLevel = Execution['risk_level']
export interface ExecutionFilter { status: ExecutionStatus | ''; risk_level: RiskLevel | '' }

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, enabled: computed(() => active()) }
}

export function useExecutions(scope: string, filter: () => ExecutionFilter) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: computed(() => ['ai-tool-executions', c.user, scope, 'list', filter()]), enabled: c.enabled, initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const f = filter()
      const query = { limit: 30, ...(f.status ? { status: [f.status] } : {}), ...(f.risk_level ? { risk_level: [f.risk_level] } : {}), ...(pageParam ? { cursor: pageParam } : {}) }
      return dataOf(await c.auth.api.GET('/api/v1/ai/tool-executions', { params: { header: c.header, query }, signal }))
    },
    getNextPageParam: p => p.next_cursor ?? undefined })
}

export function useExecution(scope: string, id: () => string | null) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => ['ai-tool-executions', c.user, scope, 'one', id()]), enabled: computed(() => c.enabled.value && !!id()), retry: false,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/ai/tool-executions/{execution_id}', { params: { header: c.header, path: { execution_id: id()! } }, signal })) })
}

export const STATUS_LABELS: Record<ExecutionStatus, string> = {
  pending_approval: '待审批', rejected: '已拒绝', expired: '已过期（自动拒绝）', executing: '执行中',
  succeeded: '已执行', failed: '执行失败', unknown: '结果未知', denied: '权限不够，已拒绝',
}
export const RISK_LABELS: Record<RiskLevel, string> = { read: '读取', write: '写操作', critical: '高危' }

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
