<script setup lang="ts">
import { computed, ref, watchEffect, type Component } from 'vue'
import { useQuery, keepPreviousData } from '@tanstack/vue-query'
import { ArrowRight, FileText, RefreshCw, Square, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, errorMessage, type Schema } from '../../services/api'
import type { WidgetValue } from '../registry'
import { relativeTime } from './format'
import { useWidgetHeader } from './header'
import './home.css'

// 任务进展（T-0164，首页效果图）：四种状态的页签和任务表（GET /tasks/progress，docs/v2/HOME_PLAN.md 第 2 节）。
// 阻塞 Issue 的标题来自人或智能体，按纯文本显示
const props = defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
type Filter = 'all' | 'todo' | 'in_progress' | 'review' | 'blocked'
type Item = Schema['TaskProgressItem']
const filter = ref<Filter>('all')
const limit = computed(() => (props.value.widget_type === 'task_progress' ? props.value.config.limit : 7))
const query = useQuery({
  queryKey: computed(() => ['task-progress', user, scope, filter.value, limit.value]),
  placeholderData: keepPreviousData,
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/tasks/progress', { params: { header, query: { status: filter.value, limit: limit.value } }, signal })),
})
const data = computed(() => query.data.value)
const tabs = computed(() => {
  const counts = data.value?.counts
  return ([['all', '全部'], ['todo', '待办'], ['in_progress', '进行中'], ['review', '待查看'], ['blocked', '阻塞']] as const).map(([key, label]) => ({ key, label, count: counts?.[key] }))
})
const widgetHeader = useWidgetHeader()
watchEffect(() => {
  const attention = data.value?.attention ?? 0
  widgetHeader.value = { badge: attention > 0 ? { text: `需要你关注 ${attention} 项`, tone: 'warning' } : null }
})

const STATUS: Record<Item['status'], { text: string; icon: Component; action: string }> = {
  review: { text: '已完成 · 待查看', icon: FileText, action: '查看结果' },
  in_progress: { text: '进行中', icon: RefreshCw, action: '查看进展' },
  blocked: { text: '阻塞', icon: TriangleAlert, action: '处理阻塞' },
  todo: { text: '待办', icon: Square, action: '打开任务' },
}
function target(item: Item) {
  if (item.status === 'blocked' && item.blocker) return `/issues/${item.blocker.issue_id}`
  if (item.status === 'in_progress') return { path: '/tasks/timeline', query: { task: item.id, project: item.project_id } }
  return { path: '/tasks', query: { task: item.id } }
}
const executor = (item: Item) => item.executor?.name || '未指派'
const empty = computed(() => {
  if (filter.value === 'all') return '还没有需要跟进的任务。新建任务或把任务交给智能体后会显示在这里。'
  return `没有${tabs.value.find((tab) => tab.key === filter.value)?.label}的任务。`
})
</script>

<template>
  <div class="task-progress">
    <div class="progress-tabs" role="group" aria-label="按状态筛选任务">
      <button v-for="tab in tabs" :key="tab.key" type="button" class="progress-tab" :aria-pressed="filter === tab.key" @click="filter = tab.key">
        {{ tab.label }}<span class="progress-tab-count">{{ tab.count ?? '—' }}</span>
      </button>
    </div>
    <p v-if="query.isPending.value" role="status" class="card-state">正在加载任务…</p>
    <div v-else-if="query.error.value && !data" class="card-state"><p role="alert">{{ errorMessage(query.error.value) }}</p><button type="button" class="secondary-button" @click="query.refetch()">重试</button></div>
    <template v-else-if="data">
      <p v-if="!data.items.length" class="card-state progress-empty">{{ empty }}</p>
      <table v-else class="progress-table">
        <thead><tr><th scope="col">任务</th><th scope="col">项目与执行者</th><th scope="col">状态</th><th scope="col">最近更新</th><th scope="col">操作</th></tr></thead>
        <tbody>
          <tr v-for="item in data.items" :key="item.id" :data-status="item.status">
            <td>
              <div class="progress-task">
                <span class="progress-icon" :class="`status-${item.status}`" aria-hidden="true"><component :is="STATUS[item.status].icon" /></span>
                <span class="progress-title-wrap">
                  <RouterLink class="progress-title" :to="{ path: '/tasks', query: { task: item.id } }">{{ item.title }}</RouterLink>
                  <span v-if="item.status === 'blocked' && item.blocker" class="progress-note">{{ item.blocker.title }}{{ item.blocker.count > 1 ? `（共 ${item.blocker.count} 个阻塞）` : '' }}</span>
                </span>
              </div>
            </td>
            <td class="progress-owner"><span>{{ item.project_title }}</span><span aria-hidden="true"> · </span><span>{{ executor(item) }}</span></td>
            <td><span class="status-chip" :class="`status-${item.status}`"><component :is="STATUS[item.status].icon" aria-hidden="true" />{{ STATUS[item.status].text }}</span></td>
            <td class="progress-time"><time :datetime="item.updated_at">{{ relativeTime(item.updated_at) }}</time></td>
            <td class="progress-action"><RouterLink :to="target(item)" :aria-label="`${STATUS[item.status].action}：${item.title}`">{{ STATUS[item.status].action }}</RouterLink></td>
          </tr>
        </tbody>
      </table>
      <p v-if="query.error.value" role="alert" class="card-state">数据可能已过期：{{ errorMessage(query.error.value) }}</p>
      <RouterLink class="home-more" to="/tasks">查看全部 {{ data.counts.all }} 项任务<ArrowRight aria-hidden="true" /></RouterLink>
    </template>
  </div>
</template>
