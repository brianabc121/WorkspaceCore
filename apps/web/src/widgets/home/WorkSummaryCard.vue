<script setup lang="ts">
import { computed, type Component } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { CalendarDays, CirclePlay, Eye, SquareCheckBig, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, type Schema } from '../../services/api'
import type { WidgetValue } from '../registry'
import { reminderRange } from './format'
import './home.css'

// 工作概况（T-0164，首页效果图第一行）：待办、进行中、已完成待查看、阻塞（GET /tasks/progress 的数量），
// 近期提醒（从现在到第 reminder_days 天结束的日程数）。数字未知时显示“—”，不以 0 冒充
const props = defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const days = computed(() => (props.value.widget_type === 'work_summary' ? props.value.config.reminder_days : 2))
const progress = useQuery({
  queryKey: ['task-progress', user, scope, 'counts'],
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/tasks/progress', { params: { header, query: { limit: 1 } }, signal })),
})
const reminders = useQuery({
  queryKey: computed(() => ['calendar', user, scope, 'reminders', days.value]),
  queryFn: async ({ signal }) => {
    const range = reminderRange(days.value)
    return dataOf(await auth.api.GET('/api/v1/calendar/events', { params: { header, query: { ...range, limit: 100 } }, signal }))
  },
})
type Tile = { key: string; label: string; value: string; icon: Component; tone: string; to: string; hint: string }
const count = (value: number | undefined) => (value === undefined ? '—' : String(value))
const tiles = computed<Tile[]>(() => {
  const counts = progress.data.value?.counts
  const page = reminders.data.value
  const events = page ? `${page.items.length}${page.next_cursor ? '+' : ''}` : '—'
  return [
    { key: 'todo', label: '待办事项', value: count(counts?.todo), icon: SquareCheckBig, tone: 'blue', to: '/tasks', hint: '还没开始的任务' },
    { key: 'in_progress', label: '进行中', value: count(counts?.in_progress), icon: CirclePlay, tone: 'indigo', to: '/tasks/timeline', hint: '正在执行或开始过的任务' },
    { key: 'review', label: '已完成待查看', value: count(counts?.review), icon: Eye, tone: 'amber', to: '/tasks', hint: '智能体完成、等你确认结果的任务' },
    { key: 'blocked', label: '阻塞任务', value: count(counts?.blocked), icon: TriangleAlert, tone: 'red', to: '/tasks', hint: '有未关闭的阻塞 Issue 的任务' },
    { key: 'reminders', label: '近期提醒', value: events, icon: CalendarDays, tone: 'violet', to: '/calendar', hint: days.value === 1 ? '今天剩下的日程' : `今天起 ${days.value} 天内的日程` },
  ]
})
const error = computed(() => progress.error.value ?? reminders.error.value)
</script>

<template>
  <div class="work-summary">
    <ul class="summary-tiles" aria-label="工作概况">
      <li v-for="tile in tiles" :key="tile.key" :data-tile="tile.key">
        <RouterLink class="summary-tile" :class="`tone-${tile.tone}`" :to="tile.to" :title="tile.hint">
          <span class="summary-icon" aria-hidden="true"><component :is="tile.icon" /></span>
          <span class="summary-text"><span class="summary-label">{{ tile.label }}</span><strong class="summary-value">{{ tile.value }}</strong></span>
        </RouterLink>
      </li>
    </ul>
    <p v-if="error" role="alert" class="card-state">{{ errorMessage(error) }}</p>
  </div>
</template>
