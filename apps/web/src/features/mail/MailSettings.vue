<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { formatTime } from '../time'
import { CONNECT_RESULTS, mailError, useMailAccounts, useMailActions, useMailAgents, useMailSettings, type MailAccount } from './api'
import MailAssign from './MailAssign.vue'

// 设置 → 邮件（T-0130，MAIL_PLAN 第 6 节）：Google OAuth 客户端、连接 / 断开 Gmail 邮箱（可以连多个）、
// 每个邮箱分配给哪些智能体（T-0132）与“智能体发信加署名”。授权回调跳回这里，结果在 ?mail=…。
const auth = useAuthStore(), scope = auth.workspaceId!
const route = useRoute(), router = useRouter()
const settings = useMailSettings(scope), accounts = useMailAccounts(scope), agents = useMailAgents(scope), actions = useMailActions(scope)
const clientId = ref(''), clientSecret = ref(''), loginHint = ref('')
const busy = ref(false), error = ref(''), notice = ref(''), copied = ref(false)
const result = ref(typeof route.query.mail === 'string' ? route.query.mail : '')
const resultText = computed(() => (result.value ? CONNECT_RESULTS[result.value] ?? '连接没有完成，请重试。' : ''))
const items = computed(() => accounts.data.value?.items ?? [])
const agentItems = computed(() => agents.data.value?.items ?? [])
watch(() => settings.data.value, (value) => { if (value && !clientId.value) clientId.value = value.client_id ?? '' }, { immediate: true })
if (result.value) void router.replace({ query: { ...route.query, mail: undefined } })

async function run(effect: () => Promise<unknown>, success: string) {
  if (busy.value) return false
  busy.value = true; error.value = ''; notice.value = ''; result.value = ''
  try { await effect(); notice.value = success; return true }
  catch (cause) { error.value = mailError(cause); return false }
  finally { busy.value = false }
}
async function save() {
  const id = clientId.value.trim(), secret = clientSecret.value.trim()
  if (id.length < 10) { error.value = '请填写 Google OAuth 客户端 ID。'; return }
  if (!settings.data.value?.configured && !secret) { error.value = '第一次设置要填客户端密钥。'; return }
  const ok = await run(() => actions.saveSettings({ client_id: id, ...(secret ? { client_secret: secret } : {}), version: settings.data.value?.version ?? null }), 'OAuth 客户端已保存')
  if (ok) clientSecret.value = ''
}
async function connect() {
  await run(async () => { window.location.assign(await actions.connect(loginHint.value.trim())) }, '正在打开 Google 授权页…')
}
async function copyRedirect() {
  const uri = settings.data.value?.redirect_uri
  if (!uri) return
  try { await navigator.clipboard.writeText(uri); copied.value = true; setTimeout(() => { copied.value = false }, 2000) } catch { error.value = '复制失败，请手动选中复制。' }
}
function toggleSignature(account: MailAccount) {
  void run(() => actions.patch(account, { agent_signature: !account.agent_signature }), '邮箱设置已保存')
}
function assign(account: MailAccount, instanceIds: string[]) {
  const before = new Set(account.agents.map(a => a.id))
  const added = agentItems.value.filter(a => instanceIds.includes(a.id) && !before.has(a.id)).map(a => a.name)
  if (added.length && !window.confirm(`把 ${account.email} 分配给 ${added.join('、')}？它们能查这个邮箱的邮件（每个实例还有自己的查询开关）；发信还要在“OpenClaw 实例”页单独给实例打开。`)) return
  void run(() => actions.assign(account, instanceIds), '邮箱分配已保存')
}
function disconnect(account: MailAccount) {
  if (!window.confirm(`断开 ${account.email}？会撤销 Google 授权并删除保存的令牌；发件记录保留。`)) return
  void run(() => actions.disconnect(account), '邮箱已断开')
}
const STATUS: Record<MailAccount['status'], string> = { active: '正常', needs_reconnect: '需要重新连接', disconnected: '已断开' }
</script>

