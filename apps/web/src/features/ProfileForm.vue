<script setup lang="ts">
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { useAuthStore } from '../stores/auth'
import { dataOf } from '../services/api'
import { profileSchema, useAction } from './forms'
import TextField from '../components/TextField.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
const auth = useAuthStore()
const { handleSubmit, isSubmitting } = useForm({ validationSchema: toTypedSchema(profileSchema), initialValues: { display_name: auth.user?.display_name ?? '' } })
const { pending, error, notice, run } = useAction()
const submit = handleSubmit((body) => run(async () => {
  auth.user = dataOf(await auth.api.PATCH('/api/v1/me', { body }))
}, '个人资料已保存'))
</script>
<template>
  <form class="panel form-panel" aria-label="个人资料" novalidate @submit.prevent="submit">
    <h2>个人资料</h2><p class="form-help">{{ auth.user?.email }}</p>
    <TextField name="display_name" label="显示名" autocomplete="nickname" :maxlength="64" />
    <ActionFeedback :error="error" :notice="notice" />
    <button class="primary-button" :disabled="pending || isSubmitting">{{ pending ? '正在保存…' : '保存个人资料' }}</button>
  </form>
</template>
