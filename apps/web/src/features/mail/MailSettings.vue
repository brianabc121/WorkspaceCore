<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, Link2 } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import ManagementPage from '../../components/management/ManagementPage.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { CONNECT_RESULTS, mailError, useMailAccounts, useMailActions, useMailAgents, useMailSettings, type MailAccount } from './api'
import GoogleClientSettingsCard from './GoogleClientSettingsCard.vue'
import MailAccountSettingsTable from './MailAccountSettingsTable.vue'
import MailAccountSettingsPanel from './MailAccountSettingsPanel.vue'
import './mail-settings.css'

// 设置 → 邮件（T-0130，MAIL_PLAN 第 6 节；T-0194 按效果图 05 改版）：Google OAuth 客户端、多个 Gmail 邮箱、
// 所选邮箱（?account=）的授权 / 分配给智能体 / 发信署名。授权回调跳回这里，结果在 ?mail=…。
// 只读邮箱元数据：这一页不请求邮件正文、附件或发件内容。
const auth = useAuthStore(), scope = auth.workspaceId!
const route = useRoute(), router = useRouter()
const settings = useMailSettings(scope), accounts = useMailAccounts(scope), agents = useMailAgents(scope), actions = useMailActions(scope)
const busy = ref(false), error = ref(''), notice = ref('')
const result = ref(typeof route.query.mail === 'string' ? route.query.mail : '')
const resultText = computed(() => (result.value ? CONNECT_RESULTS[result.value] ?? '连接没有完成，请重试。' : ''))
if (result.value) void router.replace({ query: { ...route.query, mail: undefined } })

const items = computed(() => accounts.data.value?.items)
const configured = computed(() => !!settings.data.value?.configured)
// 所选邮箱：地址栏 ?account=；没带时默认第一个。带了但列表里没有（已断开、别的工作空间）就不显示任何邮箱
const accountParam = computed(() => (typeof route.query.account === 'string' && route.query.account ? route.query.account : null))
const selected = computed(() => {
  const list = items.value ?? []
  return accountParam.value ? list.find(a => a.id === accountParam.value) ?? null : list[0] ?? null
})
const missing = computed(() => !!accountParam.value && !!items.value && !selected.value)
const panel = ref<InstanceType<typeof MailAccountSettingsPanel> | null>(null)
async function select(id: string) {
  if (id !== accountParam.value) await router.replace({ query: { ...route.query, account: id } })
  // 窄屏时详情排在列表下面：把焦点移过去
  await nextTick()
  if (window.matchMedia?.('(max-width: 1179px)').matches) panel.value?.focus()
}

// 连接新邮箱：可以预填要连接的地址（只是 Google 账户选择页的提示，最终以 Google 返回的邮箱为准）
const connecting = ref(false), loginHint = ref(''), hintInput = ref<HTMLInputElement | null>(null), connectButton = ref<HTMLButtonElement | null>(null)
function openConnect() {
  connecting.value = true
  void nextTick(() => hintInput.value?.focus())
}
function closeConnect() {
  connecting.value = false; loginHint.value = ''
  void nextTick(() => connectButton.value?.focus())
}
async function run(effect: () => Promise<unknown>, success: string) {
  if (busy.value) return false
  busy.value = true; error.value = ''; notice.value = ''; result.value = ''
  try { await effect(); notice.value = success; return true }
  catch (cause) { error.value = mailError(cause); return false }
  finally { busy.value = false }
}
const startGoogle = (hint: string) => run(async () => { window.location.assign(await actions.connect(hint)) }, '正在打开 Google 授权页…')
const connect = () => startGoogle(loginHint.value.trim())
// 重新连接：同样走 Google 授权页与原来的回调；用这个邮箱预填账户选择
const reconnect = (account: MailAccount) => startGoogle(account.email)
async function disconnect(account: MailAccount) {
  if (!window.confirm(`断开 ${account.email}？会撤销 Google 授权并删除保存的令牌，取消它对智能体的全部分配；发件记录保留。`)) return
  const ok = await run(() => actions.disconnect(account), '邮箱已断开')
  if (ok && route.query.account === account.id) await router.replace({ query: { ...route.query, account: undefined } })
}
</script>

<template>
  <ManagementPage title="邮件设置" description="连接多个 Gmail 邮箱，分别管理授权与智能体的使用范围。" aside-label="所选邮箱">
    <template #actions>
      <RouterLink class="secondary-button" to="/mail">前往邮件<ArrowRight aria-hidden="true" /></RouterLink>
      <button ref="connectButton" type="button" class="primary-button" :disabled="!configured || busy" :title="configured ? undefined : '先保存 Google OAuth 客户端'" @click="openConnect"><Link2 aria-hidden="true" />连接邮箱</button>
    </template>
    <p v-if="resultText" :role="result === 'connected' ? 'status' : 'alert'" class="feedback">{{ resultText }}</p>
    <ActionFeedback :error="error" :notice="notice" />
    <GoogleClientSettingsCard :scope="scope" :settings="settings.data.value" :loading="settings.isPending.value" :load-error="settings.error.value" @retry="settings.refetch()" />
    <MailAccountSettingsTable
      :accounts="items" :loading="accounts.isPending.value" :load-error="accounts.error.value" :selected-id="selected?.id ?? null" :busy="busy"
      @select="select" @reconnect="reconnect" @retry="accounts.refetch()"
    >
      <template #connect>
        <p v-if="settings.data.value && !configured" class="mg-hint">先保存上面的 Google OAuth 客户端，再连接邮箱。</p>
        <form v-if="connecting && configured" class="ms-connect" aria-label="连接 Gmail 邮箱" @submit.prevent="connect">
          <p class="form-help">每次连接都会打开 Google 授权页并让你选账户，请勾选“查看您的电子邮件和设置”（查收）和“代表您发送电子邮件”（发信）。邮件内容不保存在工作台；新连接的邮箱默认不分配给任何智能体。</p>
          <div class="ms-form-row mg-stack-narrow">
            <label class="form-field"><span>要连接的邮箱（可不填）</span><input ref="hintInput" v-model="loginHint" type="email" autocomplete="email" placeholder="name@gmail.com" /></label>
            <div class="form-actions">
              <button type="submit" class="primary-button" :disabled="busy">前往 Google 授权</button>
              <button type="button" class="secondary-button" :disabled="busy" @click="closeConnect">取消</button>
            </div>
          </div>
        </form>
      </template>
    </MailAccountSettingsTable>
    <template #aside>
      <MailAccountSettingsPanel
        v-if="selected" ref="panel" :key="selected.id" :scope="scope" :account="selected" :agents="agents.data.value?.items ?? []"
        :agents-loading="agents.isPending.value" :agents-error="agents.error.value ? mailError(agents.error.value) : ''" :busy="busy" :can-manage-instances="auth.isOwner"
        @reconnect="reconnect" @disconnect="disconnect"
      />
      <section v-else class="ws-panel ms-panel ms-panel-empty">
        <p v-if="accounts.isPending.value" role="status">正在加载邮箱…</p>
        <p v-else-if="missing">没有找到链接里的邮箱（可能已断开）。在邮箱账户表里选择一个邮箱。</p>
        <p v-else>连接邮箱后，在这里管理它的授权、分配给哪些智能体与发信署名。</p>
      </section>
    </template>
  </ManagementPage>
</template>
