<script setup lang="ts">
import { computed } from 'vue'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '../services/forms'
import { useAuthStore } from '../stores/auth'
import { dataOf } from '../services/api'
import { profileSchema, useAction } from './forms'
import TextField from '../components/TextField.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
import { initialOf } from '../components/management/roles'

// 个人资料（效果图 02 左卡）：登录邮箱只读、显示名可改；头像用首字母，不提供上传（没有这项功能）
const auth = useAuthStore()
const { handleSubmit, isSubmitting } = useForm({ validationSchema: toTypedSchema(profileSchema), initialValues: { display_name: auth.user?.display_name ?? '' } })
const { pending, error, notice, run } = useAction()
const initial = computed(() => initialOf(auth.user?.display_name, auth.user?.email))
const submit = handleSubmit((body) => run(async () => {
  auth.user = dataOf(await auth.api.PATCH('/api/v1/me', { body }))
}, '个人资料已保存'))
</script>
<template>
  <form class="ws-panel mg-section st-card" aria-label="个人资料" novalidate @submit.prevent="submit">
    <h2>个人资料</h2>
    <div class="st-identity">
      <span class="mg-avatar" aria-hidden="true">{{ initial }}</span>
      <div class="st-fields">
        <div class="st-readonly">
          <span class="st-label">登录邮箱</span>
          <span class="st-value">{{ auth.user?.email }}</span>
          <span class="asv-pending">只读</span>
        </div>
        <TextField name="display_name" label="显示名" autocomplete="nickname" :maxlength="64" />
      </div>
    </div>
    <ActionFeedback :error="error" :notice="notice" />
    <div><button class="primary-button" :disabled="pending || isSubmitting">{{ pending ? '正在保存…' : '保存个人资料' }}</button></div>
  </form>
</template>
