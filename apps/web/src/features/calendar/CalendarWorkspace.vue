<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, isAccessError, type Schema } from '../../services/api'
import { useResourceMutation } from '../resources/mutation'
import { addDays, browserZone, dateInZone, eventDates, eventTime, validZone, weekDates, zonedInstant } from './time'
import DataState from '../../components/DataState.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import MutationNotice from '../resources/MutationNotice.vue'
import CalendarForm from './CalendarForm.vue'
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const { run, reload, busy, error, needsReload, accessDenied, message } = useResourceMutation(scope, ['calendar', 'overview'])
const route = useRoute(), initialZone = browserZone()
function initialDate() { try { return typeof route.query.date === 'string' ? addDays(route.query.date, 0) : dateInZone(new Date().toISOString(), initialZone) } catch { return dateInZone(new Date().toISOString(), initialZone) } }
const date = ref(initialDate()), zone = ref(initialZone)
const selected = ref({ date: date.value, zone: zone.value }), rangeError = ref(''), view = ref<'agenda' | 'week'>('agenda')
const days = computed(() => weekDates(selected.value.date))
// All-day events retain their civil dates even when their event timezone is on
// the opposite side of the date line. Pad reads, then filter the visible days.
const range = computed(() => ({ start: new Date(Date.parse(zonedInstant(`${days.value[0]}T00:00`, selected.value.zone)) - 2 * 86400000).toISOString(), end: new Date(Date.parse(zonedInstant(`${addDays(days.value[6]!, 1)}T00:00`, selected.value.zone)) + 2 * 86400000).toISOString() }))
const query = useInfiniteQuery({ queryKey: computed(() => ['calendar', user, scope, range.value]), initialPageParam: null as string | null, queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/calendar/events', { params: { header, query: { ...range.value, limit: 100, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })), getNextPageParam: (page) => page.next_cursor ?? undefined })
const blocked = computed(() => accessDenied.value || isAccessError(query.error.value))
const items = computed(() => blocked.value ? [] : (query.data.value?.pages.flatMap((p) => p.items) ?? []).filter((event) => eventDates(event, selected.value.zone).some((day) => days.value.includes(day))))
const groups = computed(() => days.value.map((day) => ({ day, events: items.value.filter((event) => eventDates(event, selected.value.zone).includes(day)) })).filter((group) => view.value === 'week' || group.events.length))
const writable = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? '') && !blocked.value)
const opened = ref<Schema['CalendarEventOut'] | 'new' | null>(null)
const feedback = computed(() => error.value ? errorMessage(error.value) : '')
watch(blocked, (value) => { if (value) opened.value = null })
function apply() {
  try {
    if (!validZone(zone.value)) throw new Error('请输入有效的显示时区。')
    const week = weekDates(date.value)
    zonedInstant(`${week[0]}T00:00`, zone.value); zonedInstant(`${addDays(week[6]!, 1)}T00:00`, zone.value)
    selected.value = { date: date.value, zone: zone.value }; rangeError.value = ''
  } catch (cause) { rangeError.value = cause instanceof Error ? cause.message : '日期范围无效。' }
}
watch(() => route.query.date, (value) => {
  if (typeof value !== 'string') return
  try { date.value = addDays(value, 0); zone.value = selected.value.zone; apply() } catch { /* Invalid route dates leave the current range unchanged. */ }
})
function move(offset: number) { date.value = addDays(selected.value.date, offset); zone.value = selected.value.zone; apply() }
async function reset() { if (await reload()) opened.value = null }
async function save(body: Schema['CalendarEventCreate']) {
  const current = opened.value
  if (!current || !writable.value) return
  if (current === 'new') {
    if (await run(async () => dataOf(await auth.api.POST('/api/v1/calendar/events', { params: { header }, body })), '日程已保存')) opened.value = null
    return
  }
  const patch: Schema['CalendarEventUpdate'] = { version: current.version }
  for (const key of ['title', 'description', 'location', 'all_day', 'timezone', 'start_at', 'end_at', 'start_date', 'end_date'] as const) {
    if (body[key] !== undefined && body[key] !== current[key]) Object.assign(patch, { [key]: body[key] })
  }
  if (Object.keys(patch).length === 1) { message.value = '没有需要保存的修改'; return }
  if (await run(async () => dataOf(await auth.api.PATCH('/api/v1/calendar/events/{event_id}', { params: { header, path: { event_id: current.id } }, body: patch })), '日程已保存')) opened.value = null
}
async function remove() {
  const current = opened.value
  if (!current || current === 'new' || !writable.value || !window.confirm(`确认删除日程“${current.title}”？此操作无法撤销。`)) return
  if (await run(() => auth.api.DELETE('/api/v1/calendar/events/{event_id}', { params: { header, path: { event_id: current.id }, query: { version: current.version } } }), '日程已删除')) opened.value = null
}
</script>
<template>
  <div class="task-toolbar"><div class="form-actions"><button class="secondary-button" :aria-pressed="view === 'agenda'" @click="view = 'agenda'">日程列表</button><button class="secondary-button" :aria-pressed="view === 'week'" @click="view = 'week'">周视图</button><button class="secondary-button" :disabled="query.isFetching.value || busy" @click="query.refetch()">刷新日历</button></div><button v-if="writable" class="primary-button" :disabled="busy || needsReload" @click="opened = 'new'">新建日程</button></div>
  <form class="panel task-filters" aria-label="日历范围" @submit.prevent="apply"><label>查看日期<input v-model="date" type="date"></label><label>显示时区<input v-model="zone" maxlength="64" list="calendar-zones"></label><datalist id="calendar-zones"><option>Asia/Shanghai</option><option>UTC</option><option>America/New_York</option><option>Europe/London</option></datalist><button class="secondary-button">应用日历范围</button><p v-if="rangeError" role="alert">{{ rangeError }}</p></form>
  <div class="task-toolbar"><button class="secondary-button" @click="move(-7)">上一周</button><p>{{ days[0] }} — {{ days[6] }} · {{ selected.zone }}</p><button class="secondary-button" @click="move(7)">下一周</button></div>
  <MutationNotice v-if="!opened" :error="feedback" :message="message" :needs-reload="needsReload" :busy="busy" reload-label="重新加载日历" @reload="reset" />
  <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!items.length" empty-text="此时间范围暂无日程。" :stale="!!items.length" @retry="query.isFetchNextPageError.value ? query.fetchNextPage() : query.refetch()" />
  <div :class="view === 'week' ? 'calendar-week' : 'calendar-agenda'"><section v-for="group in groups" :key="group.day" class="panel calendar-day" role="region" :aria-label="group.day"><h2>{{ group.day }}</h2><article v-for="event in group.events" :key="event.id" class="calendar-record"><button class="task-title" @click="opened = event">{{ event.title }}</button><p class="form-help">{{ eventTime(event) }}</p><p class="note-text">{{ event.location }}</p></article><p v-if="!group.events.length" class="form-help">暂无日程</p></section></div>
  <button v-if="query.hasNextPage.value && !blocked" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多日程</button>
  <ModalPanel v-if="opened" :title="opened === 'new' ? '新建日程' : '日程详情'" :busy="busy" @close="!busy && (opened = null)"><CalendarForm :event="opened === 'new' ? undefined : opened" :date="selected.date" :zone="selected.zone" :writable="writable" :busy="busy" :locked="needsReload" @save="save" @remove="remove" /><MutationNotice :error="feedback" :message="message" :needs-reload="needsReload" :busy="busy" reload-label="重新加载日历" @reload="reset" /></ModalPanel>
</template>
