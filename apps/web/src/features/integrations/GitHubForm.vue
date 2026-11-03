<script setup lang="ts">
import { ErrorMessage, useForm } from 'vee-validate'
import { toTypedSchema } from '../../services/forms'
import { z } from 'zod'
import { computed, ref } from 'vue'
import { dataOf, errorMessage, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import TextField from '../../components/TextField.vue'
import { toLocalInput } from '../tasks/shared'
import { MAX_REPOSITORIES, repositoryList } from './githubValidation'
const props = defineProps<{ integration?: Schema['IntegrationOut']; busy: boolean; locked: boolean }>()
const emit = defineEmits<{ save: [body: Schema['GitHubIntegrationCreate'] | Schema['IntegrationUpdate']] }>()
const auth = useAuthStore()
// 生成 Token 的页面：预填名称、90 天有效期与只读权限（Issues、Pull requests、Actions；Metadata 总是只读），T-0091
const TOKEN_URL = 'https://github.com/settings/personal-access-tokens/new?name=My+Workspace&description=My+Workspace+read-only+sync&expires_in=90&issues=read&pull_requests=read&actions=read'
const TOKEN_DAYS = 90
const tokenPattern = /^github_pat_.+$/
const token = z.string().min(12).max(512).regex(tokenPattern, '请使用细粒度 Token（github_pat_ 开头），不接受经典 Token。')
const repos = z.string().superRefine((v, ctx) => { try { repositoryList(v) } catch (cause) { ctx.addIssue({ code: 'custom', message: (cause as Error).message }) } })
// 新建时按生成链接里的 90 天预填过期时间（用于到期提醒）；在 GitHub 上改了有效期的话这里也改
const initialExpires = toLocalInput(props.integration ? props.integration.token_expires_at ?? null : new Date(Date.now() + TOKEN_DAYS * 86_400_000).toISOString())
const { handleSubmit, defineField, setFieldValue, values } = useForm({ validationSchema: toTypedSchema(z.object({ name: z.string().trim().min(1).max(64), api_token: props.integration ? token.or(z.literal('')) : token, repositories: repos, expires: z.string().refine((v) => !v || Number.isFinite(Date.parse(v)), '过期时间无效'), enabled: z.boolean() })), initialValues: { name: props.integration?.name ?? 'GitHub', api_token: '', repositories: props.integration?.repositories?.join('\n') ?? '', expires: initialExpires, enabled: props.integration?.status !== 'disabled' } })
const [repositories, repoAttrs] = defineField('repositories'), [expires] = defineField('expires'), [enabled] = defineField('enabled'), notice = ref('')

// ---------------------------------------------------------------- 读取 Token 能访问的仓库
const available = ref<string[] | null>(null), complete = ref(true), loading = ref(false), listError = ref('')
const chosen = computed(() => new Set(String(repositories.value ?? '').split(/[\s,]+/).filter(Boolean).map((name) => name.toLowerCase())))
const canList = computed(() => !props.busy && !props.locked && !loading.value && (Boolean(values.api_token) || Boolean(props.integration?.token_set)))
async function listRepositories() {
  if (!canList.value || !auth.workspaceId) return
  const typed = String(values.api_token ?? '')
  if (typed && !tokenPattern.test(typed)) { listError.value = '请使用细粒度 Token（github_pat_ 开头），不接受经典 Token。'; return }
  loading.value = true; listError.value = ''
  try {
    const result = dataOf(await auth.api.POST('/api/v1/integrations/github/repositories', { params: { header: { 'X-Workspace-Id': auth.workspaceId } }, body: typed ? { api_token: typed } : {} }))
    available.value = result.repositories; complete.value = result.complete
    // 还没有填写仓库时默认全选（最多 20 个）
    if (!chosen.value.size && result.repositories.length) setFieldValue('repositories', result.repositories.slice(0, MAX_REPOSITORIES).join('\n'))
  } catch (cause) { available.value = null; listError.value = errorMessage(cause) }
  finally { loading.value = false }
}
function toggle(name: string, checked: boolean) {
  const current = String(repositories.value ?? '').split(/[\s,]+/).filter(Boolean)
  const next = checked ? (chosen.value.has(name.toLowerCase()) ? current : [...current, name]) : current.filter((item) => item.toLowerCase() !== name.toLowerCase())
  setFieldValue('repositories', next.join('\n'))
}

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
<template>
  <form aria-label="GitHub 连接设置" novalidate @submit.prevent="submit">
    <fieldset :disabled="busy || locked">
      <section class="setup-steps" aria-label="生成 GitHub Token 的步骤">
        <h3>{{ integration ? '更换 Token（可选）' : '三步接入 GitHub（只读）' }}</h3>
        <ol>
          <li>打开 <a :href="TOKEN_URL" target="_blank" rel="noopener noreferrer">GitHub 生成 Token 页面</a>：已预填名称、{{ TOKEN_DAYS }} 天有效期，以及只读的 Issues、Pull requests、Actions 权限。</li>
          <li>Repository access 选 <strong>Only select repositories</strong>，勾选要同步的仓库（最多 {{ MAX_REPOSITORIES }} 个），点 <strong>Generate token</strong>。</li>
          <li>复制 <code>github_pat_</code> 开头的 Token 粘贴到下面，点“读取可访问的仓库”，勾选后保存。保存后会自动检查权限，通过后开始同步。</li>
        </ol>
      </section>
      <TextField name="name" label="GitHub 连接名称" :maxlength="64" />
      <TextField name="api_token" label="GitHub Token" type="password" :maxlength="512" autocomplete="new-password" />
      <p class="form-help">仅支持细粒度 Personal Access Token；只读权限：Metadata、Issues、Pull requests、Actions。{{ integration ? '留空保留现有 Token。' : '' }}凭据只写入，不回显。</p>
      <div class="form-actions"><button type="button" class="secondary-button" :disabled="!canList" @click="listRepositories">{{ loading ? '正在读取…' : '读取可访问的仓库' }}</button></div>
      <p v-if="listError" role="alert" class="field-error">{{ listError }}</p>
      <fieldset v-if="available" class="repo-picker">
        <legend>Token 能访问的仓库（已选 {{ chosen.size }} / {{ MAX_REPOSITORIES }}）</legend>
        <p v-if="!available.length" class="form-help">这个 Token 还不能访问任何仓库：重新生成时在 Repository access 里勾选要同步的仓库。</p>
        <label v-for="repo in available" :key="repo" class="check-field"><input type="checkbox" :checked="chosen.has(repo.toLowerCase())" :disabled="!chosen.has(repo.toLowerCase()) && chosen.size >= MAX_REPOSITORIES" @change="toggle(repo, ($event.target as HTMLInputElement).checked)">{{ repo }}</label>
        <p v-if="!complete" class="form-help">仓库太多，只列出了前面一部分；其余的可以在下面手动填写。</p>
      </fieldset>
      <label class="form-field">GitHub 仓库列表<textarea v-model="repositories" v-bind="repoAttrs" rows="5" maxlength="3000" placeholder="owner/repo"></textarea></label>
      <ErrorMessage name="repositories" as="p" class="field-error" />
      <p class="form-help">勾选的仓库会自动填在这里，也可以手动填写（每行一个 owner/repo）。最多 {{ MAX_REPOSITORIES }} 个，忽略大小写去重；Token 必须授权访问这些仓库。</p>
      <label class="form-field">GitHub Token 过期时间<input v-model="expires" type="datetime-local"></label>
      <p class="form-help">用于到期提醒；在 GitHub 上改了有效期的话这里也改。</p>
      <label v-if="integration" class="check-field"><input v-model="enabled" type="checkbox">启用 GitHub 连接</label>
      <p v-if="notice" role="status">{{ notice }}</p>
      <button class="primary-button">保存 GitHub 连接</button>
    </fieldset>
  </form>
</template>
