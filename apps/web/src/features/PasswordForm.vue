<script setup lang="ts">
import { useForm } from 'vee-validate'
import { toTypedSchema } from '../services/forms'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { passwordSchema, useAction } from './forms'
import TextField from '../components/TextField.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
const auth = useAuthStore()
const queryClient = useQueryClient()
const { handleSubmit, resetForm, isSubmitting } = useForm({ validationSchema: toTypedSchema(passwordSchema), initialValues: { current_password: '', new_password: '' } })
const { pending, error, notice, run } = useAction()
const submit = handleSubmit(async (body) => {
  await run(async () => {
    await auth.changePassword(body)
    await queryClient.invalidateQueries({ queryKey: ['sessions'] })
  }, '密码已更新，其他会话已撤销')
  resetForm()
})
</script>
<template>
  <form class="panel form-panel" aria-label="修改密码" novalidate @submit.prevent="submit">
    <h2>修改密码</h2><p class="form-help">修改成功后其他会话将撤销，当前浏览器保持登录。</p>
    <TextField name="current_password" label="当前密码" type="password" autocomplete="current-password" :maxlength="128" />
    <TextField name="new_password" label="新密码" type="password" autocomplete="new-password" :maxlength="128" />
    <p class="form-help">新密码需要 12–128 个字符。</p>
    <ActionFeedback :error="error" :notice="notice" />
    <button class="primary-button" :disabled="pending || isSubmitting">{{ pending ? '正在修改…' : '修改密码' }}</button>
  </form>
</template>
