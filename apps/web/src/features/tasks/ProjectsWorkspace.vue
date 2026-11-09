<script setup lang="ts">
// 项目管理（T-0082）：工作台自己的项目，member 及以上可以新建、改名、归档、删除；viewer 只读。
// 项目页改造（T-0168，效果图 02-projects；规范第 5 节）：顶部三个数（未归档、需要关注、本周完成）、
// 未归档 / 需要关注 / 已归档筛选、搜索、排序、网格 / 列表，卡片用真实的状态计数，下面是最近动态。
// 写接口用整数 version 做乐观并发：别处改过时返回 409，提示重新加载。删除项目会一起删除它的任务。
// 智能体成员与项目群（T-0110）：owner / admin 新建项目时可以勾选智能体并建 IM 项目群，卡片上管理成员。
import { computed, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { CheckCircle2, FolderKanban, LayoutGrid, List, Plus, RefreshCw, Search, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, errorMessage, isAccessError, type Schema } from '../../services/api'
import { useResourceMutation } from '../resources/mutation'
import { useCreateIntent } from '../../composables/useCreateIntent'
import { useProjectList, useProjectSummary, type ProjectListFilters } from './projectQueries'
import { relativeTime } from '../../widgets/home/format'
import { useAssignees } from '../timeline/api'
import DataState from '../../components/DataState.vue'
import ModalPanel from '../../components/ModalPanel.vue'
import ProjectAgentsPanel from './ProjectAgentsPanel.vue'
import ProjectCard from './ProjectCard.vue'
import ProjectActivityList from './ProjectActivityList.vue'
import '../workspace/workspace-pages.css'
import './projects.css'

type Project = Schema['ProjectOut']
const TITLE_MAX = 120, DESCRIPTION_MAX = 2000
const auth = useAuthStore(), scope = auth.workspaceId!, header = { 'X-Workspace-Id': scope }
const filters = ref<ProjectListFilters>({ scope: 'active', q: '', sort: 'updated' })
const search = ref('')
const query = useProjectList(scope, filters)
const summary = useProjectSummary(scope)
const grid = ref(true)
function applySearch() { filters.value = { ...filters.value, q: search.value } }
function refreshAll() { void query.refetch(); void summary.refetch() }
const mutation = useResourceMutation(scope, ['projects', 'tasks', 'task', 'overview', 'search'])
const cache = useQueryClient()
watch(mutation.message, (text) => { if (text) void cache.invalidateQueries({ predicate: (q) => ['project-summary', 'project-activity'].includes(String(q.queryKey[0])) && q.queryKey[2] === scope }) })
const { busy, needsReload, error, message } = mutation
const blocked = computed(() => mutation.accessDenied.value || isAccessError(query.error.value))
const items = computed(() => blocked.value ? [] : query.data.value?.pages.flatMap((p) => p.items) ?? [])
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer' && !blocked.value)
// 列表视图里的进度：没有任务时不显示百分比
const percent = (project: Project) => { const total = project.open_tasks + project.done_tasks; return total ? `${project.done_tasks}/${total} · ${Math.round((project.done_tasks / total) * 100)}%` : '尚无任务' }
const count = (value: number | undefined) => (value === undefined ? '—' : String(value))
const editing = ref<Project | 'new' | null>(null), title = ref(''), description = ref(''), validation = ref('')
// 智能体与 IM 只开放给 owner / admin（member 新建项目照旧）
const admin = computed(() => writable.value && ['owner', 'admin'].includes(auth.currentWorkspace?.role ?? ''))
const candidates = useAssignees(scope)
const instances = computed(() => candidates.data.value?.instances ?? [])
const picked = ref<string[]>([]), withGroup = ref(true), managing = ref<Project | null>(null)
const statusText: Record<string, string> = { online: '在线', offline: '离线', pending: '未接入', disabled: '已停用' }

function open(project: Project | 'new') {
  if (!writable.value || busy.value || needsReload.value) return
  editing.value = project; validation.value = ''
  title.value = project === 'new' ? '' : project.title
  description.value = project === 'new' ? '' : project.description
  picked.value = []; withGroup.value = true
}
useCreateIntent(() => writable.value && !needsReload.value, () => open('new'))

async function save() {
  const current = editing.value
  if (!current || busy.value) return
  const name = title.value.trim()
  if (!name) { validation.value = '请输入项目名称'; return }
  if (name.length > TITLE_MAX) { validation.value = `项目名称不能超过 ${TITLE_MAX} 字`; return }
  if (description.value.length > DESCRIPTION_MAX) { validation.value = `项目说明不能超过 ${DESCRIPTION_MAX} 字`; return }
  validation.value = ''
  if (current === 'new') {
    const body: Schema['ProjectCreate'] = { title: name, description: description.value, im_group: admin.value && withGroup.value }
    if (admin.value && picked.value.length) body.instance_ids = [...picked.value]
    if (await mutation.run(() => auth.api.POST('/api/v1/projects', { params: { header }, body }), `已新建项目「${name}」`)) editing.value = null
    return
  }
  const body: Schema['ProjectUpdate'] = { version: current.version }
  if (name !== current.title) body.title = name
  if (description.value !== current.description) body.description = description.value
  if (Object.keys(body).length === 1) { validation.value = '没有需要保存的修改'; return }
  if (await mutation.run(() => auth.api.PATCH('/api/v1/projects/{project_id}', { params: { header, path: { project_id: current.id } }, body }), '项目已保存')) editing.value = null
}
async function archive(project: Project) {
  if (!writable.value) return
  await mutation.run(() => auth.api.PATCH('/api/v1/projects/{project_id}', { params: { header, path: { project_id: project.id } }, body: { version: project.version, archived: !project.archived } }), project.archived ? `已恢复项目「${project.title}」` : `已归档项目「${project.title}」，任务保留`)
}
async function remove(project: Project) {
  if (!writable.value || busy.value || needsReload.value) return
  const total = project.open_tasks + project.done_tasks
  const warning = total ? `它的 ${total} 个任务会一起删除，` : ''
  if (!window.confirm(`删除项目「${project.title}」？${warning}此操作无法撤销。`)) return
  await mutation.run(() => auth.api.DELETE('/api/v1/projects/{project_id}', { params: { header, path: { project_id: project.id }, query: { version: project.version } } }), `已删除项目「${project.title}」`)
}
const problem = computed(() => {
  const cause = error.value
  if (!cause) return ''
  return cause instanceof ApiError && cause.code === 'VERSION_CONFLICT' ? '项目已在别处修改，请重新加载后再试。' : errorMessage(cause)
})
</script>
<template>
  <div class="ws-header">
    <div><h1>项目</h1><p>项目进展、待处理事项与协作成员，一处掌握。</p></div>
    <div class="ws-actions">
      <button type="button" class="secondary-button" :disabled="query.isFetching.value" aria-label="刷新项目" @click="refreshAll"><RefreshCw aria-hidden="true" />刷新</button>
      <button v-if="writable" type="button" class="primary-button" :disabled="busy || needsReload" @click="open('new')"><Plus aria-hidden="true" />新建项目</button>
    </div>
  </div>

  <ul class="project-summary" aria-label="项目统计">
    <li class="ws-panel tone-blue"><FolderKanban aria-hidden="true" /><span>未归档项目</span><strong>{{ count(summary.data.value?.active) }}</strong></li>
    <li class="ws-panel tone-red"><TriangleAlert aria-hidden="true" /><span>需要关注</span><strong>{{ count(summary.data.value?.attention) }}</strong></li>
    <li class="ws-panel tone-green" title="本周一 00:00 起完成、现在仍是已完成的任务（不是完整的历史完成次数）"><CheckCircle2 aria-hidden="true" /><span>本周完成</span><strong>{{ count(summary.data.value?.completed) }}</strong><span>项</span></li>
  </ul>
  <p v-if="summary.error.value" role="alert" class="feedback">统计暂时读不到：{{ errorMessage(summary.error.value) }}</p>

  <div class="project-toolbar">
    <div class="ws-chips" role="group" aria-label="筛选项目">
      <button type="button" class="ws-chip" :aria-pressed="filters.scope === 'active'" @click="filters = { ...filters, scope: 'active' }">未归档<span class="ws-chip-count">{{ count(summary.data.value?.active) }}</span></button>
      <button type="button" class="ws-chip tone-red" :aria-pressed="filters.scope === 'attention'" @click="filters = { ...filters, scope: 'attention' }">需要关注<span class="ws-chip-count">{{ count(summary.data.value?.attention) }}</span></button>
      <button type="button" class="ws-chip" :aria-pressed="filters.scope === 'archived'" @click="filters = { ...filters, scope: 'archived' }">已归档</button>
    </div>
    <form class="ws-toolbar" role="search" aria-label="搜索项目" @submit.prevent="applySearch">
      <label class="ws-search"><Search aria-hidden="true" /><input v-model="search" type="search" maxlength="100" placeholder="搜索项目" aria-label="搜索项目" @change="applySearch"></label>
      <select v-model="filters.sort" aria-label="排序"><option value="updated">排序：最近更新</option><option value="title">排序：名称</option></select>
      <div class="ws-segment" role="group" aria-label="显示方式">
        <button type="button" :aria-pressed="grid" aria-label="网格" @click="grid = true"><LayoutGrid aria-hidden="true" /></button>
        <button type="button" :aria-pressed="!grid" aria-label="列表" @click="grid = false"><List aria-hidden="true" /></button>
      </div>
    </form>
  </div>

  <p v-if="problem && !editing" role="alert" class="feedback">{{ problem }}</p>
  <p v-if="message" role="status" class="feedback">{{ message }}</p>
  <button v-if="needsReload" class="secondary-button" :disabled="busy" @click="mutation.reload()">重新加载项目</button>
  <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!items.length" :empty-text="filters.scope === 'attention' ? '没有需要你关注的项目。' : filters.scope === 'archived' ? '没有已归档的项目。' : writable ? '还没有项目，点「新建项目」开始。' : '还没有项目。'" @retry="query.refetch()" />
  <div v-if="grid" class="project-grid">
    <ProjectCard v-for="project in items" :key="project.id" :project="project" :writable="writable" :busy="busy || needsReload" @edit="open(project)" @archive="archive(project)" @remove="remove(project)" @members="managing = project" />
  </div>
  <div v-else class="ws-panel ws-table-wrap">
    <table class="ws-table project-table">
      <thead><tr><th scope="col">项目</th><th scope="col">进度</th><th scope="col">进行中</th><th scope="col">待查看</th><th scope="col">阻塞</th><th scope="col">智能体</th><th scope="col">最近更新</th></tr></thead>
      <tbody>
        <tr v-for="project in items" :key="project.id">
          <td><RouterLink :to="`/projects/${project.id}`">{{ project.title }}</RouterLink><span v-if="project.archived" class="badge">已归档</span></td>
          <td>{{ percent(project) }}</td>
          <td>{{ project.task_states?.running ?? '—' }}</td>
          <td>{{ project.task_states?.awaiting_view ?? '—' }}</td>
          <td>{{ project.task_states?.blocked ?? '—' }}</td>
          <td>{{ project.agents.map((a) => a.name).join('、') || '无' }}</td>
          <td>{{ project.last_activity_at ? relativeTime(project.last_activity_at) : '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <button v-if="query.hasNextPage.value" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多项目</button>
  <p v-if="!writable && !blocked" class="form-help">当前角色只读。</p>

  <ProjectActivityList :scope="scope" />
  <ModalPanel v-if="editing" :title="editing === 'new' ? '新建项目' : '编辑项目'" :busy="busy" @close="!busy && (editing = null)">
    <form aria-label="项目设置" novalidate @submit.prevent="save">
      <label class="form-field">项目名称<input v-model="title" :disabled="busy" :maxlength="TITLE_MAX" autocomplete="off" autofocus></label>
      <label class="form-field">项目说明<textarea v-model="description" :disabled="busy" :maxlength="DESCRIPTION_MAX" rows="4"></textarea></label>
      <fieldset v-if="editing === 'new' && admin" class="form-field">
        <legend>加入的智能体</legend>
        <p v-if="!instances.length" class="form-help">还没有可以加入的智能体。</p>
        <label v-for="i in instances" :key="i.id" class="check-field"><input v-model="picked" type="checkbox" :value="i.id" :disabled="busy">{{ i.name }}（{{ statusText[i.status] ?? i.status }}）<span v-if="i.description" class="form-help">{{ i.description }}</span></label>
        <label class="check-field"><input v-model="withGroup" type="checkbox" :disabled="busy">同时建 IM 项目群（群主是你，成员是你和勾选的智能体）</label>
      </fieldset>
      <p v-if="validation || problem" role="alert">{{ validation || problem }}</p>
      <div class="form-actions"><button class="primary-button" type="submit" :disabled="busy || needsReload">{{ editing === 'new' ? '创建项目' : '保存项目' }}</button></div>
    </form>
  </ModalPanel>
  <ModalPanel v-if="managing" :title="`${managing.title} 的成员`" @close="managing = null">
    <ProjectAgentsPanel :scope="scope" :project="managing" :admin="admin" />
  </ModalPanel>
</template>
