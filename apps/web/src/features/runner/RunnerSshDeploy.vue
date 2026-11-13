<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { HOST_PATTERN, SSH_JOB_KIND, SSH_JOB_STATUS, SSH_STEP, SSH_STEP_STATUS, USERNAME_PATTERN, hostKeyCommand, sshError } from '../agents/ssh'
import { useRunnerAction } from './api'

// 页面上通过 SSH 部署容器服务器（T-0136，RUNNER_PLAN 第 9 节，owner）：与 OpenClaw 实例的 SSH 自动部署同一套流程——
// 填写服务器 → 只读取主机公钥、在服务器上核对指纹 → 输入一次登录密码部署（工作台换成自己的专用密钥，密码不保存）。
// 部署：上传接入包，以 root 运行 install.sh（Docker、gVisor、runner-agent），等 runner-agent 连上工作台。
// 不是 root 账号时 install.sh 要用 sudo，所以每次部署都要填密码。
const props = defineProps<{ hostId: string }>()
type State = Schema['AgentSshState']
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const key = ['runner', user, scope, 'ssh', props.hostId] as const
const ssh = useQuery({
  queryKey: key,
  enabled: computed(() => user === auth.user?.id && scope === auth.workspaceId && auth.isOwner),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/runner/hosts/{host_id}/ssh', { params: { header: { 'X-Workspace-Id': scope }, path: { host_id: props.hostId } }, signal })),
  refetchInterval: query => query.state.data?.job?.status === 'running' ? 2000 : false,
})
const { busy, error, notice, act, refresh, header } = useRunnerAction(scope)
const target = computed(() => ssh.data.value?.target ?? null)
const job = computed(() => ssh.data.value?.job ?? null)
const running = computed(() => job.value?.status === 'running')
const form = reactive({ host: '', port: 22, username: 'root' })
const editing = ref(false), checked = ref(false), password = ref(''), removeFiles = ref(false), warning = ref(''), forceable = ref(false)
const showForm = computed(() => !target.value || editing.value)
const formValid = computed(() => HOST_PATTERN.test(form.host.trim()) && USERNAME_PATTERN.test(form.username.trim()) && Number.isInteger(Number(form.port)) && Number(form.port) >= 1 && Number(form.port) <= 65535)
const replaceable = computed(() => !!target.value && !target.value.client_key_installed && !running.value)

function put(state: State) { cache.setQueryData(key, state) }
// 任务结束后容器服务器的状态（在线、版本）也变了
watch(() => job.value?.status, (now, before) => { if (before === 'running' && now && now !== 'running') { notice.value = ''; void refresh() } })
// 换了服务器（地址、端口、账号、主机公钥）就清空输入过的密码：绝不把一台服务器的密码发给另一台
watch(() => target.value ? [target.value.address, target.value.port, target.value.username, target.value.host_key_fingerprint].join('\n') : '', () => { password.value = '' })

// SSH 相关的错误码用实例部署那边的说明（同一套接口）
function explain(cause: unknown) { if (cause instanceof ApiError) error.value = sshError(cause) }

