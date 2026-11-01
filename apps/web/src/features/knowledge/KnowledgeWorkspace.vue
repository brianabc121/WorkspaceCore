<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, isAccessError, type Schema } from '../../services/api'
import { useProjects } from '../tasks/shared'
import { useResourceMutation } from '../resources/mutation'
import { formatTime } from '../time'
import DataState from '../../components/DataState.vue'
import ExternalLink from '../../components/ExternalLink.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import MutationNotice from '../resources/MutationNotice.vue'
import BookmarkForm from './BookmarkForm.vue'
import SourceForm from './SourceForm.vue'
import type { BookmarkFields } from './validation'
import { useCreateIntent } from '../../composables/useCreateIntent'
const auth = useAuthStore(), route = useRoute(), scope = auth.workspaceId!, user = auth.user?.id
const header = { 'X-Workspace-Id': scope }, linked = ref(typeof route.query.linked_resource_id === 'string' ? route.query.linked_resource_id : '')
const { run, reload, busy, error, needsReload, accessDenied, message } = useResourceMutation(scope, ['knowledge', 'knowledge-sources', 'related-docs', 'overview'])
const visibility = ref<'all' | 'workspace' | 'private'>('all'), search = ref(''), q = ref('')
const sources = useQuery({ queryKey: ['knowledge-sources', user, scope], queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/knowledge/sources', { params: { header }, signal })) })
const blocked = computed(() => accessDenied.value || isAccessError(query.error.value) || isAccessError(sources.error.value))
const source = computed(() => blocked.value ? undefined : sources.data.value?.items[0])
const projects = useProjects(scope), projectItems = computed(() => isAccessError(projects.error.value) ? [] : projects.data.value?.pages.flatMap((p) => p.items) ?? [])
const filters = computed(() => ({ limit: 30, visibility: visibility.value, ...(linked.value ? { linked_resource_id: linked.value } : {}), ...(q.value ? { q: q.value } : {}) }))
const query = useInfiniteQuery({ queryKey: computed(() => ['knowledge', user, scope, filters.value]), initialPageParam: null as string | null, queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/knowledge/resources', { params: { header, query: { ...filters.value, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })), getNextPageParam: (page) => page.next_cursor ?? undefined })
const items = computed(() => blocked.value ? [] : query.data.value?.pages.flatMap((p) => p.items) ?? [])
const writable = computed(() => ['owner', 'admin', 'member'].includes(auth.currentWorkspace?.role ?? '') && !blocked.value)
const opened = ref<Schema['KnowledgeResourceOut'] | 'new' | null>(null), manageSource = ref(false)
const feedback = computed(() => {
  if (error.value instanceof ApiError) {
    const reason = String(error.value.details?.reason ?? '')
    const reasons: Record<string, string> = { scheme_not_allowed: '文档链接的协议与来源不一致。', credentials_not_allowed: '文档链接不能带用户名或密码。', host_mismatch: '文档地址必须属于已配置的 AFFiNE 来源及其子路径。', not_a_document: '请填写具体文档链接，不要填写功能页地址。', invalid_id: '文档或 AFFiNE 工作空间 ID 格式无效。', not_configured: '请先配置 AFFiNE 知识来源。' }
    if (reasons[reason]) return reasons[reason]
    if (error.value.code === 'BOOKMARK_EXISTS') return '该文档已有同样可见性的收藏，请刷新列表查看。'
    if (error.value.code === 'LIMIT_EXCEEDED') return '每个工作空间最多配置一个知识来源。'
  }
  return error.value ? errorMessage(error.value) : ''
})
watch(blocked, (value) => { if (value) { opened.value = null; manageSource.value = false } })
// 顶栏“创建 → 收藏文档”（规范 §8.1）。还没配置知识来源时，管理员先打开来源配置，其他人看到页面上的提示
useCreateIntent(() => sources.isPending.value ? 'wait' : writable.value && !busy.value && !needsReload.value && (!!source.value || (auth.canManage && !sources.error.value)), () => { if (source.value) opened.value = 'new'; else manageSource.value = true })
async function reset() { if (await reload()) { opened.value = null; manageSource.value = false } }
async function saveBookmark(body: BookmarkFields) {
  if (!writable.value || !opened.value) return
  const current = opened.value
  if (await run(async () => current === 'new' ? dataOf(await auth.api.POST('/api/v1/knowledge/bookmarks', { params: { header }, body: { ...body, visibility: body.visibility ?? 'workspace' } })) : dataOf(await auth.api.PATCH('/api/v1/knowledge/bookmarks/{bookmark_id}', { params: { header, path: { bookmark_id: current.id } }, body: { ...body, version: current.version } })), '收藏已保存')) opened.value = null
}
async function removeBookmark(item: Schema['KnowledgeResourceOut']) {
  if (!writable.value || !window.confirm(`确认删除收藏“${item.title}”？只删除工作台收藏，不删除 AFFiNE 文档。`)) return
  await run(() => auth.api.DELETE('/api/v1/knowledge/bookmarks/{bookmark_id}', { params: { header, path: { bookmark_id: item.id }, query: { version: item.version } } }), '收藏已删除')
}
async function saveSource(body: Schema['KnowledgeSourceCreate']) {
  if (!auth.canManage || blocked.value) return
  const current = source.value
  if (await run(async () => current ? dataOf(await auth.api.PATCH('/api/v1/knowledge/sources/{source_id}', { params: { header, path: { source_id: current.id } }, body: { name: body.name, base_url: body.base_url, version: current.version } })) : dataOf(await auth.api.POST('/api/v1/knowledge/sources', { params: { header }, body })), '知识来源已保存')) manageSource.value = false
}
async function removeSource() {
  const current = source.value
  if (!current || !auth.canManage || blocked.value || !window.confirm(`确认删除知识来源及其 ${current.bookmark_count} 条收藏（包含所有人的私有收藏）、关联与搜索索引？AFFiNE 文档不受影响。`)) return
  if (await run(() => auth.api.DELETE('/api/v1/knowledge/sources/{source_id}', { params: { header, path: { source_id: current.id }, query: { version: current.version } } }), '知识来源已删除')) manageSource.value = false
}
</script>
<template>
  <div class="task-toolbar"><div class="form-actions"><button class="secondary-button" :disabled="query.isFetching.value || busy" @click="query.refetch()">刷新收藏</button><button v-if="auth.canManage && !blocked" class="secondary-button" :disabled="busy || needsReload || sources.isPending.value || !!sources.error.value" @click="manageSource = true">管理知识来源</button></div><button v-if="writable" class="primary-button" :disabled="!source || busy || needsReload" @click="opened = 'new'">新增收藏</button></div>
  <MutationNotice v-if="!opened && !manageSource" :error="feedback" :message="message" :needs-reload="needsReload" :busy="busy" reload-label="重新加载知识" @reload="reset" />
  <DataState :loading="sources.isPending.value" :error="sources.error.value" :empty="!source" empty-text="尚未配置知识来源，请管理员配置 AFFiNE 外部地址。" @retry="sources.refetch()" />
  <form class="panel task-filters" aria-label="收藏筛选" @submit.prevent="q = search.trim()"><label>关联项目筛选<select v-model="linked"><option value="">全部项目与任务</option><option v-if="linked && !projectItems.some(p => p.id === linked)" :value="linked">当前关联资源</option><option v-for="p in projectItems" :key="p.id" :value="p.id">{{ p.title }}</option></select></label><label>可见性筛选<select v-model="visibility"><option value="all">全部可见收藏</option><option value="workspace">工作空间可见</option><option value="private">仅自己的私有收藏</option></select></label><label>搜索收藏<input v-model="search" maxlength="100"></label><button class="secondary-button">搜索收藏</button></form>
  <DataState :loading="false" :error="projects.error.value" :empty="false" empty-text="" @retry="projects.refetch()" /><button v-if="projects.hasNextPage.value" class="secondary-button" :disabled="projects.isFetching.value" @click="projects.fetchNextPage()">加载更多筛选项目</button>
  <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!items.length" empty-text="没有符合条件的收藏。" :stale="!!items.length" @retry="query.isFetchNextPageError.value ? query.fetchNextPage() : query.refetch()" />
  <div class="knowledge-list"><article v-for="item in items" :key="item.id" class="panel form-panel resource-record"><div class="section-heading"><h2>{{ item.title }}</h2><span class="resource-badge">{{ item.visibility === 'private' ? '仅自己可见' : '工作空间可见' }}</span></div><p class="note-text">{{ item.note }}</p><p class="form-help">AFFiNE · {{ item.affine_workspace_id }} / {{ item.doc_id }} · 更新于 {{ formatTime(item.updated_at) }}</p><ul v-if="item.links.length" class="resource-relations"><li v-for="link in item.links" :key="link.resource_id">{{ link.resource_type === 'project' ? '项目' : '任务' }} · {{ link.title }} <ExternalLink :href="link.source_url" /></li></ul><div class="form-actions"><ExternalLink :href="item.url" label="在 AFFiNE 中打开" /><button v-if="writable" class="secondary-button" :aria-label="`编辑收藏 ${item.title}`" :disabled="!source || busy || needsReload" @click="opened = item">编辑</button><button v-if="writable" class="secondary-button" :aria-label="`删除收藏 ${item.title}`" :disabled="busy || needsReload" @click="removeBookmark(item)">删除</button></div></article></div>
  <button v-if="query.hasNextPage.value && !blocked" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多收藏</button>
  <ModalPanel v-if="opened && source" :title="opened === 'new' ? '新增收藏' : '编辑收藏'" :busy="busy" @close="!busy && (opened = null)"><BookmarkForm :source="source" :bookmark="opened === 'new' ? undefined : opened" :linked-id="linked" :busy="busy" :locked="needsReload || !writable" @save="saveBookmark" /><MutationNotice :error="feedback" :message="message" :needs-reload="needsReload" :busy="busy" reload-label="重新加载知识" @reload="reset" /></ModalPanel>
  <ModalPanel v-if="manageSource && auth.canManage" title="配置知识来源" :busy="busy" @close="!busy && (manageSource = false)"><SourceForm :source="source" :busy="busy" :locked="needsReload" @save="saveSource" @remove="removeSource" /><MutationNotice :error="feedback" :message="message" :needs-reload="needsReload" :busy="busy" reload-label="重新加载知识" @reload="reset" /></ModalPanel>
</template>
