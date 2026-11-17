<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, toRef } from 'vue'
import { Activity, ArrowRight, Check, Clock, KeyRound, Pencil, Play, RefreshCw, UserRound } from '@lucide/vue'
import ActionFeedback from '../../components/ActionFeedback.vue'
import AccountQuota from './AccountQuota.vue'
import { PLATFORM_MARK } from './chat/chatApi'
import BridgeConnectivity from './BridgeConnectivity.vue'
import { ApiError } from '../../services/api'
import { ACCOUNT_STATE, METHODS, MODES, PLATFORMS, PLATFORM_SLOT, bridgeError, methodName, platformName, seconds, type Account, type ConnectivityTarget, type Bridge, type BridgeStatus, type LlmBridgeApi, type LoginBody, type LoginMethod, type Platform, type Slot } from './api'

// 一个 MCP 管理器的平台与账号：它控制哪些平台（owner 勾选，立即保存）、这些平台的账号登录情况与操作、
// 通道、排队与进行中的回合数。没勾选的平台不能登录，智能体也用不了；已有的登录保留着，可以退出。
// 登录用的密钥 / 令牌只在提交时发出去，提交成功就清空输入框；页面不显示、不记录它们。
const props = defineProps<{ api: LlmBridgeApi; bridge: Bridge; status?: BridgeStatus; loading: boolean; loadError: unknown; canWrite: boolean }>()
const emit = defineEmits<{ reload: [] }>()

const POLL_MS = 3000
// 开始网页登录没等到答复时，最多再查多久的登录进度（管理器启动登录服务最多 40 秒、申请网址最多 60 秒）
const RECOVER_MS = 120_000
// 网页登录：和设备码一样，拿到网址后等人在自己的浏览器里完成
const WEB_METHODS: LoginMethod[] = ['web_cn', 'web_intl']
// 平台连通性（T-0240）：这个管理器所在的服务器到各平台能不能用、延迟多少；管理器连得上才测
const bridgeId = toRef(() => (props.status?.reachable ? props.bridge.id : null))
const net = props.api.useConnectivity(bridgeId)
const netBusy = ref(false), netError = ref('')
async function recheckNet() {
  if (netBusy.value) return
  netBusy.value = true; netError.value = ''
  try { await props.api.recheckConnectivity(props.bridge.id) } catch (e) { netError.value = bridgeError(e) } finally { netBusy.value = false }
}
const netFailed = computed(() => netError.value || (net.error.value ? bridgeError(net.error.value) : ''))
const error = ref(''), notice = ref(''), busy = ref('')
const login = reactive({ slot: null as Slot | null, method: 'api_key' as LoginMethod, secret: '' })
// fetching：开始登录那一次没等到答复（页面等不及、连接中断、网关超时），正在向管理器查登录网址
const device = ref<{ slot: Slot; label: string; url: string; code: string; fetching?: boolean } | null>(null)
let recoverUntil = 0
const ackBusy = ref(false)
let timer: ReturnType<typeof setInterval> | null = null, polling = false