function edit() {
  const t = target.value
  Object.assign(form, t ? { host: t.host, port: t.port, username: t.username } : { host: '', port: 22, username: 'root' })
  password.value = ''
  editing.value = true
}
async function call(effect: () => Promise<State | void>, success: string) {
  let failure: unknown = null
  warning.value = ''
  const ok = await act(async () => {
    try { const state = await effect(); if (state) put(state) } catch (cause) { failure = cause; throw cause }
  }, success)
  if (!ok) explain(failure)
  return ok
}
async function probe() {
  if (!formValid.value) return
  const t = target.value
  const body: Schema['AgentSshTargetSet'] = { host: form.host.trim(), port: Number(form.port), username: form.username.trim(), ...(t ? { version: t.version } : {}) }
  checked.value = false
  if (await call(async () => dataOf(await auth.slowApi.PUT('/api/v1/runner/hosts/{host_id}/ssh', { params: { header, path: { host_id: props.hostId } }, body })), '已读取主机公钥：请在服务器上核对指纹')) editing.value = false
}
async function confirm() {
  const t = target.value
  if (!t || !checked.value) return
  await call(async () => dataOf(await auth.api.POST('/api/v1/runner/hosts/{host_id}/ssh/confirm', { params: { header, path: { host_id: props.hostId } }, body: { version: t.version, fingerprint: t.host_key_fingerprint } })), '指纹已确认：之后只接受这把主机公钥')
}
async function install() {
  const t = target.value
  if (!t) return
  // 密码只随这一次请求发送，发送前就从页面上清掉
  const secret = password.value
  password.value = ''
  if (!secret && !t.client_key_installed) return
  await call(async () => dataOf(await auth.api.POST('/api/v1/runner/hosts/{host_id}/ssh/install', { params: { header, path: { host_id: props.hostId } }, body: { version: t.version, ...(secret ? { password: secret } : {}) } })), '已开始部署：下面显示每一步的进度')
}
async function uninstall() {
  const t = target.value
  if (!t) return
  const files = removeFiles.value, secret = password.value
  if (!window.confirm(files ? '停止服务器上的 runner-agent 并删除接入包目录？Docker 与 gVisor 保留。' : '停止服务器上的 runner-agent？接入包目录保留，之后可以重新部署。')) return
  password.value = ''
  await call(async () => dataOf(await auth.api.POST('/api/v1/runner/hosts/{host_id}/ssh/uninstall', { params: { header, path: { host_id: props.hostId } }, body: { version: t.version, remove_files: files, ...(secret ? { password: secret } : {}) } })), '已开始卸载：下面显示每一步的进度')
}
async function revoke(force = false) {
  const t = target.value
  if (!t) return
  if (!window.confirm(force
    ? '服务器连不上（或专用私钥已无法使用）。只删除工作台保存的私钥？服务器上的那一行公钥要你手动删除。'
    : '吊销专用密钥？工作台会登录服务器删掉这把公钥，再删除自己保存的私钥；之后重新部署要再输入一次密码。')) return
  forceable.value = false
  warning.value = ''
  let failure: unknown = null
  const ok = await act(async () => {
    try {
      const state = dataOf(await auth.slowApi.POST('/api/v1/runner/hosts/{host_id}/ssh/revoke', { params: { header, path: { host_id: props.hostId } }, body: { version: t.version, force } }))
      put(state); warning.value = state.warning ?? ''
    } catch (cause) { failure = cause; throw cause }
  }, force ? '已删除工作台保存的专用私钥' : '专用密钥已吊销')
  if (!ok) { explain(failure); forceable.value = !force && failure instanceof ApiError && ['AGENT_SSH_UNREACHABLE', 'AGENT_SSH_KEY_INVALID'].includes(failure.code) }
}
async function remove() {
  const t = target.value
  if (!t || !window.confirm(`删除 SSH 地址 ${t.host}？之后可以重新填写。`)) return
  password.value = ''
  await call(async () => {
    await auth.api.DELETE('/api/v1/runner/hosts/{host_id}/ssh', { params: { header, path: { host_id: props.hostId }, query: { version: t.version } } })
    put({ target: null, job: null, warning: null })
  }, '已删除 SSH 地址')
}
</script>

