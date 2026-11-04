<script setup lang="ts">
import { useForm } from 'vee-validate'
import { toTypedSchema } from '../../services/forms'
import { z } from 'zod'
import { ref, watch } from 'vue'
import type { Schema } from '../../services/api'
import type { TaskCommand } from '../../stores/taskOperations'
import { LABEL_MAX, LABELS_MAX, parseLabels, priorities, toLocalInput } from './shared'
import { formatTime } from '../time'
import TextField from '../../components/TextField.vue'
import RelatedDocuments from '../knowledge/RelatedDocuments.vue'
import TaskRuns from '../timeline/TaskRuns.vue'
import TaskClaimPanel from './TaskClaimPanel.vue'
import { useAssignees } from '../timeline/api'
import { useAuthStore } from '../../stores/auth'
const props = defineProps<{ task?: Schema['TaskOut']; projects: Schema['ProjectOut'][]; writable: boolean; locked: boolean }>()
const emit = defineEmits<{ submit: [command: TaskCommand] }>()
const labelsSchema = z.string().refine((v) => parseLabels(v).length <= LABELS_MAX, `最多 ${LABELS_MAX} 个标签`).refine((v) => parseLabels(v).every((l) => l.length <= LABEL_MAX), `每个标签不超过 ${LABEL_MAX} 字`)
const validTime = (v: string) => !v || !Number.isNaN(new Date(v).getTime())
const schema = z.object({ title: z.string().trim().min(1, '请输入任务标题').max(250), description: z.string().max(10000), labels: labelsSchema, project_id: props.task ? z.string().uuid().or(z.literal('')) : z.string({ error: '请选择项目' }).uuid('请选择项目'), priority: z.number().int().min(0).max(5), due: z.string().refine(validTime, '截止时间无效'), planned: z.string().refine(validTime, '计划开始时间无效'), assignee: z.string(), scope: z.enum(['all', 'project']), done: z.boolean() })
// 执行者（T-0105）：'' 未分配、'user:<id>' 成员、'instance:<id>' OpenClaw 实例、'open' 可自由接取（T-0111，还没人领）
const assigneeOf = (task?: Schema['TaskOut']) => (task?.assignee_instance_id ? `instance:${task.assignee_instance_id}` : task?.assignee_user_id ? `user:${task.assignee_user_id}` : task?.claim_mode === 'open' ? 'open' : '')
const valuesOf = (task?: Schema['TaskOut']) => ({ title: task?.title ?? '', description: task?.description ?? '', labels: (task?.labels ?? []).join('，'), project_id: task?.project_id ?? '', priority: task?.priority ?? 0, due: toLocalInput(task?.due_at ?? null), planned: toLocalInput(task?.planned_start_at ?? null), assignee: assigneeOf(task), scope: task?.claim_scope ?? 'all' as 'all' | 'project', done: task?.done ?? false })
const { handleSubmit, defineField, isSubmitting, values, resetForm } = useForm({ validationSchema: toTypedSchema(schema), initialValues: valuesOf(props.task) })
// 编辑的基准是打开时的任务（T-0049）：后台重新拉取（SSE 失效、对账）拿到新版本时，
// 用户还没改就换成新版本；已经改了就保留输入，保存时仍按打开时的版本提交，真有冲突由后端返回 409
const base = ref(props.task)
watch(() => props.task, (task) => {
  const old = base.value
  if (!task || !old || task.version === old.version) return
  const start = valuesOf(old)
  if ((Object.keys(start) as (keyof typeof start)[]).every((key) => values[key] === start[key])) { base.value = task; resetForm({ values: valuesOf(task) }) }
  else message.value = '这个任务刚在别处更新过。你的修改还在；保存时如果有冲突，会提示重新加载。'
})
const [description] = defineField('description'), [labels] = defineField('labels'), [projectId] = defineField('project_id'), [priority] = defineField('priority'), [due] = defineField('due'), [planned] = defineField('planned'), [assignee] = defineField('assignee'), [scope] = defineField('scope'), [done] = defineField('done')
const auth = useAuthStore()
const assignees = useAssignees(auth.workspaceId!)
// 自由接取只能由 owner / admin 设置（后端也会检查）
const admin = () => ['owner', 'admin'].includes(auth.currentWorkspace?.role ?? '')
const statusLabel: Record<string, string> = { online: '在线', offline: '离线', pending: '还没连接' }
const message = ref('')
const submit = handleSubmit((value) => {
  if (!props.writable) return
  if (props.locked) { message.value = '有任务操作正在进行，请等它结束后再保存。'; return }
  const due_at = value.due ? new Date(value.due).toISOString() : null
  const tags = parseLabels(value.labels)
  if (!props.task) { emit('submit', { method: 'POST', body: { title: value.title, description: value.description, project_id: value.project_id, priority: value.priority, due_at, labels: tags } }); return }
  const old = base.value ?? props.task
  const body: Schema['TaskUpdate'] = { version: old.version }
  if (value.title !== old.title) body.title = value.title
  if (value.description !== old.description) body.description = value.description
  if (tags.join('\n') !== old.labels.join('\n')) body.labels = tags
  if (value.priority !== old.priority) body.priority = value.priority
  if (value.done !== old.done) body.done = value.done
  if (value.project_id && value.project_id !== old.project_id) body.project_id = value.project_id
  if (value.due !== toLocalInput(old.due_at)) body.due_at = due_at
  if (value.planned !== toLocalInput(old.planned_start_at)) body.planned_start_at = value.planned ? new Date(value.planned).toISOString() : null
  if (value.assignee === 'open') {
    if (assigneeOf(old) !== 'open') { body.claim_mode = 'open'; if (old.assignee_user_id || old.assignee_instance_id) { body.assignee_user_id = null; body.assignee_instance_id = null } }
    if (value.scope !== old.claim_scope) body.claim_scope = value.scope
  } else if (value.assignee !== assigneeOf(old)) {
    const [kind, id] = value.assignee.split(':')
    if (kind === 'user') body.assignee_user_id = id
    else if (kind === 'instance') body.assignee_instance_id = id
    else { body.assignee_user_id = null; body.assignee_instance_id = null; if (old.claim_mode === 'open' && admin()) body.claim_mode = 'assigned' }
  }
  if (Object.keys(body).length === 1) { message.value = '没有需要保存的修改'; return }
  emit('submit', { method: 'PATCH', id: old.id, body })
}, ({ errors }) => { message.value = Object.values(errors).join('；') })
function remove() { if (props.task && props.writable && !props.locked && window.confirm('确认删除此任务？此操作无法撤销。')) emit('submit', { method: 'DELETE', id: props.task.id, version: props.task.version }) }
</script>
<template>
  <form aria-label="任务编辑" novalidate @submit.prevent="submit">
    <fieldset :disabled="!writable || locked || isSubmitting">
      <TextField name="title" label="任务标题" :maxlength="250" />
      <label class="form-field">任务项目<select v-model="projectId" aria-label="任务项目"><option value="">{{ task ? '保留当前项目' : '请选择项目' }}</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.title }}</option></select></label>
      <label class="form-field">任务描述<textarea v-model="description" maxlength="10000" rows="5"></textarea></label>
      <label class="form-field">任务标签<input v-model="labels" maxlength="1100" placeholder="用逗号分隔，例如：周报，紧急" aria-describedby="task-labels-help"></label>
      <p id="task-labels-help" class="form-help">最多 20 个标签。</p>
      <label class="form-field">任务优先级<select v-model.number="priority" aria-label="任务优先级"><option v-for="(label, index) in priorities" :key="index" :value="index">{{ label }}</option></select></label>
      <label class="form-field">截止时间<input v-model="due" type="datetime-local"></label>
      <template v-if="task">
        <label class="form-field">计划开始<input v-model="planned" type="datetime-local" aria-describedby="task-planned-help"></label>
        <p id="task-planned-help" class="form-help">时间轴上从计划开始画到截止时间。</p>
        <label class="form-field">执行者<select v-model="assignee" aria-label="执行者">
          <option value="">未分配</option>
          <option v-if="admin() || assignee === 'open'" value="open">可自由接取（空闲的智能体自主领取）</option>
          <optgroup v-if="assignees.data.value?.users.length" label="成员"><option v-for="u in assignees.data.value.users" :key="u.id" :value="`user:${u.id}`">{{ u.display_name }}</option></optgroup>
          <optgroup v-if="assignees.data.value?.instances.length" label="OpenClaw 实例"><option v-for="i in assignees.data.value.instances" :key="i.id" :value="`instance:${i.id}`">{{ i.name }}（{{ statusLabel[i.status] ?? i.status }}）</option></optgroup>
          <option v-if="assignee && assignees.data.value && ![...assignees.data.value.users.map(u => `user:${u.id}`), ...assignees.data.value.instances.map(i => `instance:${i.id}`)].includes(assignee)" :value="assignee">当前执行者（已不可选）</option>
        </select></label>
        <label v-if="assignee === 'open'" class="form-field">谁可以领取<select v-model="scope" aria-label="谁可以领取" :disabled="!admin()"><option value="all">全部智能体</option><option value="project">只限项目成员</option></select></label>
        <p v-if="assignee === 'open'" class="form-help">保存后，工作台用你的 IM 账号通知在线、空闲的智能体；它们看过描述觉得合适就会领取。</p>
      </template>
      <p v-else class="form-help">建好任务后可以设计划开始与执行者。</p>
      <label v-if="task" class="check-field"><input v-model="done" type="checkbox">已完成任务</label>
      <p v-if="message" role="status">{{ message }}</p>
      <button v-if="writable" class="primary-button" :disabled="locked || isSubmitting">{{ task ? '保存任务' : '创建任务' }}</button>
      <button v-if="writable && task" type="button" class="secondary-button" :disabled="locked" @click="remove">删除任务</button>
    </fieldset>
  </form>
  <p v-if="!writable" class="form-help">当前角色只读。</p>
  <p v-if="task?.done_at" class="form-help">完成于 {{ formatTime(task.done_at) }}</p>
  <TaskRuns v-if="task" :key="`runs:${task.id}`" :task="task" :writable="writable" />
  <TaskClaimPanel v-if="task" :key="`claim:${task.id}`" :task="task" />
  <RelatedDocuments v-if="task" :key="task.id" :resource-id="task.id" />
</template>
