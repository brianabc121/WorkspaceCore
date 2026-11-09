<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import { CircleCheck, Flag, Sun } from '@lucide/vue'
import { DAY_MINUTES, layoutWeekEvents, wallMinutes, type CalendarEvent, type CollapsedGroup, type DateRange, type DueTask, type LaneItem, type TimedSegment } from './weekLayout'
import { clock, fullDate, isWeekend, monthDay, weekday } from './labels'
import { dateInZone, localDateTime } from './time'

// 周视图七天时间画布（T-0169）：顶部全天区域（全天日程与只读的任务截止），下面按时间定位的日程块。
// 日程块点一下在右侧（或抽屉里）显示详情；任务截止是链接，去任务页修改，不走日程编辑接口。
const props = defineProps<{ events: CalendarEvent[]; tasks: DueTask[]; range: DateRange; zone: string; today: string; selectedDate: string; selectedEventId: string | null; now: number; scrollKey: string; ready: boolean }>()
const emit = defineEmits<{ select: [eventId: string, date: string]; pick: [date: string]; showDay: [date: string] }>()
const HOUR = 52
const uid = useId()
const hours = Array.from({ length: 23 }, (_, index) => index + 1)

// 一组重叠日程最多并排几列，按每天这一列的实际宽度定（约 40px 一列，2–4 列）；量不到宽度时用 3
const columnsEl = ref<HTMLElement | null>(null), dayWidth = ref(0)
let observer: ResizeObserver | undefined
onMounted(() => {
  const measure = () => { dayWidth.value = (columnsEl.value?.getBoundingClientRect().width ?? 0) / 7 }
  measure()
  observer = new ResizeObserver(measure)
  if (columnsEl.value) observer.observe(columnsEl.value)
})
onUnmounted(() => observer?.disconnect())
const maxColumns = computed(() => (dayWidth.value ? Math.max(2, Math.min(4, Math.floor(dayWidth.value / 40))) : 3))
const layout = computed(() => layoutWeekEvents(props.events, props.range, props.zone, { tasks: props.tasks, maxColumns: maxColumns.value }))

const laneMore = computed(() => layout.value.laneHidden.some((count) => count > 0))
const laneRows = computed(() => Math.max(1, layout.value.laneRows + (laneMore.value ? 1 : 0)))
const nowTop = computed(() => (layout.value.days.some((day) => day.date === props.today) ? (wallMinutes(props.now, props.zone) / 60) * HOUR : null))

function place(top: number, bottom: number, column: number, columns: number, min = 16) {
  return { top: `${(top / 60) * HOUR}px`, height: `${Math.max(((bottom - top) / 60) * HOUR - 2, min)}px`, left: `calc(${(column / columns) * 100}% + 2px)`, width: `calc(${100 / columns}% - 4px)` }
}
const segmentStyle = (segment: TimedSegment) => place(segment.top, segment.bottom, segment.column, segment.columns)
const groupStyle = (group: CollapsedGroup) => place(group.top, Math.min(DAY_MINUTES, group.bottom), group.column, group.columns, 46)
// 不到 45 分钟：标题与时间排一行；不到 80 分钟：标题只留一行，时间不被挤掉
const isShort = (segment: TimedSegment) => segment.bottom - segment.top < 45
const isCompact = (segment: TimedSegment) => segment.bottom - segment.top < 80
// 块上的时间：日程本身的起止；不在这一天的那一端带上日期（跨日日程）。窄块只显示开始时间，完整时间给读屏软件
function segmentTime(segment: TimedSegment): [string, string] {
  const start = localDateTime(segment.event.start_at, props.zone), end = localDateTime(segment.event.end_at, props.zone)
  return [`${segment.startsBefore ? `${monthDay(start.slice(0, 10))} ` : ''}${start.slice(11)}`, `${segment.endsAfter ? `${monthDay(end.slice(0, 10))} ` : ''}${end.slice(11)}`]
}
function dueLabel(item: LaneItem) {
  if (item.kind !== 'task') return ''
  return `截止 ${monthDay(dateInZone(item.task.due_at!, props.zone))} ${clock(item.task.due_at!, props.zone)}${item.task.done ? '（已完成）' : ''}`
}
function dayLabel(date: string, hoursInDay: number) {
  return `${fullDate(date)} ${weekday(date)}${date === props.today ? '，今天' : ''}${hoursInDay !== 24 ? `，这一天有 ${hoursInDay} 小时（夏令时调整）` : ''}`
}

// 换区间或时区后，把时间画布滚到 8 点附近（这周更早有日程就滚到最早的那条）
const body = ref<HTMLElement | null>(null)
let scrolledFor = ''
watch(() => [props.scrollKey, props.ready] as const, async ([key, ready]) => {
  if (!ready || key === scrolledFor) return
  scrolledFor = key
  await nextTick()
  // 从前一天延续过来的那段不算（它总是从 0 点开始）
  const earliest = Math.min(8 * 60, ...layout.value.days.flatMap((day) => day.segments.filter((segment) => !segment.startsBefore).map((segment) => segment.top)))
  if (body.value) body.value.scrollTop = (Math.max(0, earliest - 30) / 60) * HOUR
}, { immediate: true })
</script>

