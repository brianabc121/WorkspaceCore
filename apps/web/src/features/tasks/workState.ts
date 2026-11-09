import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { CircleCheck, CircleDashed, Eye, PlayCircle, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'

// 任务状态（T-0166 / T-0167，docs/v2/pages-redesign 规范第 4.2 节）：五种互斥状态由服务端统一判定，
// 这里只放显示用的文字、图标与色调，以及对状态接口的薄封装。数字一律来自服务端，不用当前页数组凑。
export type WorkState = Schema['TaskOut']['work_state']
export const WORK_STATES: WorkState[] = ['todo', 'running', 'awaiting_view', 'blocked', 'done']
export const STATE_META: Record<WorkState, { label: string; short: string; icon: typeof Eye; tone: string }> = {
  todo: { label: '待办', short: '待办', icon: CircleDashed, tone: 'muted' },
  running: { label: '进行中', short: '进行中', icon: PlayCircle, tone: 'blue' },
  awaiting_view: { label: '已完成待查看', short: '待查看', icon: Eye, tone: 'amber' },
  blocked: { label: '阻塞', short: '阻塞', icon: TriangleAlert, tone: 'red' },
  done: { label: '已完成', short: '已完成', icon: CircleCheck, tone: 'green' },
}

export type SummaryFilters = NonNullable<import('../../services/schema').operations['task_summary']['parameters']['query']>

/** 状态筛选标签的计数：与列表同一组筛选（不含状态），GET /tasks/summary。 */
export function useTaskSummary(scope: string, filters: Ref<SummaryFilters>) {
  const auth = useAuthStore()
  return useQuery({
    queryKey: computed(() => ['tasks', auth.user?.id, scope, 'summary', filters.value]),
    queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/tasks/summary', { params: { header: { 'X-Workspace-Id': scope }, query: filters.value }, signal })),
  })
}

/** 写入后刷新：任务列表、详情、计数、首页任务进展、项目卡片。 */
export function useTaskRefresh(scope: string) {
  const auth = useAuthStore(), cache = useQueryClient()
  return () => cache.invalidateQueries({ predicate: (q) => ['tasks', 'task', 'task-progress', 'overview', 'projects', 'project-summary', 'project-activity', 'task-events'].includes(String(q.queryKey[0])) && q.queryKey[1] === auth.user?.id && q.queryKey[2] === scope })
}

/** 标为已查看：只标当前用户、当前这一轮；轮次变了返回 409 COMPLETION_CHANGED（调用方刷新后重新判断）。 */
export async function markViewed(scope: string, task: Schema['TaskOut']) {
  const auth = useAuthStore()
  return dataOf(await auth.api.PUT('/api/v1/tasks/{task_id}/result-view', { params: { header: { 'X-Workspace-Id': scope }, path: { task_id: task.id } }, body: { completion_seq: task.completion_seq } }))
}

/** 标记 / 解除阻塞（member 以上）；带 Idempotency-Key 与 version。 */
export async function setBlocked(scope: string, task: Schema['TaskOut'], blocked: boolean, reason = '') {
  const auth = useAuthStore()
  return dataOf(await auth.api.PATCH('/api/v1/tasks/{task_id}/block', { params: { header: { 'X-Workspace-Id': scope, 'Idempotency-Key': crypto.randomUUID() }, path: { task_id: task.id } }, body: { version: task.version, blocked, reason } }))
}

export function stateError(cause: unknown) {
  if (cause instanceof ApiError && cause.code === 'COMPLETION_CHANGED') return '任务的完成结果已经变化，已重新读取，请看最新结果后再标记。'
  if (cause instanceof ApiError && cause.code === 'VERSION_CONFLICT') return '任务已在别处修改，已重新读取，请再试一次。'
  if (cause instanceof ApiError && cause.code === 'TASK_DONE') return '已完成的任务不能标记阻塞。'
  return null
}