// 控制的平台：保存失败时重画勾选框，回到服务器上的状态
const controlled = computed(() => (props.status?.reachable ? props.status.platforms : []))
const redraw = ref(0)
const checked = (event: Event) => (event.target as HTMLInputElement).checked
function setPlatform(platform: Platform, on: boolean) {
  const next = PLATFORMS.map(p => p.id).filter(id => (id === platform ? on : controlled.value.includes(id)))
  const name = platformName(platform)
  if (!next.length) { error.value = '至少要控制一个平台。不想再用这个 MCP 管理器，可以停用或删除它。'; notice.value = ''; redraw.value += 1; return }
  if (!on && !window.confirm(`不再让「${props.bridge.name}」控制 ${name}？\n\n智能体不能再经它用 ${name}；已有的登录和会话记录都保留，重新勾选后可以接着用。`)) { redraw.value += 1; return }
  return act('platforms', async () => {
    try { await props.api.saveSettings(props.bridge.id, { platforms: next }) } catch (e) { redraw.value += 1; throw e }
    return on ? `已让「${props.bridge.name}」控制 ${name}。${accountOf(platform)?.state === 'logged_in' ? '' : '接下来在下面登录它的账号。'}` : `「${props.bridge.name}」不再控制 ${name}。`
  })
}
const accountOf = (platform: Platform) => props.status?.accounts.find(a => a.slot === PLATFORM_SLOT[platform])
// 没勾选的平台：只有还登录着（或账号不一致）才显示，方便退出登录
const accounts = computed(() => (props.status?.accounts ?? []).filter(a => a.enabled || a.state === 'logged_in' || a.state === 'mismatch'))
const channels = computed(() => (props.status?.channels ?? []).filter(c => c.enabled))
// 通道可用性表按平台一行：支持哪几种用法（不可用的标出来）、整体能不能用、各用法的说明
const channelRows = computed(() => {
  const rows = new Map<string, { platform: string; modes: { mode: string; available: boolean; note: string }[] }>()
  for (const c of channels.value) {
    const row = rows.get(c.platform) ?? { platform: c.platform, modes: [] }
    row.modes.push({ mode: c.mode, available: c.available, note: c.note })
    rows.set(c.platform, row)
  }
  // 说明按文字去重；同一平台不同用法的说明不一样时，前面标上用法
  return [...rows.values()].map(r => {
    const texts = [...new Set(r.modes.map(m => m.note).filter(Boolean))]
    const notes = texts.map(note => (texts.length > 1 ? `${r.modes.filter(m => m.note === note).map(m => MODES[m.mode] ?? m.mode).join('、')}：${note}` : note))
    return { ...r, available: r.modes.some(m => m.available), notes }
  })
})
// 已控制平台：平时只读，点“编辑”才能勾选（勾选仍然立即保存，取消要确认）
const editing = ref(false)
// 一个账号槽位对应的平台（OpenAI 的账号同时给 ChatGPT 和 Codex 用）：卡片的标记和额度按它们来
const platformsOf = (slot: string) => PLATFORMS.map(p => p.id).filter(id => PLATFORM_SLOT[id] === slot)
const markOf = (slot: string) => platformsOf(slot).map(id => PLATFORM_MARK[id]).join('') || '?'
const quotaOf = (slot: string) => platformsOf(slot).flatMap(id => (props.status?.quota?.[id] ? [{ platform: id, quota: props.status.quota[id]! }] : []))
// 有额度、却没有账号卡片的平台（很少见）：放在卡片后面
const looseQuota = computed(() => Object.entries(props.status?.quota ?? {}).filter(([id]) => !accounts.value.some(a => platformsOf(a.slot).includes(id as Platform))).map(([platform, quota]) => ({ platform, quota })))
const canAsk = computed(() => Object.values(props.status?.quota ?? {}).some(q => q.can_refresh))
const asking = ref(false), askError = ref('')
async function askQuota() {
  if (asking.value) return
  asking.value = true; askError.value = ''
  try { await props.api.refreshQuota(props.bridge.id) } catch (e) { askError.value = bridgeError(e) } finally { asking.value = false }
}
// 连通性的一句话：可用（最快的那个地址的延迟）/ 被平台拒绝 / 连不上
function netSummary(slot: string) {
  const info = net.data.value?.slots[slot]
  if (!info) return null
  const ok = info.targets.filter((t: ConnectivityTarget) => t.ok && t.latency_ms != null).map((t: ConnectivityTarget) => t.latency_ms!)
  if (info.ok) return { tone: 'ok', text: `可用${ok.length ? ` · ${Math.min(...ok)} ms` : ''}` }
  return { tone: 'bad', text: info.targets.some((t: ConnectivityTarget) => t.state === 'blocked') ? '被平台拒绝' : '连不上' }
}
// 运行概览：展开看每个平台同时在跑几个会话、上限多少
const showLoad = ref(false)

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
  // 有网页登录的平台（CodeBuddy）默认用它；别的平台照旧默认 API 密钥
  const first = methods.find(m => WEB_METHODS.includes(m)) ?? (methods.includes('api_key') ? 'api_key' : methods[0] ?? 'api_key')
  Object.assign(login, { slot: account.slot as Slot, method: first, secret: '' })
  error.value = notice.value = ''
}
function closeLogin() { Object.assign(login, { slot: null, secret: '' }) }
// 开始登录的请求没有完整的答复：页面等不及或连接中断（WRITE_UNKNOWN）、CDN / 网关超时（502、504、52x 的网页）、
// 工作台等管理器超时（EXTERNAL_UNAVAILABLE）。管理器那边可能还在申请登录网址，结果未知
const uncertain = (e: unknown) => e instanceof ApiError && (['WRITE_UNKNOWN', 'EXTERNAL_UNAVAILABLE'].includes(e.code) || (e.code === 'HTTP_ERROR' && (e.status === 502 || e.status === 504 || e.status >= 520)))

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
      // 网页登录没等到答复：不再重新发起（会重启管理器里的登录服务，又要等同样久），改查登录进度，网址出来就显示
      if (WEB_METHODS.includes(method) && uncertain(e)) {
        login.secret = ''
        device.value = { slot, label: account.label, url: '', code: '', fetching: true }
        recoverUntil = Date.now() + RECOVER_MS
        closeLogin(); startPolling(slot, account.label)
        return ''
      }
      throw e
    }
    login.secret = ''
    if (out.state === 'failed') throw new ApiError(409, 'CONFLICT', out.detail || '登录没有成功，请重试。')
    if (out.state === 'pending' && (method === 'device_code' || WEB_METHODS.includes(method))) {
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
      if (!timer) return
      const fetching = !!device.value?.fetching
      if (state.state === 'pending') {
        // 找回的网址：开始登录那一次没等到答复，管理器随后申请到了
        if (fetching && state.verification_url) device.value = { ...device.value!, url: state.verification_url, code: state.user_code ?? '', fetching: false }
        return
      }
      // 管理器还在启动登录服务、申请网址：这期间登录进度是“没有在登录”
      if (state.state === 'none' && fetching && Date.now() < recoverUntil) return
      stopPolling(); device.value = null
      if (state.state === 'done') { notice.value = `「${label}」已登录。`; error.value = '' }
      else if (fetching && state.state === 'none') error.value = `没有拿到「${label}」的登录网址：管理器可能还在启动登录服务，或者已经失败。请稍后再点“获取登录网址”；一直不行时看 MCP 管理器的日志，或改用 API 密钥登录。`
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
  if (!window.confirm(`更换「${account.label}」的账号？\n\n这个 MCP 管理器上这个平台的全部会话都会被归档，登记的账号会被忘掉；之后可以登录新的账号。`)) return
  return act(`${account.slot}:reset`, async () => { const out = await props.api.resetAccount(props.bridge.id, account.slot as Slot); return `已归档 ${out.archived_sessions} 个会话；现在可以登录新的账号。` })
}
</script>

