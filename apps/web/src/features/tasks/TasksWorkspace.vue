<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData } from '@tanstack/vue-query'
import { useMediaQuery } from '../../composables/useMediaQuery'
import { CalendarRange, Columns3, Flag, List, Plus, RefreshCw, Search } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useTaskOperations, type TaskCommand } from '../../stores/taskOperations'
import { ApiError, dataOf, errorMessage, isAccessError, type Schema } from '../../services/api'
import { dueInfo, endOfToday, priorities, useProjects, viewQuery } from './shared'
import { useAssignees } from '../timeline/api'
import { STATE_META, WORK_STATES, useTaskSummary, type SummaryFilters, type WorkState } from './workState'
import DataState from '../../components/DataState.vue'
import DrawerDetail from '../../components/DrawerDetail.vue'
import TaskDetailPanel from './TaskDetailPanel.vue'
import { useCreateIntent } from '../../composables/useCreateIntent'
import OperationPanel from './OperationPanel.vue'
import '../workspace/workspace-pages.css'
import './tasks.css'

// 任务页（T-0167，效果图 01-tasks；规范第 4 节）：状态筛选标签（服务端计数）、搜索与筛选、任务表、
// 右侧详情（1440 以上并排，以下用抽屉）。状态由服务端判定；“全部”是五种互斥状态之和。
// 地址里 ?task= 打开任务、?state= 选状态、?q= 搜索、?project_id= 筛选项目、?board=1 看板，刷新或分享后可恢复。
const auth = useAuthStore(), operation = useTaskOperations(), cache = useQueryClient()
const scope = auth.workspaceId!, userId = auth.user!.id
const header = { 'X-Workspace-Id': scope }
const route = useRoute(), router = useRouter()
const param = (name: string, max = 100) => { const v = route.query[name]; return typeof v === 'string' ? v.trim().slice(0, max) : '' }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const linkedProject = uuid.test(param('project_id')) ? param('project_id') : '', linkedTask = uuid.test(param('task')) ? param('task') : ''
const linkedState = WORK_STATES.includes(param('state') as WorkState) ? (param('state') as WorkState) : 'all'

type Sort = 'due_at' | 'priority' | 'updated'
type Due = '' | 'today' | 'overdue' | 'upcoming'
const form = ref({ q: param('q'), project_id: linkedProject, assignee: '', priority: '', due: '' as Due, sort: 'due_at' as Sort })
const state = ref<WorkState | 'all'>(linkedState)
type Filters = NonNullable<import('../../services/schema').operations['list_tasks']['parameters']['query']>
function common(): SummaryFilters {
  const value = form.value
  const due = value.due ? viewQuery(value.due) : null
  const [kind, id] = value.assignee.split(':')
  return {
    ...(value.q.trim() ? { q: value.q.trim() } : {}),
    ...(value.project_id ? { project_id: value.project_id } : {}),
    ...(value.priority !== '' ? { priority: [Number(value.priority)] } : {}),
    ...(due?.due_after ? { due_after: due.due_after } : {}),
    ...(due?.due_before ? { due_before: due.due_before } : {}),
    ...(value.assignee === 'me' ? { assignee: 'me' as const } : kind === 'user' ? { assignee_user_id: id } : kind === 'instance' ? { assignee_instance_id: id } : {}),
  }
}
// “今天 / 已逾期 / 未来 7 天”只看未完成的（与原来的智能视图一致）
const listFilters = (): Filters => ({ ...common(), status: form.value.due ? 'open' : 'all', limit: 30, ...(state.value !== 'all' ? { work_state: state.value } : {}), ...(form.value.sort !== 'due_at' ? { sort: form.value.sort } : {}) })
const filters = ref<Filters>(listFilters())
const summaryFilters = ref<SummaryFilters>(common())
function apply() {
  filters.value = listFilters()
  summaryFilters.value = common()
}
function selectState(next: WorkState | 'all') {
  state.value = next
  filters.value = listFilters()
  void router.replace({ query: { ...route.query, state: next === 'all' ? undefined : next } })
}

