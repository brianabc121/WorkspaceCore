<script setup lang="ts">
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { useAuthStore } from '../stores/auth'
import { dataOf, type Schema } from '../services/api'
import { workspaceSchema, useAction } from './forms'
import TextField from '../components/TextField.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
const props = defineProps<{ workspace: Schema['WorkspaceOut'] }>()
const auth = useAuthStore()
const { handleSubmit, isSubmitting } = useForm({ validationSchema: toTypedSchema(workspaceSchema), initialValues: { name: props.workspace.name } })
const { pending, error, notice, run } = useAction()
const submit = handleSubmit((body) => {
  if (!auth.canManage || auth.workspaceId !== props.workspace.id) return
  const id = props.workspace.id
  return run(async () => {
    const updated = dataOf(await auth.api.PATCH('/api/v1/workspaces/{workspace_id}', { params: { path: { workspace_id: id } }, body }))
    auth.workspaces = auth.workspaces.map((item) => item.id === id ? updated : item)
  }, '工作空间已保存')
})
</script>
<template>
  <form class="panel form-panel" aria-label="工作空间设置" novalidate @submit.prevent="submit">
    <h2>工作空间</h2><p class="form-help">当前角色：{{ workspace.role }}</p>
    <TextField name="name" label="工作空间名称" :readonly="!auth.canManage" :maxlength="64" />
    <p v-if="!auth.canManage" class="form-help">只有 owner 或 admin 可以修改工作空间名称。</p>
    <ActionFeedback :error="error" :notice="notice" />
    <button v-if="auth.canManage" class="primary-button" :disabled="pending || isSubmitting">{{ pending ? '正在保存…' : '保存工作空间' }}</button>
  </form>
</template>
