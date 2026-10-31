<script setup lang="ts">
import { computed } from 'vue'
import type { Schema } from '../services/api'
import { errorMessage, isAccessError } from '../services/api'
import { useAuthStore } from '../stores/auth'
import type { WidgetValue } from './registry'
import { useGitHubList, githubSync } from '../features/developer/queries'
import GitHubRecord from '../features/developer/GitHubRecord.vue'
import SyncNotice from '../components/SyncNotice.vue'
const props = defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
const emit = defineEmits<{ retry: [] }>(), auth = useAuthStore(), scope = auth.workspaceId!
const config = computed(() => props.value.widget_type === 'github_activity' ? props.value.config : { kind: 'runs' as const, limit: 5 })
const module = computed(() => props.overview?.modules.github)
const query = useGitHubList(() => config.value.kind, scope, () => ({ limit: config.value.limit }), 'widget', () => ['ok', 'stale'].includes(module.value?.status ?? ''))
const state = computed(() => githubSync(query.data.value?.pages))
const sync = computed<Schema['SyncState'] | undefined>(() => {
  const m = module.value, states: Schema['SyncState'][] = []
  if (m) states.push({ ...m, as_of: m.as_of ?? null })
  if (state.value) states.push(state.value)
  const protectedState = states.find((s) => s.status === 'unauthorized') ?? states.find((s) => s.status === 'not_configured' || s.reason === 'not_configured')
  if (protectedState) return protectedState
  const limits = states.filter((s) => s.reason === 'rate_limited')
  if (limits.length) {
    const retry = limits.map((s) => s.retry_at).filter((value): value is string => !!value).sort((a, b) => Date.parse(b) - Date.parse(a))[0] ?? null
    return { status: states.some((s) => ['ok', 'stale'].includes(s.status)) ? 'stale' : 'unavailable', reason: 'rate_limited', retry_at: retry, as_of: state.value?.as_of ?? m?.as_of ?? null }
  }
  if (state.value && (state.value.status !== 'ok' || !m || m.status === 'ok')) return state.value
  return states[0]
})
const denied = computed(() => isAccessError(query.error.value) || sync.value?.status === 'unauthorized')
const available = computed(() => ['ok', 'stale'].includes(sync.value?.status ?? ''))
const items = computed(() => denied.value || !available.value ? [] : (query.data.value?.pages.flatMap((page) => page.items) ?? []).slice(0, config.value.limit))
function retry() { if (available.value) void query.refetch(); emit('retry') }
</script>
<template><p v-if="loading || (available && query.isPending.value)" role="status" class="card-state">正在加载 GitHub 动态…</p><template v-else><p v-if="isAccessError(query.error.value)" class="card-state">无权查看此组件，请联系管理员。</p><SyncNotice v-else :sync="sync" provider="github" /><p v-if="query.error.value && !denied" role="alert">{{ errorMessage(query.error.value) }}</p><p v-if="failed || query.error.value && items.length" class="card-state">数据可能已过期，当前显示缓存。</p><p v-if="!module" class="card-state">GitHub 模块暂不可用。</p><div v-if="!denied" class="github-activity"><GitHubRecord v-for="item in items" :key="item.id" :kind="config.kind" :item="item" compact /></div><p v-if="!items.length && !query.error.value && sync?.status === 'ok' && !sync.reason" class="card-state">暂无 GitHub 动态。</p><RouterLink v-if="auth.canManage && sync?.status === 'unauthorized'" to="/settings/integrations">重新连接 GitHub</RouterLink><RouterLink v-else-if="!denied" to="/developer">查看开发者页</RouterLink><button v-if="!denied && (query.error.value || module?.retryable || failed)" class="secondary-button" :disabled="query.isFetching.value" @click="retry">刷新 GitHub 动态</button></template></template>
