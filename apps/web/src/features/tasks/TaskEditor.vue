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
const props = defineProps<{ task?: Schema['TaskOut']; projects: Schema['ProjectOut'][]; writable: boolean; locked: boolean }>()
const emit = defineEmits<{ submit: [command: TaskCommand] }>()
const labelsSchema = z.string().refine((v) => parseLabels(v).length <= LABELS_MAX, `最多 ${LABELS_MAX} 个标签`).refine((v) => parseLabels(v).every((l) => l.length <= LABEL_MAX), `每个标签不超过 ${LABEL_MAX} 字`)
const schema = z.object({ title: z.string().trim().min(1, '请输入任务标题').max(250), description: z.string().max(10000), labels: labelsSchema, project_id: props.task ? z.string().uuid().or(z.literal('')) : z.string({ error: '请选择项目' }).uuid('请选择项目'), priority: z.number().int().min(0).max(5), due: z.string().refine((v) => !v || !Number.isNaN(new Date(v).getTime()), '截止时间无效'), done: z.boolean() })
const valuesOf = (task?: Schema['TaskOut']) => ({ title: task?.title ?? '', description: task?.description ?? '', labels: (task?.labels ?? []).join('，'), project_id: task?.project_id ?? '', priority: task?.priority ?? 0, due: toLocalInput(task?.due_at ?? null), done: task?.done ?? false })
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
const [description] = defineField('description'), [labels] = defineField('labels'), [projectId] = defineField('project_id'), [priority] = defineField('priority'), [due] = defineField('due'), [done] = defineField('done')
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
      <label v-if="task" class="check-field"><input v-model="done" type="checkbox">已完成任务</label>
      <p v-if="message" role="status">{{ message }}</p>
      <button v-if="writable" class="primary-button" :disabled="locked || isSubmitting">{{ task ? '保存任务' : '创建任务' }}</button>
      <button v-if="writable && task" type="button" class="secondary-button" :disabled="locked" @click="remove">删除任务</button>
    </fieldset>
  </form>
  <p v-if="!writable" class="form-help">当前角色只读。</p>
  <p v-if="task?.done_at" class="form-help">完成于 {{ formatTime(task.done_at) }}</p>
  <RelatedDocuments v-if="task" :key="task.id" :resource-id="task.id" />
</template>
