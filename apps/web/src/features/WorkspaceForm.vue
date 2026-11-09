<script setup lang="ts">
import { computed } from 'vue'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '../services/forms'
import { useAuthStore } from '../stores/auth'
import { dataOf, type Schema } from '../services/api'
import { workspaceSchema, useAction } from './forms'
import TextField from '../components/TextField.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
import { initialOf, roleName } from '../components/management/roles'

// 工作空间（效果图 02 中卡）：owner / admin 改名称，单独保存；显示本人角色。不提供删除工作空间或转移 owner
const props = defineProps<{ workspace: Schema['WorkspaceOut'] }>()
const auth = useAuthStore()
const { handleSubmit, isSubmitting } = useForm({ validationSchema: toTypedSchema(workspaceSchema), initialValues: { name: props.workspace.name } })
const { pending, error, notice, run } = useAction()
const initial = computed(() => initialOf(props.workspace.name, 'W'))
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
  <form class="ws-panel mg-section st-card" aria-label="工作空间设置" novalidate @submit.prevent="submit">
    <h2>工作空间</h2>
    <div class="st-identity">
      <span class="mg-avatar is-square" aria-hidden="true">{{ initial }}</span>
      <div class="st-fields">
        <TextField name="name" label="工作空间名称" :readonly="!auth.canManage" :maxlength="64" />
        <div class="st-readonly"><span class="st-label">当前角色</span><span class="mg-role" :class="`is-${workspace.role}`">{{ roleName(workspace.role) }} <small>{{ workspace.role }}</small></span></div>
      </div>
    </div>
    <p v-if="!auth.canManage" class="form-help">只有所有者或管理员可以修改工作空间名称。</p>
    <ActionFeedback :error="error" :notice="notice" />
    <div v-if="auth.canManage"><button class="primary-button" :disabled="pending || isSubmitting">{{ pending ? '正在保存…' : '保存工作空间' }}</button></div>
  </form>
</template>
