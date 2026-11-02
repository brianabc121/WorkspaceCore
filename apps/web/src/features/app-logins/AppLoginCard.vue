<script setup lang="ts">
// Vikunja / n8n 登录账号（T-0080）：只给 owner/admin 显示；密码默认遮住，显示或复制时才向服务端取明文（写审计）。
import { computed, onUnmounted, reactive, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { useResourceMutation } from '../resources/mutation'
import { formatTime } from '../time'
import ExternalLink from '../../components/ExternalLink.vue'
import ModalPanel from '../../components/ModalPanel.vue'

type App = Schema['AppLoginOut']['app']
const props = defineProps<{ app: App }>()
const HIDE_AFTER_MS = 60_000
const title = props.app === 'vikunja' ? 'Vikunja' : 'n8n'
const userLabel = props.app === 'n8n' ? '登录邮箱' : '用户名'
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, header = { 'X-Workspace-Id': scope }
const active = () => auth.user?.id === user && auth.workspaceId === scope
const query = useQuery({
  queryKey: ['app-logins', user, scope],
  enabled: computed(() => active() && auth.canManage),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/app-logins', { params: { header }, signal })),
})
const login = computed(() => query.data.value?.items.find(item => item.app === props.app))
const mutation = reactive(useResourceMutation(scope, ['app-logins']))
const password = ref<string | null>(null), revealing = ref(false), notice = ref(''), problem = ref('')
const editing = ref(false), username = ref(''), newPassword = ref(''), validation = ref('')
let timer: ReturnType<typeof setTimeout> | undefined

function hide() { password.value = null; clearTimeout(timer) }
onUnmounted(hide)
function explain(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 404) return '还没有保存密码。'
  if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) return '无权查看，请重新登录或联系管理员。'
  return '暂时读不到密码，请稍后重试。'
}
async function fetchPassword(): Promise<string | null> {
  if (password.value !== null) return password.value
  if (revealing.value || !active()) return null
  revealing.value = true; problem.value = ''
  try {
    const secret = dataOf(await auth.api.POST('/api/v1/app-logins/{app}/reveal', { params: { header, path: { app: props.app } } }))
    if (!active()) return null
    password.value = secret.password
    clearTimeout(timer); timer = setTimeout(hide, HIDE_AFTER_MS)
    return secret.password
  } catch (cause) { if (active()) problem.value = explain(cause); return null }
  finally { revealing.value = false }
}
async function toggle() { if (password.value !== null) hide(); else await fetchPassword() }
async function copy(kind: 'username' | 'password') {
  notice.value = ''; problem.value = ''
  const text = kind === 'username' ? login.value?.username : await fetchPassword()
  if (!text) return
  try { await navigator.clipboard.writeText(text); notice.value = kind === 'username' ? `已复制${userLabel}` : '已复制密码' }
  catch { problem.value = '浏览器不允许复制，请点「显示」后手动复制。' }
}
function edit() {
  username.value = login.value?.username ?? ''; newPassword.value = ''; validation.value = ''; editing.value = true
}
async function save() {
  const current = login.value
  if (!current || mutation.busy) return
  validation.value = ''
  const name = username.value.trim()
  if (!name) { validation.value = `请填写${userLabel}。`; return }
  const body: Schema['AppLoginUpsert'] = current.configured ? { version: current.version! } : {}
  if (!current.configured || name !== current.username) body.username = name
  if (newPassword.value) body.password = newPassword.value
  if (Object.keys(body).filter(key => key !== 'version').length === 0) { validation.value = '请至少修改一项。'; return }
  newPassword.value = ''
  if (await mutation.run(() => auth.api.PUT('/api/v1/app-logins/{app}', { params: { header, path: { app: props.app } }, body }), '账号已保存')) { editing.value = false; hide() }
}
async function remove() {
  const current = login.value
  if (!current?.configured || mutation.busy || !window.confirm(`删除工作台里保存的 ${title} 账号？${title} 里的账号不受影响。`)) return
  if (await mutation.run(() => auth.api.DELETE('/api/v1/app-logins/{app}', { params: { header, path: { app: props.app }, query: { version: current.version! } } }), '已删除保存的账号')) hide()
}
const error = computed(() => mutation.error instanceof ApiError && mutation.error.code === 'VERSION_CONFLICT' ? '账号已被其他人修改，请重新加载。' : mutation.error ? '保存失败，请重新加载后再试。' : '')
</script>
<template>
  <section v-if="auth.canManage" class="panel app-login" :aria-label="`${title} 登录`" role="region">
    <h2>{{ title }} 登录账号</h2>
    <p class="form-help">只有管理员能看到。点「显示」或复制密码时会写入审计日志；显示的密码 1 分钟后自动遮住。</p>
    <p v-if="query.isPending.value" role="status">正在加载账号…</p>
    <p v-else-if="query.error.value" role="alert">{{ explain(query.error.value) }}</p>
    <template v-else-if="login">
      <dl v-if="login.configured" class="app-login-fields">
        <dt>{{ userLabel }}</dt>
        <dd><code>{{ login.username }}</code><button class="secondary-button" type="button" @click="copy('username')">复制{{ userLabel }}</button></dd>
        <dt>密码</dt>
        <dd v-if="login.password_set"><code :aria-label="password === null ? '密码已遮住' : undefined">{{ password ?? '••••••••••' }}</code><button class="secondary-button" type="button" :disabled="revealing" @click="toggle">{{ password === null ? '显示密码' : '隐藏密码' }}</button><button class="secondary-button" type="button" :disabled="revealing" @click="copy('password')">复制密码</button></dd>
        <dd v-else>没有保存密码</dd>
        <template v-if="login.updated_at"><dt>更新时间</dt><dd><time :datetime="login.updated_at">{{ formatTime(login.updated_at) }}</time></dd></template>
      </dl>
      <p v-else>还没有保存 {{ title }} 的登录账号。一键部署脚本接入时会自动保存；之前接入的可以在这里填写。</p>
      <div class="form-actions">
        <ExternalLink v-if="login.login_url" :href="login.login_url" :label="`打开 ${title} 登录页`" />
        <button class="secondary-button" type="button" :disabled="mutation.busy || mutation.needsReload" @click="edit">{{ login.configured ? `修改 ${title} 账号` : `填写 ${title} 账号` }}</button>
        <button v-if="login.configured" class="secondary-button" type="button" :disabled="mutation.busy || mutation.needsReload" @click="remove">删除保存的账号</button>
        <button v-if="mutation.needsReload" class="secondary-button" type="button" :disabled="mutation.busy" @click="mutation.reload()">重新加载</button>
      </div>
    </template>
    <p v-if="problem || error && !editing" role="alert">{{ problem || error }}</p>
    <p v-if="notice || mutation.message" role="status">{{ notice || mutation.message }}</p>
    <ModalPanel v-if="editing" :title="`${title} 登录账号`" :busy="mutation.busy" @close="!mutation.busy && (editing = false)">
      <p class="form-help">这里只是给管理员看的备忘，工作台不会用它登录 {{ title }}。密码加密保存，留空表示不改。</p>
      <form novalidate @submit.prevent="save">
        <label class="form-field">{{ userLabel }}<input v-model="username" :disabled="mutation.busy" autocomplete="off" maxlength="254"></label>
        <label class="form-field">密码<input v-model="newPassword" :disabled="mutation.busy" type="password" autocomplete="new-password" maxlength="256" :placeholder="login?.password_set ? '留空保留原密码' : ''"></label>
        <p v-if="validation || error" role="alert">{{ validation || error }}</p>
        <div class="form-actions"><button class="primary-button" type="submit" :disabled="mutation.busy || mutation.needsReload">保存账号</button></div>
      </form>
    </ModalPanel>
  </section>
</template>
