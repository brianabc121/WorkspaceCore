<script setup lang="ts">
import { z } from 'zod'
import { useForm } from 'vee-validate'
import { Info, LockKeyhole } from '@lucide/vue'
import { toTypedSchema } from '../services/forms'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../stores/auth'
import { passwordSchema, useAction } from './forms'
import TextField from '../components/TextField.vue'
import ActionFeedback from '../components/ActionFeedback.vue'

// 修改密码（T-0193，效果图 04 左卡）：“确认新密码”只在前端核对，请求仍只有 current_password / new_password；
// 每次提交（成功或失败）后清空三个输入框，密码不留在页面上
const formSchema = passwordSchema.extend({ confirm_new_password: z.string().max(128) })
  .refine((v) => v.new_password === v.confirm_new_password, { message: '两次输入的新密码不一致', path: ['confirm_new_password'] })
const auth = useAuthStore()
const queryClient = useQueryClient()
const { handleSubmit, resetForm, isSubmitting } = useForm({ validationSchema: toTypedSchema(formSchema), initialValues: { current_password: '', new_password: '', confirm_new_password: '' } })
const { pending, error, notice, run } = useAction()
const submit = handleSubmit(async ({ current_password, new_password }) => {
  await run(async () => {
    await auth.changePassword({ current_password, new_password })
    await queryClient.invalidateQueries({ queryKey: ['sessions'] })
  }, '密码已更新，其他会话已撤销')
  resetForm()
})
</script>
<template>
  <form class="ws-panel mg-section sc-card" aria-label="修改密码" novalidate @submit.prevent="submit">
    <h2 class="sc-title"><span class="sc-icon" aria-hidden="true"><LockKeyhole /></span>修改密码</h2>
    <TextField name="current_password" label="当前密码" type="password" autocomplete="current-password" :maxlength="128" />
    <TextField name="new_password" label="新密码" type="password" autocomplete="new-password" :maxlength="128" />
    <p class="form-help sc-help"><Info aria-hidden="true" />12–128 个字符。</p>
    <TextField name="confirm_new_password" label="确认新密码" type="password" autocomplete="new-password" :maxlength="128" />
    <p class="mg-hint"><Info aria-hidden="true" />修改成功后，其他会话将撤销，当前浏览器保持登录。</p>
    <ActionFeedback :error="error" :notice="notice" />
    <button class="primary-button sc-submit" :disabled="pending || isSubmitting">{{ pending ? '正在修改…' : '修改密码' }}</button>
  </form>
</template>
