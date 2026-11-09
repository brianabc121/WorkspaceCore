<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { CircleCheck, CircleDashed, KeyRound, ShieldAlert, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { CONNECTION_HELP, CONNECTION_STATUS, endpoint, initStatus, putServerDetail, serversKey, type ServerDetail } from './servers'
import { hostKeyCommand, sshError } from './ssh'
import SshJob from './SshJob.vue'

// 服务器的连接与初始化（T-0181，改造规范第 4、5、7 节）：核对主机指纹 → 测试连接（用保存的密码登录，只读检查）→
// 初始化（检测 / 安装 Docker 与 Compose、准备部署目录，后台任务逐步显示）；旧服务器补充登录密码、旧专用公钥定向退役。
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
const PLAN = ['检测系统、架构、sudo 权限、磁盘与网络', '检测 Docker 与 Compose；缺失时安装平台锁定的版本，已有兼容版本直接复用', '准备平台管理的部署目录与基础依赖', '验证 Docker、部署目录与平台接入地址可用']

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
    <div class="asv-panel-head">
      <h3>{{ server.kind === 'ssh' ? '连接与初始化' : '自动识别的服务器' }}</h3>
      <span v-if="server.kind === 'ssh'" class="asv-state" :class="`tone-${init.tone}`">{{ init.label }}</span>
    </div>

    <template v-if="server.kind === 'auto' || !ssh">
      <p class="form-help">这台服务器是按实例上报的服务器指纹自动识别的（手动部署的实例）。工作台没有它的登录信息，不能在上面初始化、自动部署或运维；它的归属记录照常保留。要经工作台部署，请用 IP、账号和密码添加一台服务器。</p>
      <div class="form-actions"><button type="button" class="secondary-button im-danger" :disabled="busy" @click="remove">移除服务器</button></div>
    </template>

    <template v-else>
      <dl class="ws-facts">
        <dt>登录</dt><dd>{{ ssh.username }}@{{ endpoint(ssh.host, ssh.port) }}<template v-if="ssh.address !== ssh.host">（{{ ssh.address }}）</template></dd>
        <dt>登录方式</dt><dd>密码 · {{ ssh.password_set ? `已加密保存（第 ${ssh.credential_revision} 版）` : '还没有保存' }}</dd>
        <dt>主机身份</dt><dd>{{ ssh.host_key_confirmed ? `指纹已确认${ssh.host_key_confirmed_at ? `（${formatTime(ssh.host_key_confirmed_at)}）` : ''}` : '指纹待核对' }}</dd>
        <dt>连接状态</dt><dd><span class="asv-state" :class="`tone-${connection!.tone}`">{{ connection!.label }}</span><span class="form-help"> · 最近检测 {{ ssh.last_connection_at ? formatTime(ssh.last_connection_at) : '—' }}</span></dd>
        <dt>环境</dt><dd>{{ init.label }}<template v-if="ssh.prepared_at"> · 最近一次初始化成功 {{ formatTime(ssh.prepared_at) }}</template></dd>
      </dl>

      <div v-if="!ssh.password_set" class="ws-callout tone-amber" role="group" aria-label="补充服务器登录密码">
        <strong><KeyRound aria-hidden="true" />补充服务器登录密码</strong>
        <p>这台服务器是旧方案添加的，工作台没有保存它的登录密码（不能从旧专用私钥还原）。补充后才能初始化、部署、卸载与运维；上面已有的智能体照常运行，不会停止或重装。</p>
        <div class="form-actions"><button type="button" class="primary-button" :disabled="busy || running" @click="emit('edit', 'credentials')">补充服务器登录密码</button></div>
      </div>

      <div v-else-if="!ssh.host_key_confirmed" class="im-group-form asv-verify" role="group" aria-label="核对主机公钥">
        <p>工作台读到的主机公钥（{{ ssh.host_key_algorithm }}）指纹：</p>
        <p><code class="asv-fingerprint" data-testid="ssh-fingerprint">{{ ssh.host_key_fingerprint }}</code></p>
        <p class="form-help">用云服务商的网页控制台（或其他你信任的方式）登录这台服务器运行下面的命令，SHA256: 开头的那一段必须和上面完全一致。确认之前密码不会发给服务器；不一致时不要确认。</p>
        <pre class="ai-arguments">{{ hostKeyCommand(ssh.host_key_algorithm) }}</pre>
        <label class="check-field"><input v-model="checked" type="checkbox" /> 我已在服务器上核对，指纹完全一致</label>
        <div class="form-actions"><button type="button" class="primary-button" :disabled="busy || !checked" @click="confirm">确认指纹</button></div>
      </div>

      <div v-if="ssh.password_set && ['auth_failed', 'unreachable', 'host_key_changed', 'credential_unreadable'].includes(ssh.connection_status)" class="ws-callout" role="note">
        <strong><ShieldAlert aria-hidden="true" />{{ connection!.label }}</strong>
        <p>{{ CONNECTION_HELP[ssh.connection_status] }}</p>
      </div>
      <div v-if="detail.warning" class="ws-callout tone-amber" role="note"><strong><TriangleAlert aria-hidden="true" />需要处理</strong><p>{{ detail.warning }}</p></div>

      <SshJob v-if="job" :job="job" />
      <section v-if="!job && server.initialization_status !== 'ready'" class="asv-checklist" aria-label="初始化将完成">
        <h4>初始化将完成</h4>
        <ul><li v-for="item in PLAN" :key="item"><CircleDashed aria-hidden="true" /><span>{{ item }}</span><span class="asv-pending">待执行</span></li></ul>
        <p class="form-help">初始化不会创建智能体或容器、不安装任何公钥、不改 SSH 设置、不清理其他容器；重复执行不影响已有的智能体。</p>
      </section>
      <p v-else-if="!job && server.initialization_status === 'ready'" class="asv-ready"><CircleCheck aria-hidden="true" />服务器环境初始化完成：Docker、Compose 与部署目录可用。</p>

      <div v-if="legacy" class="ws-callout tone-amber" role="group" aria-label="旧公钥退役">
        <strong><KeyRound aria-hidden="true" />{{ ssh.legacy_key_cleanup_state === 'manual_required' ? '旧专用公钥需要手动删除' : '旧专用公钥待退役' }}</strong>
        <p v-if="ssh.legacy_key_cleanup_state === 'manual_required'">工作台没能自动删除（服务器拒绝写 authorized_keys，或连不上）。请在服务器上从登录账号的 ~/.ssh/authorized_keys 里删除工作台以前装的那一行，然后再试一次。</p>
        <p v-else>以前的方案在这台服务器上装过工作台的专用公钥。现在全程用密码登录：用保存的密码登录后，只删除那一行公钥，其他用户与容器服务器的公钥保持不变。</p>
        <div class="form-actions"><button type="button" class="secondary-button" :disabled="busy || running || !canLogin" @click="cleanup">清理旧公钥</button></div>
        <p v-if="!canLogin" class="form-help">先补充密码、确认指纹，才能清理。</p>
      </div>

      <div class="form-actions asv-init-actions">
        <button type="button" class="secondary-button" :disabled="busy || running || !canLogin" @click="test">测试连接</button>
        <button type="button" class="primary-button" :disabled="busy || running || !canLogin || ssh.connection_status === 'auth_failed'" @click="prepare">{{ server.initialization_status === 'ready' ? '重新初始化' : '初始化服务器' }}</button>
        <button v-if="ssh.password_set" type="button" class="secondary-button" :disabled="busy || running" @click="emit('edit', 'credentials')">更新登录密码</button>
        <button type="button" class="secondary-button" :disabled="busy || running || hasDeployments" @click="emit('edit', 'address')">更换地址</button>
        <button type="button" class="secondary-button im-danger" :disabled="busy || running || hasDeployments || ssh.legacy_key_cleanup_state === 'pending'" @click="remove">移除服务器</button>
      </div>
      <p v-if="running" class="form-help">这台服务器正在执行任务，结束后再操作。</p>
      <p v-else-if="ssh.legacy_key_cleanup_state === 'pending'" class="form-help">服务器上还留有旧方案的专用公钥：先完成上面的“旧公钥退役”，再移除服务器。</p>
      <p v-else-if="hasDeployments" class="form-help">这台服务器上有 {{ server.managed_deployments }} 条平台部署记录：要更换地址或移除服务器，先在 OpenClaw 智能体页卸载它们。</p>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>
