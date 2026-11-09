<script setup lang="ts">
import { ref, useId } from 'vue'
import { CircleCheck, Flag } from '@lucide/vue'
import type { DayEntry } from './weekLayout'
import { allDayRange, clock, monthDay, timedRange, weekday } from './labels'

// 选中日期的日程列表（T-0169，效果图右上）：全天在前，其余按时间；任务截止只读，点击去任务页。
const props = defineProps<{ date: string; entries: DayEntry[]; zone: string; today: string; selectedEventId: string | null }>()
defineEmits<{ select: [eventId: string] }>()
const id = useId(), heading = ref<HTMLElement | null>(null)
defineExpose({ focus: () => heading.value?.focus() })
// 跨日日程在后一天从 00:00 算起；全天日程不写时刻
const time = (entry: DayEntry) => (entry.kind === 'event' && entry.allDay ? '全天' : clock(entry.start, props.zone))
</script>

<template>
  <section class="ws-panel cal-day" :aria-labelledby="id">
    <h2 :id="id" ref="heading" tabindex="-1" class="cal-day-heading">
      {{ monthDay(date) }} {{ weekday(date) }}<span v-if="date === today" class="cal-today-tag">今天</span>
    </h2>
    <ol v-if="entries.length" class="cal-day-list">
      <li v-for="entry in entries" :key="entry.key">
        <button
          v-if="entry.kind === 'event'"
          type="button"
          class="cal-day-item"
          :class="[entry.allDay ? 'kind-allday' : 'kind-timed', { 'is-selected': entry.event.id === selectedEventId }]"
          :aria-current="entry.event.id === selectedEventId ? 'true' : undefined"
          @click="$emit('select', entry.event.id)"
        >
          <span class="cal-day-dot" aria-hidden="true"></span>
          <span class="cal-day-time">{{ time(entry) }}</span>
          <span class="cal-day-text"><strong>{{ entry.event.title }}</strong><small>{{ entry.allDay ? allDayRange(entry.event) : timedRange(entry.event, zone) }}</small></span>
        </button>
        <RouterLink v-else class="cal-day-item kind-due" :class="{ 'is-done': entry.task.done }" :to="{ path: '/tasks', query: { task: entry.task.id } }" :aria-label="`任务截止：${entry.task.title}`">
          <span class="cal-day-dot" aria-hidden="true"><CircleCheck v-if="entry.task.done" /><Flag v-else /></span>
          <span class="cal-day-time">{{ time(entry) }}</span>
          <span class="cal-day-text"><strong>{{ entry.task.title }}</strong><small>任务截止 · 只读{{ entry.task.done ? ' · 已完成' : '' }}</small></span>
        </RouterLink>
      </li>
    </ol>
    <p v-else class="cal-empty">这一天没有日程。</p>
  </section>
</template>
