<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { isAccessError, type Schema } from '../../services/api'
import { useGitHubList, githubSync, type GitHubFilters, type GitHubKind } from './queries'
import GitHubRecord from './GitHubRecord.vue'
import SyncNotice from '../../components/SyncNotice.vue'
import DataState from '../../components/DataState.vue'
const auth = useAuthStore(), scope = auth.workspaceId!
const kinds: Record<GitHubKind, string> = { repos: '仓库', issues: 'Issues', pulls: 'Pull requests', runs: 'Actions' }
const kind = ref<GitHubKind>('repos'), repository = ref(''), state = ref<'open' | 'closed' | 'all'>('open'), title = ref(''), conclusion = ref<'' | 'failure' | 'success'>('')
const filters = ref<GitHubFilters>({}), choices = ref<Schema['RepoOut'][]>([])
const query = useGitHubList(kind, scope, filters), sync = computed(() => githubSync(query.data.value?.pages))
const denied = computed(() => isAccessError(query.error.value) || ['unauthorized', 'not_configured'].includes(sync.value?.status ?? ''))
const items = computed(() => denied.value ? [] : query.data.value?.pages.flatMap((page) => page.items) ?? [])
watch(() => query.data.value, () => { if (kind.value === 'repos' && !denied.value) choices.value = items.value as Schema['RepoOut'][] })
watch(denied, (value) => { if (value) choices.value = [] })
function apply() { filters.value = { ...(repository.value ? { repo_id: repository.value } : {}), state: state.value, ...(title.value.trim() ? { q: title.value.trim() } : {}), ...(conclusion.value ? { conclusion: conclusion.value } : {}) } }
function change(value: GitHubKind) { kind.value = value; repository.value = ''; state.value = 'open'; title.value = ''; conclusion.value = ''; filters.value = {} }
</script>
<template><div class="task-toolbar"><div class="form-actions"><button v-for="(label, value) in kinds" :key="value" class="secondary-button" :aria-pressed="kind === value" @click="change(value)">{{ label }}</button></div><button class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()">刷新 GitHub 数据</button></div><form v-if="kind !== 'repos'" class="panel task-filters" aria-label="GitHub 筛选" @submit.prevent="apply"><label>GitHub 仓库<select v-model="repository"><option value="">全部仓库</option><option v-for="repo in choices" :key="repo.id" :value="repo.id">{{ repo.full_name }}</option></select></label><template v-if="kind !== 'runs'"><label>GitHub 状态<select v-model="state"><option value="open">Open</option><option value="closed">Closed</option><option value="all">全部状态</option></select></label><label>GitHub 标题搜索<input v-model="title" maxlength="100"></label></template><label v-else>Actions 结论<select v-model="conclusion"><option value="">全部结论</option><option value="failure">失败</option><option value="success">成功</option></select></label><button class="secondary-button">应用 GitHub 筛选</button></form><SyncNotice :sync="sync" provider="github" /><RouterLink v-if="auth.canManage && ['unauthorized', 'not_configured'].includes(sync?.status ?? '')" to="/settings/integrations">重新连接 GitHub</RouterLink><DataState :loading="query.isPending.value" :error="query.error.value" :empty="!items.length && sync?.status === 'ok' && !sync?.reason" empty-text="没有符合条件的 GitHub 数据。" :stale="!!items.length" @retry="query.isFetchNextPageError.value ? query.fetchNextPage() : query.refetch()" /><p v-if="!query.error.value && !items.length && sync?.status === 'stale' && sync.reason !== 'rate_limited'" class="feedback">暂无可用的缓存数据，请等待下一次成功同步。</p><div class="github-list"><GitHubRecord v-for="item in items" :key="item.id" :kind="kind" :item="item" /></div><button v-if="query.hasNextPage.value && !denied" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多 GitHub 数据</button></template>
