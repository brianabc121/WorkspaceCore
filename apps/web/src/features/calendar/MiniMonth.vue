<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight } from '@lucide/vue'
import { addDays, addMonths, monthDates } from './time'
import { fullDate, monthTitle, weekday } from './labels'

// 左侧小月历（T-0169）：点日期跳到那一天；今天（圆圈）与选中日期（实心）分开标识，当前显示的区间浅色底。
// 方向键在日期间移动（↑↓ 一周，←→ 一天，PageUp / PageDown 一个月），回车或空格选中。
const props = defineProps<{ selected: string; today: string; rangeStart: string; rangeEnd: string }>()
const emit = defineEmits<{ pick: [date: string] }>()
const shown = ref(props.selected.slice(0, 7))
watch(() => props.selected, (date) => { shown.value = date.slice(0, 7); focusable.value = date })
const days = computed(() => monthDates(`${shown.value}-01`))
const focusable = ref(props.selected)
const tabStop = computed(() => (days.value.includes(focusable.value) && focusable.value.startsWith(shown.value) ? focusable.value : `${shown.value}-01`))
const table = ref<HTMLElement | null>(null)
function shift(months: number) { shown.value = addMonths(`${shown.value}-01`, months).slice(0, 7) }
async function move(date: string) {
  focusable.value = date
  if (!date.startsWith(shown.value)) shown.value = date.slice(0, 7)
  await nextTick()
  table.value?.querySelector<HTMLElement>(`[data-date="${date}"]`)?.focus()
}
function keys(event: KeyboardEvent, date: string) {
  const steps: Record<string, () => string> = { ArrowLeft: () => addDays(date, -1), ArrowRight: () => addDays(date, 1), ArrowUp: () => addDays(date, -7), ArrowDown: () => addDays(date, 7), PageUp: () => addMonths(date, -1), PageDown: () => addMonths(date, 1) }
  const step = steps[event.key]
  if (!step) return
  event.preventDefault()
  void move(step())
}
function label(date: string) { return `${fullDate(date)} ${weekday(date)}${date === props.today ? '，今天' : ''}` }
</script>

<template>
  <div class="cal-mini">
    <div class="cal-mini-head">
      <button type="button" class="cal-icon-button" aria-label="小月历：上个月" @click="shift(-1)"><ChevronLeft aria-hidden="true" /></button>
      <h2 :id="`cal-mini-${shown}`" aria-live="polite">{{ monthTitle(`${shown}-01`) }}</h2>
      <button type="button" class="cal-icon-button" aria-label="小月历：下个月" @click="shift(1)"><ChevronRight aria-hidden="true" /></button>
    </div>
    <table ref="table" role="grid" :aria-labelledby="`cal-mini-${shown}`">
      <thead>
        <tr><th v-for="name in ['一', '二', '三', '四', '五', '六', '日']" :key="name" scope="col" :abbr="`周${name}`">{{ name }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="week in 6" :key="week">
          <td v-for="day in days.slice((week - 1) * 7, week * 7)" :key="day" role="gridcell" :aria-selected="day === selected">
            <button
              type="button"
              class="cal-mini-day"
              :class="{ 'is-outside': !day.startsWith(shown), 'is-today': day === today, 'is-selected': day === selected, 'in-range': day >= rangeStart && day < rangeEnd }"
              :data-date="day"
              :tabindex="day === tabStop ? 0 : -1"
              :aria-label="label(day)"
              :aria-current="day === today ? 'date' : undefined"
              @click="focusable = day; emit('pick', day)"
              @keydown="keys($event, day)"
            >
              {{ Number(day.slice(8)) }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
