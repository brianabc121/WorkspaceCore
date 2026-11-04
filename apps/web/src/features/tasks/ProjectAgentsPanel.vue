<script setup lang="ts">
// 项目的智能体成员与项目群（T-0110，docs/v2/AGENT_OPS_PLAN.md 第 2.1、2.2 节）。
// 加入项目 = 能读写项目知识库、收到项目简报，有项目群时也在群里；owner / admin 能加入、移出、建项目群或重试。
import { computed, ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'
import { useAssignees } from '../timeline/api'
import { groupState } from './projectAgents'
import DataState from '../../components/DataState.vue'

const props = defineProps<{ scope: string; project: Schema['ProjectOut']; admin: boolean }>()
const auth = useAuthStore(), cache = useQueryClient(), header = { 'X-Workspace-Id': props.scope }
const list = useQuery({
  queryKey: ['projects', auth.user?.id, props.scope, 'agents', props.project.id],
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/projects/{project_id}/agents', { params: { header, path: { project_id: props.project.id } }, signal })),
})
const candidates = useAssignees(props.scope)
const members = computed(() => list.data.value?.items ?? [])
const group = computed(() => list.data.value?.im_group ?? props.project.im_group)
const addable = computed(() => (candidates.data.value?.instances ?? []).filter((i) => !members.value.some((m) => m.instance_id === i.id)))
const picked = ref(''), busy = ref(false), problem = ref(''), notice = ref('')
const statusText: Record<string, string> = { online: '在线', offline: '离线', pending: '未接入', disabled: '已停用' }
const state = computed(() => groupState(group.value))

async function act(effect: () => Promise<unknown>, done: string) {
  if (busy.value) return
  busy.value = true; problem.value = ''; notice.value = ''
  try {
    await effect()
    await cache.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'projects' && q.queryKey[2] === props.scope })
    notice.value = done
  } catch (cause) {
    problem.value = cause instanceof ApiError && cause.code === 'IM_NOT_CONFIGURED' ? '没有部署 OpenIM，不能建项目群。' : errorMessage(cause)
  } finally { busy.value = false }
}
const path = (instance_id: string) => ({ project_id: props.project.id, instance_id })
function add() {
  const id = picked.value, name = addable.value.find((i) => i.id === id)?.name ?? ''
  if (!id) return
  void act(async () => { dataOf(await auth.api.PUT('/api/v1/projects/{project_id}/agents/{instance_id}', { params: { header: { ...header }, path: path(id) } })); picked.value = '' }, `「${name}」已加入项目`)
}
function remove(member: Schema['ProjectAgentOut']) {
  if (!window.confirm(`把「${member.name}」移出项目？它不能再读写项目知识库，也会被移出项目群；它已经领取的任务不受影响。`)) return
  void act(async () => dataOf(await auth.api.DELETE('/api/v1/projects/{project_id}/agents/{instance_id}', { params: { header, path: path(member.instance_id) } })), `「${member.name}」已移出项目`)
}
function requestGroup() {
  void act(async () => dataOf(await auth.api.POST('/api/v1/projects/{project_id}/im-group', { params: { header, path: { project_id: props.project.id } } })), group.value.status === 'none' ? '正在建项目群' : '已重新同步项目群')
}
</script>
<template>
  <section class="project-agents" aria-label="项目成员">
    <h3>智能体成员</h3>
    <DataState :loading="list.isPending.value" :error="list.error.value" :empty="!members.length" empty-text="还没有智能体加入这个项目。" @retry="list.refetch()" />
    <ul v-if="members.length" class="project-agent-list">
      <li v-for="member in members" :key="member.instance_id">
        <span class="project-agent-name">{{ member.name }}</span>
        <span class="badge">{{ statusText[member.status] ?? member.status }}</span>
        <span v-if="group.status !== 'none'" class="form-help">{{ member.in_group ? '在项目群里' : '还没进项目群' }}</span>
        <span v-if="member.access === 'read'" class="form-help">知识库只读</span>
        <p v-if="member.description" class="form-help">{{ member.description }}</p>
        <button v-if="admin" class="secondary-button" :aria-label="`移出 ${member.name}`" :disabled="busy" @click="remove(member)">移出</button>
      </li>
    </ul>
    <div v-if="admin" class="form-actions">
      <label class="form-field">加入智能体<select v-model="picked" :disabled="busy || !addable.length"><option value="">{{ addable.length ? '选择一个智能体' : '没有可加入的智能体' }}</option><option v-for="i in addable" :key="i.id" :value="i.id">{{ i.name }}（{{ statusText[i.status] ?? i.status }}）</option></select></label>
      <button class="primary-button" :disabled="busy || !picked" @click="add">加入</button>
    </div>
    <h3>项目群</h3>
    <p class="form-help" role="status">{{ state.text }}</p>
    <div class="form-actions">
      <RouterLink v-if="admin && group.conversation_id" :to="{ path: '/im', query: { c: group.conversation_id } }">打开项目群</RouterLink>
      <button v-if="admin && state.action" class="secondary-button" :disabled="busy" @click="requestGroup">{{ state.action }}</button>
    </div>
    <p v-if="problem" role="alert">{{ problem }}</p>
    <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
  </section>
</template>
