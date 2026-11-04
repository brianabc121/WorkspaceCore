// 任务时间轴的接口（T-0105）：时间轴、执行记录、执行者候选；开始 / 交给实例执行 / 结束一段。
// 查询键以 'timeline' / 'task-runs' / 'task-assignees' 开头：SSE 的 timeline、ai 主题会让它们失效（useWorkspaceEvents）。
import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'

export type Group = Schema['TimelineOut']['group']
export interface TimelineParams { from: string; to: string; project_id?: string; group: Group }

function context(scope: string) {
  const auth = useAuthStore()
  return { auth, user: auth.user?.id, header: { 'X-Workspace-Id': scope } }
}

export function useTimeline(scope: string, params: MaybeRefOrGetter<TimelineParams>, follow: MaybeRefOrGetter<boolean> = false, enabled: MaybeRefOrGetter<boolean> = true) {
  const { auth, user, header } = context(scope)
  return useQuery({
    queryKey: computed(() => ['timeline', user, scope, toValue(params)]),
    enabled: computed(() => toValue(enabled)),
    queryFn: async ({ signal, queryKey }) => {
      const p = queryKey[3] as TimelineParams
      return dataOf(await auth.api.GET('/api/v1/tasks/timeline', { params: { header, query: { from: p.from, to: p.to, group: p.group, ...(p.project_id ? { project_id: p.project_id } : {}) } }, signal }))
    },
    // 跟随当前时间时每分钟刷新一次（用量每分钟上报；SSE 断开时也不落后太多）
    refetchInterval: computed(() => (toValue(follow) ? 60_000 : false)),
    placeholderData: (previous) => previous,
  })
}

export function useRuns(scope: string, taskId: MaybeRefOrGetter<string | null>) {
  const { auth, user, header } = context(scope)
  return useQuery({
    queryKey: computed(() => ['task-runs', user, scope, toValue(taskId)]),
    enabled: computed(() => !!toValue(taskId)),
    queryFn: async ({ signal, queryKey }) => dataOf(await auth.api.GET('/api/v1/tasks/{task_id}/runs', { params: { header, path: { task_id: String(queryKey[3]) } }, signal })),
  })
}

export function useAssignees(scope: string) {
  const { auth, user, header } = context(scope)
  return useQuery({
    queryKey: ['task-assignees', user, scope],
    queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/tasks/assignees', { params: { header }, signal })),
    staleTime: 60_000,
  })
}

// 开始一段 / 交给实例执行：同一个任务、同一种操作用同一个 Idempotency-Key，直到成功或确定失败；
// 结果不确定（504、网络中断）时再点一次就是用同一个键查询结果，不会多开一段，也不会给智能体多发一条消息
const startKeys = new Map<string, string>()
export function useRunActions(scope: string) {
  const { auth, header } = context(scope)
  const cache = useQueryClient()
  const refresh = () => cache.invalidateQueries({ predicate: q => ['timeline', 'task-runs', 'task', 'tasks'].includes(String(q.queryKey[0])) && q.queryKey[2] === scope })
  async function start(taskId: string, dispatch: boolean) {
    const tag = `${scope}:${taskId}:${dispatch ? 'dispatch' : 'start'}`
    let key = startKeys.get(tag)
    if (!key) { key = crypto.randomUUID(); startKeys.set(tag, key) }
    try {
      const run = dataOf(await auth.api.POST('/api/v1/tasks/{task_id}/runs', { params: { header: { ...header, 'Idempotency-Key': key }, path: { task_id: taskId } }, body: { dispatch } }))
      startKeys.delete(tag)
      return run
    } catch (error) {
      // 确定的结果（不是 5xx、不是网络中断）：下次是新的请求
      if (error instanceof ApiError && error.status < 500 && error.code !== 'NETWORK_ERROR' && error.code !== 'WRITE_UNKNOWN') startKeys.delete(tag)
      throw error
    } finally { void refresh() }
  }
  async function stop(taskId: string, runId: string, reason: Schema['TaskRunStop']['reason']) {
    try { return dataOf(await auth.api.POST('/api/v1/tasks/{task_id}/runs/{run_id}/stop', { params: { header, path: { task_id: taskId, run_id: runId } }, body: { reason } })) }
    finally { void refresh() }
  }
  return { start, stop, refresh }
}

// 时间轴相关的错误说明
const MESSAGES: Record<string, string> = {
  TASK_DONE: '任务已经完成。',
  RUN_ALREADY_OPEN: '这个任务已经有一段正在执行。',
  AGENT_TASK: '执行者是智能体：请用“交给它执行”。',
  NO_AGENT_ASSIGNEE: '先把执行者设成一个 OpenClaw 实例。',
  AGENT_DISABLED: '这个实例已停用。',
  AGENT_OFFLINE: '这个实例现在不在线，消息发不到；等它上线再交给它。',
  IM_NOT_READY: '这个实例的 IM 账号还没就绪，请稍后再试。',
  IM_SEND_REJECTED: 'OpenIM 拒收了这条消息。',
  IM_UNAVAILABLE: 'IM 暂时不可用，消息没有发出，可以稍后重试。',
  RESULT_UNKNOWN: '消息是否发出还不确定：再点一次会查询结果，不会重复发送。',
}
export function runError(error: unknown) {
  return error instanceof ApiError && MESSAGES[error.code] ? MESSAGES[error.code] : errorMessage(error)
}
