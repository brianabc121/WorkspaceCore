<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { CalendarDays, ChevronLeft, ChevronRight, Clock, LoaderCircle, Plus, RefreshCw, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, isAccessError, type Schema } from '../../services/api'
import { useResourceMutation } from '../resources/mutation'
import { useCreateIntent } from '../../composables/useCreateIntent'
import { addDays, addMonths, browserZone, dateInZone, monthDates, validZone, weekDates } from './time'
import { dayStart, entriesByDay, filterEvents, type CalendarFilters, type DateRange } from './weekLayout'
import { monthTitle, rangeTitle, zoneOffset } from './labels'
import { useAllPages } from './useAllPages'
import DataState from '../../components/DataState.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import MutationNotice from '../resources/MutationNotice.vue'
import CalendarForm from './CalendarForm.vue'
import CalendarSidePanel from './CalendarSidePanel.vue'
import WeekTimeGrid from './WeekTimeGrid.vue'
import MonthGrid from './MonthGrid.vue'
import AgendaList from './AgendaList.vue'
import DayAgenda from './DayAgenda.vue'
import EventDetails from './EventDetails.vue'
import '../workspace/workspace-pages.css'
import './calendar.css'

// 日历页（T-0169，四页改造规范 §6，效果图 03-calendar.png）：
// 桌面周视图三栏（小月历与筛选 / 七天时间画布 / 选中日期与日程详情）；中等宽度详情改抽屉；
// 1024 以下默认日程列表（仍可选周视图，画布只在自己的区域横向滚动）。
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const { run, reload, busy, error, needsReload, accessDenied, message } = useResourceMutation(scope, ['calendar', 'overview'])
const route = useRoute(), initialZone = browserZone()
function initialDate() { try { return typeof route.query.date === 'string' ? addDays(route.query.date, 0) : dateInZone(new Date().toISOString(), initialZone) } catch { return dateInZone(new Date().toISOString(), initialZone) } }
type View = 'agenda' | 'week' | 'month'
const date = ref(initialDate()), zone = ref(initialZone)
const selected = ref({ date: date.value, zone: zone.value }), rangeError = ref('')
const view = ref<View>(window.matchMedia?.('(max-width: 1023px)').matches ? 'agenda' : 'week')
const filters = ref<CalendarFilters>({ timed: true, due: true, allDay: true })

// 当前显示的日子（显示时区的日期）与读取区间
const days = computed(() => (view.value === 'month' ? monthDates(selected.value.date) : weekDates(selected.value.date)))
const visible = computed<DateRange>(() => ({ start: days.value[0]!, end: addDays(days.value.at(-1)!, 1) }))
// 全天日程按事件自己时区的日期显示，可能落在显示区间的 UTC 范围之外：前后多读两天，再按日期过滤
const range = computed(() => ({ start: new Date(dayStart(visible.value.start, selected.value.zone) - 2 * 86400000).toISOString(), end: new Date(dayStart(visible.value.end, selected.value.zone) + 2 * 86400000).toISOString() }))
// 任务截止按 [当天 0 点, 区间结束) 读取（due_after 含、due_before 不含）
const dueRange = computed(() => ({ start: new Date(dayStart(visible.value.start, selected.value.zone)).toISOString(), end: new Date(dayStart(visible.value.end, selected.value.zone)).toISOString() }))

