<script setup lang="ts">
import { computed, useId } from 'vue'
import { CalendarDays, Clock, FileText, MapPin, Pencil } from '@lucide/vue'
import type { CalendarEvent } from './weekLayout'
import { eventWhen } from './labels'
import { localDateTime } from './time'

// 日程详情（T-0169，效果图右下）：标题、时间、位置、备注；“编辑日程”打开现有的 CalendarForm。
// 日程（CalendarEventOut）没有项目字段，所以不显示“查看关联项目”，也不从标题猜项目。
// bare：放在抽屉（对话框）里时不再套面板和“日程详情”标题。
const props = defineProps<{ event: CalendarEvent | null; zone: string; writable: boolean; disabled?: boolean; bare?: boolean }>()
defineEmits<{ edit: [] }>()
const id = useId()
// 事件时区与显示时区不同时，另写一行事件时区里的时间，避免误解
const ownZone = computed(() => {
  const event = props.event
  if (!event || event.timezone === props.zone) return ''
  if (event.all_day) return `按事件时区 ${event.timezone} 的日期`
  const start = localDateTime(event.start_at, event.timezone), end = localDateTime(event.end_at, event.timezone)
  return `事件时区 ${event.timezone}：${start.replace('T', ' ')} – ${start.slice(0, 10) === end.slice(0, 10) ? end.slice(11) : end.replace('T', ' ')}`
})
</script>

<template>
  <section :class="bare ? 'cal-details is-bare' : 'ws-panel cal-details'" :aria-labelledby="id">
    <h2 v-if="!bare" :id="id" class="cal-details-heading">日程详情</h2>
    <template v-if="event">
      <h3 :id="bare ? id : undefined" class="cal-details-title" aria-live="polite"><CalendarDays aria-hidden="true" /><span>{{ event.title }}</span></h3>
      <ul class="cal-facts">
        <li>
          <Clock aria-hidden="true" /><span class="sr-only">时间：</span>
          <div><p>{{ eventWhen(event, zone) }}</p><p v-if="ownZone" class="cal-muted">{{ ownZone }}</p></div>
        </li>
        <li><MapPin aria-hidden="true" /><span class="sr-only">地点：</span><p :class="{ 'cal-muted': !event.location }">{{ event.location || '未填写地点' }}</p></li>
        <li><FileText aria-hidden="true" /><span class="sr-only">备注：</span><p class="note-text" :class="{ 'cal-muted': !event.description }">{{ event.description || '没有备注' }}</p></li>
      </ul>
      <button v-if="writable" type="button" class="primary-button cal-edit" :disabled="disabled" @click="$emit('edit')"><Pencil aria-hidden="true" />编辑日程</button>
      <p v-else class="cal-muted">当前角色只读，不能修改日程。</p>
    </template>
    <p v-else class="cal-empty">在日历中选择一个日程，查看时间、地点与备注。</p>
  </section>
</template>