<template>
  <section class="lb-accounts-panel" :aria-label="`平台与账号：${bridge.name}`">
    <p v-if="!bridge.enabled" role="note" class="lb-warn">这个 MCP 管理器已停用：智能体不能经它发指令。</p>
    <ActionFeedback :error="error" :notice="notice" />

    <p v-if="loading" role="status" class="data-state">正在读取这个 MCP 管理器的状态…</p>
    <div v-else-if="loadError || !status" class="data-state">
      <p role="alert">{{ bridgeError(props.loadError) }}</p>
      <button type="button" class="secondary-button" @click="emit('reload')">重试</button>
    </div>
    <div v-else-if="!status.reachable" class="data-state">
      <p role="alert">连不上这个 MCP 管理器：{{ status.error || '没有给出原因' }}</p>
      <p class="form-help">请确认它的容器在运行、填的地址和令牌正确。</p>
      <button type="button" class="secondary-button" @click="emit('reload')">重试</button>
    </div>
    <template v-else>
      <div class="lb-control-row">
        <fieldset :key="redraw" class="lb-control" :class="{ 'is-editing': editing }" :disabled="!canWrite || !editing || !!busy" aria-label="控制的平台">
          <legend>已控制平台：</legend>
          <label v-for="p in PLATFORMS" :key="p.id" class="lb-control-chip"><input type="checkbox" :checked="controlled.includes(p.id)" @change="setPlatform(p.id, checked($event))" />{{ p.name }}</label>
        </fieldset>
        <p class="lb-control-note">{{ editing ? '勾选的平台才能在这里登录、被智能体使用；改动立即保存，取消前会确认。' : '只有勾选的平台能在这里登录、被智能体使用。' }}ChatGPT 与 Codex 共用同一个账号。</p>
        <button v-if="canWrite" type="button" class="secondary-button lb-control-edit" :aria-pressed="editing" :aria-label="editing ? '完成编辑控制的平台' : '编辑控制的平台'" @click="editing = !editing"><component :is="editing ? Check : Pencil" aria-hidden="true" />{{ editing ? '完成' : '编辑' }}</button>
      </div>

      <div class="lb-overview" role="group" aria-label="运行概览">
        <strong class="lb-overview-title">运行概览</strong>
        <span class="lb-overview-item"><Play aria-hidden="true" />进行中 <b>{{ status.running }}</b></span>
        <span class="lb-overview-item"><Clock aria-hidden="true" />排队中 <b>{{ status.queued }}</b></span>
        <span v-if="status.version" class="lb-overview-version">版本 {{ status.version }}</span>
        <button v-if="Object.keys(status.load).length" type="button" class="asv-link lb-overview-more" :aria-expanded="showLoad" @click="showLoad = !showLoad">{{ showLoad ? '收起平台并发' : '查看平台并发' }}<ArrowRight aria-hidden="true" /></button>
      </div>
      <ul v-if="showLoad" class="lb-load" aria-label="各平台的并发">
        <li v-for="(load, platform) in status.load" :key="platform" :class="{ 'is-full': load.running >= load.limit }">{{ platformName(String(platform)) }}：{{ load.running }} / {{ load.limit }} 个会话在跑<template v-if="load.waiting">，{{ load.waiting }} 个在等</template></li>
      </ul>

      <div v-if="device" class="lb-code" role="region" :aria-label="device.code ? '设备码登录' : '网页登录'">
        <template v-if="device.fetching">
          <p>正在为「{{ device.label }}」申请登录网址：管理器要先启动官方程序的登录服务，再向平台申请网址，可能要一两分钟。网址出来后显示在这里。</p>
          <p role="status" class="form-help">正在等待登录网址…</p>
        </template>
        <template v-else>
          <p>正在登录「{{ device.label }}」：在你自己的浏览器里打开下面的网址{{ device.code ? '，输入验证码' : '并登录' }}。完成后这里会自动更新。</p>
          <p>网址：<a v-if="device.url.startsWith('https://')" class="asv-link lb-login-url" :href="device.url" target="_blank" rel="noopener noreferrer">{{ device.url }}</a><span v-else class="asv-code">{{ device.url || '没有给出网址' }}</span></p>
          <p v-if="device.code">验证码：<strong>{{ device.code }}</strong></p>
          <p role="status" class="form-help">正在等待你完成登录…</p>
        </template>
        <div><button type="button" class="secondary-button" @click="cancelDevice">不等了</button></div>
      </div>
      <p v-if="askError" class="lb-warn is-red" role="alert">{{ askError }}</p>

      <div class="lb-accounts">
        <article v-for="a in accounts" :key="a.slot" class="lb-account" :class="{ 'is-off': !a.enabled }" :aria-label="`账号：${a.label}`">
          <div class="lb-account-head">
            <span class="lb-account-mark" :data-slot="a.slot" aria-hidden="true">{{ markOf(a.slot) }}</span>
            <h3>{{ a.label }}</h3>
            <span class="asv-state" :class="stateOf(a).tone">{{ stateOf(a).text }}</span>
          </div>
          <p v-if="!a.enabled" role="note" class="lb-warn">这个管理器没有勾选这个平台：智能体用不了它，也不能在这里重新登录。原来的登录还在，不需要的话可以退出。</p>
          <dl class="lb-kv">
            <dt><UserRound aria-hidden="true" />身份</dt><dd>{{ a.identity || '—' }}</dd>
            <dt><KeyRound aria-hidden="true" />登录方式</dt><dd :title="a.first_login_at ? `首次登录：${seconds(a.first_login_at)}` : undefined">{{ methodName(a.method) }}</dd>
            <template v-if="a.enabled && netSummary(a.slot)">
              <dt><Activity aria-hidden="true" />连通性</dt><dd><span class="lb-net-sum" :data-tone="netSummary(a.slot)!.tone">{{ netSummary(a.slot)!.text }}</span></dd>
            </template>
            <dt><Play aria-hidden="true" />运行</dt><dd>{{ a.running }} 个回合</dd>
          </dl>
          <BridgeConnectivity v-if="a.enabled" :data="net.data.value" :account="a.slot" :label="a.label" :loading="net.isPending.value || netBusy" :failed="netFailed" />
          <AccountQuota v-for="q in quotaOf(a.slot)" :key="q.platform" :platform="q.platform" :quota="q.quota" />
          <p v-if="a.detail" class="form-help">{{ a.detail }}</p>
          <p v-if="a.registered && a.state === 'logged_out'" class="form-help">这个管理器登记过这个平台的账号{{ a.identity ? `（${a.identity}）` : '' }}。重新登录同一个账号可以接着用原来的会话；要换成别的账号，先点“更换账号”。</p>
          <p v-if="a.state === 'mismatch'" role="note" class="lb-warn is-red">现在登录的账号，和这个管理器最初登记的不是同一个。为了不把两个账号的会话混在一起，这个平台已暂停使用。要继续用，只能点“更换账号”：这个管理器上这个平台的全部会话会被归档，然后登记新的账号。</p>
          <p v-if="a.enabled && a.state !== 'logged_in' && a.state !== 'mismatch' && netSummary(a.slot)?.tone !== 'ok'" class="lb-hint-amber">登录前请确认这台服务器能访问这个平台（看上面的连通性）。</p>

          <div class="lb-inline lb-account-actions">
            <button v-if="canWrite && a.enabled && a.state !== 'mismatch' && methodsOf(a).length && login.slot !== a.slot" type="button" class="primary-button" :disabled="!!busy || !!device" :aria-label="`登录${a.label}`" @click="openLogin(a)">{{ a.state === 'logged_in' ? '重新登录' : '登录账号' }}</button>
            <button v-if="canAsk && quotaOf(a.slot).some(q => q.quota.can_refresh)" type="button" class="secondary-button" :disabled="asking" @click="askQuota">{{ asking ? '正在查…' : '向平台查一次' }}</button>
            <button type="button" class="secondary-button" :disabled="!!busy" :aria-label="`重新检查${a.label}`" @click="recheck(a)"><RefreshCw aria-hidden="true" />{{ busy === `${a.slot}:refresh` ? '正在检查…' : '重新检查' }}</button>
            <button v-if="canWrite && a.state === 'logged_in'" type="button" class="secondary-button" :disabled="!!busy" :aria-label="`退出登录${a.label}`" @click="logout(a)">退出登录</button>
            <button v-if="canWrite && (a.state === 'mismatch' || a.registered)" type="button" class="secondary-button lb-danger" :disabled="!!busy" :aria-label="`更换账号${a.label}`" @click="reset(a)">更换账号</button>
          </div>

          <form v-if="canWrite && a.enabled && login.slot === a.slot" class="asv-form lb-form" novalidate :aria-label="`登录${a.label}`" @submit.prevent="submitLogin(a)">
            <fieldset class="lb-checks">
              <legend>登录方式</legend>
              <label v-for="m in methodsOf(a)" :key="m" class="check-field"><input v-model="login.method" type="radio" :name="`lb-method-${a.slot}`" :value="m" @change="login.secret = ''" />{{ METHODS[m].name }}</label>
            </fieldset>
            <p class="form-help">{{ METHODS[login.method].help }}</p>
            <div v-if="needsAck(a.slot as Slot, login.method)" class="lb-warn is-red" role="group" aria-label="Claude 订阅登录的风险确认">
              <p><strong>先确认风险：</strong>Anthropic 要求开发者用 API 密钥接入；把订阅账号的登录用于自动化处在灰区，账号可能被限制。详见方案第 6.3 节（docs/v2/LLM_BRIDGE_PLAN.md）。不想承担这个风险，就改用“API 密钥”。</p>
              <label class="check-field"><input type="checkbox" :disabled="ackBusy" @change="acknowledge" />我已了解并接受这个风险，允许这个 MCP 管理器用订阅方式登录 Claude</label>
            </div>
            <div v-if="needsSecret" class="form-field">
              <label :for="`lb-secret-${a.slot}`">{{ secretLabel }}</label>
              <input :id="`lb-secret-${a.slot}`" v-model="login.secret" type="password" autocomplete="new-password" :maxlength="login.method === 'api_key' ? 500 : 2000" :disabled="needsAck(a.slot as Slot, login.method)" placeholder="提交后不再显示" />
            </div>
            <div class="lb-actions">
              <button type="button" class="secondary-button" :disabled="!!busy" @click="closeLogin">取消</button>
              <button type="submit" class="primary-button" :disabled="!!busy || needsAck(a.slot as Slot, login.method)">{{ busy === `${a.slot}:login` ? '正在登录…' : login.method === 'device_code' ? '获取验证码' : WEB_METHODS.includes(login.method) ? '获取登录网址' : login.method === 'existing' ? '使用已有的登录' : '登录' }}</button>
            </div>
            <p v-if="busy === `${a.slot}:login` && WEB_METHODS.includes(login.method)" role="status" class="form-help">正在启动「{{ a.label }}」的登录服务并申请登录网址，可能要一分钟左右，请稍候。</p>
          </form>
        </article>
        <AccountQuota v-for="q in looseQuota" :key="q.platform" :platform="q.platform" :quota="q.quota" />
      </div>
      <p v-if="!accounts.length" class="ws-state-line">没有可以登录的账号：先在上面勾选要控制的平台。</p>

      <section class="lb-channels mg-section" aria-label="通道可用性">
        <div class="lb-channels-head">
          <h3>通道可用性</h3>
          <button type="button" class="secondary-button" :disabled="netBusy || net.isFetching.value" @click="recheckNet"><RefreshCw aria-hidden="true" />{{ netBusy || net.isFetching.value ? '正在检测…' : '重新检测连通性' }}</button>
        </div>
        <table class="asv-table lb-table" aria-label="通道">
          <thead><tr><th scope="col">平台</th><th scope="col">支持用法</th><th scope="col" class="lb-nowrap">当前可用性</th><th scope="col">说明</th></tr></thead>
          <tbody>
            <tr v-for="r in channelRows" :key="r.platform">
              <th scope="row" class="lb-nowrap"><span class="lb-table-mark" :data-platform="r.platform" aria-hidden="true">{{ PLATFORM_MARK[r.platform] ?? '?' }}</span>{{ platformName(r.platform) }}</th>
              <td data-label="支持用法">
                <span class="lb-modes-list"><span v-for="m in r.modes" :key="m.mode" class="lb-mode" :data-ok="m.available" :title="m.note || undefined">{{ MODES[m.mode] ?? m.mode }}<span v-if="!m.available" class="lb-mode-off">不可用</span></span></span>
              </td>
              <td data-label="可用" class="lb-nowrap"><span class="lb-avail" :data-ok="r.available">{{ r.available ? '可用' : '不可用' }}</span></td>
              <td data-label="说明" class="lb-full">
                <template v-if="r.notes.length"><span v-for="n in r.notes" :key="n" class="lb-channel-note">{{ n }}</span></template>
                <template v-else>—</template>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="lb-channels-note">通道 = 平台 + 用法，只列出这个管理器控制的平台；可用性受平台控制、登录状态和连通性影响。“开发”是在这个管理器的容器里的开发环境中干活；云端通道目前只是占位。</p>
      </section>
    </template>
  </section>
</template>
