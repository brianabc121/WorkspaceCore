import { onUnmounted, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { useModulesStore } from '../../stores/modules'
import { useNavigationStore } from '../../stores/navigation'
import { moduleEvents, type ModuleTopic } from '../../modules/registry'
import type { Schema } from '../../services/api'

type Topic = Schema['InvalidateEvent']['topics'][number]
// 核心功能的主题 → 要刷新的查询键前缀；插拔式模块的主题写在模块的 events 里（T-0159），这里必须覆盖其余所有主题
const core: Record<Exclude<Topic, ModuleTopic>, string[]> = {
  // task-progress：首页“工作概况 / 任务进展”（T-0164）；项目卡片的状态计数、本周完成与最近动态也随任务变化（T-0168）
  overview: ['overview', 'widgets'], tasks: ['tasks', 'task', 'knowledge-link-tasks', 'search', 'task-progress', 'projects', 'project-summary', 'project-activity', 'task-events'],
  projects: ['projects', 'search', 'project-summary'], knowledge: ['knowledge', 'knowledge-sources', 'related-docs', 'kb', 'search'],
  calendar: ['calendar', 'search'], developer: ['developer', 'search'], integrations: ['integrations'],
  notifications: ['notifications', 'overview'], operations: ['operations'],
  im: ['im'], agents: ['agents'], ai: ['ai-tool-executions', 'im', 'timeline'],
  // 任务时间轴（T-0104）：执行记录与 token 用量
  timeline: ['timeline', 'task-runs', 'task-progress'],
  // 项目 Issues（T-0123）
  issues: ['issues', 'search', 'task-progress'],
  // 模块启用 / 停用（T-0159）：启用状态由 modules store 重新读取（见 invalidate），首页组件跟着刷新；
  // 应用中心（T-0211）的管理信息、同步结果与操作记录也跟着刷新
  modules: ['overview', 'widgets', 'app-center'],
  // 侧栏布局（T-0161）：在 navigation store 里，由 invalidate 重新读取
  navigation: [],
}
const families: Partial<Record<Topic, readonly string[]>> = { ...core, ...moduleEvents }
// Backoff for rebuilding a stream the browser closed; reset after every successful open.
const RETRY_BASE_MS = 1000, RETRY_MAX_MS = 30000
export function useWorkspaceEvents() {
  const auth = useAuthStore(), modules = useModulesStore(), navigation = useNavigationStore(), cache = useQueryClient(), available = ref(false)
  let close = () => {}
  const refresh = () => {
    const user = auth.user?.id, scope = auth.workspaceId
    if (user && scope) { void cache.invalidateQueries({ predicate: q => q.queryKey[1] === user && q.queryKey[2] === scope }); void modules.refresh(); void navigation.refresh() }
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
      if (data.topics.includes('modules')) void modules.refresh()
      if (data.topics.includes('navigation')) void navigation.refresh()
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
