<script setup lang="ts">
import { computed, ref } from 'vue'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { useTaskOperations, type TaskCommand } from '../../stores/taskOperations'
import { ApiError, dataOf, errorMessage, isAccessError } from '../../services/api'
import { useProjects, priorities } from './shared'
import { formatTime } from '../time'
import DataState from '../../components/DataState.vue'
import SyncNotice from '../../components/SyncNotice.vue'
import ExternalLink from '../../components/ExternalLink.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import TaskEditor from './TaskEditor.vue'
import OperationPanel from './OperationPanel.vue'
const auth = useAuthStore(), operation = useTaskOperations(), cache = useQueryClient()
const scope = auth.workspaceId!, userId = auth.user!.id
const header = { 'X-Workspace-Id': scope }
const form = ref({ status: 'all' as 'all' | 'open' | 'done', project_id: '', priority: [] as string[], due_after: '', due_before: '', q: '' })
type Filters = NonNullable<import('../../services/schema').operations['list_tasks']['parameters']['query']>
const filters = ref<Filters>({ status: 'all', limit: 30 })
const query = useInfiniteQuery({ queryKey: computed(() => ['tasks', userId, scope, filters.value]), initialPageParam: null as string | null,
  queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/tasks', { params: { header, query: { ...filters.value, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })), getNextPageParam: (page) => page.next_cursor ?? undefined })
const projects = useProjects(scope)
const projectItems = computed(() => projects.data.value?.pages.flatMap((p) => p.items) ?? [])
const tasks = computed(() => isAccessError(query.error.value) ? [] : query.data.value?.pages.flatMap((p) => p.items) ?? [])
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer' && !isAccessError(query.error.value))
const board = ref(false), opened = ref<string | null>(null), formError = ref(''), reloadError = ref('')
const detail = useQuery({ queryKey: computed(() => ['task', userId, scope, opened.value]), enabled: computed(() => !!opened.value && opened.value !== 'new'), queryFn: async ({ signal, queryKey }) => dataOf(await auth.api.GET('/api/v1/tasks/{task_id}', { params: { header, path: { task_id: queryKey[3]! } }, signal })) })
function apply() {
  const value = form.value
  if (value.due_after && value.due_before && new Date(value.due_after) > new Date(value.due_before)) { formError.value = '截止时间范围无效'; return }
  formError.value = ''
  filters.value = { status: value.status, limit: 30, ...(value.q.trim() ? { q: value.q.trim() } : {}), ...(value.project_id ? { project_id: value.project_id } : {}), ...(value.priority.length ? { priority: value.priority.map(Number) } : {}), ...(value.due_after ? { due_after: new Date(value.due_after).toISOString() } : {}), ...(value.due_before ? { due_before: new Date(value.due_before).toISOString() } : {}) }
}
function invalidate() { void cache.invalidateQueries({ predicate: (q) => ['tasks', 'task', 'overview'].includes(String(q.queryKey[0])) && q.queryKey[1] === userId && q.queryKey[2] === scope }) }
async function finish(run: () => Promise<boolean>) { if (await run()) { opened.value = null; invalidate() } }
async function reloadOperation() {
  reloadError.value = ''
  const cmd = operation.current?.command
  const list = await query.refetch()
  if (list.error) return
  if (cmd && cmd.method !== 'POST') {
    opened.value = cmd.id
    try {
      const fresh = await auth.api.GET('/api/v1/tasks/{task_id}', { params: { header, path: { task_id: cmd.id } } })
      cache.setQueryData(['task', userId, scope, cmd.id], dataOf(fresh))
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) {
        opened.value = null; operation.clearResolved(); reloadError.value = '任务已不存在或无权访问，请从列表选择其他任务。'
      } else reloadError.value = errorMessage(cause)
      return
    }
  }
  operation.clearResolved()
}
const operationEvents = { retry: () => finish(() => operation.retry()), resubmit: () => { if (window.confirm('已确认原操作未生效，重新提交一次？')) void finish(() => operation.confirmResubmit()) }, reload: reloadOperation }
function submit(command: TaskCommand) { void finish(() => operation.submit(command)) }
</script>
<template>
  <div class="task-toolbar"><div class="form-actions"><button class="secondary-button" :aria-pressed="!board" @click="board = false">列表</button><button class="secondary-button" :aria-pressed="board" @click="board = true">看板</button><button class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()">刷新任务</button></div><button v-if="writable" class="primary-button" :disabled="operation.locked" @click="opened = 'new'">新建任务</button></div>
  <OperationPanel v-if="!opened" :writable="writable" v-on="operationEvents" />
  <p v-if="reloadError && !opened" role="alert" class="feedback">{{ reloadError }}</p>
  <form class="panel task-filters" aria-label="任务筛选" @submit.prevent="apply">
    <label>任务状态<select v-model="form.status" aria-label="任务状态"><option value="all">全部</option><option value="open">未完成</option><option value="done">已完成</option></select></label>
    <label>筛选项目<select v-model="form.project_id" aria-label="筛选项目"><option value="">全部项目</option><option v-for="p in projectItems" :key="p.id" :value="p.id">{{ p.title }}</option></select></label>
    <label>优先级筛选<select v-model="form.priority" aria-label="优先级筛选" multiple><option v-for="(label, index) in priorities" :key="index" :value="String(index)">{{ label }}</option></select></label>
    <label>最低截止时间<input v-model="form.due_after" type="datetime-local"></label><label>最高截止时间<input v-model="form.due_before" type="datetime-local"></label><label>搜索标题<input v-model="form.q" maxlength="100"></label>
    <button class="secondary-button">应用筛选</button><p v-if="formError" role="alert">{{ formError }}</p>
  </form>
  <DataState v-if="!opened" :loading="projects.isPending.value" :error="projects.error.value" :empty="false" empty-text="" @retry="projects.refetch()" /><button v-if="projects.hasNextPage.value && !opened" class="secondary-button" :disabled="projects.isFetching.value" @click="projects.fetchNextPage()">加载更多项目</button>
  <SyncNotice :sync="query.data.value?.pages[0]?.sync" />
  <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!tasks.length" :empty-text="query.data.value?.pages[0]?.sync.status === 'ok' ? '没有符合筛选条件的任务' : '暂无可用的缓存任务'" :stale="!!tasks.length" @retry="query.isFetchNextPageError.value ? query.fetchNextPage() : query.refetch()" />
  <div :class="board ? 'task-board' : 'task-list-view'">
    <section v-for="column in (board ? [{ title: '未完成', done: false }, { title: '已完成', done: true }] : [{ title: '任务列表', done: null }])" :key="column.title" role="region" :aria-label="column.title" class="panel task-column">
      <h2 v-if="board">{{ column.title }}</h2><article v-for="item in tasks.filter(t => column.done === null || t.done === column.done)" :key="item.id" class="task-record"><div><button class="task-title" @click="opened = item.id">{{ item.title }}</button><p class="form-help">Vikunja · {{ item.identifier ?? item.external_id }}</p><p>{{ item.done ? '已完成' : '未完成' }} · {{ priorities[item.priority] }} · {{ item.due_at ? formatTime(item.due_at) : '无截止时间' }}</p></div><ExternalLink :href="item.source_url" /></article>
    </section>
  </div>
  <p v-if="board" class="form-help">看板展示当前筛选下已加载的任务，状态在编辑抽屉中修改。</p><button v-if="query.hasNextPage.value && !isAccessError(query.error.value)" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多任务</button>
  <ModalPanel v-if="opened" :title="opened === 'new' ? '新建任务' : '任务详情'" :busy="operation.current?.status === 'pending'" @close="operation.current?.status !== 'pending' && (opened = null)">
    <DataState :loading="projects.isPending.value" :error="projects.error.value" :empty="!projectItems.length" empty-text="尚无可选项目，请检查同步状态。" @retry="projects.refetch()" />
    <button v-if="projects.hasNextPage.value" class="secondary-button" :disabled="projects.isFetching.value" @click="projects.fetchNextPage()">加载更多项目</button>
    <DataState v-if="opened !== 'new'" :loading="detail.isPending.value" :error="detail.error.value" :empty="false" empty-text="" @retry="detail.refetch()" />
    <TaskEditor v-if="opened === 'new' || (detail.data.value && !detail.error.value)" :key="opened + (detail.data.value?.version ?? '')" :task="opened === 'new' ? undefined : detail.data.value" :projects="projectItems" :writable="writable" :locked="operation.locked || (opened !== 'new' && detail.isFetching.value)" @submit="submit" />
    <OperationPanel :writable="writable" v-on="operationEvents" />
    <p v-if="reloadError" role="alert">{{ reloadError }}</p>
  </ModalPanel>
</template>
