import { useInfiniteQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'
export const priorities = ['未设置', '低', '中', '高', '紧急', '立即']
// 任务标签（T-0082）：用逗号或顿号分隔，最多 20 个、每个不超过 50 字；去掉空白，重复的只保留一个
export const LABELS_MAX = 20, LABEL_MAX = 50
export function parseLabels(text: string) { return [...new Set(text.split(/[,，、]/).map((v) => v.trim()).filter(Boolean))] }
export function toLocalInput(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
// 工作台自己的项目（T-0082）；默认不含已归档的，项目页可以选择显示
export function useProjects(scope: string, includeArchived: MaybeRefOrGetter<boolean> = false) {
  const auth = useAuthStore()
  return useInfiniteQuery({ queryKey: computed(() => ['projects', auth.user?.id, scope, toValue(includeArchived)]), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/projects', { params: { header: { 'X-Workspace-Id': scope }, query: { limit: 100, include_archived: toValue(includeArchived), ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: (page) => page.next_cursor ?? undefined, enabled: computed(() => !!auth.user),
  })
}
