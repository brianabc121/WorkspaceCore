import { ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, isAccessError } from '../../services/api'

export function useResourceMutation(scope: string, prefixes: string[]) {
  const auth = useAuthStore(), cache = useQueryClient(), user = auth.user?.id
  const busy = ref(false), error = ref<unknown>(null), needsReload = ref(false), accessDenied = ref(false), message = ref('')
  const active = () => auth.user?.id === user && auth.workspaceId === scope
  const refresh = () => cache.invalidateQueries({ predicate: (q) => prefixes.includes(String(q.queryKey[0])) && q.queryKey[1] === user && q.queryKey[2] === scope }, { throwOnError: true })
  async function run(effect: () => Promise<unknown>, success = '更改已保存') {
    if (busy.value || needsReload.value || !active() || !['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? '')) return false
    busy.value = true; error.value = null; message.value = ''
    let committed = false
    try {
      await effect(); committed = true
      if (!active()) return false
      await refresh()
      if (!active()) return false
      message.value = success
      return true
    } catch (cause) {
      if (!active()) return false
      const uncertain = !(cause instanceof ApiError) || cause.status === 0 || cause.status >= 500
      accessDenied.value = isAccessError(cause)
      needsReload.value = committed || uncertain || accessDenied.value || (cause instanceof ApiError && cause.code === 'VERSION_CONFLICT')
      error.value = committed ? new ApiError(0, 'REFRESH_REQUIRED', '') : uncertain ? new ApiError(0, 'WRITE_UNKNOWN', '') : cause
      return false
    } finally { busy.value = false }
  }
  async function reload() {
    if (busy.value || !active()) return false
    busy.value = true
    try { await refresh(); if (!active()) return false; error.value = null; needsReload.value = false; accessDenied.value = false; message.value = ''; return true }
    catch (cause) { if (active()) { error.value = cause; accessDenied.value ||= isAccessError(cause) }; return false }
    finally { busy.value = false }
  }
  return { run, reload, busy, error, needsReload, accessDenied, message }
}
