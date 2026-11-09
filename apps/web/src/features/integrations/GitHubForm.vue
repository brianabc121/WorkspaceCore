<script setup lang="ts">
import { ErrorMessage, useForm } from 'vee-validate'
import { toTypedSchema } from '../../services/forms'
import { z } from 'zod'
import { computed, ref, useId } from 'vue'
import { ArrowRight, Info, Layers } from '@lucide/vue'
import { dataOf, errorMessage, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import TextField from '../../components/TextField.vue'
import { toLocalInput } from '../tasks/shared'
import { CLASSIC_TOKEN_MESSAGE, MAX_REPOSITORIES, TOKEN_DAYS, TOKEN_PATTERN, TOKEN_URL, repositoryList } from './githubValidation'

// GitHub 连接配置表单（T-0091；T-0192 放进页面右侧一栏，效果图 03）：
// Token 只写不回显——输入框只在编辑时存在于组件内存，保存后清空，取消时随组件卸载；不进 URL、缓存或浏览器存储。
// 已有连接留空表示保留原 Token；保存只提交改过的字段，带打开编辑时那一版的 version（冲突时由页面提示重新载入）。
const props = defineProps<{ integration?: Schema['IntegrationOut']; busy: boolean; locked: boolean }>()
const emit = defineEmits<{ save: [body: Schema['GitHubIntegrationCreate'] | Schema['IntegrationUpdate']]; cancel: [] }>()
const auth = useAuthStore()
const token = z.string().min(12).max(512).regex(TOKEN_PATTERN, CLASSIC_TOKEN_MESSAGE)
const repos = z.string().superRefine((v, ctx) => { try { repositoryList(v) } catch (cause) { ctx.addIssue({ code: 'custom', message: (cause as Error).message }) } })
// 新建时按生成链接里的 90 天预填过期时间（用于到期提醒）；在 GitHub 上改了有效期的话这里也改。已有连接用保存的值，不从 Token 推测
const initialExpires = toLocalInput(props.integration ? props.integration.token_expires_at ?? null : new Date(Date.now() + TOKEN_DAYS * 86_400_000).toISOString())
const { handleSubmit, defineField, setFieldValue, values, errors } = useForm({ validationSchema: toTypedSchema(z.object({ name: z.string().trim().min(1).max(64), api_token: props.integration ? token.or(z.literal('')) : token, repositories: repos, expires: z.string().refine((v) => !v || Number.isFinite(Date.parse(v)), '过期时间无效'), enabled: z.boolean() })), initialValues: { name: props.integration?.name ?? 'GitHub', api_token: '', repositories: props.integration?.repositories?.join('\n') ?? '', expires: initialExpires, enabled: props.integration?.status !== 'disabled' } })
const [apiToken, tokenAttrs] = defineField('api_token'), [repositories, repoAttrs] = defineField('repositories'), [expires] = defineField('expires'), [enabled] = defineField('enabled'), notice = ref('')
const ids = { token: useId(), repos: useId(), expires: useId(), enabled: useId() }
const form = ref<HTMLFormElement | null>(null)

// ---------------------------------------------------------------- 读取 Token 能访问的仓库
const available = ref<string[] | null>(null), complete = ref(true), loading = ref(false), listError = ref('')
const typedNames = computed(() => String(repositories.value ?? '').split(/[\s,]+/).filter(Boolean))
const chosen = computed(() => new Set(typedNames.value.map((name) => name.toLowerCase())))
// 勾选列表：读取到的仓库，加上已经填写但不在读取结果里的仓库（忽略大小写去重）；还没读取时就是当前已选的仓库
const choices = computed(() => {
  const seen = new Set<string>(), list: { name: string; listed: boolean }[] = []
  for (const name of available.value ?? []) if (!seen.has(name.toLowerCase())) { seen.add(name.toLowerCase()); list.push({ name, listed: true }) }
  for (const name of typedNames.value) if (!seen.has(name.toLowerCase())) { seen.add(name.toLowerCase()); list.push({ name, listed: available.value === null }) }
  return list
})
const canList = computed(() => !props.busy && !props.locked && !loading.value && (Boolean(values.api_token) || Boolean(props.integration?.token_set)))
async function listRepositories() {
  if (!canList.value || !auth.workspaceId) return
  const typed = String(values.api_token ?? '')
  if (typed && !TOKEN_PATTERN.test(typed)) { listError.value = CLASSIC_TOKEN_MESSAGE; return }
  loading.value = true; listError.value = ''
  try {
    // Token 放在请求体里（不进 URL）；留空时用已保存的 Token
    const result = dataOf(await auth.api.POST('/api/v1/integrations/github/repositories', { params: { header: { 'X-Workspace-Id': auth.workspaceId } }, body: typed ? { api_token: typed } : {} }))
    available.value = result.repositories; complete.value = result.complete
    // 还没有填写仓库时默认全选（最多 20 个）
    if (!chosen.value.size && result.repositories.length) setFieldValue('repositories', result.repositories.slice(0, MAX_REPOSITORIES).join('\n'))
  } catch (cause) { available.value = null; listError.value = errorMessage(cause) }
  finally { loading.value = false }
}
function toggle(name: string, checked: boolean) {
  const current = typedNames.value
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
// 页面上的“修改连接 / 重新连接”把焦点移到名称或 Token 输入框
function focusField(field: 'name' | 'token') {
  form.value?.querySelector<HTMLInputElement>(field === 'token' ? 'input[name="api_token"]' : 'input[name="name"]')?.focus()
}
defineExpose({ focusField })
</script>
<template>
  <form ref="form" class="ig-form" aria-label="GitHub 连接设置" novalidate @submit.prevent="submit">
    <fieldset :disabled="busy || locked">
      <section v-if="!integration" class="setup-steps" aria-label="生成 GitHub Token 的步骤">
        <h3>三步接入 GitHub（只读）</h3>
        <ol>
          <li>打开 <a :href="TOKEN_URL" target="_blank" rel="noopener noreferrer">GitHub 生成 Token 页面</a>：已预填名称、{{ TOKEN_DAYS }} 天有效期，以及只读的 Issues、Pull requests、Actions 权限。</li>
          <li>Repository access 选 <strong>Only select repositories</strong>，勾选要同步的仓库（最多 {{ MAX_REPOSITORIES }} 个），点 <strong>Generate token</strong>。</li>
          <li>复制 <code>github_pat_</code> 开头的 Token 粘贴到下面，点“读取可访问的仓库”，勾选后保存。保存后会自动检查权限，通过后开始同步。</li>
        </ol>
      </section>
      <TextField name="name" label="连接名称" :maxlength="64" />
      <div v-if="integration" class="ig-row">
        <span class="ig-label">Token 状态</span>
        <span class="ig-chip" :class="integration.token_set ? 'tone-green' : 'tone-amber'">{{ integration.token_set ? '已设置 · 不回显' : '未设置' }}</span>
      </div>
      <div class="form-field">
        <label :for="ids.token">{{ integration ? '更换细粒度 Token' : 'GitHub Token' }}</label>
        <input :id="ids.token" v-model="apiToken" v-bind="tokenAttrs" name="api_token" type="password" maxlength="512" autocomplete="new-password" spellcheck="false" :placeholder="integration ? '留空保持现有 Token' : 'github_pat_…'" :aria-invalid="!!errors.api_token" :aria-describedby="`${ids.token}-help`">
        <small v-if="errors.api_token" class="field-error">{{ errors.api_token }}</small>
        <p :id="`${ids.token}-help`" class="ig-help"><Info aria-hidden="true" />仅支持 github_pat_ 开头的细粒度 Token，只读权限：Metadata、Issues、Pull requests、Actions。{{ integration ? '留空保留现有 Token。' : '' }}只写入，不回显。</p>
      </div>
      <button type="button" class="secondary-button ig-wide" :disabled="!canList" @click="listRepositories"><Layers aria-hidden="true" />{{ loading ? '正在读取…' : '读取可访问的仓库' }}</button>
      <p v-if="listError" role="alert" class="field-error">{{ listError }}</p>
      <fieldset v-if="choices.length || available" class="repo-picker ig-picker">
        <legend>{{ available ? 'Token 能访问的仓库' : '选择同步的仓库' }}（已选 {{ chosen.size }} / {{ MAX_REPOSITORIES }}）</legend>
        <p v-if="available && !available.length" class="form-help">这个 Token 还不能访问任何仓库：重新生成时在 Repository access 里勾选要同步的仓库。</p>
        <label v-for="repo in choices" :key="repo.name.toLowerCase()" class="check-field">
          <input type="checkbox" :checked="chosen.has(repo.name.toLowerCase())" :disabled="!chosen.has(repo.name.toLowerCase()) && chosen.size >= MAX_REPOSITORIES" @change="toggle(repo.name, ($event.target as HTMLInputElement).checked)">
          <span>{{ repo.name }}</span><small v-if="!repo.listed" class="ig-unlisted">不在读取结果里</small>
        </label>
        <p v-if="!complete" class="form-help">仓库太多，只列出了前面一部分；其余的可以在下面手动填写。</p>
      </fieldset>
      <p class="ig-help"><Info aria-hidden="true" />最多 {{ MAX_REPOSITORIES }} 个仓库，忽略大小写去重；Token 必须授权访问这些仓库。</p>
      <div class="form-field">
        <label :for="ids.repos">GitHub 仓库列表</label>
        <textarea :id="ids.repos" v-model="repositories" v-bind="repoAttrs" rows="3" maxlength="3000" placeholder="owner/repo" :aria-describedby="`${ids.repos}-help`"></textarea>
        <ErrorMessage name="repositories" as="p" class="field-error" />
        <p :id="`${ids.repos}-help`" class="form-help">勾选的仓库会自动填在这里，也可以手动填写，每行一个 owner/repo。</p>
      </div>
      <div class="form-field">
        <label :for="ids.expires">Token 到期时间</label>
        <input :id="ids.expires" v-model="expires" type="datetime-local" :aria-describedby="`${ids.expires}-help`">
        <ErrorMessage name="expires" as="p" class="field-error" />
        <p :id="`${ids.expires}-help`" class="form-help">用于到期提醒；在 GitHub 上改了有效期的话这里也改。</p>
      </div>
      <div v-if="integration" class="ig-switch">
        <span><label :for="ids.enabled">启用连接</label><small :id="`${ids.enabled}-help`">停用后不同步，旧的核验结果也会清除。</small></span>
        <input :id="ids.enabled" v-model="enabled" type="checkbox" role="switch" :aria-describedby="`${ids.enabled}-help`">
      </div>
      <p v-if="notice" role="status" class="form-help">{{ notice }}</p>
      <div class="ig-form-actions">
        <button class="primary-button">保存 GitHub 连接</button>
        <button type="button" class="secondary-button" @click="emit('cancel')">取消</button>
      </div>
      <a v-if="integration" class="asv-link ig-token-link" :href="TOKEN_URL" target="_blank" rel="noopener noreferrer">获取细粒度 Token<ArrowRight aria-hidden="true" /></a>
    </fieldset>
  </form>
</template>
