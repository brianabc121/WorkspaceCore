<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { HOST_PATTERN, SSH_JOB_KIND, SSH_JOB_STATUS, SSH_STEP, SSH_STEP_STATUS, USERNAME_PATTERN, hostKeyCommand, sshError, sshKey, useAgentSsh } from './ssh'

// "通过 SSH 自动部署" on an instance card (T-0090): server → fingerprint check → one-time password → progress.
const props = defineProps<{ instance: Schema['AgentInstanceOut'] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const supported = computed(() => props.instance.network_mode === 'public')
const ssh = useAgentSsh(scope, () => props.instance.id, () => supported.value)
const { busy, error, notice, failure, act, refresh, header } = useAgentAction(scope, sshError)
const target = computed(() => ssh.data.value?.target ?? null)
const job = computed(() => ssh.data.value?.job ?? null)
const running = computed(() => job.value?.status === 'running')
const disabled = computed(() => props.instance.status === 'disabled')
const path = computed(() => ({ instance_id: props.instance.id }))
const form = reactive({ host: '', port: 22, username: '' })
const editing = ref(false), checked = ref(false), password = ref(''), removeFiles = ref(false), warning = ref(''), forceable = ref(false)
const showForm = computed(() => !target.value || editing.value)
const formValid = computed(() => HOST_PATTERN.test(form.host.trim()) && USERNAME_PATTERN.test(form.username.trim()) && Number.isInteger(Number(form.port)) && Number(form.port) >= 1 && Number(form.port) <= 65535)
// The key fields can only change while the workbench key is not on the server (revoke first).
const replaceable = computed(() => !!target.value && !target.value.client_key_installed && !running.value)

function put(state: Schema['AgentSshState']) { cache.setQueryData(sshKey(user, scope, props.instance.id), state) }
function settle() { if (failure.value instanceof ApiError && failure.value.code === 'WRITE_UNKNOWN') void refresh() }
// When a job ends the instance's status and credentials have changed too.
watch(() => job.value?.status, (now, before) => {
  if (before !== 'running' || !now || now === 'running') return
  notice.value = ''
  void cache.invalidateQueries({ queryKey: ['agents', user, scope, 'instances'] })
})

// A password typed for one server must never be sent to another: clear it whenever the target's identity changes
// (another address, port, account or host key — including a removed target) and when the server is being edited.
const identity = computed(() => {
  const t = target.value
  return t ? [t.address, t.port, t.username, t.host_key_fingerprint].join('\n') : ''
})
watch(identity, () => { password.value = '' })

function edit() {
  const t = target.value
  Object.assign(form, t ? { host: t.host, port: t.port, username: t.username } : { host: '', port: 22, username: '' })
  password.value = ''
  editing.value = true
}
async function probe() {
  if (!formValid.value) return
  const t = target.value
  const body: Schema['AgentSshTargetSet'] = { host: form.host.trim(), port: Number(form.port), username: form.username.trim(), ...(t ? { version: t.version } : {}) }
  const ok = await act(async () => put(dataOf(await auth.slowApi.PUT('/api/v1/agents/instances/{instance_id}/ssh', { params: { header, path: path.value }, body }))), '已读取主机公钥：请在服务器上核对指纹')
  if (ok) { editing.value = false; checked.value = false } else settle()
}
async function confirm() {
  const t = target.value
  if (!t || !checked.value) return
  await act(async () => put(dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/ssh/confirm', { params: { header, path: path.value }, body: { version: t.version, fingerprint: t.host_key_fingerprint } }))), '指纹已确认：之后只接受这把主机公钥')
  settle()
}
async function install() {
  const t = target.value
  if (!t) return
  // The password goes out with this one request and is cleared from the page right away.
  const secret = password.value
  password.value = ''
  if (!secret && !t.client_key_installed) return
  await act(async () => put(dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/ssh/install', { params: { header, path: path.value }, body: { version: t.version, ...(secret ? { password: secret } : {}) } }))), '已开始部署：下面显示每一步的进度')
  settle()
}
async function uninstall() {
  const t = target.value
  if (!t) return
  const files = removeFiles.value
  if (!window.confirm(files
    ? `卸载「${props.instance.name}」并删除服务器上的接入包目录？实例凭证、OpenClaw 的会话与记忆都会删除，不可恢复。`
    : `卸载「${props.instance.name}」？会停止并删除容器、删掉定时任务，接入包目录保留。`)) return
  await act(async () => put(dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/ssh/uninstall', { params: { header, path: path.value }, body: { version: t.version, remove_files: files } }))), '已开始卸载：下面显示每一步的进度')
  settle()
}
async function revoke(force = false) {
  const t = target.value
  if (!t) return
  if (!window.confirm(force
    ? '服务器连不上（或专用私钥已无法使用）。只删除工作台保存的私钥？服务器上的那一行公钥要你手动删除。'
    : '吊销专用密钥？工作台会登录服务器删掉这把公钥，再删除自己保存的私钥；之后重新部署要再输入一次密码。')) return
  warning.value = ''; forceable.value = false
  const ok = await act(async () => {
    const state = dataOf(await auth.slowApi.POST('/api/v1/agents/instances/{instance_id}/ssh/revoke', { params: { header, path: path.value }, body: { version: t.version, force } }))
    put(state); warning.value = state.warning ?? ''
  }, force ? '已删除工作台保存的专用私钥' : '专用密钥已吊销：服务器上的公钥与工作台保存的私钥都已删除')
  if (!ok) {
    forceable.value = !force && failure.value instanceof ApiError && ['AGENT_SSH_UNREACHABLE', 'AGENT_SSH_KEY_INVALID'].includes(failure.value.code)
    settle()
  }
}
async function remove() {
  const t = target.value
  if (!t || !window.confirm(`删除目标服务器 ${t.host}？之后可以重新填写。`)) return
  password.value = ''
  await act(async () => {
    await auth.api.DELETE('/api/v1/agents/instances/{instance_id}/ssh', { params: { header, path: path.value, query: { version: t.version } } })
    put({ target: null, job: null, warning: null })
  }, '目标服务器已删除')
  settle()
}
</script>

<template>
  <section class="agent-section" aria-label="SSH 自动部署">
    <h3>通过 SSH 自动部署</h3>
    <p v-if="!supported" class="form-help">只支持外网模式的实例。内网模式请生成接入包，按部署教程在服务器上手动部署。</p>
    <p v-else-if="ssh.isPending.value" role="status">正在加载…</p>
    <p v-else-if="ssh.error.value" role="alert">{{ sshError(ssh.error.value) }}</p>
    <template v-else>
      <p v-if="!target" class="form-help">
        填写一台 Linux 服务器的 SSH 地址与账号，工作台会自动安装 OpenClaw 并让它连回工作台。先只读取服务器的主机公钥（不登录），你核对指纹后，
        再输入一次登录密码开始部署：工作台换成自己的专用密钥登录，需要时用 sudo 安装 Docker（Ubuntu 22.04 / 24.04、Debian 12；已经装了 Docker 的其他发行版也可以），密码不会保存。
      </p>
      <dl v-if="target && !editing" class="agent-facts">
        <div><dt>服务器</dt><dd>{{ target.username }}@{{ target.host }}:{{ target.port }}<template v-if="target.address !== target.host">（{{ target.address }}）</template></dd></div>
        <div><dt>主机公钥</dt><dd>{{ target.host_key_confirmed ? `已确认${target.host_key_confirmed_at ? `（${formatTime(target.host_key_confirmed_at)}）` : ''}` : '待核对' }}</dd></div>
        <div><dt>专用密钥</dt><dd>{{ target.client_key_installed ? `已装在服务器上${target.client_key_fingerprint ? `（${target.client_key_fingerprint}）` : ''}` : '未安装' }}</dd></div>
        <div><dt>最近部署成功</dt><dd>{{ target.installed_at ? formatTime(target.installed_at) : '—' }}</dd></div>
        <div v-if="target.install_dir"><dt>安装目录</dt><dd><code>{{ target.install_dir }}</code></dd></div>
      </dl>

      <form v-if="showForm" class="im-group-form" aria-label="目标服务器" @submit.prevent="probe">
        <div class="im-group-add">
          <label class="form-field"><span>服务器 IP 或域名 *</span><input v-model.trim="form.host" maxlength="253" autocomplete="off" placeholder="203.0.113.10" :aria-invalid="!!form.host && !HOST_PATTERN.test(form.host)" /></label>
          <label class="form-field"><span>SSH 端口 *</span><input v-model.number="form.port" type="number" min="1" max="65535" /></label>
          <label class="form-field"><span>SSH 账号 *</span><input v-model.trim="form.username" maxlength="32" autocomplete="off" placeholder="root 或能用 sudo 的账号" :aria-invalid="!!form.username && !USERNAME_PATTERN.test(form.username)" /></label>
        </div>
        <div class="form-actions">
          <button type="submit" class="primary-button" :disabled="busy || !formValid">读取主机公钥</button>
          <button v-if="target" type="button" class="secondary-button" :disabled="busy" @click="editing = false">取消</button>
        </div>
      </form>

      <div v-else-if="target && !target.host_key_confirmed" class="im-group-form" role="group" aria-label="核对主机公钥">
        <p>工作台读到的主机公钥（{{ target.host_key_algorithm }}）指纹：</p>
        <p><code data-testid="ssh-fingerprint">{{ target.host_key_fingerprint }}</code></p>
        <p class="form-help">
          核对方法：用云服务商的网页控制台（或其他你信任的方式）登录这台服务器，运行下面的命令，输出里 SHA256: 开头的那一段必须和上面完全一致。
          不一致时不要确认——可能连到了别的机器。
        </p>
        <pre class="ai-arguments">{{ hostKeyCommand(target.host_key_algorithm) }}</pre>
        <label class="check-field"><input v-model="checked" type="checkbox" /> 我已在服务器上核对，指纹完全一致</label>
        <div class="form-actions">
          <button type="button" class="primary-button" :disabled="busy || !checked" @click="confirm">确认指纹</button>
          <button type="button" class="secondary-button" :disabled="busy" @click="edit">更换服务器</button>
          <button type="button" class="secondary-button" :disabled="busy" @click="remove">删除服务器</button>
        </div>
      </div>


      <section v-if="job" class="im-group-form" :aria-label="`${SSH_JOB_KIND[job.kind]}进度`">
        <p><strong>{{ SSH_JOB_KIND[job.kind] }}：{{ SSH_JOB_STATUS[job.status] }}</strong><span class="form-help"> · 开始于 {{ formatTime(job.started_at) }}<template v-if="job.finished_at"> · 结束于 {{ formatTime(job.finished_at) }}</template></span></p>
        <ol class="im-contact-list ssh-steps">
          <li v-for="step in job.steps" :key="step.key" :data-status="step.status">
            <span>{{ SSH_STEP[step.key] }}<template v-if="step.detail">：{{ step.detail }}</template></span>
            <span class="badge">{{ SSH_STEP_STATUS[step.status] }}</span>
          </li>
        </ol>
        <p v-if="job.status === 'succeeded' && job.kind === 'install'" role="status">部署完成，实例已连上工作台。</p>
        <p v-else-if="job.status === 'succeeded'" role="status">卸载完成。</p>
        <p v-else-if="job.status === 'failed'" role="alert">{{ job.error_message ?? '失败，详见日志' }}<template v-if="job.error_code">（{{ job.error_code }}）</template></p>
        <p v-else-if="job.status === 'interrupted'" role="alert">任务被中断（工作台重启过），可以重新开始。</p>
        <details v-if="job.log" :open="job.status === 'failed'"><summary>命令输出（密码、令牌、私钥已替换成 ***）</summary><pre class="ai-arguments">{{ job.log }}</pre></details>
      </section>

      <form v-if="target?.host_key_confirmed && !editing && !running" class="im-group-form" aria-label="部署" @submit.prevent="install">
        <p class="form-help">
          {{ target.client_key_installed
            ? '重新部署会重新生成接入包（旧凭证再保留 24 小时），更新服务器上的文件并重启 OpenClaw。专用密钥已经能直接使用 Docker 时不用填密码。'
            : '部署时：用密码登录一次，装上工作台的专用公钥，需要时用 sudo 安装 Docker；之后改用专用密钥上传接入包、安装 IM 插件、启动 OpenClaw、写定时任务，最后等实例连上工作台。第一次要下载镜像，可能需要十几分钟。' }}
        </p>
        <label class="form-field"><span>{{ target.client_key_installed ? '登录密码（可选）' : '登录密码 *' }}</span><input v-model="password" type="password" autocomplete="off" maxlength="1024" /></label>
        <p class="form-help">密码只随这一次请求发送，用于登录、安装公钥和 sudo，工作台不保存；提交后输入框会清空。</p>
        <p v-if="disabled" class="form-help">实例已停用，启用后才能部署。</p>
        <div class="form-actions">
          <button type="submit" class="primary-button" :disabled="busy || disabled || (!target.client_key_installed && !password)">{{ target.installed_at ? '重新部署' : '开始部署' }}</button>
          <button v-if="replaceable" type="button" class="secondary-button" :disabled="busy" @click="edit">更换服务器</button>
          <button v-if="replaceable" type="button" class="secondary-button" :disabled="busy" @click="remove">删除服务器</button>
        </div>
      </form>

      <div v-if="target?.client_key_installed && !running" class="im-group-form" role="group" aria-label="卸载与吊销">
        <label class="check-field"><input v-model="removeFiles" type="checkbox" /> 卸载时同时删除接入包目录（含实例凭证、OpenClaw 的会话与记忆，不可恢复）</label>
        <div class="form-actions">
          <button type="button" class="secondary-button im-danger" :disabled="busy" @click="uninstall">卸载</button>
          <button type="button" class="secondary-button im-danger" :disabled="busy" @click="revoke()">吊销专用密钥</button>
          <button v-if="forceable" type="button" class="secondary-button im-danger" :disabled="busy" @click="revoke(true)">强制吊销（只删除工作台保存的私钥）</button>
        </div>
        <p class="form-help">卸载不会卸载 Docker，也不会吊销专用密钥与实例凭证。吊销专用密钥后才能更换或删除目标服务器。</p>
      </div>

      <p v-if="warning" class="feedback" role="alert">{{ warning }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="notice" role="status">{{ notice }}</p>
    </template>
  </section>
</template>
