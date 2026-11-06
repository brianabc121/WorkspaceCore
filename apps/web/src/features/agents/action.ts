import { ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { agentError } from './queries'

// One write at a time; on success refresh the agents queries, IM (contacts list the instances) and mail (mailbox assignments).
// `failure` keeps the last error so a caller can offer a follow-up (e.g. a forced revoke).
export function useAgentAction(scope: string, describe: (error: unknown) => string = agentError) {
  const auth = useAuthStore(), cache = useQueryClient(), user = auth.user?.id
  const busy = ref(false), error = ref(''), notice = ref(''), failure = ref<unknown>(null)
  const active = () => auth.user?.id === user && auth.workspaceId === scope
  const refresh = () => cache.invalidateQueries({ predicate: q => ['agents', 'im', 'mail'].includes(String(q.queryKey[0])) && q.queryKey[1] === user && q.queryKey[2] === scope })
  async function act(effect: () => Promise<unknown>, success = '') {
    if (busy.value || !active()) return false
    busy.value = true; error.value = ''; notice.value = ''; failure.value = null
    try { await effect(); if (!active()) return false; notice.value = success; await refresh(); return true }
    catch (cause) { if (active()) { failure.value = cause; error.value = describe(cause) } return false }
    finally { busy.value = false }
  }
  return { busy, error, notice, failure, act, refresh, header: { 'X-Workspace-Id': scope } }
}
