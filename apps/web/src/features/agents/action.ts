import { ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { agentError } from './queries'

// One write at a time; on success refresh the agents queries and IM (contacts list the instances).
export function useAgentAction(scope: string) {
  const auth = useAuthStore(), cache = useQueryClient(), user = auth.user?.id
  const busy = ref(false), error = ref(''), notice = ref('')
  const active = () => auth.user?.id === user && auth.workspaceId === scope
  const refresh = () => cache.invalidateQueries({ predicate: q => ['agents', 'im'].includes(String(q.queryKey[0])) && q.queryKey[1] === user && q.queryKey[2] === scope })
  async function act(effect: () => Promise<unknown>, success = '') {
    if (busy.value || !active()) return false
    busy.value = true; error.value = ''; notice.value = ''
    try { await effect(); if (!active()) return false; notice.value = success; await refresh(); return true }
    catch (cause) { if (active()) error.value = agentError(cause); return false }
    finally { busy.value = false }
  }
  return { busy, error, notice, act, header: { 'X-Workspace-Id': scope } }
}
