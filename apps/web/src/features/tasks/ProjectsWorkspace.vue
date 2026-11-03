<script setup lang="ts">
// 项目管理（T-0082）：工作台自己的项目，member 及以上可以新建、改名、归档、删除；viewer 只读。
// 写接口用整数 version 做乐观并发：别处改过时返回 409，提示重新加载。删除项目会一起删除它的任务。
import { computed, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, errorMessage, isAccessError, type Schema } from '../../services/api'
import { useResourceMutation } from '../resources/mutation'
import { useCreateIntent } from '../../composables/useCreateIntent'
import { useProjects } from './shared'
import { formatTime } from '../time'
import DataState from '../../components/DataState.vue'
import ModalPanel from '../../components/ModalPanel.vue'

type Project = Schema['ProjectOut']
const TITLE_MAX = 120, DESCRIPTION_MAX = 2000
const auth = useAuthStore(), scope = auth.workspaceId!, header = { 'X-Workspace-Id': scope }
const showArchived = ref(false)
const query = useProjects(scope, showArchived)
const mutation = useResourceMutation(scope, ['projects', 'tasks', 'task', 'overview', 'search'])
const { busy, needsReload, error, message } = mutation
const blocked = computed(() => mutation.accessDenied.value || isAccessError(query.error.value))
const items = computed(() => blocked.value ? [] : query.data.value?.pages.flatMap((p) => p.items) ?? [])
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer' && !blocked.value)
// 完成进度（T-0084）：已完成 / 全部；没有任务时是 0
const percent = (project: Project) => { const total = project.open_tasks + project.done_tasks; return total ? Math.round((project.done_tasks / total) * 100) : 0 }
const editing = ref<Project | 'new' | null>(null), title = ref(''), description = ref(''), validation = ref('')

function open(project: Project | 'new') {
  if (!writable.value || busy.value || needsReload.value) return
  editing.value = project; validation.value = ''
  title.value = project === 'new' ? '' : project.title
  description.value = project === 'new' ? '' : project.description
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
    if (await mutation.run(() => auth.api.POST('/api/v1/projects', { params: { header }, body: { title: name, description: description.value } }), `已新建项目「${name}」`)) editing.value = null
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
  <div class="task-toolbar">
    <div class="form-actions">
      <label class="check-field"><input v-model="showArchived" type="checkbox">显示已归档的项目</label>
      <button class="secondary-button" :disabled="query.isFetching.value" @click="query.refetch()">刷新项目</button>
    </div>
    <button v-if="writable" class="primary-button" :disabled="busy || needsReload" @click="open('new')">新建项目</button>
  </div>
  <p v-if="problem && !editing" role="alert" class="feedback">{{ problem }}</p>
  <p v-if="message" role="status" class="feedback">{{ message }}</p>
  <button v-if="needsReload" class="secondary-button" :disabled="busy" @click="mutation.reload()">重新加载项目</button>
  <DataState :loading="query.isPending.value" :error="query.error.value" :empty="!items.length" :empty-text="writable ? '还没有项目，点「新建项目」开始。' : '还没有项目。'" @retry="query.refetch()" />
  <div class="project-grid">
    <section v-for="project in items" :key="project.id" class="panel form-panel integration-card project-card" role="region" :aria-label="`项目 ${project.title}`">
      <div class="section-heading"><h2>{{ project.title }}</h2><span v-if="project.archived" class="badge">已归档</span></div>
      <p v-if="project.description" class="note-text">{{ project.description }}</p>
      <div class="progress-row"><div class="progress-track" role="progressbar" :aria-label="`${project.title} 完成进度`" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="percent(project)"><span :style="{ width: `${percent(project)}%` }"></span></div><span class="progress-value">{{ percent(project) }}%</span></div>
      <p class="form-help">未完成 {{ project.open_tasks }} · 已完成 {{ project.done_tasks }} · 更新于 {{ formatTime(project.updated_at) }}</p>
      <div class="form-actions">
        <RouterLink :to="{ path: '/tasks', query: { project_id: project.id } }">查看任务</RouterLink>
        <RouterLink :to="{ path: '/knowledge', query: { linked_resource_id: project.id } }">相关文档</RouterLink>
        <template v-if="writable">
          <button class="secondary-button" :aria-label="`编辑项目 ${project.title}`" :disabled="busy || needsReload" @click="open(project)">编辑</button>
          <button class="secondary-button" :aria-label="`${project.archived ? '恢复' : '归档'}项目 ${project.title}`" :disabled="busy || needsReload" @click="archive(project)">{{ project.archived ? '恢复' : '归档' }}</button>
          <button class="secondary-button" :aria-label="`删除项目 ${project.title}`" :disabled="busy || needsReload" @click="remove(project)">删除</button>
        </template>
      </div>
    </section>
  </div>
  <button v-if="query.hasNextPage.value" class="secondary-button" :disabled="query.isFetching.value" @click="query.fetchNextPage()">加载更多项目</button>
  <p v-if="!writable && !blocked" class="form-help">当前角色只读。</p>
  <ModalPanel v-if="editing" :title="editing === 'new' ? '新建项目' : '编辑项目'" :busy="busy" @close="!busy && (editing = null)">
    <form aria-label="项目设置" novalidate @submit.prevent="save">
      <label class="form-field">项目名称<input v-model="title" :disabled="busy" :maxlength="TITLE_MAX" autocomplete="off" autofocus></label>
      <label class="form-field">项目说明<textarea v-model="description" :disabled="busy" :maxlength="DESCRIPTION_MAX" rows="4"></textarea></label>
      <p v-if="validation || problem" role="alert">{{ validation || problem }}</p>
      <div class="form-actions"><button class="primary-button" type="submit" :disabled="busy || needsReload">{{ editing === 'new' ? '创建项目' : '保存项目' }}</button></div>
    </form>
  </ModalPanel>
</template>
