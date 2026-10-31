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
// Backoff for rebuilding a stream the browser closed; reset after every successful open.
const RETRY_BASE_MS = 1000, RETRY_MAX_MS = 30000
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
    const url = '/api/v1/events/stream?workspace_id=' + encodeURIComponent(scope)
    let current = true, attempt = 0, timer: ReturnType<typeof setTimeout> | undefined, stream: EventSource | null = null
    const active = () => current && auth.user?.id === user && auth.workspaceId === scope
    const open = () => { if (active()) { attempt = 0; available.value = true; refresh() } }
    const error = () => {
      if (!active() || !stream) return
      available.value = false
      // Network errors keep the stream CONNECTING and the browser retries with Last-Event-ID.
      // A non-200 response (502/503 while api restarts, 429, 401) closes it for good, so rebuild it with backoff.
      if (stream.readyState === 2) { detach(stream); stream = null; timer = setTimeout(connect, Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** attempt++)) }
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
    const detach = (target: EventSource) => { target.removeEventListener('open', open); target.removeEventListener('error', error); target.removeEventListener('invalidate', invalidate); target.close() }
    const connect = () => {
      timer = undefined
      if (!active()) return
      try { stream = new EventSource(url) } catch { return }
      stream.addEventListener('open', open); stream.addEventListener('error', error); stream.addEventListener('invalidate', invalidate)
    }
    close = () => { current = false; clearTimeout(timer); if (stream) detach(stream); stream = null }
    connect()
  }, { immediate: true, flush: 'sync' })
  const visible = () => { if (document.visibilityState === 'visible') refresh() }
  document.addEventListener('visibilitychange', visible)
  onUnmounted(() => { close(); document.removeEventListener('visibilitychange', visible) })
  return { available }
}
