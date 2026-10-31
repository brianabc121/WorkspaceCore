import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useAuthStore } from './auth'
import { dataOf, isAccessError } from '../services/api'

// Both operations endpoints require the same workspace-viewer permission.
// A successful old read cannot undo a newer denial in another widget.
export const useOperationsAccessStore = defineStore('operations-access', () => {
  const auth = useAuthStore(), denied = ref(false), revision = ref(0), identity = ref(0)
  const checking = ref(false), error = ref<unknown>(null)
  let recovery = 0
  const active = (user: string | undefined, scope: string) => auth.user?.id === user && auth.workspaceId === scope
  watch(() => auth.user?.id + ':' + auth.workspaceId, () => {
    denied.value = false; revision.value++; identity.value++; recovery++
    checking.value = false; error.value = null
  }, { flush: 'sync' })
  function deny(user: string | undefined, scope: string, context = identity.value) {
    if (!active(user, scope) || context !== identity.value) return
    denied.value = true; revision.value++
  }
  async function recover(user: string | undefined, scope: string) {
    if (checking.value || !active(user, scope)) return false
    const token = ++recovery, before = revision.value, header = { 'X-Workspace-Id': scope }
    checking.value = true; error.value = null
    try {
      await Promise.all([
        auth.api.GET('/api/v1/operations/services', { params: { header } }).then(dataOf),
        auth.api.GET('/api/v1/operations/metrics', { params: { header, query: { range: '1h', views: ['latency_p95'] } } }).then(dataOf),
      ])
      if (!active(user, scope) || token !== recovery || before !== revision.value) return false
      denied.value = false
      return true
    } catch (cause) {
      if (active(user, scope) && token === recovery) { error.value = cause; if (isAccessError(cause)) deny(user, scope) }
      return false
    } finally { if (token === recovery) checking.value = false }
  }
  return { denied, revision, identity, checking, error, deny, recover }
})
