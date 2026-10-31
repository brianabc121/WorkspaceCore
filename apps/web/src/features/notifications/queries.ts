import { computed, ref, watch, toValue, type MaybeRefOrGetter } from 'vue'
import { defineStore } from 'pinia'
import { useInfiniteQuery, useQueryClient, type InfiniteData } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, isAccessError, errorMessage, type Schema } from '../../services/api'

export const useNotificationAccess = defineStore('notification-access', () => {
  const auth = useAuthStore(), denied = ref(false), busy = ref(false), epoch = ref(0)
  watch(() => [auth.user?.id, auth.workspaceId], () => { denied.value = false; busy.value = false; epoch.value++ }, { flush: 'sync' })
  return { denied, busy, epoch }
})
export function useNotifications(scope: string, area: string, limit: MaybeRefOrGetter<number>, unreadOnly: MaybeRefOrGetter<boolean>) {
  const auth = useAuthStore(), access = useNotificationAccess(), user = auth.user?.id, epoch = access.epoch
  const active = () => user === auth.user?.id && scope === auth.workspaceId && epoch === access.epoch
  return useInfiniteQuery({
    queryKey: computed(() => ['notifications', user, scope, area, toValue(limit), toValue(unreadOnly)]),
    enabled: computed(() => active() && !access.denied), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      try { return dataOf(await auth.api.GET('/api/v1/notifications', { params: { header: { 'X-Workspace-Id': scope }, query: { limit: toValue(limit), unread_only: toValue(unreadOnly), ...(pageParam ? { cursor: pageParam } : {}) } }, signal })) }
      catch (error) { if (active() && isAccessError(error)) access.denied = true; throw error }
    }, getNextPageParam: page => page.next_cursor ?? undefined,
  })
}
export function useNotificationMutation(scope: string) {
  const auth = useAuthStore(), access = useNotificationAccess(), cache = useQueryClient(), user = auth.user?.id, epoch = access.epoch
  const message = ref(''), error = ref<unknown>(null)
  const active = () => user === auth.user?.id && scope === auth.workspaceId && epoch === access.epoch
  const matches = (key: readonly unknown[]) => key[1] === user && key[2] === scope && ['notifications', 'overview'].includes(String(key[0]))
  async function mark(body: Schema['NotificationMark']) {
    if (access.busy || access.denied || !active()) return
    access.busy = true; error.value = null; message.value = ''
    let committed = false
    try {
      await cache.cancelQueries({ predicate: q => matches(q.queryKey) })
      const result = dataOf(await auth.api.PATCH('/api/v1/notifications', { params: { header: { 'X-Workspace-Id': scope } }, body }))
      if (!active()) return
      committed = true
      cache.setQueriesData<InfiniteData<Schema['NotificationPage']>>({ predicate: q => matches(q.queryKey) && q.queryKey[0] === 'notifications' }, old => old && ({ ...old, pages: old.pages.map(page => ({ ...page, unread_count: result.unread_count })) }))
      cache.setQueriesData<Schema['DashboardOverview']>({ predicate: q => matches(q.queryKey) && q.queryKey[0] === 'overview' }, old => old && ({ ...old, summary: { ...old.summary, unread_notifications: result.unread_count } }))
      await cache.invalidateQueries({ predicate: q => matches(q.queryKey) }, { throwOnError: true })
      if (active()) message.value = '通知已标为已读。'
    } catch (cause) {
      if (active()) { error.value = cause; if (isAccessError(cause)) access.denied = true; message.value = committed ? '已读状态已保存，刷新失败，请刷新确认。' : '请刷新通知确认已读状态。' }
    } finally { if (active()) access.busy = false }
  }
  async function refresh() {
    if (access.busy || !active()) return
    access.busy = true; error.value = null; message.value = ''
    try {
      // Deliberate fresh authorization read; ordinary old successes never clear denial.
      dataOf(await auth.api.GET('/api/v1/notifications', { params: { header: { 'X-Workspace-Id': scope }, query: { limit: 1 } } }))
      if (!active()) return
      access.denied = false
      await cache.invalidateQueries({ predicate: q => matches(q.queryKey) }, { throwOnError: true })
    } catch (cause) { if (active()) { error.value = cause; if (isAccessError(cause)) access.denied = true } }
    finally { if (active()) access.busy = false }
  }
  return { mark, refresh, error, message, access, errorText: computed(() => error.value ? errorMessage(error.value) : '') }
}
