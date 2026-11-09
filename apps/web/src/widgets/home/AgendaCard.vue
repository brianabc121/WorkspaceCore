<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { ArrowRight, Plus } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, type Schema } from '../../services/api'
import { browserZone, dateInZone } from '../../features/calendar/time'
import CalendarForm from '../../features/calendar/CalendarForm.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import type { WidgetValue } from '../registry'
import { dayClock, dayOffset } from './format'
import './home.css'

// 提醒与日程（T-0164，首页效果图）：从现在起 days 天内的日程，按时间排成时间轴，最近的一条高亮；
// “添加提醒”就地新建日程（与日历页同一个表单和接口）
const props = defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
const auth = useAuthStore(), cache = useQueryClient()
const scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const days = computed(() => (props.value.widget_type === 'calendar_agenda' ? props.value.config.days : 7))
const limit = computed(() => (props.value.widget_type === 'calendar_agenda' ? props.value.config.limit : 5))
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer')
const query = useQuery({
  queryKey: computed(() => ['calendar', user, scope, 'widget', limit.value, days.value]),
  // 这个缓存是“从现在起”的移动窗口：每次刷新都从当前时刻算
  queryFn: async ({ signal }) => {
    const start = new Date().toISOString(), end = new Date(Date.now() + days.value * 86400000).toISOString()
    return dataOf(await auth.api.GET('/api/v1/calendar/events', { params: { header, query: { start, end, limit: limit.value } }, signal }))
  },
})
const items = computed(() => (query.data.value?.items ?? []).slice(0, limit.value))
const zone = browserZone()
function when(event: Schema['CalendarEventOut']) {
  if (!event.all_day) return dayClock(event.start_at)
  const date = event.start_date ?? dateInZone(event.start_at, zone)
  const offset = dayOffset(new Date(`${date}T00:00`))
  return `${offset === 0 ? '今天' : offset === 1 ? '明天' : `${Number(date.slice(5, 7))}月${Number(date.slice(8, 10))}日`} 全天`
}

const adding = ref(false), busy = ref(false), problem = ref(''), notice = ref('')
const today = dateInZone(new Date().toISOString(), zone)
async function save(body: Schema['CalendarEventCreate']) {
  if (!writable.value || busy.value) return
  busy.value = true; problem.value = ''
  try {
    dataOf(await auth.api.POST('/api/v1/calendar/events', { params: { header }, body }))
    adding.value = false
    notice.value = `已添加：${body.title}`
    await cache.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'calendar' && q.queryKey[2] === scope })
  } catch (error) { problem.value = errorMessage(error) } finally { busy.value = false }
}
</script>

<template>
  <div class="agenda">
    <p v-if="query.isPending.value" role="status" class="card-state">正在加载日程…</p>
    <div v-else-if="query.error.value && !query.data.value" class="card-state"><p role="alert">{{ errorMessage(query.error.value) }}</p><button type="button" class="secondary-button" @click="query.refetch()">重试</button></div>
    <template v-else>
      <ol v-if="items.length" class="agenda-list" aria-label="近期提醒与日程">
        <li v-for="(item, index) in items" :key="`${item.id}:${item.start_at}`" class="agenda-item" :class="{ 'is-next': index === 0 }">
          <span class="agenda-dot" aria-hidden="true"></span>
          <time class="agenda-time" :datetime="item.start_at">{{ when(item) }}</time>
          <span class="agenda-title">{{ item.title }}</span>
        </li>
      </ol>
      <p v-else class="card-state">{{ days }} 天内没有日程。</p>
      <p v-if="notice" role="status" class="card-state">{{ notice }}</p>
      <div class="agenda-footer">
        <RouterLink class="home-more" to="/calendar">查看日历<ArrowRight aria-hidden="true" /></RouterLink>
        <button v-if="writable" type="button" class="secondary-button agenda-add" @click="adding = true; problem = ''"><Plus aria-hidden="true" />添加提醒</button>
      </div>
    </template>
    <ModalPanel v-if="adding" title="添加提醒" :busy="busy" @close="!busy && (adding = false)">
      <CalendarForm :date="today" :zone="zone" :writable="writable" :busy="busy" :locked="false" @save="save" />
      <p v-if="problem" role="alert">{{ problem }}</p>
    </ModalPanel>
  </div>
</template>
