<script setup lang="ts">
import { computed, onUnmounted, reactive, ref } from 'vue'
import { RefreshCw } from '@lucide/vue'
import ManagementSection from '../../components/management/ManagementSection.vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import { ApiError } from '../../services/api'
import { ACCOUNT_STATE, METHODS, MODES, bridgeError, methodName, platformName, seconds, type Account, type Bridge, type BridgeStatus, type LlmBridgeApi, type LoginBody, type LoginMethod, type Slot } from './api'

// 一座会话桥的状态：三个账号槽位的登录情况与操作、通道、排队与进行中的回合数。
// 登录用的密钥 / 令牌只在提交时发出去，提交成功就清空输入框；页面不显示、不记录它们。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; status?: BridgeStatus; loading: boolean; loadError: unknown; canWrite: boolean }>()
const emit = defineEmits<{ reload: [] }>()

const POLL_MS = 3000
const error = ref(''), notice = ref(''), busy = ref('')
const login = reactive({ slot: null as Slot | null, method: 'api_key' as LoginMethod, secret: '' })
const device = ref<{ slot: Slot; label: string; url: string; code: string } | null>(null)
const ackBusy = ref(false)
let timer: ReturnType<typeof setInterval> | null = null, polling = false

const acked = computed(() => props.status?.settings?.claude_subscription_ack ?? false)
const needsAck = (slot: Slot | null, method: LoginMethod) => slot === 'claude' && method !== 'api_key' && !acked.value
const methodsOf = (account: Account) => account.login_methods.filter((m): m is LoginMethod => m in METHODS)
const stateOf = (account: Account) => ACCOUNT_STATE[account.state] ?? { text: account.state, tone: 'tone-muted' }
const secretLabel = computed(() => (login.method === 'api_key' ? 'API 密钥' : '订阅令牌'))
const needsSecret = computed(() => login.method === 'api_key' || login.method === 'oauth_token')

function stopPolling() { if (timer) clearInterval(timer); timer = null }
onUnmounted(stopPolling)

function openLogin(account: Account) {
  const methods = methodsOf(account)
  Object.assign(login, { slot: account.slot as Slot, method: methods.includes('api_key') ? 'api_key' : methods[0] ?? 'api_key', secret: '' })
  error.value = notice.value = ''
}
function closeLogin() { Object.assign(login, { slot: null, secret: '' }) }

async function act(name: string, action: () => Promise<string>) {
  if (busy.value) return
  busy.value = name; error.value = notice.value = ''
  try { notice.value = await action() } catch (e) { error.value = bridgeError(e) } finally { busy.value = '' }
}

async function acknowledge(event: Event) {
  const box = event.target as HTMLInputElement
  if (!box.checked || ackBusy.value) return
  ackBusy.value = true; error.value = ''
  try { await props.api.saveSettings(props.bridge.id, { claude_subscription_ack: true }) } catch (e) { box.checked = false; error.value = bridgeError(e) } finally { ackBusy.value = false }
}

function submitLogin(account: Account) {
  const slot = account.slot as Slot, method = login.method
  if (needsAck(slot, method)) { error.value = 'Claude 用订阅登录前，要先勾选下面的风险确认。'; return }
  if (needsSecret.value && !login.secret.trim()) { error.value = `请填写${secretLabel.value}。`; return }
  const body: LoginBody = { method, ...(method === 'api_key' ? { api_key: login.secret.trim() } : {}), ...(method === 'oauth_token' ? { oauth_token: login.secret.trim() } : {}) }
  return act(`${slot}:login`, async () => {
    let out
    try { out = await props.api.login(props.bridge.id, slot, body) } catch (e) {
      // 会话桥的设置里还没确认风险（例如别人刚关掉）：重新读状态，让确认框显示出来
      if (e instanceof ApiError && e.status === 403 && slot === 'claude' && method !== 'api_key') { emit('reload'); throw new ApiError(403, 'LB_ACK_REQUIRED', '要先确认风险') }
      throw e
    }
    login.secret = ''
    if (out.state === 'failed') throw new ApiError(409, 'CONFLICT', out.detail || '登录没有成功，请重试。')
    if (method === 'device_code' && out.state === 'pending') {
      device.value = { slot, label: account.label, url: out.verification_url ?? '', code: out.user_code ?? '' }
      closeLogin(); startPolling(slot, account.label)
      return ''
    }
    closeLogin(); emit('reload')
    return `「${account.label}」已登录。`
  })
}

