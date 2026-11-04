<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData } from '@tanstack/vue-query'
import { Plus, RefreshCw, SlidersHorizontal } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useTaskOperations, type TaskCommand } from '../../stores/taskOperations'
import { ApiError, dataOf, errorMessage, isAccessError, type Schema } from '../../services/api'
import { dueInfo, endOfToday, priorities, taskViews, useProjects, viewQuery, type TaskView } from './shared'
import DataState from '../../components/DataState.vue'
import DrawerDetail from '../../components/DrawerDetail.vue'
import TaskEditor from './TaskEditor.vue'
import { useCreateIntent } from '../../composables/useCreateIntent'
import OperationPanel from './OperationPanel.vue'
const auth = useAuthStore(), operation = useTaskOperations(), cache = useQueryClient()
const scope = auth.workspaceId!, userId = auth.user!.id
const header = { 'X-Workspace-Id': scope }
// ?q= pre-fills the search; ?project_id= filters one project (project page, progress widget, knowledge links);
// ?task= opens that task in the drawer (search results, calendar deadlines, knowledge links).
const route = useRoute()
const param = (name: string, max = 100) => { const v = route.query[name]; return typeof v === 'string' ? v.trim().slice(0, max) : '' }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const linked = param('q'), linkedProject = uuid.test(param('project_id')) ? param('project_id') : '', linkedTask = uuid.test(param('task')) ? param('task') : ''
type Sort = 'due_at' | 'priority' | 'updated'
const form = ref({ status: 'all' as 'all' | 'open' | 'done', project_id: linkedProject, priority: [] as string[], due_after: '', due_before: '', q: linked })
// 智能视图（T-0084）：今天 / 已逾期 / 未来 7 天按点选时的时间算；在“更多筛选”里填了截止范围时是 custom
const view = ref<TaskView | 'custom'>('all'), sort = ref<Sort>('due_at')
type Filters = NonNullable<import('../../services/schema').operations['list_tasks']['parameters']['query']>
function build(): Filters {
  const value = form.value
  const smart = view.value === 'today' || view.value === 'overdue' || view.value === 'upcoming' ? viewQuery(view.value) : null
  const due_after = smart ? smart.due_after : value.due_after ? new Date(value.due_after).toISOString() : undefined
  const due_before = smart ? smart.due_before : value.due_before ? new Date(value.due_before).toISOString() : undefined
  return { status: value.status, limit: 30, ...(value.q.trim() ? { q: value.q.trim() } : {}), ...(value.project_id ? { project_id: value.project_id } : {}), ...(value.priority.length ? { priority: value.priority.map(Number) } : {}), ...(due_after ? { due_after } : {}), ...(due_before ? { due_before } : {}), ...(sort.value !== 'due_at' ? { sort: sort.value } : {}) }
}
const filters = ref<Filters>(build())
const query = useInfiniteQuery({ queryKey: computed(() => ['tasks', userId, scope, filters.value]), initialPageParam: null as string | null,
  queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/tasks', { params: { header, query: { ...filters.value, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })), getNextPageParam: (page) => page.next_cursor ?? undefined })
const projects = useProjects(scope)
const projectItems = computed(() => projects.data.value?.pages.flatMap((p) => p.items) ?? [])
const projectTitles = computed(() => new Map(projectItems.value.map((p) => [p.id, p.title])))
const tasks = computed(() => isAccessError(query.error.value) ? [] : query.data.value?.pages.flatMap((p) => p.items) ?? [])
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer' && !isAccessError(query.error.value))
const board = ref(route.query.board === '1'), opened = ref<string | null>(linkedTask || null), formError = ref(''), reloadError = ref('')
// 编辑器只在打开另一个任务或用户点“重新加载”时重建；后台重新拉取不重建，避免丢掉正在输入的内容（T-0049）
const editorGeneration = ref(0)
const detail = useQuery({ queryKey: computed(() => ['task', userId, scope, opened.value]), enabled: computed(() => !!opened.value && opened.value !== 'new'), queryFn: async ({ signal, queryKey }) => dataOf(await auth.api.GET('/api/v1/tasks/{task_id}', { params: { header, path: { task_id: queryKey[3]! } }, signal })) })
function selectView(next: TaskView) {
  view.value = next
  form.value = { ...form.value, status: viewQuery(next).status, due_after: '', due_before: '' }
  formError.value = ''
  filters.value = build()
}
function apply() {
  const value = form.value
  if (value.due_after && value.due_before && new Date(value.due_after) > new Date(value.due_before)) { formError.value = '截止时间范围无效'; return }
  formError.value = ''
  view.value = value.due_after || value.due_before ? 'custom' : value.status
  filters.value = build()
}
function changeSort() { filters.value = build() }
const advancedCount = computed(() => [form.value.project_id, form.value.q.trim(), form.value.due_after || form.value.due_before, form.value.priority.length].filter(Boolean).length)
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
      editorGeneration.value += 1
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
// 顶栏“创建 → 新建任务”（规范 §8.1）；有未结束的任务操作时不开新的，结果面板会说明
useCreateIntent(() => writable.value && !operation.locked, () => { opened.value = 'new' })

// 列表里直接完成 / 取消完成、快速添加（T-0084）：仍走同一个任务写入（Idempotency-Key、结果未知时查询原请求），
// 成功后清掉结果面板、把新版本写进列表缓存（连续勾选不会拿旧版本冲突），再后台刷新
const notice = ref(''), pendingId = ref<string | null>(null)
type Page = Schema['TaskPage']
function remember(task: Schema['TaskOut']) {
  cache.setQueriesData<InfiniteData<Page>>({ queryKey: ['tasks', userId, scope] }, (data) => data && { ...data, pages: data.pages.map((page) => ({ ...page, items: page.items.map((item) => (item.id === task.id ? task : item)) })) })
}
async function quick(command: TaskCommand, done: (task: Schema['TaskOut'] | null) => void) {
  if (!writable.value || operation.locked) return false
  const ok = await operation.submit(command)
  if (!ok) return false
  const result = operation.current?.result ?? null
  operation.clearResolved()
  done(result)
  invalidate()
  return true
}
const displayDone = (item: Schema['TaskOut']) => (pendingId.value === item.id ? !item.done : item.done)
// box 是被点击的勾选框：浏览器已经切换了它的状态；结束后按列表里的实际状态校正（失败时恢复原样）
async function toggle(item: Schema['TaskOut'], box?: HTMLInputElement) {
  if (pendingId.value) return
  pendingId.value = item.id
  try {
    await quick({ method: 'PATCH', id: item.id, body: { version: item.version, done: !item.done } }, (task) => {
      if (task) remember(task)
      notice.value = `${item.done ? '已取消完成' : '已完成'}：${item.title}`
    })
  } finally {
    pendingId.value = null
    await nextTick()
    const latest = tasks.value.find((t) => t.id === item.id)
    if (box?.isConnected && latest) box.checked = latest.done
  }
}
const quickTitle = ref(''), quickProject = ref(linkedProject)
watch(projectItems, (items) => { if (!items.some((p) => p.id === quickProject.value)) quickProject.value = items[0]?.id ?? '' }, { immediate: true })
watch(() => form.value.project_id, (id) => { if (id) quickProject.value = id })
async function quickAdd() {
  const title = quickTitle.value.trim()
  if (!title || !quickProject.value) return
  await quick({ method: 'POST', body: { title, description: '', project_id: quickProject.value, priority: 0, due_at: view.value === 'today' ? endOfToday() : null, labels: [] } }, () => {
    quickTitle.value = ''
    notice.value = `已添加任务：${title}`
  })
}

// 看板：把卡片拖到另一列改变完成状态；勾选框是键盘可用的同等操作
const dragging = ref<string | null>(null), dropColumn = ref<boolean | null>(null)
const canDrag = computed(() => board.value && writable.value && !operation.locked && !pendingId.value)
function dragStart(event: DragEvent, item: Schema['TaskOut']) {
  if (!canDrag.value) { event.preventDefault(); return }
  dragging.value = item.id
  event.dataTransfer?.setData('text/plain', item.id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}
function dragOver(event: DragEvent, done: boolean | null) {
  const item = tasks.value.find((t) => t.id === dragging.value)
  if (done === null || !item || item.done === done) return
  event.preventDefault()
  dropColumn.value = done
}
function drop(event: DragEvent, done: boolean | null) {
  event.preventDefault()
  const item = tasks.value.find((t) => t.id === dragging.value)
  dragging.value = null; dropColumn.value = null
  if (done !== null && item && item.done !== done) void toggle(item)
}
function dragEnd() { dragging.value = null; dropColumn.value = null }
const columns = computed(() => (board.value ? [{ title: '未完成', done: false }, { title: '已完成', done: true }] : [{ title: '任务列表', done: null }]))
</script>
<template>
  <div class="task-toolbar">
    <div class="view-chips" role="group" aria-label="任务视图">
      <button v-for="item in taskViews" :key="item.id" type="button" class="chip-button" :aria-pressed="view === item.id" @click="selectView(item.id)">{{ item.label }}</button>
    </div>
    <div class="form-actions">
      <label class="inline-select"><span class="sr-only">排序</span><select v-model="sort" aria-label="排序" @change="changeSort"><option value="due_at">按截止时间</option><option value="priority">按优先级</option><option value="updated">最近更新</option></select></label>
      <div class="segmented" role="group" aria-label="显示方式"><button type="button" :aria-pressed="!board" @click="board = false">列表</button><button type="button" :aria-pressed="board" @click="board = true">看板</button><RouterLink v-slot="{ navigate }" :to="{ path: '/tasks/timeline', query: form.project_id ? { project: form.project_id } : {} }" custom><button type="button" aria-pressed="false" @click="navigate">时间轴</button></RouterLink></div>
      <button type="button" class="shell-icon" aria-label="刷新任务" title="刷新任务" :disabled="query.isFetching.value" @click="query.refetch()"><RefreshCw aria-hidden="true" /></button>
      <button v-if="writable" class="primary-button" :disabled="operation.locked" @click="opened = 'new'">新建任务</button>
    </div>
  </div>
  <form v-if="writable" class="quick-add" aria-label="快速添加任务" @submit.prevent="quickAdd">
    <Plus class="quick-add-icon" aria-hidden="true" />
    <input v-model="quickTitle" aria-label="快速添加" :placeholder="view === 'today' ? '添加今天要做的事，按回车保存' : '添加任务，按回车保存'" maxlength="250" autocomplete="off" :disabled="operation.locked">
    <select v-model="quickProject" aria-label="添加到项目" :disabled="operation.locked || !projectItems.length"><option v-if="!projectItems.length" value="">还没有项目</option><option v-for="p in projectItems" :key="p.id" :value="p.id">{{ p.title }}</option></select>
    <button class="secondary-button" :disabled="operation.locked || !quickTitle.trim() || !quickProject">添加</button>
  </form>
  <p class="sr-only" role="status">{{ notice }}</p>
  <OperationPanel v-if="!opened" :writable="writable" v-on="operationEvents" />
  <p v-if="reloadError && !opened" role="alert" class="feedback">{{ reloadError }}</p>
  <details class="panel task-filters-panel" :open="advancedCount > 0 || undefined">
    <summary><SlidersHorizontal aria-hidden="true" />更多筛选<span v-if="advancedCount" class="count-pill">{{ advancedCount }}</span></summary>
    <form class="task-filters" aria-label="任务筛选" @submit.prevent="apply">
      <label>任务状态<select v-model="form.status" aria-label="任务状态"><option value="all">全部</option><option value="open">未完成</option><option value="done">已完成</option></select></label>
      <label>筛选项目<select v-model="form.project_id" aria-label="筛选项目"><option value="">全部项目</option><option v-for="p in projectItems" :key="p.id" :value="p.id">{{ p.title }}</option></select></label>
      <label>优先级筛选<select v-model="form.priority" aria-label="优先级筛选" multiple><option v-for="(label, index) in priorities" :key="index" :value="String(index)">{{ label }}</option></select></label>
      <label>最低截止时间<input v-model="form.due_after" type="datetime-local"></label><label>最高截止时间<input v-model="form.due_before" type="datetime-local"></label><label>搜索标题<input v-model="form.q" maxlength="100"></label>
      <button class="secondary-button">应用筛选</button><p v-if="formError" role="alert">{{ formError }}</p>
    </form>
  </details>
  <DataState v-if="!opened" :loading="projects.isPending.value" :error="projects.error.value" :empty="false" empty-text="" @retry="projects.refetch()" /><button v-if="projects.hasNextPage.value && !opened" class="secondary-button" :disabled="projects.isFetching.value" @click="projects.fetchNextPage()">加载更多项目</button>
  <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!tasks.length" :empty-text="projectItems.length || projects.isPending.value ? (view === 'today' ? '今天没有要截止的任务' : view === 'overdue' ? '没有逾期的任务' : '没有符合筛选条件的任务') : '还没有项目，请先在「项目」页新建一个项目。'" :stale="!!tasks.length" @retry="query.isFetchNextPageError.value ? query.fetchNextPage() : query.refetch()" />
  <div :class="board ? 'task-board' : 'task-list-view'">
    <section v-for="column in columns" :key="column.title" role="region" :aria-label="column.title" class="panel task-column" :class="{ 'is-drop-target': dropColumn === column.done && column.done !== null }" @dragover="dragOver($event, column.done)" @dragleave="dropColumn = null" @drop="drop($event, column.done)">
      <h2 v-if="board">{{ column.title }}<span class="column-count">{{ tasks.filter(t => t.done === column.done).length }}</span></h2>
      <article v-for="item in tasks.filter(t => column.done === null || t.done === column.done)" :key="item.id" class="task-record" :class="{ 'is-done': displayDone(item), 'is-dragging': dragging === item.id }" :draggable="canDrag" @dragstart="dragStart($event, item)" @dragend="dragEnd">
        <input v-if="writable" type="checkbox" class="task-check" :checked="displayDone(item)" :disabled="operation.locked || !!pendingId" :aria-label="`${item.done ? '取消完成' : '完成'}：${item.title}`" @change="toggle(item, $event.target as HTMLInputElement)">
        <div class="task-main">
          <button class="task-title" @click="opened = item.id">{{ item.title }}</button>
          <p class="task-meta">
            <span class="task-context">{{ projectTitles.get(item.project_id) ?? '已归档的项目' }}<template v-if="item.labels.length"> · {{ item.labels.join(' · ') }}</template></span>
            <span v-if="item.priority" class="chip" :class="item.priority >= 3 ? 'chip-high' : ''">{{ priorities[item.priority] }}</span>
            <span class="chip" :class="`chip-due-${dueInfo(item.due_at, item.done).tone}`">{{ dueInfo(item.due_at, item.done).text }}</span>
            <span v-if="item.claimable" class="chip">可自由接取</span>
            <span v-if="!writable" class="chip">{{ item.done ? '已完成' : '未完成' }}</span>
          </p>
        </div>
      </article>
    </section>
  </div>
  <p v-if="board && writable" class="form-help">把卡片拖到另一列，或勾选卡片上的方框，改变完成状态。</p><button v-if="query.hasNextPage.value && !isAccessError(query.error.value)" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多任务</button>
  <!-- Task Drawer（规范 §8.4）：新建 / 编辑放在右侧抽屉的“详情”页 -->
  <DrawerDetail v-if="opened" :title="opened === 'new' ? '新建任务' : '任务详情'" :busy="operation.current?.status === 'pending'" @close="operation.current?.status !== 'pending' && (opened = null)">
    <DataState :loading="projects.isPending.value" :error="projects.error.value" :empty="!projectItems.length" empty-text="还没有项目，请先在「项目」页新建一个项目。" @retry="projects.refetch()" />
    <button v-if="projects.hasNextPage.value" class="secondary-button" :disabled="projects.isFetching.value" @click="projects.fetchNextPage()">加载更多项目</button>
    <DataState v-if="opened !== 'new'" :loading="detail.isPending.value" :error="detail.error.value" :empty="false" empty-text="" @retry="detail.refetch()" />
    <TaskEditor v-if="opened === 'new' || (detail.data.value && !detail.error.value)" :key="`${opened}:${editorGeneration}`" :task="opened === 'new' ? undefined : detail.data.value" :projects="projectItems" :writable="writable" :locked="operation.locked" @submit="submit" />
    <OperationPanel :writable="writable" v-on="operationEvents" />
    <p v-if="reloadError" role="alert">{{ reloadError }}</p>
  </DrawerDetail>
</template>
