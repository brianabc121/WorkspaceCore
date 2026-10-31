import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, isAccessError } from '../../services/api'

export const useAutomationAccess = defineStore('automation-access', () => {
  const auth = useAuthStore(), denied = ref(false), epoch = ref(0), revision = ref(0), blocked = ref<Record<string, boolean>>({})
  watch(() => [auth.user?.id, auth.workspaceId], () => { denied.value = false; epoch.value++; revision.value++; blocked.value = {} }, { flush: 'sync' })
  function block(id: string) { blocked.value[id] = true; revision.value++ }
  return { denied, epoch, revision, blocked, block }
})
function context(scope: string) {
  const auth = useAuthStore(), access = useAutomationAccess(), user = auth.user?.id, epoch = access.epoch
  const active = () => user === auth.user?.id && scope === auth.workspaceId && epoch === access.epoch
  async function read<T>(action: () => Promise<T>): Promise<T> {
    try { return await action() } catch (error) { if (active() && isAccessError(error)) access.denied = true; throw error }
  }
  return { auth, access, user, header: { 'X-Workspace-Id': scope }, active, read }
}
export function useAutomations(scope: string) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: ['automations', c.user, scope], enabled: computed(() => c.active() && !c.access.denied), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const before = c.access.revision
      const page = await c.read(async () => dataOf(await c.auth.api.GET('/api/v1/automations', { params: { header: c.header, query: { limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })))
      if (c.active() && before === c.access.revision) for (const item of page.items) delete c.access.blocked[item.id]
      return page
    }, getNextPageParam: p => p.next_cursor ?? undefined })
}
export function useAutomationConnection(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: ['automation-connection', c.user, scope], enabled: computed(() => c.active() && c.auth.canManage && !c.access.denied), queryFn: ({ signal }) => c.read(async () => dataOf(await c.auth.api.GET('/api/v1/automations/connection', { params: { header: c.header }, signal }))) })
}
export function useRuns(scope: string, id: string) {
  const c = context(scope)
  return useInfiniteQuery({ queryKey: ['automation-runs', c.user, scope, id], enabled: computed(() => c.active() && !c.access.denied), initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => c.read(async () => dataOf(await c.auth.api.GET('/api/v1/automations/{automation_id}/runs', { params: { header: c.header, path: { automation_id: id }, query: { limit: 30, ...(pageParam ? { cursor: pageParam } : {}) } }, signal }))), getNextPageParam: p => p.next_cursor ?? undefined })
}
