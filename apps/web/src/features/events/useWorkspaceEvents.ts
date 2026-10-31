import { onUnmounted, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import type { Schema } from '../../services/api'

const families: Record<Schema['InvalidateEvent']['topics'][number], string[]> = {
  overview: ['overview', 'widgets'], tasks: ['tasks', 'task', 'knowledge-link-tasks', 'search'],
  projects: ['projects', 'search'], knowledge: ['knowledge', 'knowledge-sources', 'related-docs', 'search'],
  calendar: ['calendar', 'search'], developer: ['developer', 'search'], integrations: ['integrations'],
  notifications: ['notifications', 'overview'], operations: ['operations'],
  automations: ['automations', 'automation-connection', 'automation-runs'],
}
export function useWorkspaceEvents() {
  const auth = useAuthStore(), cache = useQueryClient(), available = ref(false)
  let close = () => {}
  const refresh = () => {
    const user = auth.user?.id, scope = auth.workspaceId
    if (user && scope) void cache.invalidateQueries({ predicate: q => q.queryKey[1] === user && q.queryKey[2] === scope })
  }
  watch(() => [auth.user?.id, auth.workspaceId], ([user, scope]) => {
    close(); available.value = false
    if (!user || !scope || typeof EventSource === 'undefined') return
    let stream: EventSource
    try { stream = new EventSource('/api/v1/events/stream?workspace_id=' + encodeURIComponent(scope)) }
    catch { return }
    let current = true
    const active = () => current && auth.user?.id === user && auth.workspaceId === scope
    const open = () => { if (active()) { available.value = true; refresh() } }
    const error = () => {
      if (!active()) return
      available.value = false
      // Native EventSource retains Last-Event-ID during ordinary reconnects.
      // Fatal HTTP responses (including 429) close it; manual refresh remains.
      if (stream.readyState === 2) stream.close()
    }
    const invalidate = (event: Event) => {
      if (!active()) return
      let data: Partial<Schema['InvalidateEvent']>
      try { data = JSON.parse((event as MessageEvent<string>).data) } catch { return }
      if (!data || data.workspace_id !== scope || !Array.isArray(data.topics) || typeof data.at !== 'string' || (data.reset !== undefined && typeof data.reset !== 'boolean')) return
      if (data.reset) { refresh(); return }
      const prefixes = new Set(data.topics.flatMap(topic => typeof topic === 'string' ? families[topic] ?? [] : []))
      if (prefixes.size) void cache.invalidateQueries({ predicate: q => q.queryKey[1] === user && q.queryKey[2] === scope && prefixes.has(String(q.queryKey[0])) })
    }
    stream.addEventListener('open', open); stream.addEventListener('error', error); stream.addEventListener('invalidate', invalidate)
    close = () => { current = false; stream.removeEventListener('open', open); stream.removeEventListener('error', error); stream.removeEventListener('invalidate', invalidate); stream.close() }
  }, { immediate: true, flush: 'sync' })
  const visible = () => { if (document.visibilityState === 'visible') refresh() }
  document.addEventListener('visibilitychange', visible)
  onUnmounted(() => { close(); document.removeEventListener('visibilitychange', visible) })
  return { available }
}