function startPolling(slot: Slot, label: string) {
  stopPolling()
  timer = setInterval(async () => {
    if (polling) return
    polling = true
    try {
      const state = await props.api.loginState(props.bridge.id, slot)
      if (!timer || state.state === 'pending') return
      stopPolling(); device.value = null
      if (state.state === 'done') { notice.value = `「${label}」已登录。`; error.value = '' }
      else error.value = state.detail || '登录没有完成，请重新登录。'
      emit('reload')
    } catch (e) {
      if (timer) { stopPolling(); device.value = null; error.value = `查不到登录进度：${bridgeError(e)}` }
    } finally { polling = false }
  }, POLL_MS)
}
function cancelDevice() { stopPolling(); device.value = null; emit('reload') }

function recheck(account: Account) {
  return act(`${account.slot}:refresh`, async () => { await props.api.refreshAccount(props.bridge.id, account.slot as Slot); return `已重新检查「${account.label}」。` })
}
function logout(account: Account) {
  if (!window.confirm(`退出「${account.label}」的登录？退出后智能体不能再用这个平台，已有的会话记录保留。`)) return
  return act(`${account.slot}:logout`, async () => { await props.api.logout(props.bridge.id, account.slot as Slot); return `已退出「${account.label}」的登录。` })
}
function reset(account: Account) {
  if (!window.confirm(`更换「${account.label}」的账号？\n\n这座会话桥上这个平台的全部会话都会被归档，登记的账号会被忘掉；之后可以登录新的账号。`)) return
  return act(`${account.slot}:reset`, async () => { const out = await props.api.resetAccount(props.bridge.id, account.slot as Slot); return `已归档 ${out.archived_sessions} 个会话；现在可以登录新的账号。` })
}
</script>

