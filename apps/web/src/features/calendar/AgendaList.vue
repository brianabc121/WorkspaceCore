<script setup lang="ts">
import { CircleCheck, Flag, MapPin } from '@lucide/vue'
import type { DayEntry } from './weekLayout'
import { allDayRange, clock, monthDay, timedRange, weekday } from './labels'

// 日程列表视图（T-0169）：本周有内容的日子逐天列出；手机与 1024 以下默认用它。
defineProps<{ groups: { day: string; entries: DayEntry[] }[]; zone: string; today: string; selectedEventId: string | null }>()
defineEmits<{ select: [eventId: string, date: string] }>()
</script>

<template>
  <div class="cal-agenda">
    <section v-for="group in groups" :key="group.day" class="ws-panel cal-agenda-day" :aria-label="`${monthDay(group.day)} ${weekday(group.day)}`">
      <h2 class="cal-agenda-date">
        <span>{{ monthDay(group.day) }}</span><small>{{ weekday(group.day) }}</small><span v-if="group.day === today" class="cal-today-tag">今天</span>
      </h2>
      <ul class="cal-agenda-list">
        <li v-for="entry in group.entries" :key="entry.key">
          <article v-if="entry.kind === 'event'" class="cal-agenda-row" :class="[entry.allDay ? 'kind-allday' : 'kind-timed', { 'is-selected': entry.event.id === selectedEventId }]">
            <span class="cal-agenda-time">{{ entry.allDay ? '全天' : timedRange(entry.event, zone) }}</span>
            <div class="cal-agenda-main">
              <button type="button" class="cal-agenda-title" @click="$emit('select', entry.event.id, group.day)">{{ entry.event.title }}</button>
              <p v-if="entry.allDay && (entry.startsBefore || entry.endsAfter)" class="cal-muted">{{ allDayRange(entry.event) }}</p>
              <p v-if="entry.event.location" class="cal-muted cal-agenda-place"><MapPin aria-hidden="true" />{{ entry.event.location }}</p>
            </div>
          </article>
          <article v-else class="cal-agenda-row kind-due" :class="{ 'is-done': entry.task.done }" :aria-label="`任务截止：${entry.task.title}`">
            <span class="cal-agenda-time">{{ clock(entry.start, zone) }} 截止</span>
            <div class="cal-agenda-main">
              <RouterLink class="cal-agenda-title" :to="{ path: '/tasks', query: { task: entry.task.id } }"><CircleCheck v-if="entry.task.done" aria-hidden="true" /><Flag v-else aria-hidden="true" />{{ entry.task.title }}</RouterLink>
              <p class="cal-muted">任务截止 · 只读（在任务页修改）<span v-if="entry.task.done" class="cal-done-tag">已完成</span></p>
            </div>
          </article>
        </li>
      </ul>
    </section>
  </div>
</template>
