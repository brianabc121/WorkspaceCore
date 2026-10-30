<script setup lang="ts">
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { dataOf, errorMessage, isAccessError, type Schema } from '../services/api'
import type { WidgetValue } from './registry'
import { eventTime } from '../features/calendar/time'
import { formatTime } from '../features/time'
import ExternalLink from '../components/ExternalLink.vue'
const props = defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
const emit = defineEmits<{ retry: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const docs = computed(() => props.value.widget_type === 'recent_docs')
const limit = computed(() => props.value.widget_type === 'recent_docs' || props.value.widget_type === 'calendar_agenda' ? props.value.config.limit : 5)
const days = computed(() => props.value.widget_type === 'calendar_agenda' ? props.value.config.days : 7)
const module = computed(() => props.overview?.modules[docs.value ? 'knowledge' : 'calendar'])
type CardData = { documents: Schema['KnowledgeResourceOut'][]; events: Schema['CalendarEventOut'][] }
const query = useQuery<CardData>({ queryKey: computed(() => [docs.value ? 'knowledge' : 'calendar', user, scope, 'widget', limit.value, docs.value ? null : days.value]), enabled: computed(() => ['ok', 'stale'].includes(module.value?.status ?? '')), queryFn: async ({ signal }) => {
  if (docs.value) return { documents: dataOf(await auth.api.GET('/api/v1/knowledge/resources', { params: { header, query: { sort: 'updated', limit: limit.value } }, signal })).items, events: [] }
  // This cache represents a moving upcoming window; every refresh starts now.
  const start = new Date().toISOString(), end = new Date(Date.parse(start) + days.value * 86400000).toISOString()
  return { documents: [], events: dataOf(await auth.api.GET('/api/v1/calendar/events', { params: { header, query: { start, end, limit: limit.value } }, signal })).items }
} })
const state = computed(() => {
  if (module.value?.status === 'unauthorized' || isAccessError(query.error.value)) return 'unauthorized'
  if (props.loading) return 'loading'
  if (props.failed && !props.overview) return 'error'
  if (!module.value || module.value.status === 'unavailable') return 'unavailable'
  if (query.isPending.value) return 'loading'
  if (query.error.value) return query.data.value ? 'stale' : 'error'
  return module.value.status === 'stale' || props.failed ? 'stale' : 'ok'
})
const documents = computed(() => query.data.value?.documents.slice(0, limit.value) ?? []), events = computed(() => query.data.value?.events.slice(0, limit.value) ?? [])
function retry() { if (['ok', 'stale'].includes(module.value?.status ?? '')) void query.refetch(); emit('retry') }
</script>
<template>
  <p v-if="state === 'loading'" role="status" class="card-state">正在加载组件…</p>
  <p v-else-if="state === 'unauthorized'" class="card-state">无权查看此组件，请重新登录或联系管理员。</p>
  <p v-else-if="state === 'unavailable'" class="card-state">{{ docs ? '知识' : '日历' }}模块暂不可用{{ module?.reason === 'not_configured' ? '，请先配置知识来源' : '' }}。</p>
  <p v-else-if="state === 'error'" role="alert" class="card-state">{{ query.error.value ? errorMessage(query.error.value) : '组件暂时无法加载。' }}</p>
  <template v-else>
    <p v-if="state === 'stale'" class="card-state">数据可能已过期 · {{ formatTime(module?.as_of ?? null) }}</p>
    <p v-if="query.error.value" role="alert" class="card-state">{{ errorMessage(query.error.value) }}</p>
    <template v-if="docs"><ul v-if="documents.length" class="quick-links"><li v-for="item in documents" :key="item.id"><span>{{ item.title }}</span><small>{{ formatTime(item.updated_at) }}</small><ExternalLink :href="item.url" label="在 AFFiNE 中打开" /></li></ul><p v-else-if="!query.error.value" class="card-state">{{ state === 'stale' ? '暂无可用的缓存文档' : '暂无收藏文档' }}</p></template>
    <template v-else><ul v-if="events.length" class="task-list"><li v-for="item in events" :key="item.id"><span>{{ item.title }}</span><small>{{ eventTime(item) }}</small></li></ul><p v-else-if="!query.error.value" class="card-state">{{ state === 'stale' ? '暂无可用的缓存日程' : '近期暂无日程' }}</p></template>
    <RouterLink :to="docs ? '/knowledge' : '/calendar'">{{ docs ? '查看知识库' : '查看日历' }}</RouterLink>
  </template>
  <button v-if="state !== 'unauthorized' && state !== 'loading' && (query.error.value || module?.retryable || failed)" class="secondary-button" :disabled="query.isFetching.value || loading" @click="retry">{{ query.error.value ? '重试组件数据' : '刷新组件数据' }}</button>
</template>
