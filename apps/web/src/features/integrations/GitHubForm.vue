<script setup lang="ts">
import { ErrorMessage, useForm } from 'vee-validate'
import { toTypedSchema } from '../../services/forms'
import { z } from 'zod'
import { ref } from 'vue'
import type { Schema } from '../../services/api'
import TextField from '../../components/TextField.vue'
import { toLocalInput } from '../tasks/shared'
import { repositoryList } from './githubValidation'
const props = defineProps<{ integration?: Schema['IntegrationOut']; busy: boolean; locked: boolean }>()
const emit = defineEmits<{ save: [body: Schema['GitHubIntegrationCreate'] | Schema['IntegrationUpdate']] }>()
const token = z.string().min(12).max(512).regex(/^github_pat_.+$/, '请使用细粒度 Token（github_pat_ 开头），不接受经典 Token。')
const repos = z.string().superRefine((v, ctx) => { try { repositoryList(v) } catch (cause) { ctx.addIssue({ code: 'custom', message: (cause as Error).message }) } })
const initialExpires = toLocalInput(props.integration?.token_expires_at ?? null)
const { handleSubmit, defineField, setFieldValue } = useForm({ validationSchema: toTypedSchema(z.object({ name: z.string().trim().min(1).max(64), api_token: props.integration ? token.or(z.literal('')) : token, repositories: repos, expires: z.string().refine((v) => !v || Number.isFinite(Date.parse(v)), '过期时间无效'), enabled: z.boolean() })), initialValues: { name: props.integration?.name ?? 'GitHub', api_token: '', repositories: props.integration?.repositories?.join('\n') ?? '', expires: initialExpires, enabled: props.integration?.status !== 'disabled' } })
const [repositories, repoAttrs] = defineField('repositories'), [expires] = defineField('expires'), [enabled] = defineField('enabled'), notice = ref('')
const submit = handleSubmit((v) => {
  if (props.busy || props.locked) return
  const names = repositoryList(v.repositories), token_expires_at = v.expires ? new Date(v.expires).toISOString() : null
  if (props.integration) {
    const body: Schema['IntegrationUpdate'] = { version: props.integration.version }
    if (v.name !== props.integration.name) body.name = v.name
    if (JSON.stringify(names) !== JSON.stringify(props.integration.repositories ?? [])) body.repositories = names
    if (v.api_token) body.api_token = v.api_token
    if (v.expires !== initialExpires) body.token_expires_at = token_expires_at
    if (v.enabled !== (props.integration.status !== 'disabled')) body.enabled = v.enabled
    if (Object.keys(body).length === 1) { notice.value = '没有需要保存的修改'; return }
    emit('save', body)
  } else emit('save', { provider: 'github', name: v.name, api_token: v.api_token, repositories: names, token_expires_at })
  setFieldValue('api_token', '')
})
</script>
<template><form aria-label="GitHub 连接设置" novalidate @submit.prevent="submit"><fieldset :disabled="busy || locked"><TextField name="name" label="GitHub 连接名称" :maxlength="64" /><TextField name="api_token" label="GitHub Token" type="password" :maxlength="512" autocomplete="new-password" /><p class="form-help">仅支持细粒度 Personal Access Token；只读权限：Metadata、Issues、Pull requests、Actions。{{ integration ? '留空保留现有 Token。' : '' }}凭据只写入，不回显。</p><label class="form-field">GitHub 仓库列表<textarea v-model="repositories" v-bind="repoAttrs" rows="5" maxlength="3000" placeholder="owner/repo"></textarea></label><ErrorMessage name="repositories" as="p" class="field-error" /><p class="form-help">最多 20 个仓库，忽略大小写去重。仅连接 github.com；Token 必须授权访问这些仓库。</p><label class="form-field">GitHub Token 过期时间<input v-model="expires" type="datetime-local"></label><label v-if="integration" class="check-field"><input v-model="enabled" type="checkbox">启用 GitHub 连接</label><p v-if="notice" role="status">{{ notice }}</p><button class="primary-button">保存 GitHub 连接</button></fieldset></form></template>
