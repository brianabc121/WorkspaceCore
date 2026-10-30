import { useInfiniteQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { computed } from 'vue'
export const priorities = ['未设置', '低', '中', '高', '紧急', '立即']
export function toLocalInput(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
export function useProjects(scope: string) {
  const auth = useAuthStore()
  return useInfiniteQuery({ queryKey: ['projects', auth.user?.id, scope], initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/projects', { params: { header: { 'X-Workspace-Id': scope }, query: { limit: 100, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: (page) => page.next_cursor ?? undefined, enabled: computed(() => !!auth.user),
  })
}
