<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { Plus } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { useTaskOperations } from '../../stores/taskOperations'
import { dataOf, errorMessage, type Schema } from '../../services/api'
import { useProjects } from '../../features/tasks/shared'
import type { WidgetValue } from '../registry'
import { todoTag } from './format'
import './home.css'

// 我的待办（T-0164，首页效果图）：执行者是自己的未完成任务（GET /tasks?assignee=me），勾选即完成；
// 添加时直接指派给自己（POST /tasks 带 assignee_user_id）。写入走任务页同一个操作（Idempotency-Key、结果未知可查）
const props = defineProps<{ value: WidgetValue; overview?: Schema['DashboardOverview']; loading?: boolean; failed?: boolean }>()
const auth = useAuthStore(), operation = useTaskOperations(), cache = useQueryClient()
const scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const limit = computed(() => (props.value.widget_type === 'my_todos' ? props.value.config.limit : 5))
const writable = computed(() => !!auth.currentWorkspace && auth.currentWorkspace.role !== 'viewer')
const query = useQuery({
  queryKey: computed(() => ['tasks', user, scope, 'mine', limit.value]),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/tasks', { params: { header, query: { assignee: 'me', status: 'open', sort: 'due_at', limit: limit.value } }, signal })),
})
const items = computed(() => query.data.value?.items ?? [])
const busyId = ref<string | null>(null), problem = ref(''), notice = ref('')
const LOCKED = '还有一个任务操作结果未知：请先到任务页处理，再在这里勾选或添加。'
function refreshAll() { void cache.invalidateQueries({ predicate: (q) => ['tasks', 'task-progress', 'overview'].includes(String(q.queryKey[0])) && q.queryKey[2] === scope }) }
function showFailure() {
  const error = operation.current?.error
  const status = operation.current?.status
  problem.value = status === 'unknown' ? '网络中断，结果未知：请到任务页确认这一步有没有生效。' : status === 'conflict' ? '任务已在别处修改，已重新读取。' : error ? errorMessage(error) : '操作没有完成，请重试。'
  if (status === 'conflict' || status === 'failed') operation.clearResolved()
  refreshAll()
}
async function complete(item: Schema['TaskOut'], box: HTMLInputElement) {
  if (!writable.value || busyId.value) { box.checked = false; return }
  if (operation.locked) { box.checked = false; problem.value = LOCKED; return }
  busyId.value = item.id; problem.value = ''; notice.value = ''
  const ok = await operation.submit({ method: 'PATCH', id: item.id, body: { version: item.version, done: true } })
  busyId.value = null
  if (!ok) { box.checked = false; showFailure(); return }
  operation.clearResolved()
  notice.value = `已完成：${item.title}`
  refreshAll()
}

// 添加待办：项目默认用上次选的（记在本机），没有就用第一个
const adding = ref(false), title = ref(''), project = ref(''), saving = ref(false)
const projects = useProjects(scope)
const projectItems = computed(() => projects.data.value?.pages.flatMap((page) => page.items) ?? [])
const PROJECT_KEY = `home-todo-project:${user}:${scope}`
function remembered() { try { return localStorage.getItem(PROJECT_KEY) ?? '' } catch { return '' } }
watch(projectItems, (list) => {
  if (list.some((p) => p.id === project.value)) return
  const saved = remembered()
  project.value = list.find((p) => p.id === saved)?.id ?? list[0]?.id ?? ''
}, { immediate: true })
async function add() {
  const text = title.value.trim()
  if (!text || !project.value || saving.value || !auth.user) return
  if (operation.locked) { problem.value = LOCKED; return }
  saving.value = true; problem.value = ''; notice.value = ''
  const ok = await operation.submit({ method: 'POST', body: { title: text, description: '', project_id: project.value, priority: 0, due_at: null, labels: [], assignee_user_id: auth.user.id } })
  saving.value = false
  if (!ok) { showFailure(); return }
  operation.clearResolved()
  try { localStorage.setItem(PROJECT_KEY, project.value) } catch { /* 本机存储不可用时不记 */ }
  title.value = ''; adding.value = false
  notice.value = `已添加待办：${text}`
  refreshAll()
}
const tagOf = (item: Schema['TaskOut']) => todoTag(item)
</script>

<template>
  <div class="my-todos">
    <p v-if="query.isPending.value" role="status" class="card-state">正在加载待办…</p>
    <div v-else-if="query.error.value && !query.data.value" class="card-state"><p role="alert">{{ errorMessage(query.error.value) }}</p><button type="button" class="secondary-button" @click="query.refetch()">重试</button></div>
    <template v-else>
      <p v-if="!items.length" class="card-state">没有指派给你的未完成任务。</p>
      <ul v-else class="todo-list" aria-label="我的待办">
        <li v-for="item in items" :key="item.id" class="todo-item">
          <label class="todo-check">
            <input type="checkbox" :disabled="!writable || !!busyId" :aria-label="`完成：${item.title}`" @change="complete(item, $event.target as HTMLInputElement)">
          </label>
          <RouterLink class="todo-title" :to="{ path: '/tasks', query: { task: item.id } }">{{ item.title }}</RouterLink>
          <span v-if="tagOf(item)" class="todo-tag" :class="`tone-${tagOf(item)!.tone}`">{{ tagOf(item)!.text }}</span>
        </li>
      </ul>
      <p v-if="notice" role="status" class="card-state">{{ notice }}</p>
      <p v-if="problem" role="alert" class="card-state">{{ problem }}</p>
      <template v-if="writable">
        <form v-if="adding" class="todo-add-form" aria-label="添加待办" @submit.prevent="add">
          <input v-model="title" maxlength="250" placeholder="要做什么？" aria-label="待办内容" :disabled="saving">
          <select v-model="project" aria-label="放进哪个项目" :disabled="saving || !projectItems.length"><option v-for="p in projectItems" :key="p.id" :value="p.id">{{ p.title }}</option></select>
          <button class="primary-button" :disabled="saving || !title.trim() || !project">{{ saving ? '正在添加…' : '添加' }}</button>
          <button type="button" class="secondary-button" :disabled="saving" @click="adding = false; title = ''">取消</button>
          <p v-if="!projectItems.length && !projects.isPending.value" class="form-help">还没有项目：先到 <RouterLink to="/projects">项目</RouterLink> 新建一个。</p>
        </form>
        <button v-else type="button" class="home-add" @click="adding = true"><Plus aria-hidden="true" />添加待办</button>
      </template>
    </template>
  </div>
</template>
