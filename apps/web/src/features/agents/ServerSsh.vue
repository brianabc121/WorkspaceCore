<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { HOST_PATTERN, USERNAME_PATTERN, hostKeyCommand, sshError } from './ssh'
import SshJob from './SshJob.vue'

// 服务器详情里的“SSH 与专用密钥”（T-0147）：核对主机公钥 → 用一次密码准备服务器（每台服务器一把专用密钥，D7）
// → 之后部署实例不用密码；更换地址、吊销专用密钥、移除服务器。密码只随一次请求发出，不保存。
const props = defineProps<{ detail: Schema['AgentServerDetail'] }>()
const emit = defineEmits<{ removed: [] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const { busy, error, notice, failure, act, refresh, header } = useAgentAction(scope, sshError)
const server = computed(() => props.detail.server)
const ssh = computed(() => server.value.ssh)
const job = computed(() => props.detail.job)
const running = computed(() => job.value?.status === 'running')
const deployed = computed(() => props.detail.agents.filter(a => a.deployed).length)
const path = computed(() => ({ server_id: server.value.id }))
const form = reactive({ host: '', port: 22, username: '' })
const editing = ref(false), checked = ref(false), password = ref(''), warning = ref(''), forceable = ref(false)
const formValid = computed(() => HOST_PATTERN.test(form.host.trim()) && USERNAME_PATTERN.test(form.username.trim()) && Number.isInteger(Number(form.port)) && Number(form.port) >= 1 && Number(form.port) <= 65535)

// 密码只用于这一台服务器：地址、端口、账号或主机公钥变了就清掉
const identity = computed(() => ssh.value ? [ssh.value.address, ssh.value.port, ssh.value.username, ssh.value.host_key_fingerprint].join('\n') : '')
watch(identity, () => { password.value = ''; checked.value = false })
watch(() => props.detail.warning, now => { if (now) warning.value = now })

function settle() { if (failure.value instanceof ApiError && failure.value.code === 'WRITE_UNKNOWN') void refresh() }
function edit() {
  const s = ssh.value
  Object.assign(form, s ? { host: s.host, port: s.port, username: s.username } : { host: '', port: 22, username: '' })
  password.value = ''
  editing.value = true
}
async function changeAddress() {
  if (!formValid.value) return
  const ok = await act(async () => dataOf(await auth.slowApi.PUT('/api/v1/agents/servers/{server_id}/address', { params: { header, path: path.value }, body: { host: form.host.trim(), port: Number(form.port), username: form.username.trim(), version: server.value.version } })), '已重新读取主机公钥：请在服务器上核对指纹')
  if (ok) editing.value = false; else settle()
}
async function confirm() {
  const s = ssh.value
  if (!s || !checked.value) return
  await act(async () => dataOf(await auth.api.POST('/api/v1/agents/servers/{server_id}/confirm', { params: { header, path: path.value }, body: { version: server.value.version, fingerprint: s.host_key_fingerprint } })), '指纹已确认：之后只接受这把主机公钥')
  settle()
}
async function prepare() {
  // 密码只随这一次请求发出，发出后立即从页面上清掉
  const secret = password.value
  password.value = ''
  if (!secret) return
  await act(async () => dataOf(await auth.api.POST('/api/v1/agents/servers/{server_id}/prepare', { params: { header, path: path.value }, body: { version: server.value.version, password: secret } })), '已开始准备服务器：下面显示每一步的进度')
  settle()
}
async function revoke(force = false) {
  if (!window.confirm(force
    ? '服务器连不上（或专用私钥已无法使用）。只删除工作台保存的私钥？服务器上的那一行公钥要你手动删除。'
    : '吊销这台服务器的专用密钥？工作台会登录服务器删掉这把公钥，再删除自己保存的私钥；之后要重新准备服务器（再输一次密码）才能部署或运维。')) return
  warning.value = ''; forceable.value = false
  const ok = await act(async () => {
    const state = dataOf(await auth.slowApi.POST('/api/v1/agents/servers/{server_id}/revoke', { params: { header, path: path.value }, body: { version: server.value.version, force } }))
    warning.value = state.warning ?? ''
  }, force ? '已删除工作台保存的专用私钥' : '专用密钥已吊销：服务器上的公钥与工作台保存的私钥都已删除')
  if (!ok) {
    forceable.value = !force && failure.value instanceof ApiError && ['AGENT_SSH_UNREACHABLE', 'AGENT_SSH_KEY_INVALID'].includes(failure.value.code)
    settle()
  }
}
async function remove() {
  const auto = server.value.kind === 'auto'
  if (!window.confirm(auto
    ? `移除「${server.value.name}」？上面的实例改为未归属，下一次上报时会重新自动识别。`
    : `移除服务器「${server.value.name}」？工作台不再保存它的地址与主机公钥。`)) return
  password.value = ''
  // 删除成功就回到列表，再刷新：刷新时这台服务器的详情是 404，详情页会卸载本组件，之后再发的事件会被丢掉
  const ok = await act(async () => {
    await auth.api.DELETE('/api/v1/agents/servers/{server_id}', { params: { header, path: path.value, query: { version: server.value.version } } })
    emit('removed')
  }, '服务器已移除')
  if (!ok) settle()
}
</script>

<template>
  <section class="panel" aria-label="SSH 与专用密钥">
    <h3>{{ server.kind === 'ssh' ? 'SSH 与专用密钥' : '自动识别的服务器' }}</h3>
    <template v-if="server.kind === 'auto' || !ssh">
      <p class="form-help">这台服务器是按实例上报的服务器指纹自动识别的（手动部署的实例）。工作台不能登录它，也不能在上面部署或运维；要经工作台部署，添加一台 SSH 服务器。</p>
      <div class="form-actions"><button type="button" class="secondary-button im-danger" :disabled="busy" @click="remove">移除服务器</button></div>
    </template>
    <template v-else>
      <dl v-if="!editing" class="agent-facts">
        <div><dt>地址</dt><dd>{{ ssh.username }}@{{ ssh.host }}:{{ ssh.port }}<template v-if="ssh.address !== ssh.host">（{{ ssh.address }}）</template></dd></div>
        <div><dt>主机公钥</dt><dd>{{ ssh.host_key_confirmed ? `已确认${ssh.host_key_confirmed_at ? `（${formatTime(ssh.host_key_confirmed_at)}）` : ''}` : '待核对' }}</dd></div>
        <div><dt>专用密钥</dt><dd>{{ ssh.client_key_installed ? `已装在服务器上${ssh.client_key_fingerprint ? `（${ssh.client_key_fingerprint}）` : ''}` : '未安装' }}</dd></div>
        <div><dt>准备好</dt><dd>{{ ssh.prepared_at ? formatTime(ssh.prepared_at) : '—' }}</dd></div>
      </dl>

      <form v-if="editing" class="im-group-form" aria-label="更换地址" @submit.prevent="changeAddress">
        <div class="im-group-add">
          <label class="form-field"><span>服务器 IP 或域名 *</span><input v-model.trim="form.host" maxlength="253" autocomplete="off" :aria-invalid="!!form.host && !HOST_PATTERN.test(form.host)" /></label>
          <label class="form-field"><span>SSH 端口 *</span><input v-model.number="form.port" type="number" min="1" max="65535" /></label>
          <label class="form-field"><span>SSH 账号 *</span><input v-model.trim="form.username" maxlength="32" autocomplete="off" :aria-invalid="!!form.username && !USERNAME_PATTERN.test(form.username)" /></label>
        </div>
        <p class="form-help">更换后要重新核对主机公钥。</p>
        <div class="form-actions">
          <button type="submit" class="primary-button" :disabled="busy || !formValid">读取主机公钥</button>
          <button type="button" class="secondary-button" :disabled="busy" @click="editing = false">取消</button>
        </div>
      </form>

      <div v-else-if="!ssh.host_key_confirmed" class="im-group-form" role="group" aria-label="核对主机公钥">
        <p>工作台读到的主机公钥（{{ ssh.host_key_algorithm }}）指纹：</p>
        <p><code data-testid="ssh-fingerprint">{{ ssh.host_key_fingerprint }}</code></p>
        <p class="form-help">
          核对方法：用云服务商的网页控制台（或其他你信任的方式）登录这台服务器，运行下面的命令，输出里 SHA256: 开头的那一段必须和上面完全一致。
          不一致时不要确认——可能连到了别的机器。
        </p>
        <pre class="ai-arguments">{{ hostKeyCommand(ssh.host_key_algorithm) }}</pre>
        <label class="check-field"><input v-model="checked" type="checkbox" /> 我已在服务器上核对，指纹完全一致</label>
        <div class="form-actions">
          <button type="button" class="primary-button" :disabled="busy || !checked" @click="confirm">确认指纹</button>
          <button type="button" class="secondary-button" :disabled="busy" @click="edit">更换地址</button>
          <button type="button" class="secondary-button im-danger" :disabled="busy" @click="remove">移除服务器</button>
        </div>
      </div>

      <SshJob v-if="job" :job="job" />

      <form v-if="!editing && ssh.host_key_confirmed && !ssh.client_key_installed && !running" class="im-group-form" aria-label="准备服务器" @submit.prevent="prepare">
        <p class="form-help">
          准备时用密码登录一次，装上工作台给这台服务器的专用公钥，需要时用 sudo 安装 Docker（Ubuntu 22.04 / 24.04、Debian 12；已经装了 Docker 的其他发行版也可以），
          再改用专用密钥登录确认。之后在这台服务器上部署多少个实例都不用再输密码。
        </p>
        <label class="form-field"><span>登录密码 *</span><input v-model="password" type="password" autocomplete="off" maxlength="1024" /></label>
        <p class="form-help">密码只随这一次请求发送，用于登录、安装公钥和 sudo，工作台不保存；提交后输入框会清空。</p>
        <div class="form-actions">
          <button type="submit" class="primary-button" :disabled="busy || !password">准备服务器</button>
          <button type="button" class="secondary-button" :disabled="busy" @click="edit">更换地址</button>
          <button type="button" class="secondary-button im-danger" :disabled="busy || deployed > 0" @click="remove">移除服务器</button>
        </div>
      </form>

      <div v-if="!editing && ssh.client_key_installed && !running" class="im-group-form" role="group" aria-label="吊销与移除">
        <p class="form-help">
          吊销专用密钥后，这台服务器上的实例照常运行，但不能再经工作台部署、运维或卸载，要重新准备服务器。
          <template v-if="deployed">上面还有 {{ deployed }} 个经工作台部署的实例：要移除服务器，先在实例卡片里卸载它们，再吊销专用密钥。</template>
        </p>
        <div class="form-actions">
          <button type="button" class="secondary-button im-danger" :disabled="busy" @click="revoke()">吊销专用密钥</button>
          <button v-if="forceable" type="button" class="secondary-button im-danger" :disabled="busy" @click="revoke(true)">强制吊销（只删除工作台保存的私钥）</button>
        </div>
      </div>

      <p v-if="warning" class="feedback" role="alert">{{ warning }}</p>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>
