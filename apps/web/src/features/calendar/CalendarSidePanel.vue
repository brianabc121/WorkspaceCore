<script setup lang="ts">
import { CalendarDays, Flag, Sun } from '@lucide/vue'
import MiniMonth from './MiniMonth.vue'
import type { CalendarFilters } from './weekLayout'

// 左栏（T-0169）：小月历 + “显示内容”筛选。筛选只列实际存在的数据来源：日程、任务截止、全天事件
// （效果图里的“提醒”没有数据来源，本轮不做提醒推送，所以不放这个开关）。
defineProps<{ selected: string; today: string; rangeStart: string; rangeEnd: string }>()
const filters = defineModel<CalendarFilters>('filters', { required: true })
defineEmits<{ pick: [date: string] }>()
const options = [
  { key: 'timed', label: '日程', icon: CalendarDays, kind: 'kind-timed' },
  { key: 'due', label: '任务截止', icon: Flag, kind: 'kind-due' },
  { key: 'allDay', label: '全天事件', icon: Sun, kind: 'kind-allday' },
] as const
function toggle(key: keyof CalendarFilters, value: boolean) { filters.value = { ...filters.value, [key]: value } }
</script>

<template>
  <section class="ws-panel cal-side" aria-label="小月历与显示内容">
    <MiniMonth :selected="selected" :today="today" :range-start="rangeStart" :range-end="rangeEnd" @pick="$emit('pick', $event)" />
    <fieldset class="cal-filters">
      <legend>显示内容</legend>
      <label v-for="option in options" :key="option.key" class="cal-filter" :class="option.kind">
        <input type="checkbox" :checked="filters[option.key]" @change="toggle(option.key, ($event.target as HTMLInputElement).checked)">
        <span class="cal-filter-swatch" aria-hidden="true"><component :is="option.icon" /></span>
        <span>{{ option.label }}</span>
      </label>
    </fieldset>
  </section>
</template>
