import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'

// 侧栏布局（T-0161，GET /api/v1/navigation）。查询键以 'navigation' 开头：保存后后端发 navigation 事件，
// useWorkspaceEvents 刷新它；侧栏、搜索、零代码页面和设置页共用这一份。
export const navigationKey = (user: string | undefined, scope: string | null) => ['navigation', user, scope]

export function useNavigationLayout() {
  const auth = useAuthStore()
  return useQuery({
    queryKey: computed(() => navigationKey(auth.user?.id, auth.workspaceId)),
    enabled: computed(() => !!auth.user && !!auth.workspaceId),
    staleTime: 60_000,
    queryFn: async ({ queryKey, signal }) => dataOf(await auth.api.GET('/api/v1/navigation', { params: { header: { 'X-Workspace-Id': String(queryKey[2]) } }, signal })),
  })
}

/** 新分组、新页面的 id：小写字母和数字（后端要求 ^[a-z0-9][a-z0-9-]{0,39}$） */
export function newId(): string {
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`
  return random.replace(/[^a-z0-9]/gi, '').toLowerCase().slice(0, 12)
}
