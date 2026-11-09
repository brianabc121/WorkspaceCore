import { computed, type Ref } from 'vue'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'

// 项目页的数据（T-0168，规范第 5 节）：列表、顶部统计、最近动态。查询键都带用户与工作空间。
// 统计与动态用单独的键：写入后另外刷新，读不到时不影响项目卡片与写入结果
export type ProjectScope = 'active' | 'attention' | 'archived'
export type ProjectListFilters = { scope: ProjectScope; q: string; sort: 'title' | 'updated' }

export function useProjectList(scope: string, filters: Ref<ProjectListFilters>) {
  const auth = useAuthStore()
  return useInfiniteQuery({
    queryKey: computed(() => ['projects', auth.user?.id, scope, 'page', filters.value]),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const f = filters.value
      const query = {
        limit: 30,
        include_archived: false,
        ...(f.scope === 'attention' ? { attention_only: true } : {}),
        ...(f.scope === 'archived' ? { archived_only: true } : {}),
        ...(f.q.trim() ? { q: f.q.trim() } : {}),
        ...(f.sort === 'updated' ? { sort: 'updated' as const } : {}),
        ...(pageParam ? { cursor: pageParam } : {}),
      }
      return dataOf(await auth.api.GET('/api/v1/projects', { params: { header: { 'X-Workspace-Id': scope }, query }, signal }))
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })
}

/** 本周：显示时区（浏览器）的周一 00:00 到下周一 00:00。 */
export function weekRange(now = new Date()) {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7))
  const end = new Date(start)
  end.setDate(end.getDate() + 7)
  return { completed_after: start.toISOString(), completed_before: end.toISOString() }
}

export function useProjectSummary(scope: string) {
  const auth = useAuthStore()
  const range = weekRange()
  return useQuery({
    queryKey: computed(() => ['project-summary', auth.user?.id, scope, range]),
    queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/projects/summary', { params: { header: { 'X-Workspace-Id': scope }, query: range }, signal })),
  })
}

export function useProjectActivity(scope: string, projectId?: string) {
  const auth = useAuthStore()
  return useInfiniteQuery({
    queryKey: computed(() => ['project-activity', auth.user?.id, scope, projectId ?? null]),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/projects/activity', { params: { header: { 'X-Workspace-Id': scope }, query: { limit: 6, ...(projectId ? { project_id: projectId } : {}), ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })
}