const events = useAllPages<Schema['CalendarEventOut']>({
  queryKey: computed(() => ['calendar', user, scope, range.value]),
  enabled: computed(() => true),
  fetchPage: async (cursor, signal, key) => {
    const { start, end } = key[3] as { start: string; end: string }
    return dataOf(await auth.api.GET('/api/v1/calendar/events', { params: { header, query: { start, end, limit: 100, ...(cursor ? { cursor } : {}) } }, signal }))
  },
})
const blocked = computed(() => accessDenied.value || isAccessError(events.query.error.value))
// 任务截止（规范 §1.2、§6）：从任务派生的只读条目，在任务页修改；'tasks' 前缀让任务的实时事件刷新它们
const deadlines = useAllPages<Schema['TaskOut']>({
  queryKey: computed(() => ['tasks', user, scope, 'deadlines', dueRange.value]),
  enabled: computed(() => !blocked.value && filters.value.due),
  fetchPage: async (cursor, signal, key) => {
    const { start, end } = key[4] as { start: string; end: string }
    return dataOf(await auth.api.GET('/api/v1/tasks', { params: { header, query: { status: 'all', due_after: start, due_before: end, sort: 'due_at', limit: 100, ...(cursor ? { cursor } : {}) } }, signal }))
  },
})

const allEvents = computed(() => (blocked.value ? [] : events.items.value))
const shownEvents = computed(() => filterEvents(allEvents.value, filters.value))
const shownTasks = computed(() => (blocked.value || !filters.value.due ? [] : deadlines.items.value.filter((task) => !!task.due_at)))
const byDay = computed(() => entriesByDay(shownEvents.value, shownTasks.value, visible.value, selected.value.zone))
const groups = computed(() => days.value.map((day) => ({ day, entries: byDay.value.get(day) ?? [] })).filter((group) => group.entries.length))
const empty = computed(() => [...byDay.value.values()].every((list) => !list.length))
const filtered = computed(() => !filters.value.timed || !filters.value.allDay || !filters.value.due)
const writable = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? '') && !blocked.value)

// 今天与“现在”线：每分钟更新一次
const now = ref(Date.now())
const timer = window.setInterval(() => { now.value = Date.now() }, 60000)
onUnmounted(() => window.clearInterval(timer))
const today = computed(() => dateInZone(new Date(now.value).toISOString(), selected.value.zone))

// 三栏 / 两栏 / 单栏按日历区域自己的宽度（侧栏或 AI 抽屉展开时内容区会变窄）；量不到宽度（测试环境）按宽屏
const root = ref<HTMLElement | null>(null), width = ref(0)
let observer: ResizeObserver | undefined
onMounted(() => {
  const measure = () => { width.value = root.value?.getBoundingClientRect().width ?? 0 }
  measure()
  observer = new ResizeObserver(measure)
  if (root.value) observer.observe(root.value)
})
onUnmounted(() => observer?.disconnect())
const tier = computed(() => (!width.value || width.value >= 1120 ? 'wide' : width.value >= 860 ? 'medium' : 'compact'))

// 选中的日程：宽屏在右栏显示详情，否则在抽屉里显示
const selectedId = ref<string | null>(null), detailOpen = ref(false)
const selectedEvent = computed(() => allEvents.value.find((event) => event.id === selectedId.value) ?? null)
const opened = ref<Schema['CalendarEventOut'] | 'new' | null>(null)
const dialogTitle = computed(() => (opened.value ? (opened.value === 'new' ? '新建日程' : '编辑日程') : detailOpen.value && tier.value !== 'wide' && selectedEvent.value ? '日程详情' : ''))
const feedback = computed(() => (error.value ? errorMessage(error.value) : ''))
watch(blocked, (value) => { if (value) { opened.value = null; detailOpen.value = false; selectedId.value = null } })
watch(tier, (value) => { if (value === 'wide') detailOpen.value = false })
// 顶栏“创建 → 新建日程”（规范 §8.1）
useCreateIntent(() => writable.value && !busy.value && !needsReload.value, () => { opened.value = 'new' })