<template>
  <section class="panel runner-card" aria-label="通过 SSH 部署">
    <h2>通过 SSH 部署（推荐）</h2>
    <p v-if="!auth.isOwner" class="form-help">只有 owner 能通过 SSH 部署容器服务器（要输入服务器的登录密码）。</p>
    <p v-else-if="ssh.isPending.value" role="status">正在加载…</p>
    <p v-else-if="ssh.error.value" role="alert">{{ sshError(ssh.error.value) }}</p>
    <template v-else>
      <p v-if="!target" class="form-help">
        填写容器服务器的 SSH 地址与账号（root，或能用 sudo 的账号），工作台会装好 Docker、gVisor 与 runner-agent 并让它连回工作台，不用在终端里做任何事。
        先只读取服务器的主机公钥（不登录），你核对指纹后，再输入一次登录密码开始部署：工作台换成自己的专用密钥，密码不保存。
      </p>
      <dl v-if="target && !editing" class="agent-facts">
        <div><dt>服务器</dt><dd>{{ target.username }}@{{ target.host }}:{{ target.port }}<template v-if="target.address !== target.host">（{{ target.address }}）</template></dd></div>
        <div><dt>主机公钥</dt><dd>{{ target.host_key_confirmed ? '已确认' : '待核对' }}</dd></div>
        <div><dt>专用密钥</dt><dd>{{ target.client_key_installed ? `已装在服务器上${target.client_key_fingerprint ? `（${target.client_key_fingerprint}）` : ''}` : '未安装' }}</dd></div>
        <div><dt>最近部署成功</dt><dd>{{ target.installed_at ? formatTime(target.installed_at) : '—' }}</dd></div>
        <div v-if="target.install_dir"><dt>安装目录</dt><dd><code>{{ target.install_dir }}</code></dd></div>
      </dl>

      <form v-if="showForm" class="runner-filters" aria-label="容器服务器的 SSH 地址" @submit.prevent="probe">
        <label class="form-field"><span>服务器 IP 或域名 *</span><input v-model.trim="form.host" maxlength="253" autocomplete="off" placeholder="203.0.113.20" /></label>
        <label class="form-field"><span>SSH 端口 *</span><input v-model.number="form.port" type="number" min="1" max="65535" /></label>
        <label class="form-field"><span>SSH 账号 *</span><input v-model.trim="form.username" maxlength="32" autocomplete="off" /></label>
        <div class="form-actions">
          <button type="submit" class="primary-button" :disabled="busy || !formValid">读取主机公钥</button>
          <button v-if="target" type="button" class="secondary-button" :disabled="busy" @click="editing = false">取消</button>
        </div>
      </form>

      <div v-else-if="target && !target.host_key_confirmed" role="group" aria-label="核对主机公钥" class="runner-form">
        <p>工作台读到的主机公钥（{{ target.host_key_algorithm }}）指纹：</p>
        <p><code data-testid="runner-ssh-fingerprint">{{ target.host_key_fingerprint }}</code></p>
        <p class="form-help">用云服务商的网页控制台（或其他你信任的方式）登录这台服务器，运行下面的命令，SHA256: 开头的那一段必须完全一致；不一致时不要确认。</p>
        <pre class="runner-output">{{ hostKeyCommand(target.host_key_algorithm) }}</pre>
        <label class="check-field"><input v-model="checked" type="checkbox" /> 我已在服务器上核对，指纹完全一致</label>
        <div class="form-actions">
          <button type="button" class="primary-button" :disabled="busy || !checked" @click="confirm">确认指纹</button>
          <button type="button" class="secondary-button" :disabled="busy" @click="edit">更换服务器</button>
          <button type="button" class="secondary-button" :disabled="busy" @click="remove">删除</button>
        </div>
      </div>

      <section v-if="job" :aria-label="`${SSH_JOB_KIND[job.kind]}进度`" class="runner-form">
        <p><strong>{{ SSH_JOB_KIND[job.kind] }}：{{ SSH_JOB_STATUS[job.status] }}</strong><span class="form-help"> · 开始于 {{ formatTime(job.started_at) }}</span></p>
        <ol class="im-contact-list ssh-steps">
          <li v-for="step in job.steps" :key="step.key" :data-status="step.status">
            <span>{{ SSH_STEP[step.key] }}<template v-if="step.detail">：{{ step.detail }}</template></span>
            <span class="badge">{{ SSH_STEP_STATUS[step.status] }}</span>
          </li>
        </ol>
        <p v-if="job.status === 'succeeded' && job.kind === 'install'" role="status">部署完成，容器服务器已连上工作台。</p>
        <p v-else-if="job.status === 'succeeded'" role="status">卸载完成。</p>
        <p v-else-if="job.status === 'failed'" role="alert">{{ job.error_message ?? '失败，详见日志' }}<template v-if="job.error_code">（{{ job.error_code }}）</template></p>
        <p v-else-if="job.status === 'interrupted'" role="alert">任务被中断（工作台重启过），可以重新开始。</p>
        <details v-if="job.log" :open="job.status === 'failed'"><summary>命令输出（密码、凭证、私钥已替换成 ***）</summary><pre class="runner-output">{{ job.log }}</pre></details>
      </section>

      <form v-if="target?.host_key_confirmed && !editing && !running" class="runner-form" aria-label="部署容器服务器" @submit.prevent="install">
        <p class="form-help">部署会签发新的容器服务器凭证（旧的立即失效）、上传接入包，以 root 运行安装脚本：装 Docker 29.9.1 与 gVisor release-20261019.0（核对 sha512），启动 runner-agent。第一次要下载，可能需要几分钟。已有的环境不受影响。</p>
        <label class="form-field"><span>{{ target.client_key_installed ? '登录密码（root 账号可以不填；其他账号要用 sudo，必须填）' : '登录密码 *' }}</span><input v-model="password" type="password" autocomplete="off" maxlength="1024" /></label>
        <p class="form-help">密码只随这一次请求发送，用于登录、安装公钥和 sudo，工作台不保存；提交后输入框会清空。</p>
        <div class="form-actions">
          <button type="submit" class="primary-button" :disabled="busy || (!target.client_key_installed && !password)">{{ target.installed_at ? '重新部署' : '开始部署' }}</button>
          <button v-if="replaceable" type="button" class="secondary-button" :disabled="busy" @click="edit">更换服务器</button>
          <button v-if="replaceable" type="button" class="secondary-button" :disabled="busy" @click="remove">删除</button>
        </div>
      </form>

      <div v-if="target?.client_key_installed && !running" role="group" aria-label="卸载与吊销" class="runner-form">
        <label class="check-field"><input v-model="removeFiles" type="checkbox" /> 卸载时同时删除接入包目录（含容器服务器凭证）</label>
        <div class="form-actions">
          <button type="button" class="secondary-button im-danger" :disabled="busy" @click="uninstall">卸载 runner-agent</button>
          <button type="button" class="secondary-button im-danger" :disabled="busy" @click="revoke()">吊销专用密钥</button>
          <button v-if="forceable" type="button" class="secondary-button im-danger" :disabled="busy" @click="revoke(true)">强制吊销（只删除工作台保存的私钥）</button>
        </div>
        <p class="form-help">卸载要先在“环境”里删掉所有环境；不是 root 账号时卸载也要填上面的登录密码。Docker 与 gVisor 保留。</p>
      </div>

      <p v-if="warning" class="feedback" role="alert">{{ warning }}</p>
      <p v-if="error" role="alert" class="feedback">{{ error }}</p>
      <p v-if="notice" role="status" class="feedback">{{ notice }}</p>
    </template>
  </section>
</template>
