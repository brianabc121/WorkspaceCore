<script setup lang="ts">
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { ref } from 'vue'
import type { Schema } from '../../services/api'
import TextField from '../../components/TextField.vue'
import { safeUrl } from '../../widgets/registry'
import { toLocalInput } from '../tasks/shared'
const props = defineProps<{ integration?: Schema['IntegrationOut']; busy: boolean; locked: boolean }>()
const emit = defineEmits<{ save: [body: Schema['IntegrationCreate'] | Schema['IntegrationUpdate']] }>()
const url = z.string().max(2048).refine((value) => { try { const parsed = new URL(value); return safeUrl(value) && !parsed.search && !parsed.hash } catch { return false } }, '请输入不含凭据、查询参数或片段的 http(s) 地址')
const token = z.string().min(4).max(512).regex(/^tk_.+$/, '请输入 tk_ 开头的 API Token')
const schema = z.object({ name: z.string().trim().min(1, '请输入连接名称').max(64), base_url: props.integration ? url.or(z.literal('')) : url, api_token: props.integration ? token.or(z.literal('')) : token, public_url: url.or(z.literal('')), expires: z.string().refine((v) => !v || !Number.isNaN(new Date(v).getTime()), '过期时间无效'), enabled: z.boolean() })
const { handleSubmit, defineField, setFieldValue, isSubmitting } = useForm({ validationSchema: toTypedSchema(schema), initialValues: { name: props.integration?.name ?? 'Vikunja', base_url: '', api_token: '', public_url: '', expires: toLocalInput(props.integration?.token_expires_at ?? null), enabled: props.integration?.status !== 'disabled' } })
const [expires] = defineField('expires'), [enabled] = defineField('enabled')
const notice = ref('')
const submit = handleSubmit((value) => {
  if (props.busy || props.locked) return
  const token_expires_at = value.expires ? new Date(value.expires).toISOString() : null
  if (props.integration) {
    const body: Schema['IntegrationUpdate'] = { version: props.integration.version }
    if (value.name !== props.integration.name) body.name = value.name
    if (value.base_url) body.base_url = value.base_url
    if (value.api_token) body.api_token = value.api_token
    if (value.public_url) body.public_url = value.public_url
    if (value.expires !== toLocalInput(props.integration.token_expires_at)) body.token_expires_at = token_expires_at
    if (value.enabled !== (props.integration.status !== 'disabled')) body.enabled = value.enabled
    if (Object.keys(body).length === 1) { notice.value = '没有需要保存的修改'; return }
    emit('save', body)
  } else emit('save', { provider: 'vikunja', name: value.name, base_url: value.base_url, api_token: value.api_token, public_url: value.public_url || null, token_expires_at })
  setFieldValue('api_token', ''); setFieldValue('base_url', ''); setFieldValue('public_url', '')
})
</script>
<template>
  <form aria-label="Vikunja 连接设置" novalidate @submit.prevent="submit">
    <fieldset :disabled="busy || locked || isSubmitting">
      <TextField name="name" label="连接名称" :maxlength="64" />
      <TextField name="base_url" label="服务地址" :maxlength="2048" />
      <p class="form-help">{{ integration ? '留空保留已保存的服务地址；地址不回显。' : '由工作台服务器访问的 Vikunja 地址，内网域名需由部署管理员放行。' }}</p>
      <TextField name="public_url" label="浏览器访问地址" :maxlength="2048" />
      <TextField name="api_token" label="API Token" type="password" :maxlength="512" autocomplete="new-password" />
      <p class="form-help">{{ integration ? '留空保留现有 Token。' : '' }}从 Vikunja 创建受限 API Token，凭据只写入，不回显。</p>
      <label class="form-field">Token 过期时间<input v-model="expires" type="datetime-local"></label>
      <label v-if="integration" class="check-field"><input v-model="enabled" type="checkbox">启用连接</label>
      <p v-if="notice" role="status">{{ notice }}</p><button class="primary-button" :disabled="busy || locked || isSubmitting">{{ busy ? '正在保存…' : '保存连接' }}</button>
    </fieldset>
  </form>
</template>
