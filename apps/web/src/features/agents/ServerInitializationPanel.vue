<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { CircleAlert, CircleCheck, CircleDashed, CircleX, Info, KeyRound, ListChecks, LoaderCircle, Play, RefreshCw, RotateCcw, ShieldAlert, ShieldCheck, Trash2, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { CONNECTION_HELP, CONNECTION_STATUS, endpoint, initStatus, putServerDetail, serversKey, type ServerDetail } from './servers'
import { hostKeyCommand, sshError } from './ssh'
import SshJob from './SshJob.vue'

// 服务器的连接与初始化（T-0181，改造规范第 4、5、7 节；按效果图重排 T-0266）：核对主机指纹 → 测试连接（用保存的密码登录，只读检查）→
// 初始化（检测 / 安装 Docker 与 Compose、准备部署目录，后台任务逐步显示）；旧服务器补充登录密码、旧专用公钥定向退役。
// 布局：标题右边只有一个主按钮（初始化 / 重新初始化）；左边是需要处理的提示与最近一次任务，右边是“登录前提”检查清单
// （地址、密码、指纹、连接测试，各自的操作放在旁边）；底部单独一行移除服务器。一次性的成功提示在右下角停几秒。
// 进页、刷新、切换服务器都只读（GET），每个写操作都要 owner 点按钮；失败显示任务记录里的真实步骤与原因。
// 旧方案的“专用密钥 / 吊销”入口已经移除（专用密钥登录停用，见 docs/decisions/2026-11-09-agent-server-password-auth.md）。
const props = defineProps<{ detail: ServerDetail }>()
const emit = defineEmits<{ edit: [mode: 'credentials' | 'address']; removed: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const { busy, error, notice, failure, act, refresh, header } = useAgentAction(scope, sshError)
const server = computed(() => props.detail.server)
const ssh = computed(() => server.value.ssh)
const job = computed(() => props.detail.job)
const running = computed(() => job.value?.status === 'running')
const path = computed(() => ({ server_id: server.value.id }))
const checked = ref(false)
watch(() => [server.value.id, ssh.value?.host_key_fingerprint] as const, () => { checked.value = false })

const init = computed(() => initStatus(server.value))
const connection = computed(() => ssh.value ? CONNECTION_STATUS[ssh.value.connection_status] : null)
const blocked = computed(() => !ssh.value || ['host_key_changed', 'credential_unreadable', 'credentials_required'].includes(ssh.value.connection_status))
// 能用保存的密码登录：有密码、指纹已确认、没有明确的身份 / 凭证问题
const canLogin = computed(() => !!ssh.value?.password_set && !!ssh.value.host_key_confirmed && !blocked.value)
const hasDeployments = computed(() => server.value.managed_deployments > 0)
const legacy = computed(() => ssh.value?.legacy_key_cleanup_state === 'pending' || ssh.value?.legacy_key_cleanup_state === 'manual_required')
const verifying = computed(() => !!ssh.value?.password_set && !ssh.value.host_key_confirmed)
const connectionProblem = computed(() => !!ssh.value?.password_set && ['auth_failed', 'unreachable', 'host_key_changed', 'credential_unreadable'].includes(ssh.value.connection_status))
const PLAN = ['检测系统、架构、sudo 权限、磁盘与网络', '检测 Docker 与 Compose；缺失时安装平台锁定的版本，已有兼容版本直接复用', '准备平台管理的部署目录与基础依赖', '验证 Docker、部署目录与平台接入地址可用']

// 主按钮：初始化中 / 重新初始化 / 初始化服务器
const preparing = computed(() => running.value && job.value?.kind === 'prepare')
const again = computed(() => ['ready', 'failed', 'interrupted'].includes(server.value.initialization_status ?? ''))
const prepareLabel = computed(() => preparing.value ? '初始化中…' : again.value ? '重新初始化' : '初始化服务器')
const subtitle = computed(() => {
  const s = ssh.value
  if (!s) return ''
  if (running.value) return '这台服务器正在执行任务，结束后再操作。'
  if (!s.password_set) return '补充登录密码后才能测试连接、初始化与部署。'
  if (!s.host_key_confirmed) return '先核对主机指纹，再测试连接与初始化。'
  if (connectionProblem.value) return '最近一次连接检测失败：先修好连接，再测试连接、初始化。'
  if (server.value.initialization_status === 'ready') return '环境已就绪，可以在这台服务器上部署智能体了。'
  if (server.value.initialization_status === 'failed' || server.value.initialization_status === 'interrupted') return '看下面失败的那一步，处理后重新初始化。'
  return '初始化会检测并准备 Docker 与部署目录，成功后就能部署智能体。'
})

// 登录前提：地址、密码、指纹、连接测试
type Tone = 'ok' | 'warn' | 'bad' | 'todo'
interface Check { key: 'address' | 'password' | 'fingerprint' | 'connection'; label: string; value: string; note?: string; tone: Tone }
const checks = computed<Check[]>(() => {
  const s = ssh.value
  if (!s) return []
  const status = s.connection_status
  return [
    { key: 'address', label: '登录地址', value: `${s.username}@${endpoint(s.host, s.port)}`, note: s.address !== s.host ? s.address : undefined, tone: 'ok' },
    !s.password_set ? { key: 'password', label: '登录密码', value: '还没有保存', tone: 'warn' }
      : status === 'credential_unreadable' ? { key: 'password', label: '登录密码', value: '无法解密，请重新填写', tone: 'bad' }
        : status === 'auth_failed' ? { key: 'password', label: '登录密码', value: '最近一次被服务器拒绝', note: `第 ${s.credential_revision} 版`, tone: 'bad' }
          : { key: 'password', label: '登录密码', value: '已加密保存', note: `第 ${s.credential_revision} 版`, tone: 'ok' },
    status === 'host_key_changed' ? { key: 'fingerprint', label: '主机指纹', value: '与确认过的不一致', tone: 'bad' }
      : s.host_key_confirmed ? { key: 'fingerprint', label: '主机指纹', value: '已确认', note: s.host_key_confirmed_at ? formatTime(s.host_key_confirmed_at) : undefined, tone: 'ok' }
        : { key: 'fingerprint', label: '主机指纹', value: '待核对', tone: 'warn' },
    {
      key: 'connection', label: '连接测试', value: CONNECTION_STATUS[status].label,
      note: s.last_connection_at ? formatTime(s.last_connection_at) : status === 'unknown' ? (s.host_key_confirmed ? '还没有测试' : '核对指纹后才能测试') : undefined,
      tone: status === 'connected' ? 'ok' : status === 'unknown' ? 'todo' : status === 'credentials_required' ? 'warn' : 'bad',
    },
  ]
})
const passed = computed(() => checks.value.filter(c => c.tone === 'ok').length)
const CHECK_ICON = { ok: CircleCheck, warn: CircleAlert, bad: CircleX, todo: CircleDashed } as const

const removeHelp = computed(() => {
  if (running.value) return '这台服务器正在执行任务，结束后才能移除。'
  if (ssh.value?.legacy_key_cleanup_state === 'pending') return '服务器上还留有旧方案的专用公钥：先完成上面的“旧公钥退役”，再移除服务器。'
  if (hasDeployments.value) return `这台服务器上有 ${server.value.managed_deployments} 条平台部署记录：要更换地址或移除服务器，先在 OpenClaw 智能体页卸载它们。`
  return '工作台不再保存它的地址、主机公钥与登录密码；不会停止或删除服务器上的任何容器。'
})

// 一次性的成功提示：右下角停 5 秒
let hide: ReturnType<typeof setTimeout> | undefined
watch(notice, (text) => {
  if (hide) clearTimeout(hide)
  if (text) hide = setTimeout(() => { if (notice.value === text) notice.value = '' }, 5000)
})
onBeforeUnmount(() => { if (hide) clearTimeout(hide) })

const verifyEl = ref<HTMLElement | null>(null)
async function goVerify() {
  await nextTick()
  verifyEl.value?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  verifyEl.value?.querySelector<HTMLInputElement>('input[type="checkbox"]')?.focus({ preventScroll: true })
}

function settle() { if (failure.value instanceof ApiError && failure.value.code === 'WRITE_UNKNOWN') void refresh() }
const put = (next: ServerDetail) => putServerDetail(cache, user, scope, next)

async function confirm() {
  const s = ssh.value
  if (!s || !checked.value) return
  await act(async () => put(dataOf(await auth.api.POST('/api/v1/agents/servers/{server_id}/confirm', { params: { header, path: path.value }, body: { version: server.value.version, fingerprint: s.host_key_fingerprint } }))), '指纹已确认：之后只接受这把主机公钥。下一步测试连接。')
  settle()
}
async function test() {
  let next: ServerDetail | null = null
  const ok = await act(async () => {
    next = dataOf(await auth.slowApi.POST('/api/v1/agents/servers/{server_id}/test-connection', { params: { header: { ...header, 'Idempotency-Key': crypto.randomUUID() }, path: path.value }, body: { version: server.value.version } }))
    put(next)
  })
  const result = (next as ServerDetail | null)?.server.ssh
  if (ok && result) notice.value = result.connection_status === 'connected' ? '连接正常：已用保存的密码登录服务器。' : `测试结果：${CONNECTION_STATUS[result.connection_status].label}。${CONNECTION_HELP[result.connection_status]}`
  settle()
}
async function prepare() {
  await act(async () => put(dataOf(await auth.api.POST('/api/v1/agents/servers/{server_id}/prepare', { params: { header, path: path.value }, body: { version: server.value.version } }))), '已开始初始化：下面显示每一步的进度')
  settle()
}
async function cleanup() {
  if (!window.confirm('用保存的密码登录这台服务器，只删除 authorized_keys 里工作台以前装的那一行公钥（其他用户与容器服务器的公钥不动），成功后删除工作台保存的旧私钥。继续？')) return
  await act(async () => put(dataOf(await auth.slowApi.POST('/api/v1/agents/servers/{server_id}/legacy-key-cleanup', { params: { header, path: path.value }, body: { version: server.value.version } }))), '已处理旧公钥：结果见下方状态')
  settle()
}
async function remove() {
  const auto = server.value.kind === 'auto'
  if (!window.confirm(auto
    ? `移除「${server.value.name}」？上面的实例改为未归属，下一次上报时会重新自动识别。`
    : `移除服务器「${server.value.name}」？工作台不再保存它的地址、主机公钥与登录密码；不会停止或删除服务器上的任何容器。`)) return
  // 删除成功就先从列表的缓存里去掉它、回到列表，再刷新：刷新时这台服务器的详情是 404，
  // 详情页会卸载本组件，之后再发的事件会被丢掉；列表重新取回之前也不该还显示它
  const id = server.value.id, list = serversKey(user, scope)
  const ok = await act(async () => {
    await auth.api.DELETE('/api/v1/agents/servers/{server_id}', { params: { header, path: path.value, query: { version: server.value.version } } })
    await cache.cancelQueries({ queryKey: list, exact: true })
    cache.setQueryData<Schema['AgentServerList']>(list, old => old && { ...old, items: old.items.filter(s => s.id !== id) })
    emit('removed')
  }, '服务器已移除')
  if (!ok) settle()
}
</script>

<template>
  <section class="ws-panel asv-init" aria-label="连接与初始化">
    <template v-if="server.kind === 'auto' || !ssh">
      <div class="asv-init-head">
        <div class="asv-init-title">
          <h3>自动识别的服务器</h3>
          <p class="asv-init-sub">这台服务器是按实例上报的服务器指纹自动识别的（手动部署的实例）。</p>
        </div>
      </div>
      <div class="asv-init-body is-single">
        <p class="form-help">工作台没有它的登录信息，不能在上面初始化、自动部署或运维；它的归属记录照常保留。要经工作台部署，请用 IP、账号和密码添加一台服务器。</p>
      </div>
      <div class="asv-danger">
        <p><b>移除服务器</b><span>上面的实例改为未归属，下一次上报时会重新自动识别。</span></p>
        <button type="button" class="secondary-button im-danger" :disabled="busy" @click="remove"><Trash2 aria-hidden="true" />移除服务器</button>
      </div>
    </template>

    <template v-else>
      <div class="asv-init-head">
        <div class="asv-init-title">
          <h3>连接与初始化 <span class="asv-state" :class="`tone-${init.tone}`">{{ init.label }}</span></h3>
          <p class="asv-init-sub">{{ subtitle }}</p>
        </div>
        <button type="button" class="primary-button asv-init-go" :disabled="busy || running || !canLogin || ssh.connection_status === 'auth_failed'" @click="prepare">
          <LoaderCircle v-if="preparing" class="asv-spin" aria-hidden="true" /><RotateCcw v-else-if="again" aria-hidden="true" /><Play v-else aria-hidden="true" />{{ prepareLabel }}
        </button>
      </div>
      <p v-if="error" class="asv-init-error" role="alert">{{ error }}</p>

      <div class="asv-init-body">
        <div class="asv-init-main">
          <div v-if="!ssh.password_set" class="ws-callout tone-amber" role="group" aria-label="补充服务器登录密码">
            <strong><KeyRound aria-hidden="true" />补充服务器登录密码</strong>
            <p>这台服务器是旧方案添加的，工作台没有保存它的登录密码（不能从旧专用私钥还原）。补充后才能初始化、部署、卸载与运维；上面已有的智能体照常运行，不会停止或重装。</p>
            <div class="form-actions"><button type="button" class="primary-button" :disabled="busy || running" @click="emit('edit', 'credentials')">补充服务器登录密码</button></div>
          </div>

          <div v-else-if="verifying" ref="verifyEl" class="asv-verify" role="group" aria-label="核对主机公钥">
            <strong class="asv-verify-title"><ShieldAlert aria-hidden="true" />下一步：核对主机指纹</strong>
            <p>工作台读到的主机公钥（{{ ssh.host_key_algorithm }}）指纹：</p>
            <p><code class="asv-fingerprint" data-testid="ssh-fingerprint">{{ ssh.host_key_fingerprint }}</code></p>
            <p class="form-help">用云服务商的网页控制台（或其他你信任的方式）登录这台服务器运行下面的命令，SHA256: 开头的那一段必须和上面完全一致。确认之前密码不会发给服务器；不一致时不要确认。</p>
            <pre class="ai-arguments">{{ hostKeyCommand(ssh.host_key_algorithm) }}</pre>
            <label class="check-field"><input v-model="checked" type="checkbox" /> 我已在服务器上核对，指纹完全一致</label>
            <div class="form-actions"><button type="button" class="primary-button" :disabled="busy || !checked" @click="confirm">确认指纹</button></div>
          </div>

          <div v-if="connectionProblem" class="ws-callout" role="note">
            <strong><ShieldAlert aria-hidden="true" />{{ connection!.label }}</strong>
            <p>{{ CONNECTION_HELP[ssh.connection_status] }}</p>
          </div>
          <div v-if="detail.warning" class="ws-callout tone-amber" role="note"><strong><TriangleAlert aria-hidden="true" />需要处理</strong><p>{{ detail.warning }}</p></div>

          <SshJob v-if="job" :job="job" />
          <section v-else-if="server.initialization_status !== 'ready'" class="asv-plan" aria-label="初始化将完成">
            <header><h4><ListChecks aria-hidden="true" />初始化将完成</h4><span>可以重复执行</span></header>
            <ol>
              <li v-for="(item, i) in PLAN" :key="item"><span class="asv-plan-num" aria-hidden="true">{{ i + 1 }}</span><span>{{ item }}</span><span class="asv-plan-state">待执行</span></li>
            </ol>
          </section>
          <p v-else class="asv-ready"><CircleCheck aria-hidden="true" />服务器环境初始化完成：Docker、Compose 与部署目录可用。<template v-if="ssh.prepared_at"><span>{{ formatTime(ssh.prepared_at) }}</span></template></p>

          <div v-if="legacy" class="ws-callout tone-amber" role="group" aria-label="旧公钥退役">
            <strong><KeyRound aria-hidden="true" />{{ ssh.legacy_key_cleanup_state === 'manual_required' ? '旧专用公钥需要手动删除' : '旧专用公钥待退役' }}</strong>
            <p v-if="ssh.legacy_key_cleanup_state === 'manual_required'">工作台没能自动删除（服务器拒绝写 authorized_keys，或连不上）。请在服务器上从登录账号的 ~/.ssh/authorized_keys 里删除工作台以前装的那一行，然后再试一次。</p>
            <p v-else>以前的方案在这台服务器上装过工作台的专用公钥。现在全程用密码登录：用保存的密码登录后，只删除那一行公钥，其他用户与容器服务器的公钥保持不变。</p>
            <div class="form-actions"><button type="button" class="secondary-button" :disabled="busy || running || !canLogin" @click="cleanup">清理旧公钥</button></div>
            <p v-if="!canLogin" class="form-help">先补充密码、确认指纹，才能清理。</p>
          </div>
        </div>

        <div class="asv-init-side">
          <section class="asv-prereq" aria-label="登录前提">
            <header>
              <h4><KeyRound aria-hidden="true" />登录前提</h4>
              <span class="asv-state" :class="passed === checks.length ? 'tone-green' : 'tone-amber'">{{ passed }} / {{ checks.length }} 通过</span>
            </header>
            <ul>
              <li v-for="c in checks" :key="c.key" :data-tone="c.tone">
                <component :is="CHECK_ICON[c.tone]" class="asv-check-icon" aria-hidden="true" />
                <div class="asv-check-text">
                  <span class="asv-check-label">{{ c.label }}</span>
                  <span class="asv-check-value">{{ c.value }}</span>
                  <span v-if="c.note" class="asv-check-note">{{ c.note }}</span>
                </div>
                <button v-if="c.key === 'address'" type="button" class="asv-link" :disabled="busy || running || hasDeployments" @click="emit('edit', 'address')">更换地址</button>
                <button v-else-if="c.key === 'password' && ssh.password_set" type="button" class="asv-link" :disabled="busy || running" @click="emit('edit', 'credentials')">更新密码</button>
                <button v-else-if="c.key === 'fingerprint' && verifying" type="button" class="asv-link" @click="goVerify">去核对</button>
                <button v-else-if="c.key === 'connection'" type="button" class="asv-link" :disabled="busy || running || !canLogin" @click="test"><RefreshCw aria-hidden="true" />测试连接</button>
                <span v-else />
              </li>
            </ul>
          </section>
          <div class="asv-note">
            <template v-if="running">
              <strong><Info aria-hidden="true" />可以离开这个页面</strong>
              <p>任务在工作台后台执行，关掉页面不会中断；回来时从当前进度继续显示。</p>
            </template>
            <template v-else>
              <strong><ShieldCheck aria-hidden="true" />初始化只做环境准备</strong>
              <p>不创建智能体或容器、不安装任何公钥、不改 SSH 设置、不清理其他容器；可以重复执行，不影响已有的智能体。</p>
            </template>
          </div>
        </div>
      </div>

      <div class="asv-danger">
        <p><b>移除服务器</b><span>{{ removeHelp }}</span></p>
        <button type="button" class="secondary-button im-danger" :disabled="busy || running || hasDeployments || ssh.legacy_key_cleanup_state === 'pending'" @click="remove"><Trash2 aria-hidden="true" />移除服务器</button>
      </div>
    </template>
    <p v-if="error && (server.kind === 'auto' || !ssh)" class="asv-init-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="asv-toast" role="status">{{ notice }}</p>
  </section>
</template>