<template>
  <section class="ws-panel cal-week" :aria-label="`${monthDay(layout.days[0]!.date)}—${monthDay(layout.days.at(-1)!.date)} 周视图`" :style="{ '--hour': `${HOUR}px`, '--lane-rows': laneRows }">
    <div class="cal-week-scroll">
      <div class="cal-week-inner">
        <div class="cal-week-head">
          <span class="cal-axis-spacer" aria-hidden="true"></span>
          <button
            v-for="day in layout.days"
            :key="day.date"
            type="button"
            class="cal-day-head"
            :class="{ 'is-today': day.date === today, 'is-selected': day.date === selectedDate, 'is-weekend': isWeekend(day.date) }"
            :aria-label="dayLabel(day.date, day.hours)"
            :aria-pressed="day.date === selectedDate"
            :aria-current="day.date === today ? 'date' : undefined"
            @click="emit('pick', day.date)"
          >
            <span class="cal-day-name">{{ weekday(day.date) }}</span>
            <span class="cal-day-number">{{ Number(day.date.slice(8)) }}</span>
            <span v-if="day.hours !== 24" class="cal-dst" aria-hidden="true">{{ day.hours }} 小时</span>
          </button>
        </div>
        <div class="cal-lane">
          <span class="cal-lane-label">全天</span>
          <div class="cal-lane-grid">
            <span v-for="(day, index) in layout.days" :key="day.date" class="cal-lane-cell" :class="{ 'is-weekend': isWeekend(day.date), 'is-selected': day.date === selectedDate }" :style="{ gridColumn: index + 1 }" aria-hidden="true"></span>
            <template v-for="item in layout.lane" :key="item.key">
              <button
                v-if="item.kind === 'event'"
                type="button"
                class="cal-bar kind-allday"
                :class="{ 'is-selected': item.event.id === selectedEventId, 'starts-before': item.startsBefore, 'ends-after': item.endsAfter }"
                :style="{ gridColumn: `${item.startIndex + 1} / span ${item.span}`, gridRow: item.row + 1 }"
                :aria-label="item.event.title"
                :aria-current="item.event.id === selectedEventId ? 'true' : undefined"
                :title="`${item.event.title}（全天）`"
                @click="emit('select', item.event.id, layout.days[item.startIndex]!.date)"
              >
                <Sun aria-hidden="true" /><span>{{ item.event.title }}</span>
              </button>
              <RouterLink
                v-else
                class="cal-bar kind-due"
                :class="{ 'is-done': item.task.done }"
                :style="{ gridColumn: `${item.startIndex + 1} / span 1`, gridRow: item.row + 1 }"
                :to="{ path: '/tasks', query: { task: item.task.id } }"
                :aria-label="`任务截止：${item.task.title}`"
                :title="`${item.task.title} · ${dueLabel(item)} · 在任务页修改`"
              >
                <CircleCheck v-if="item.task.done" aria-hidden="true" /><Flag v-else aria-hidden="true" /><span>{{ item.task.title }}</span>
              </RouterLink>
            </template>
            <template v-for="(count, index) in layout.laneHidden" :key="`more-${index}`">
              <button v-if="count" type="button" class="cal-lane-more" :style="{ gridColumn: index + 1, gridRow: layout.laneRows + 1 }" :aria-label="`${monthDay(layout.days[index]!.date)} 全天另有 ${count} 项`" @click="emit('showDay', layout.days[index]!.date)">另有 {{ count }} 项</button>
            </template>
          </div>
        </div>
        <div ref="body" class="cal-body">
          <div class="cal-body-inner">
            <div class="cal-axis" aria-hidden="true">
              <span v-for="hour in hours" :key="hour" :style="{ top: `${hour * HOUR}px` }">{{ String(hour).padStart(2, '0') }}:00</span>
            </div>
            <div ref="columnsEl" class="cal-columns">
              <div
                v-for="day in layout.days"
                :key="day.date"
                role="group"
                class="cal-col"
                :class="{ 'is-weekend': isWeekend(day.date), 'is-today': day.date === today, 'is-selected': day.date === selectedDate }"
                :aria-label="`${monthDay(day.date)} ${weekday(day.date)}`"
              >
                <button
                  v-for="segment in day.segments"
                  :key="`${segment.event.id}:${segment.dayIndex}`"
                  type="button"
                  class="cal-block"
                  :class="{ 'is-selected': segment.event.id === selectedEventId, 'is-short': isShort(segment), 'is-compact': isCompact(segment), 'starts-before': segment.startsBefore, 'ends-after': segment.endsAfter }"
                  :style="segmentStyle(segment)"
                  :aria-label="segment.event.title"
                  :aria-describedby="`${uid}-${segment.event.id}-${segment.dayIndex}`"
                  :aria-current="segment.event.id === selectedEventId ? 'true' : undefined"
                  @click="emit('select', segment.event.id, day.date)"
                >
                  <strong>{{ segment.event.title }}</strong>
                  <span class="cal-block-time" aria-hidden="true">{{ segmentTime(segment)[0] }}<span class="cal-block-end"> – {{ segmentTime(segment)[1] }}</span></span>
                  <span :id="`${uid}-${segment.event.id}-${segment.dayIndex}`" class="sr-only">{{ segmentTime(segment).join(' 至 ') }}</span>
                </button>
                <button
                  v-for="group in day.collapsed"
                  :key="`more-${group.top}`"
                  type="button"
                  class="cal-block-more"
                  :style="groupStyle(group)"
                  :title="group.events.map((event) => event.title).join('、')"
                  :aria-label="`${monthDay(day.date)} 另有 ${group.events.length} 项`"
                  @click="emit('showDay', day.date)"
                >
                  另有 {{ group.events.length }} 项
                </button>
                <div v-if="day.date === today && nowTop !== null" class="cal-now" :style="{ top: `${nowTop}px` }" aria-hidden="true"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