<template>
  <ManagementSection title="状态与登录" :label="`状态与登录：${bridge.name}`" description="每个平台在这座会话桥上只登录一个账号。登录信息保存在会话桥的数据卷里，不保存在工作台。">
    <template #actions>
      <button type="button" class="secondary-button" :disabled="loading" @click="emit('reload')"><RefreshCw aria-hidden="true" />刷新状态</button>
    </template>
    <p v-if="!bridge.enabled" role="note" class="lb-warn">这座会话桥已停用：智能体不能经它发指令。</p>
    <ActionFeedback :error="error" :notice="notice" />

    <p v-if="loading" role="status" class="data-state">正在读取会话桥的状态…</p>
    <div v-else-if="loadError || !status" class="data-state">
      <p role="alert">{{ bridgeError(props.loadError) }}</p>
      <button type="button" class="secondary-button" @click="emit('reload')">重试</button>
    </div>
    <div v-else-if="!status.reachable" class="data-state">
      <p role="alert">连不上这座会话桥：{{ status.error || '没有给出原因' }}</p>
      <p class="form-help">请确认会话桥容器在运行、登记的地址和令牌正确。</p>
      <button type="button" class="secondary-button" @click="emit('reload')">重试</button>
    </div>
    <template v-else>
      <div class="lb-counters" role="group" aria-label="回合">
        <span class="mg-tag">排队中 {{ status.queued }}</span>
        <span class="mg-tag">进行中 {{ status.running }}</span>
        <span v-if="status.version" class="mg-tag">会话桥版本 {{ status.version }}</span>
      </div>

      <div v-if="device" class="lb-code" role="region" aria-label="设备码登录">
        <p>正在登录「{{ device.label }}」：在你自己的浏览器里打开下面的网址，输入验证码。完成后这里会自动更新。</p>
        <p>网址：<span class="asv-code">{{ device.url || '会话桥没有给出网址' }}</span></p>
        <p>验证码：<strong>{{ device.code || '—' }}</strong></p>
        <p role="status" class="form-help">正在等待你完成登录…</p>
        <div><button type="button" class="secondary-button" @click="cancelDevice">不等了</button></div>
      </div>

      <div class="lb-accounts">
        <article v-for="a in status.accounts" :key="a.slot" class="lb-account" :aria-label="`账号：${a.label}`">
          <div class="lb-account-head"><h3>{{ a.label }}</h3><span class="asv-state" :class="stateOf(a).tone">{{ stateOf(a).text }}</span></div>
          <dl class="lb-meta">
            <dt>账号</dt><dd>{{ a.identity || '—' }}</dd>
            <dt>登录方式</dt><dd>{{ methodName(a.method) }}</dd>
            <dt>首次登录</dt><dd>{{ seconds(a.first_login_at) }}</dd>
            <dt>进行中</dt><dd>{{ a.running }} 个回合</dd>
          </dl>
          <p v-if="a.detail" class="form-help">{{ a.detail }}</p>
          <p v-if="a.registered && a.state === 'logged_out'" class="form-help">这座会话桥登记过这个平台的账号{{ a.identity ? `（${a.identity}）` : '' }}。重新登录同一个账号可以接着用原来的会话；要换成别的账号，先点“更换账号”。</p>
          <p v-if="a.state === 'mismatch'" role="note" class="lb-warn is-red">现在登录的账号，和这座会话桥最初登记的不是同一个。为了不把两个账号的会话混在一起，这个平台已暂停使用。要继续用，只能点“更换账号”：这座会话桥上这个平台的全部会话会被归档，然后登记新的账号。</p>

          <div class="lb-inline">
            <button v-if="canWrite && a.state !== 'mismatch' && methodsOf(a).length && login.slot !== a.slot" type="button" class="primary-button" :disabled="!!busy || !!device" :aria-label="`登录${a.label}`" @click="openLogin(a)">{{ a.state === 'logged_in' ? '重新登录' : '登录' }}</button>
            <button type="button" class="secondary-button" :disabled="!!busy" :aria-label="`重新检查${a.label}`" @click="recheck(a)">{{ busy === `${a.slot}:refresh` ? '正在检查…' : '重新检查' }}</button>
            <button v-if="canWrite && a.state === 'logged_in'" type="button" class="secondary-button" :disabled="!!busy" :aria-label="`退出登录${a.label}`" @click="logout(a)">退出登录</button>
            <button v-if="canWrite && (a.state === 'mismatch' || a.registered)" type="button" class="secondary-button lb-danger" :disabled="!!busy" :aria-label="`更换账号${a.label}`" @click="reset(a)">更换账号</button>
          </div>

          <form v-if="canWrite && login.slot === a.slot" class="asv-form lb-form" novalidate :aria-label="`登录${a.label}`" @submit.prevent="submitLogin(a)">
            <fieldset class="lb-checks">
              <legend>登录方式</legend>
              <label v-for="m in methodsOf(a)" :key="m" class="check-field"><input v-model="login.method" type="radio" :name="`lb-method-${a.slot}`" :value="m" @change="login.secret = ''" />{{ METHODS[m].name }}</label>
            </fieldset>
            <p class="form-help">{{ METHODS[login.method].help }}</p>
            <div v-if="needsAck(a.slot as Slot, login.method)" class="lb-warn is-red" role="group" aria-label="Claude 订阅登录的风险确认">
              <p><strong>先确认风险：</strong>Anthropic 要求开发者用 API 密钥接入；把订阅账号的登录用于自动化处在灰区，账号可能被限制。详见方案第 6.3 节（docs/v2/LLM_BRIDGE_PLAN.md）。不想承担这个风险，就改用“API 密钥”。</p>
              <label class="check-field"><input type="checkbox" :disabled="ackBusy" @change="acknowledge" />我已了解并接受这个风险，允许这座会话桥用订阅方式登录 Claude</label>
            </div>
            <div v-if="needsSecret" class="form-field">
              <label :for="`lb-secret-${a.slot}`">{{ secretLabel }}</label>
              <input :id="`lb-secret-${a.slot}`" v-model="login.secret" type="password" autocomplete="new-password" :maxlength="login.method === 'api_key' ? 500 : 2000" :disabled="needsAck(a.slot as Slot, login.method)" placeholder="提交后不再显示" />
            </div>
            <div class="lb-actions">
              <button type="button" class="secondary-button" :disabled="!!busy" @click="closeLogin">取消</button>
              <button type="submit" class="primary-button" :disabled="!!busy || needsAck(a.slot as Slot, login.method)">{{ busy === `${a.slot}:login` ? '正在登录…' : login.method === 'device_code' ? '获取验证码' : login.method === 'existing' ? '使用已有的登录' : '登录' }}</button>
            </div>
          </form>
        </article>
      </div>
      <p v-if="!status.accounts.length" class="ws-state-line">会话桥没有报告任何账号。</p>

      <h3>通道</h3>
      <p class="form-help">通道 = 平台 + 用法。“开发”是在会话桥容器里的开发环境中干活；云端通道目前只是占位。</p>
      <table class="asv-table lb-table" aria-label="通道">
        <thead><tr><th scope="col">平台</th><th scope="col">用法</th><th scope="col">可用</th><th scope="col">说明</th></tr></thead>
        <tbody>
          <tr v-for="c in status.channels" :key="`${c.platform}.${c.mode}`">
            <th scope="row">{{ platformName(c.platform) }}</th>
            <td data-label="用法" class="lb-nowrap">{{ MODES[c.mode] ?? c.mode }}</td>
            <td data-label="可用" class="lb-nowrap"><span class="asv-state" :class="c.available ? 'tone-green' : 'tone-muted'">{{ c.available ? '可用' : '不可用' }}</span></td>
            <td data-label="说明" class="lb-full">{{ c.note || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </ManagementSection>
</template>
