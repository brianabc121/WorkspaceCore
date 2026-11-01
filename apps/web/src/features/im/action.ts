import { ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { imError } from './queries'

// One write at a time per panel; on success refresh every IM query of this user and workspace.
export function useImAction(scope: string) {
  const auth = useAuthStore(), cache = useQueryClient(), user = auth.user?.id
  const busy = ref(false), error = ref(''), notice = ref('')
  const active = () => auth.user?.id === user && auth.workspaceId === scope
  const refresh = () => cache.invalidateQueries({ predicate: q => q.queryKey[0] === 'im' && q.queryKey[1] === user && q.queryKey[2] === scope })
  async function act(effect: () => Promise<unknown>, success = '') {
    if (busy.value || !active()) return false
    busy.value = true; error.value = ''; notice.value = ''
    try { await effect(); if (!active()) return false; notice.value = success; await refresh(); return true }
    catch (cause) { if (active()) error.value = imError(cause); return false }
    finally { busy.value = false }
  }
  return { busy, error, notice, act, active, refresh, header: { 'X-Workspace-Id': scope } }
}
