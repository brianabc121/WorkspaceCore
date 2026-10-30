<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { useAuthStore } from '../stores/auth'
import { ApiError, type Schema } from '../services/api'
import { loginSchema, setupSchema, useAction } from '../features/forms'
import { safeRedirect } from '../app/redirect'
import TextField from '../components/TextField.vue'
import ActionFeedback from '../components/ActionFeedback.vue'
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const installing = computed(() => route.path === '/setup')
const title = computed(() => installing.value ? '首次安装' : '登录')
const { handleSubmit, setFieldValue, isSubmitting } = useForm<Schema['SetupRequest']>({
  validationSchema: computed(() => toTypedSchema(installing.value ? setupSchema : loginSchema)),
  initialValues: { email: '', password: '', setup_token: '', display_name: '', workspace_name: '' },
})
const { pending, error, notice, run } = useAction()
const submit = handleSubmit(async (values) => {
  await run(async () => {
    try {
      if (installing.value) await auth.setup(setupSchema.parse(values))
      else await auth.login(loginSchema.parse(values))
      await router.replace(safeRedirect(route.query.redirect))
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'SETUP_COMPLETED') {
        auth.setupRequired = false
        await router.replace({ path: '/login', query: { setupCompleted: '1' } })
      } else throw cause
    }
  })
  setFieldValue('password', '')
  setFieldValue('setup_token', '')
})
</script>
<template>
  <section class="panel form-panel auth-panel">
    <h1>{{ title }}</h1>
    <p class="form-help">{{ installing ? '创建管理员账户与第一个工作空间。' : '登录你的个人工作空间。' }}</p>
    <p v-if="route.query.setupCompleted" role="status">安装已完成，请使用管理员账户登录。</p>
    <form :aria-label="title" novalidate @submit.prevent="submit">
      <template v-if="installing">
        <TextField name="setup_token" label="安装凭据" type="password" :maxlength="256" />
        <p class="form-help">使用部署时生成的 SETUP_TOKEN，由部署管理员提供。</p>
        <TextField name="display_name" label="显示名" autocomplete="nickname" :maxlength="64" />
        <TextField name="workspace_name" label="工作空间名称" :maxlength="64" />
      </template>
      <TextField name="email" label="邮箱" type="email" autocomplete="username" />
      <TextField name="password" label="密码" type="password" :autocomplete="installing ? 'new-password' : 'current-password'" :maxlength="128" />
      <p v-if="installing" class="form-help">密码需要 12–128 个字符。</p>
      <ActionFeedback :error="error" :notice="notice" />
      <button class="primary-button" :disabled="pending || isSubmitting">{{ pending ? (installing ? '正在创建…' : '正在登录…') : (installing ? '创建工作空间' : '登录') }}</button>
    </form>
  </section>
</template>