function validRange(day: string, timezone: string) {
  if (!validZone(timezone)) throw new Error('请输入有效的显示时区。')
  const span = view.value === 'month' ? monthDates(day) : weekDates(day)
  dayStart(span[0]!, timezone); dayStart(addDays(span.at(-1)!, 1), timezone)
}
function apply() {
  try {
    validRange(date.value, zone.value)
    selected.value = { date: date.value, zone: zone.value }; rangeError.value = ''
  } catch (cause) { rangeError.value = cause instanceof Error ? cause.message : '日期范围无效。' }
}
// 日期输入框选好日期就跳过去（时区仍需点“应用”）
function jump() { if (/^\d{4}-\d{2}-\d{2}$/.test(date.value)) { zone.value = selected.value.zone; apply() } }
watch(() => route.query.date, (value) => {
  if (typeof value !== 'string') return
  try { date.value = addDays(value, 0); zone.value = selected.value.zone; apply() } catch { /* Invalid route dates leave the current range unchanged. */ }
})
function pick(day: string) { date.value = day; zone.value = selected.value.zone; apply() }
function move(offset: number) { pick(view.value === 'month' ? addMonths(selected.value.date, offset) : addDays(selected.value.date, offset * 7)) }
function setView(next: View) { view.value = next; pick(selected.value.date) }
function openWeek(day: string) { view.value = 'week'; pick(day) }
const dayAgenda = ref<InstanceType<typeof DayAgenda> | null>(null)
// “另有 N 项”：选中那一天，把焦点移到选中日期的列表
async function showDay(day: string) { pick(day); await nextTick(); dayAgenda.value?.focus() }
// 点日程：同一区间内顺便把选中日期移到那一天（月视图里点相邻月份的格子不跳月）
function select(eventId: string, day?: string) {
  selectedId.value = eventId
  if (day && (view.value === 'month' ? day.slice(0, 7) === selected.value.date.slice(0, 7) : days.value.includes(day))) { date.value = day; selected.value = { ...selected.value, date: day } }
  if (tier.value !== 'wide') detailOpen.value = true
}
async function edit() {
  if (!selectedEvent.value || !writable.value) return
  opened.value = selectedEvent.value
  if (tier.value !== 'wide') { await nextTick(); document.querySelector<HTMLElement>('[role="dialog"] input')?.focus() }
}
function closeDialog() { if (busy.value) return; opened.value = null; detailOpen.value = false }
async function refresh() { await Promise.all([events.query.refetch(), filters.value.due && !blocked.value ? deadlines.query.refetch() : undefined]) }
async function reset() { if (await reload()) { opened.value = null; detailOpen.value = false } }
async function save(body: Schema['CalendarEventCreate']) {
  const current = opened.value
  if (!current || !writable.value) return
  if (current === 'new') {
    if (await run(async () => dataOf(await auth.api.POST('/api/v1/calendar/events', { params: { header }, body })), '日程已保存')) { opened.value = null; detailOpen.value = false }
    return
  }
  const patch: Schema['CalendarEventUpdate'] = { version: current.version }
  for (const key of ['title', 'description', 'location', 'all_day', 'timezone', 'start_at', 'end_at', 'start_date', 'end_date'] as const) {
    if (body[key] !== undefined && body[key] !== current[key]) Object.assign(patch, { [key]: body[key] })
  }
  if (Object.keys(patch).length === 1) { message.value = '没有需要保存的修改'; return }
  if (await run(async () => dataOf(await auth.api.PATCH('/api/v1/calendar/events/{event_id}', { params: { header, path: { event_id: current.id } }, body: patch })), '日程已保存')) { opened.value = null; detailOpen.value = false }
}
async function remove() {
  const current = opened.value
  if (!current || current === 'new' || !writable.value || !window.confirm(`确认删除日程“${current.title}”？此操作无法撤销。`)) return
  if (await run(() => auth.api.DELETE('/api/v1/calendar/events/{event_id}', { params: { header, path: { event_id: current.id }, query: { version: current.version } } }), '日程已删除')) { opened.value = null; detailOpen.value = false; selectedId.value = null }
}

const title = computed(() => (view.value === 'month' ? monthTitle(selected.value.date) : rangeTitle(days.value[0]!, days.value[6]!)))
const offset = computed(() => zoneOffset(selected.value.zone, selected.value.date))
const eventsError = computed(() => (events.query.isFetchNextPageError.value ? null : events.query.error.value))
</script>

