import { computed, nextTick, ref, watch } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { useDashboardSearch } from '../../stores/dashboardSearch'
import { ApiError, dataOf, errorMessage, isAccessError, type Schema } from '../../services/api'
import { dashboardFileSchema, layoutSchema, type WidgetValue } from '../../widgets/registry'

type Update = Schema['NoteWidgetUpdate'] | Schema['QuickLinksWidgetUpdate'] | Schema['TodoSummaryWidgetUpdate'] | Schema['TodayTasksWidgetUpdate'] | Schema['RecentDocsWidgetUpdate'] | Schema['CalendarAgendaWidgetUpdate']
export function useDashboard() {
  const auth = useAuthStore()
  const search = useDashboardSearch()
  const scope = auth.workspaceId!
  const cache = useQueryClient(), user = auth.user?.id
  const header = { 'X-Workspace-Id': scope }
  const selected = ref<Schema['DashboardOut'] | null>(null)
  const choices = ref<Schema['DashboardOut'][]>([])
  const overviewQuery = useQuery({ queryKey: ['overview', auth.user?.id, scope], queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/dashboard/overview', { params: { header }, signal })) })
  const widgetsQuery = useQuery({ queryKey: computed(() => ['widgets', auth.user?.id, scope, selected.value?.id]), enabled: computed(() => !!selected.value), queryFn: async ({ signal, queryKey }) => dataOf(await auth.api.GET('/api/v1/dashboards/{dashboard_id}/widgets', { params: { header, path: { dashboard_id: queryKey[3]! } }, signal })) })
  const dashboard = computed(() => selected.value ?? overviewQuery.data.value?.dashboard)
  const widgets = computed(() => selected.value ? widgetsQuery.data.value?.items ?? [] : overviewQuery.data.value?.widgets ?? [])
  const loading = computed(() => overviewQuery.isPending.value || (!!selected.value && widgetsQuery.isPending.value))
  const fetching = computed(() => overviewQuery.isFetching.value || (!!selected.value && widgetsQuery.isFetching.value))
  const queryError = computed(() => overviewQuery.error.value ?? (selected.value ? widgetsQuery.error.value : null))
  const failure = ref<unknown>(null)
  const notice = ref('')
  const pending = ref(false)
  const editing = ref(false)
  const dirty = ref(false)
  const draft = ref<Schema['WidgetOut'][]>([])
  let version = 1
  const blocked = computed(() => failure.value instanceof ApiError && (failure.value.status === 409 || ['WRITE_UNKNOWN', 'REFRESH_REQUIRED'].includes(failure.value.code)))
  const denied = computed(() => isAccessError(queryError.value) || isAccessError(failure.value))
  const canEdit = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer' && !denied.value && !queryError.value)
  const error = computed(() => failure.value ? errorMessage(failure.value) : '')
  const shownWidgets = computed(() => denied.value ? [] : editing.value ? draft.value : widgets.value)
  watch(widgets, (value) => { search.widgets = denied.value ? [] : value }, { immediate: true })
  watch(denied, (value) => { if (value) { editing.value = false; search.widgets = [] } })
  async function refresh(query: { refetch: () => Promise<{ error: unknown }> }) {
    const result = await query.refetch()
    if (result.error) throw result.error
  }

  async function reload() {
    if (pending.value) return
    const previousFailure = failure.value
    const keepBlocked = blocked.value
    notice.value = ''
    try {
      await refresh(overviewQuery)
      if (selected.value) {
        const list = dataOf(await auth.api.GET('/api/v1/dashboards', { params: { header } }))
        choices.value = list.items
        selected.value = list.items.find((item) => item.id === selected.value!.id) ?? null
        if (selected.value) await refresh(widgetsQuery)
      }
      await cache.invalidateQueries({ predicate: (query) => ['knowledge', 'calendar'].includes(String(query.queryKey[0])) && query.queryKey[1] === user && query.queryKey[2] === scope && query.queryKey[3] === 'widget' })
      editing.value = false; dirty.value = false; failure.value = null
    } catch (error) {
      failure.value = isAccessError(error) ? error : keepBlocked ? previousFailure : error
      if (keepBlocked) notice.value = '重新加载失败，编辑仍被锁定，请稍后重试。'
    }
  }
  async function act(action: (committed: () => void) => Promise<void>, message: string) {
    if (pending.value || !canEdit.value || blocked.value) return false
    pending.value = true; failure.value = null; notice.value = ''
    let committed = false
    try { await action(() => { committed = true }); notice.value = message; return true }
    catch (error) { failure.value = committed && !isAccessError(error) ? new ApiError(0, 'REFRESH_REQUIRED', '') : error; return committed }
    finally { pending.value = false }
  }
  function beginEdit() {
    if (!canEdit.value || pending.value || fetching.value || !dashboard.value || blocked.value) return
    draft.value = widgets.value.map((widget) => {
      const parsed = layoutSchema.safeParse(widget.layout)
      return { ...widget, layout: parsed.success ? parsed.data : { desktop: { x: 0, y: 0, w: 6, h: 3 }, mobile: { y: 0, h: 3 } } }
    })
    version = dashboard.value.version
    editing.value = true; dirty.value = false; notice.value = ''; failure.value = null
  }
  function changeLayout(placements: Schema['WidgetPlacement'][]) {
    if (!editing.value || pending.value || blocked.value) return
    for (const placement of placements) {
      const widget = draft.value.find((item) => item.id === placement.widget_id)
      const parsed = layoutSchema.safeParse(placement.layout)
      if (widget && parsed.success) widget.layout = parsed.data
    }
    dirty.value = true
  }
  async function saveLayout() {
    if (!editing.value || !dashboard.value || !dirty.value) return
    const id = dashboard.value.id
    const layout = draft.value.map((widget) => ({ widget_id: widget.id, layout: layoutSchema.parse(widget.layout) }))
    await act(async (committed) => {
      const updated = dataOf(await auth.api.PATCH('/api/v1/dashboards/{dashboard_id}', { params: { header, path: { dashboard_id: id } }, body: { version, layout } }))
      committed()
      if (selected.value) selected.value = updated
      editing.value = false; dirty.value = false
      if (selected.value) await refresh(widgetsQuery)
      else await refresh(overviewQuery)
    }, '布局已保存')
  }
  async function importJson(text: string) {
    if (text.length > 3 * 1024 * 1024) { failure.value = new ApiError(422, 'IMPORT_INVALID', ''); return false }
    let result
    try { result = dashboardFileSchema.safeParse(JSON.parse(text)) } catch { result = null }
    if (!result?.success) { failure.value = new ApiError(422, 'IMPORT_INVALID', ''); return false }
    const body: Schema['DashboardCreate'] = { title: result.data.title, widgets: result.data.widgets }
    return act(async (committed) => {
      selected.value = dataOf(await auth.api.POST('/api/v1/dashboards', { params: { header }, body }))
      committed()
      choices.value = [...choices.value.filter((item) => item.id !== selected.value!.id), selected.value]
      editing.value = false; dirty.value = false
      await nextTick()
      await refresh(widgetsQuery)
    }, '已创建新仪表盘')
  }
  async function loadChoices() {
    if (pending.value || blocked.value) return
    try { choices.value = dataOf(await auth.api.GET('/api/v1/dashboards', { params: { header } })).items }
    catch (error) { failure.value = error }
  }
  function choose(id: string) {
    if (pending.value || blocked.value || (dirty.value && !window.confirm('切换将放弃未保存的布局，继续吗？'))) return
    selected.value = choices.value.find((item) => item.id === id && !item.is_default) ?? null
    editing.value = false; dirty.value = false; failure.value = null; notice.value = ''
  }
  async function makeDefault() {
    if (!dashboard.value) return
    const current = dashboard.value
    await act(async (committed) => {
      await auth.api.PATCH('/api/v1/dashboards/{dashboard_id}', { params: { header, path: { dashboard_id: current.id } }, body: { version: current.version, is_default: true } })
      committed()
      selected.value = null
      choices.value = []
      await refresh(overviewQuery)
    }, '已设为首页')
  }
  async function saveWidget(value: WidgetValue, widget?: Schema['WidgetOut']) {
    if (!dashboard.value) return false
    const id = dashboard.value.id
    return act(async (committed) => {
      if (widget) await auth.api.PATCH('/api/v1/widgets/{widget_id}', { params: { header, path: { widget_id: widget.id } }, body: { widget_type: value.widget_type, config: value.config, version: widget.version } as Update })
      else await auth.api.POST('/api/v1/dashboards/{dashboard_id}/widgets', { params: { header, path: { dashboard_id: id } }, body: value })
      committed()
      editing.value = false; dirty.value = false
      // Read the dashboard version again after add/delete/config changes.
      if (selected.value) {
        const list = dataOf(await auth.api.GET('/api/v1/dashboards', { params: { header } }))
        selected.value = list.items.find((item) => item.id === id) ?? null
        await refresh(widgetsQuery)
      }
      await refresh(overviewQuery)
    }, widget ? '组件已更新' : '组件已添加')
  }
  async function removeWidget(widget: Schema['WidgetOut']) {
    if (!window.confirm('确认删除这个组件？未保存的布局会被放弃。')) return
    await act(async (committed) => {
      await auth.api.DELETE('/api/v1/widgets/{widget_id}', { params: { header, path: { widget_id: widget.id } } })
      committed()
      editing.value = false; dirty.value = false
      if (selected.value) {
        const list = dataOf(await auth.api.GET('/api/v1/dashboards', { params: { header } }))
        selected.value = list.items.find((item) => item.id === selected.value!.id) ?? null
        await refresh(widgetsQuery)
      }
      await refresh(overviewQuery)
    }, '组件已删除')
  }
  return { overview: overviewQuery.data, dashboard, widgets, shownWidgets, choices, loading, fetching, queryError, denied, error, failure, notice, pending, canEdit, editing, dirty, blocked, beginEdit, changeLayout, saveLayout, importJson, reload, choose, loadChoices, makeDefault, saveWidget, removeWidget }
}
