<script setup lang="ts">
import { computed } from 'vue'
import type { DayEntry } from './weekLayout'
import { clock, monthTitle } from './labels'
import { localDateTime } from './time'

// 月视图（T-0085，T-0169 换成新样式）：6 周 42 天；点日期进入那一周，点日程看详情，任务截止去任务页。
// 用表格语义（不是 grid：grid 需要方向键导航）；格子里的按钮与链接按 Tab 顺序访问（T-0086）。
const props = defineProps<{ days: string[]; month: string; today: string; selectedDate: string; byDay: Map<string, DayEntry[]>; zone: string; selectedEventId: string | null }>()
defineEmits<{ select: [eventId: string, date: string]; openWeek: [date: string] }>()
const SHOWN = 3
const title = computed(() => monthTitle(`${props.month}-01`))
const cells = computed(() => props.days.map((day) => {
  const entries = props.byDay.get(day) ?? []
  return { day, entries, events: entries.filter((entry) => entry.kind === 'event').length, due: entries.filter((entry) => entry.kind === 'task').length }
}))
</script>

<template>
  <div class="ws-panel cal-month" role="table" :aria-label="`${title}月历`">
    <div class="cal-month-head" role="row"><span v-for="name in ['一', '二', '三', '四', '五', '六', '日']" :key="name" role="columnheader">周{{ name }}</span></div>
    <div v-for="week in 6" :key="week" class="cal-month-row" role="row">
      <div
        v-for="cell in cells.slice((week - 1) * 7, week * 7)"
        :key="cell.day"
        role="cell"
        class="cal-month-cell"
        :class="{ 'is-outside': !cell.day.startsWith(month), 'is-today': cell.day === today, 'is-selected': cell.day === selectedDate }"
        :aria-label="`${cell.day}，${cell.events} 个日程${cell.due ? `，${cell.due} 个任务截止` : ''}`"
      >
        <button type="button" class="cal-month-date" :aria-label="`查看 ${cell.day} 所在的一周`" @click="$emit('openWeek', cell.day)">{{ Number(cell.day.slice(8)) }}</button>
        <template v-for="entry in cell.entries.slice(0, SHOWN)" :key="entry.key">
          <button
            v-if="entry.kind === 'event'"
            type="button"
            class="cal-chip"
            :class="[entry.allDay ? 'kind-allday' : 'kind-timed', { 'is-selected': entry.event.id === selectedEventId }]"
            :title="entry.event.title"
            @click="$emit('select', entry.event.id, cell.day)"
          >
            {{ entry.allDay || entry.startsBefore ? '' : `${localDateTime(entry.event.start_at, zone).slice(11)} ` }}{{ entry.event.title }}
          </button>
          <RouterLink v-else class="cal-chip kind-due" :class="{ 'is-done': entry.task.done }" :to="{ path: '/tasks', query: { task: entry.task.id } }" :aria-label="`任务截止：${entry.task.title}`" :title="`截止 ${clock(entry.task.due_at!, zone)} · ${entry.task.title}`">截止 {{ entry.task.title }}</RouterLink>
        </template>
        <button v-if="cell.entries.length > SHOWN" type="button" class="cal-month-more" @click="$emit('openWeek', cell.day)">还有 {{ cell.entries.length - SHOWN }} 项</button>
        <!-- 手机上只显示圆点（不可点，点日期进入那一周） -->
        <span v-if="cell.entries.length" class="cal-dots" aria-hidden="true"><i v-for="n in Math.min(cell.events, 3)" :key="`e${n}`"></i><i v-for="n in Math.min(cell.due, Math.max(0, 3 - cell.events))" :key="`d${n}`" class="is-due"></i></span>
      </div>
    </div>
  </div>
</template>