<template>
  <div class="cal-page">
    <div ref="root" class="cal-frame" :class="`tier-${tier}`">
      <header class="ws-header">
        <div><h1>日历</h1><p>日程与任务截止时间，统一安排。</p></div>
        <div class="ws-actions">
          <button type="button" class="secondary-button" aria-label="刷新日历" :disabled="events.query.isFetching.value || busy" @click="refresh"><RefreshCw aria-hidden="true" />刷新</button>
          <button v-if="writable" type="button" class="primary-button" :disabled="busy || needsReload" @click="opened = 'new'"><Plus aria-hidden="true" />新建日程</button>
        </div>
      </header>

      <form class="ws-panel cal-toolbar" aria-label="日历范围" novalidate @submit.prevent="apply">
        <div class="cal-nav">
          <button type="button" class="cal-icon-button" :aria-label="view === 'month' ? '上个月' : '上一周'" @click="move(-1)"><ChevronLeft aria-hidden="true" /></button>
          <button type="button" class="secondary-button cal-today" @click="pick(today)">今天</button>
          <button type="button" class="cal-icon-button" :aria-label="view === 'month' ? '下个月' : '下一周'" @click="move(1)"><ChevronRight aria-hidden="true" /></button>
          <h2 class="cal-title" aria-live="polite">{{ title }}</h2>
        </div>
        <div class="cal-tools">
          <div class="ws-segment" role="group" aria-label="日历视图">
            <button type="button" aria-label="月视图" :aria-pressed="view === 'month'" @click="setView('month')">月</button>
            <button type="button" aria-label="周视图" :aria-pressed="view === 'week'" @click="setView('week')">周</button>
            <button type="button" aria-label="日程列表" :aria-pressed="view === 'agenda'" @click="setView('agenda')">日程</button>
          </div>
          <label class="cal-input"><CalendarDays aria-hidden="true" /><input v-model="date" type="date" aria-label="查看日期" @change="jump"></label>
          <label class="cal-input cal-zone">
            <Clock aria-hidden="true" /><input v-model="zone" maxlength="64" list="calendar-zones" aria-label="显示时区" autocomplete="off">
            <span class="cal-offset" :title="`显示时区 ${selected.zone}`">{{ offset }}</span>
          </label>
          <datalist id="calendar-zones"><option>Asia/Shanghai</option><option>UTC</option><option>America/New_York</option><option>Europe/London</option></datalist>
          <button type="submit" class="secondary-button cal-apply" aria-label="应用日历范围">应用</button>
        </div>
        <p v-if="rangeError" role="alert" class="cal-range-error">{{ rangeError }}</p>
      </form>

      <MutationNotice v-if="!dialogTitle" :error="feedback" :message="message" :needs-reload="needsReload" :busy="busy" reload-label="重新加载日历" @reload="reset" />
      <DataState :loading="events.query.isPending.value" :error="eventsError" :empty="empty" :empty-text="filtered ? '此时间范围没有符合“显示内容”筛选的项目。' : '此时间范围暂无日程。'" :stale="!!allEvents.length" @retry="events.query.refetch()" />
      <div class="cal-status-lines">
        <p v-if="events.loadingMore.value && !blocked" role="status" class="cal-status"><LoaderCircle class="cal-spin" aria-hidden="true" />正在继续加载日程（已加载 {{ events.items.value.length }} 条）…</p>
        <p v-else-if="events.incomplete.value && !blocked" role="alert" class="cal-status is-warning">
          <TriangleAlert aria-hidden="true" />日程未加载完：已加载 {{ events.items.value.length }} 条，{{ events.capped.value ? '这段时间还有更多' : `后续页读取失败（${errorMessage(events.query.error.value)}）` }}。
          <button type="button" class="secondary-button" :disabled="events.query.isFetching.value" @click="events.loadMore()">继续加载日程</button>
        </p>
        <template v-if="filters.due && !blocked">
          <p v-if="deadlines.query.isError.value && !deadlines.query.isFetchNextPageError.value" role="alert" class="cal-status is-warning">
            <TriangleAlert aria-hidden="true" />任务截止日期暂时无法加载：{{ errorMessage(deadlines.query.error.value) }}
            <button type="button" class="secondary-button" @click="deadlines.query.refetch()">重试</button>
          </p>
          <p v-else-if="deadlines.loadingMore.value" role="status" class="cal-status"><LoaderCircle class="cal-spin" aria-hidden="true" />正在继续加载任务截止（已加载 {{ deadlines.items.value.length }} 条）…</p>
          <p v-else-if="deadlines.incomplete.value" role="alert" class="cal-status is-warning">
            <TriangleAlert aria-hidden="true" />任务截止未加载完：已加载 {{ deadlines.items.value.length }} 条，{{ deadlines.capped.value ? '这段时间还有更多' : `后续页读取失败（${errorMessage(deadlines.query.error.value)}）` }}。
            <button type="button" class="secondary-button" :disabled="deadlines.query.isFetching.value" @click="deadlines.loadMore()">继续加载任务截止</button>
          </p>
        </template>
      </div>

      <div class="cal-layout">
        <CalendarSidePanel v-model:filters="filters" class="cal-area-side" :selected="selected.date" :today="today" :range-start="visible.start" :range-end="visible.end" @pick="pick" />
        <div class="cal-area-main">
          <WeekTimeGrid v-if="view === 'week'" :events="shownEvents" :tasks="shownTasks" :range="visible" :zone="selected.zone" :today="today" :selected-date="selected.date" :selected-event-id="selectedId" :now="now" :scroll-key="`${visible.start}|${selected.zone}`" :ready="!events.query.isPending.value" @select="select" @pick="pick" @show-day="showDay" />
          <MonthGrid v-else-if="view === 'month'" :days="days" :month="selected.date.slice(0, 7)" :today="today" :selected-date="selected.date" :by-day="byDay" :zone="selected.zone" :selected-event-id="selectedId" @select="select" @open-week="openWeek" />
          <AgendaList v-else :groups="groups" :zone="selected.zone" :today="today" :selected-event-id="selectedId" @select="select" />
        </div>
        <div v-if="view !== 'agenda' || tier === 'wide'" class="cal-area-aside">
          <!-- 日程列表视图已逐天列出，不再重复“选中日期”列表；宽屏仍在右栏显示详情 -->
          <DayAgenda v-if="view !== 'agenda'" ref="dayAgenda" :date="selected.date" :entries="byDay.get(selected.date) ?? []" :zone="selected.zone" :today="today" :selected-event-id="selectedId" @select="select($event)" />
          <EventDetails v-if="tier === 'wide'" :event="selectedEvent" :zone="selected.zone" :writable="writable" :disabled="busy || needsReload" @edit="edit" />
        </div>
      </div>
    </div>

    <!-- 对话框放在尺寸容器（cal-frame）外面，fixed 定位不受容器影响 -->
    <ModalPanel v-if="dialogTitle" :class="{ 'cal-drawer': tier !== 'wide' }" :title="dialogTitle" :busy="busy" @close="closeDialog">
      <template v-if="opened">
        <CalendarForm :event="opened === 'new' ? undefined : opened" :date="selected.date" :zone="selected.zone" :writable="writable" :busy="busy" :locked="needsReload" @save="save" @remove="remove" />
      </template>
      <EventDetails v-else bare :event="selectedEvent" :zone="selected.zone" :writable="writable" :disabled="busy || needsReload" @edit="edit" />
      <MutationNotice :error="feedback" :message="message" :needs-reload="needsReload" :busy="busy" reload-label="重新加载日历" @reload="reset" />
    </ModalPanel>
  </div>
</template>