<template>
  <p v-if="resultText" :role="result === 'connected' ? 'status' : 'alert'" class="feedback">{{ resultText }}</p>
  <p v-if="error" role="alert" class="feedback">{{ error }}</p>
  <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
  <section class="panel form-panel" aria-label="Google OAuth 客户端">
    <h2>Google OAuth 客户端</h2>
    <p class="form-help">在 Google Cloud 控制台建一个“Web 应用”类型的 OAuth 客户端，打开 Gmail API，把下面的授权回调地址登记进去，再把客户端 ID 和密钥填在这里。工作台只申请“读取邮件”和“发送邮件”两个权限，不能删除或修改邮件。</p>
    <p v-if="settings.isPending.value" role="status">正在加载…</p>
    <p v-else-if="settings.error.value" role="alert">{{ mailError(settings.error.value) }}</p>
    <template v-else-if="settings.data.value">
      <div class="mail-redirect"><span>授权回调地址</span><code>{{ settings.data.value.redirect_uri }}</code><button type="button" class="secondary-button" @click="copyRedirect">{{ copied ? '已复制' : '复制' }}</button></div>
      <form class="mail-form" @submit.prevent="save">
        <label class="form-field"><span>客户端 ID</span><input v-model="clientId" autocomplete="off" maxlength="200" placeholder="1234567890-xxxx.apps.googleusercontent.com" /></label>
        <label class="form-field"><span>客户端密钥</span><input v-model="clientSecret" type="password" autocomplete="new-password" maxlength="200" :placeholder="settings.data.value.client_secret_set ? '已设置；不填表示保持不变' : '第一次必填'" /></label>
        <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy">保存 OAuth 客户端</button></div>
      </form>
    </template>
  </section>
  <section class="panel form-panel" aria-label="Gmail 邮箱">
    <div class="section-heading"><h2>Gmail 邮箱</h2></div>
    <p class="form-help">可以连接多个 Gmail 邮箱：每次连接都会打开 Google 授权页并让你选账号，请勾选“查看您的电子邮件和设置”（查收）和“代表您发送电子邮件”（发信）。邮件内容不保存在工作台，每次实时从 Gmail 读取。新连接的邮箱默认不分配给任何智能体。</p>
    <form v-if="settings.data.value?.configured" class="mail-form mail-connect" @submit.prevent="connect">
      <label class="form-field"><span>要连接的邮箱（可不填）</span><input v-model="loginHint" type="email" autocomplete="email" placeholder="name@gmail.com" /></label>
      <div class="form-actions"><button type="submit" class="primary-button" :disabled="busy">{{ items.length ? '再连接一个 Gmail 邮箱' : '连接 Gmail 邮箱' }}</button></div>
    </form>
    <p v-else-if="settings.data.value" class="empty-state">先保存上面的 OAuth 客户端，再连接邮箱。</p>
    <p v-if="accounts.isPending.value" role="status">正在加载邮箱…</p>
    <p v-else-if="accounts.error.value" role="alert">{{ mailError(accounts.error.value) }}</p>
    <p v-else-if="!items.length" class="empty-state">还没有连接邮箱。</p>
    <ul class="mail-accounts">
      <li v-for="account in items" :key="account.id" :aria-label="`邮箱：${account.email}`">
        <div class="section-heading">
          <h3>{{ account.email }} <span class="badge">{{ STATUS[account.status] }}</span><span v-if="!account.can_send" class="badge">只能查收</span></h3>
          <button type="button" class="secondary-button im-danger" :disabled="busy" @click="disconnect(account)">断开</button>
        </div>
        <p v-if="account.status === 'needs_reconnect'" class="feedback" role="note">Google 授权已失效（{{ account.last_error ?? '已撤销或过期' }}）：点“连接 Gmail 邮箱”用同一个邮箱重新授权。</p>
        <p class="form-help">连接于 {{ formatTime(account.connected_at) }}。</p>
        <MailAssign :account="account" :agents="agentItems" :busy="busy" @save="assign(account, $event)" />
        <div class="form-actions">
          <button type="button" class="secondary-button" :disabled="busy" @click="toggleSignature(account)">{{ account.agent_signature ? '智能体发信不加署名' : '智能体发信加署名' }}</button>
        </div>
        <p class="form-help">智能体发的信{{ account.agent_signature ? '末尾加一行“由工作台智能体「名字」发送”' : '不加署名' }}。每个智能体能不能查、能不能发，在“OpenClaw 实例”页的“邮件权限”里设置。</p>
      </li>
    </ul>
  </section>
</template>