const query = useInfiniteQuery({ queryKey: computed(() => ['tasks', userId, scope, filters.value]), initialPageParam: null as string | null,
  queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/tasks', { params: { header, query: { ...filters.value, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })), getNextPageParam: (page) => page.next_cursor ?? undefined })
const summary = useTaskSummary(scope, summaryFilters)
const projects = useProjects(scope)
const assignees = useAssignees(scope)
const projectItems = computed(() => projects.data.value?.pages.flatMap((p) => p.items) ?? [])
const projectTitles = computed(() => new Map(projectItems.value.map((p) => [p.id, p.title])))
const names = computed(() => new Map([...(assignees.data.value?.users ?? []).map((u) => [u.id, u.display_name] as const), ...(assignees.data.value?.instances ?? []).map((i) => [i.id, i.name] as const)]))
const tasks = computed(() => isAccessError(query.error.value) ? [] : query.data.value?.pages.flatMap((p) => p.items) ?? [])
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer' && !isAccessError(query.error.value))
const chips = computed(() => {
  const counts = summary.data.value
  return [
    { key: 'all' as const, label: '全部', count: counts?.total, icon: List, tone: '' },
    ...WORK_STATES.map((key) => ({ key, label: STATE_META[key].label, count: counts?.[key], icon: STATE_META[key].icon, tone: `tone-${STATE_META[key].tone}` })),
  ]
})
const shownTotal = computed(() => (state.value === 'all' ? summary.data.value?.total : summary.data.value?.[state.value]))

// 详情：1440 以上在右侧并排，以下放进抽屉；选中的任务写进地址（?task=）
// 没有 matchMedia 的环境（jsdom）按窄屏处理：详情用抽屉
const wide = typeof window.matchMedia === 'function' ? useMediaQuery('(min-width: 1440px)') : ref(false)
const board = ref(route.query.board === '1'), opened = ref<string | null>(linkedTask || null), reloadError = ref('')
watch(opened, (id) => { if (id !== 'new') void router.replace({ query: { ...route.query, task: id ?? undefined } }) })
const editorGeneration = ref(0)
const detail = useQuery({ queryKey: computed(() => ['task', userId, scope, opened.value]), enabled: computed(() => !!opened.value && opened.value !== 'new'), queryFn: async ({ signal, queryKey }) => dataOf(await auth.api.GET('/api/v1/tasks/{task_id}', { params: { header, path: { task_id: queryKey[3]! } }, signal })) })
function invalidate() { void cache.invalidateQueries({ predicate: (q) => ['tasks', 'task', 'overview', 'task-progress', 'projects'].includes(String(q.queryKey[0])) && q.queryKey[1] === userId && q.queryKey[2] === scope }) }
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
useCreateIntent(() => writable.value && !operation.locked, () => { opened.value = 'new' })
function close() { if (operation.current?.status !== 'pending') opened.value = null }

// 列表里直接完成 / 取消完成、快速添加（T-0084）：走同一个任务写入（Idempotency-Key、结果未知时查询原请求）
const notice = ref(''), pendingId = ref<string | null>(null)
type Page = Schema['TaskPage']
function remember(task: Schema['TaskOut']) {
  cache.setQueriesData<InfiniteData<Page>>({ queryKey: ['tasks', userId, scope] }, (data) => data && 'pages' in data ? { ...data, pages: data.pages.map((page) => ({ ...page, items: page.items.map((item) => (item.id === task.id ? task : item)) })) } : data)
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
  await quick({ method: 'POST', body: { title, description: '', project_id: quickProject.value, priority: 0, due_at: form.value.due === 'today' ? endOfToday() : null, labels: [] } }, () => {
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
function dragOver(event: DragEvent, done: boolean) {
  const item = tasks.value.find((t) => t.id === dragging.value)
  if (!item || item.done === done) return
  event.preventDefault()
  dropColumn.value = done
}
function drop(event: DragEvent, done: boolean) {
  event.preventDefault()
  const item = tasks.value.find((t) => t.id === dragging.value)
  dragging.value = null; dropColumn.value = null
  if (item && item.done !== done) void toggle(item)
}
function dragEnd() { dragging.value = null; dropColumn.value = null }
function setBoard(value: boolean) { board.value = value; void router.replace({ query: { ...route.query, board: value ? '1' : undefined } }) }
const executor = (item: Schema['TaskOut']) => {
  const id = item.assignee_user_id ?? item.assignee_instance_id
  if (id) return { name: names.value.get(id) ?? (item.assignee_instance_id ? '智能体' : '成员'), agent: !!item.assignee_instance_id }
  return { name: item.claimable ? '可自由接取' : '未分配', agent: false }
}
const priorityTone = (value: number) => (value >= 4 ? 'urgent' : value === 3 ? 'high' : value === 2 ? 'mid' : value === 1 ? 'low' : 'none')
const showPanel = computed(() => wide.value && !!opened.value)
</script>
<template>
  <div class="ws-header">
    <div><h1>任务</h1><p>清楚掌握每项工作的下一步。</p></div>
    <div class="ws-actions">
      <button v-if="writable" type="button" class="primary-button" :disabled="operation.locked" @click="opened = 'new'"><Plus aria-hidden="true" />新建任务</button>
      <div class="ws-segment" role="group" aria-label="显示方式">
        <button type="button" :aria-pressed="!board" @click="setBoard(false)"><List aria-hidden="true" />列表</button>
        <button type="button" :aria-pressed="board" @click="setBoard(true)"><Columns3 aria-hidden="true" />看板</button>
        <RouterLink :to="{ path: '/tasks/timeline', query: form.project_id ? { project: form.project_id } : {} }"><CalendarRange aria-hidden="true" />时间轴</RouterLink>
      </div>
    </div>
  </div>

  <div class="ws-chips" role="group" aria-label="按状态筛选任务">
    <button v-for="chip in chips" :key="chip.key" type="button" class="ws-chip" :class="chip.tone" :aria-pressed="state === chip.key" @click="selectState(chip.key)">
      <component :is="chip.icon" aria-hidden="true" />{{ chip.label }}<span class="ws-chip-count">{{ chip.count ?? '—' }}</span>
    </button>
  </div>
  <p v-if="summary.error.value" role="alert" class="feedback">状态计数暂时读不到：{{ errorMessage(summary.error.value) }}</p>

  <div class="tasks-layout" :class="{ 'has-detail': showPanel }">
    <section class="ws-panel tasks-main" aria-label="任务列表">
      <form class="ws-toolbar tasks-toolbar" aria-label="任务筛选" @submit.prevent="apply">
        <label class="ws-search"><Search aria-hidden="true" /><span class="sr-only">搜索任务</span><input v-model="form.q" type="search" maxlength="100" placeholder="搜索任务" aria-label="搜索任务" @change="apply"></label>
        <select v-model="form.project_id" aria-label="筛选项目" @change="apply"><option value="">全部项目</option><option v-for="p in projectItems" :key="p.id" :value="p.id">{{ p.title }}</option></select>
        <select v-model="form.assignee" aria-label="筛选执行者" @change="apply">
          <option value="">全部执行者</option><option value="me">我</option>
          <optgroup v-if="assignees.data.value?.users.length" label="成员"><option v-for="u in assignees.data.value.users" :key="u.id" :value="`user:${u.id}`">{{ u.display_name }}</option></optgroup>
          <optgroup v-if="assignees.data.value?.instances.length" label="智能体"><option v-for="i in assignees.data.value.instances" :key="i.id" :value="`instance:${i.id}`">{{ i.name }}</option></optgroup>
        </select>
        <select v-model="form.priority" aria-label="筛选优先级" @change="apply"><option value="">全部优先级</option><option v-for="(label, index) in priorities" :key="index" :value="String(index)">{{ label }}</option></select>
        <select v-model="form.due" aria-label="截止时间" @change="apply"><option value="">全部截止时间</option><option value="today">今天截止</option><option value="overdue">已逾期</option><option value="upcoming">未来 7 天</option></select>
        <select v-model="form.sort" aria-label="排序" @change="apply"><option value="due_at">按截止时间</option><option value="priority">按优先级</option><option value="updated">最近更新</option></select>
        <button type="button" class="shell-icon" aria-label="刷新任务" title="刷新任务" :disabled="query.isFetching.value" @click="query.refetch(); summary.refetch()"><RefreshCw aria-hidden="true" /></button>
      </form>

      <form v-if="writable" class="quick-add" aria-label="快速添加任务" @submit.prevent="quickAdd">
        <Plus class="quick-add-icon" aria-hidden="true" />
        <input v-model="quickTitle" aria-label="快速添加" :placeholder="form.due === 'today' ? '添加今天要做的事，按回车保存' : '添加任务，按回车保存'" maxlength="250" autocomplete="off" :disabled="operation.locked">
        <select v-model="quickProject" aria-label="添加到项目" :disabled="operation.locked || !projectItems.length"><option v-if="!projectItems.length" value="">还没有项目</option><option v-for="p in projectItems" :key="p.id" :value="p.id">{{ p.title }}</option></select>
        <button class="secondary-button" :disabled="operation.locked || !quickTitle.trim() || !quickProject">添加</button>
      </form>
      <p class="sr-only" role="status">{{ notice }}</p>
      <OperationPanel v-if="!opened" :writable="writable" v-on="operationEvents" />
      <p v-if="reloadError && !opened" role="alert" class="feedback">{{ reloadError }}</p>
      <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!tasks.length" :empty-text="projectItems.length || projects.isPending.value ? (state === 'all' ? '没有符合筛选条件的任务' : `没有${STATE_META[state].label}的任务`) : '还没有项目，请先在「项目」页新建一个项目。'" :stale="!!tasks.length" @retry="query.isFetchNextPageError.value ? query.fetchNextPage() : query.refetch()" />

      <div v-if="!board && tasks.length" class="ws-table-wrap">
        <table class="ws-table tasks-table">
          <thead><tr><th v-if="writable" scope="col" class="col-check"><span class="sr-only">完成</span></th><th scope="col">任务</th><th scope="col" class="col-project">项目</th><th scope="col" class="col-executor">执行者</th><th scope="col">状态</th><th scope="col" class="col-priority">优先级</th><th scope="col" class="col-due">截止时间</th></tr></thead>
          <tbody>
            <tr v-for="item in tasks" :key="item.id" :aria-selected="opened === item.id" :data-state="item.work_state" @click="opened = item.id">
              <td v-if="writable" class="col-check" @click.stop><input type="checkbox" class="task-check" :checked="displayDone(item)" :disabled="operation.locked || !!pendingId" :aria-label="`${item.done ? '取消完成' : '完成'}：${item.title}`" @change="toggle(item, $event.target as HTMLInputElement)"></td>
              <td class="col-title"><button type="button" class="task-title" @click.stop="opened = item.id">{{ item.title }}</button><span v-if="item.claimable" class="chip">可自由接取</span><span v-if="item.labels.length" class="task-labels">{{ item.labels.join(' · ') }}</span></td>
              <td class="col-project">{{ projectTitles.get(item.project_id) ?? '已归档的项目' }}</td>
              <td class="col-executor"><span class="task-executor" :class="{ 'is-agent': executor(item).agent }">{{ executor(item).name }}</span></td>
              <td><span class="ws-state" :class="`state-${item.work_state}`"><component :is="STATE_META[item.work_state].icon" aria-hidden="true" />{{ STATE_META[item.work_state].short }}</span></td>
              <td class="col-priority"><span class="task-priority" :class="`priority-${priorityTone(item.priority)}`"><Flag aria-hidden="true" />{{ priorities[item.priority] }}</span></td>
              <td class="col-due"><span :class="`due-${dueInfo(item.due_at, item.done).tone}`">{{ dueInfo(item.due_at, item.done).text }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="board && tasks.length" class="task-board">
        <section v-for="column in [{ title: '未完成', done: false }, { title: '已完成', done: true }]" :key="column.title" role="region" :aria-label="column.title" class="task-column" :class="{ 'is-drop-target': dropColumn === column.done }" @dragover="dragOver($event, column.done)" @dragleave="dropColumn = null" @drop="drop($event, column.done)">
          <h2>{{ column.title }}<span class="column-count">{{ tasks.filter(t => t.done === column.done).length }}</span></h2>
          <article v-for="item in tasks.filter(t => t.done === column.done)" :key="item.id" class="task-record" :class="{ 'is-done': displayDone(item), 'is-dragging': dragging === item.id }" :draggable="canDrag" @dragstart="dragStart($event, item)" @dragend="dragEnd">
            <input v-if="writable" type="checkbox" class="task-check" :checked="displayDone(item)" :disabled="operation.locked || !!pendingId" :aria-label="`${item.done ? '取消完成' : '完成'}：${item.title}`" @change="toggle(item, $event.target as HTMLInputElement)">
            <div class="task-main">
              <button type="button" class="task-title" @click="opened = item.id">{{ item.title }}</button>
              <p class="task-meta">
                <span class="task-context">{{ projectTitles.get(item.project_id) ?? '已归档的项目' }}</span>
                <span class="ws-state" :class="`state-${item.work_state}`"><component :is="STATE_META[item.work_state].icon" aria-hidden="true" />{{ STATE_META[item.work_state].short }}</span>
                <span class="chip" :class="`chip-due-${dueInfo(item.due_at, item.done).tone}`">{{ dueInfo(item.due_at, item.done).text }}</span>
              </p>
            </div>
          </article>
        </section>
      </div>
      <p v-if="board && writable" class="form-help">把卡片拖到另一列，或勾选卡片上的方框，改变完成状态。</p>
      <footer v-if="tasks.length" class="tasks-footer">
        <span>已显示 {{ tasks.length }} 项<template v-if="shownTotal !== undefined">，共 {{ shownTotal }} 项</template></span>
        <button v-if="query.hasNextPage.value && !isAccessError(query.error.value)" type="button" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多任务</button>
      </footer>
    </section>

    <aside v-if="showPanel" class="ws-panel tasks-side" aria-label="任务详情">
      <div class="ws-panel-body">
        <h2 v-if="opened === 'new'" class="tasks-side-title">新建任务</h2>
        <DataState v-if="opened !== 'new'" :loading="detail.isPending.value" :error="detail.error.value" :empty="false" empty-text="" @retry="detail.refetch()" />
        <TaskDetailPanel v-if="opened === 'new' || (detail.data.value && !detail.error.value)" :key="`${opened}:${editorGeneration}`" :task="opened === 'new' ? undefined : detail.data.value" :projects="projectItems" :writable="writable" :locked="operation.locked" :names="names" closable @submit="submit" @close="close" />
        <button v-if="opened === 'new'" type="button" class="secondary-button" @click="close">取消</button>
        <OperationPanel :writable="writable" v-on="operationEvents" />
        <p v-if="reloadError" role="alert">{{ reloadError }}</p>
      </div>
    </aside>
  </div>

  <!-- 1440 以下：详情放进右侧抽屉（规范 §8.4 Task Drawer） -->
  <DrawerDetail v-if="opened && !wide" :title="opened === 'new' ? '新建任务' : '任务详情'" :busy="operation.current?.status === 'pending'" @close="close">
    <DataState :loading="projects.isPending.value" :error="projects.error.value" :empty="!projectItems.length" empty-text="还没有项目，请先在「项目」页新建一个项目。" @retry="projects.refetch()" />
    <DataState v-if="opened !== 'new'" :loading="detail.isPending.value" :error="detail.error.value" :empty="false" empty-text="" @retry="detail.refetch()" />
    <TaskDetailPanel v-if="opened === 'new' || (detail.data.value && !detail.error.value)" :key="`${opened}:${editorGeneration}`" :task="opened === 'new' ? undefined : detail.data.value" :projects="projectItems" :writable="writable" :locked="operation.locked" :names="names" @submit="submit" />
    <OperationPanel :writable="writable" v-on="operationEvents" />
    <p v-if="reloadError" role="alert">{{ reloadError }}</p>
  </DrawerDetail>
</template>
