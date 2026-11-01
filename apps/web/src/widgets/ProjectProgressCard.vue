<script setup lang="ts">
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { dataOf, errorMessage, isAccessError, type Schema } from '../services/api'
import type { WidgetValue } from './registry'
import ExternalLink from '../components/ExternalLink.vue'

// ProjectProgress (spec §8.2, T-0064): open / done task counts per project from the local task projection.
const props = defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
const emit = defineEmits<{ retry: [] }>()
const auth = useAuthStore()
const limit = computed(() => props.value.widget_type === 'project_progress' ? props.value.config.limit : 5)
const module = computed(() => props.overview?.modules.tasks)
const query = useQuery({
  queryKey: computed(() => ['tasks', auth.user?.id, auth.workspaceId, 'project-progress', limit.value]),
  enabled: computed(() => !!auth.workspaceId && ['ok', 'stale'].includes(module.value?.status ?? '')),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/projects/progress', { params: { header: { 'X-Workspace-Id': auth.workspaceId! }, query: { limit: limit.value } }, signal })),
})
const state = computed(() => {
  if (module.value?.status === 'unauthorized' || isAccessError(query.error.value)) return 'unauthorized'
  if (props.loading) return 'loading'
  if (props.failed && !props.overview) return 'error'
  if (!module.value || module.value.status === 'unavailable') return 'unavailable'
  if (query.isPending.value) return 'loading'
  if (query.error.value) return query.data.value ? 'stale' : 'error'
  return module.value.status === 'stale' || props.failed ? 'stale' : 'ok'
})
const items = computed(() => query.data.value?.items ?? [])
const percent = (p: Schema['ProjectProgressOut']) => p.open_tasks + p.done_tasks ? Math.round(p.done_tasks / (p.open_tasks + p.done_tasks) * 100) : 0
function retry() { if (['ok', 'stale'].includes(module.value?.status ?? '')) void query.refetch(); emit('retry') }
</script>
<template>
  <p v-if="state === 'loading'" role="status" class="card-state">正在加载组件…</p>
  <p v-else-if="state === 'unauthorized'" class="card-state">无权查看此组件，请重新登录或联系管理员。</p>
  <p v-else-if="state === 'unavailable'" class="card-state">任务模块暂不可用{{ module?.reason === 'not_configured' ? '，请先在集成设置里连接 Vikunja' : '' }}。</p>
  <p v-else-if="state === 'error'" role="alert" class="card-state">{{ query.error.value ? errorMessage(query.error.value) : '组件暂时无法加载。' }}</p>
  <template v-else>
    <p v-if="state === 'stale'" class="card-state">数据可能过期。<button type="button" class="secondary-button" @click="retry">重试</button></p>
    <p v-if="!items.length" class="card-state">还没有项目。</p>
    <ul v-else class="widget-list project-progress">
      <li v-for="p in items" :key="p.project_id">
        <div class="section-heading"><strong>{{ p.title }}</strong><ExternalLink :href="p.source_url" /></div>
        <progress :value="p.done_tasks" :max="Math.max(1, p.open_tasks + p.done_tasks)" :aria-label="`${p.title} 完成 ${percent(p)}%`" />
        <p class="form-help">未完成 {{ p.open_tasks }} · 已完成 {{ p.done_tasks }}（{{ percent(p) }}%）</p>
      </li>
    </ul>
    <RouterLink class="primary-link" to="/projects">查看全部项目 →</RouterLink>
  </template>
</template>
