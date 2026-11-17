<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { CircleAlert, CircleCheck, CircleDashed, CircleX, Download, Info, KeyRound, ListChecks, Pencil, ShieldAlert, ShieldCheck, Trash2 } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import JobProgress from '../../components/JobProgress.vue'
import { HOST_PATTERN, SSH_JOB_KIND, SSH_STEP, USERNAME_PATTERN, hostKeyCommand, sshError } from '../agents/ssh'
import { HOST_STATUS, downloadRunnerBundle, useRunnerAction, type RunnerHost } from './api'
import { INGRESS } from './hosts'
import { ago } from './containerMetrics'

// 连接与部署（T-0277：按智能体服务器的“连接与初始化”排版；流程是 T-0136 的页面上 SSH 部署，RUNNER_PLAN 第 9 节，owner）：
// 填写服务器 → 只读取主机公钥、在服务器上核对指纹 → 输入一次登录密码部署（工作台换成自己的专用密钥，密码不保存）。
// 部署：上传接入包，以 root 运行 install.sh（Docker、gVisor、runner-agent），等 runner-agent 连上工作台。
// 不是 root 账号时 install.sh 要用 sudo，所以每次部署都要填密码。
// 启用 gVisor 时安装脚本尽量不重启 Docker；非重启不可、而服务器上有 MCP 管理器在运行时任务以
// gvisor_needs_docker_restart 失败（T-0234）：这时多一个“允许重启 Docker”的勾选项，勾上再部署才会重启。
// 左边是要做的事与最近一次任务（逐步进度用共用组件 JobProgress），右边是“部署前提”清单；底部是手动部署与移除容器服务器。
const props = defineProps<{ host: RunnerHost }>()
const emit = defineEmits<{ removed: [] }>()
type State = Schema['AgentSshState']
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const owner = computed(() => auth.isOwner)
const ssh = computed(() => props.host.backend !== 'kubernetes')
const key = ['runner', user, scope, 'ssh', props.host.id] as const
const state = useQuery({
  queryKey: key,
  enabled: computed(() => user === auth.user?.id && scope === auth.workspaceId && owner.value && ssh.value),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/runner/hosts/{host_id}/ssh', { params: { header: { 'X-Workspace-Id': scope }, path: { host_id: props.host.id } }, signal })),
  refetchInterval: query => query.state.data?.job?.status === 'running' ? 2000 : false,
})
const { busy, error, notice, act, refresh, header } = useRunnerAction(scope)
const target = computed(() => state.data.value?.target ?? null)
const job = computed(() => state.data.value?.job ?? null)
const running = computed(() => job.value?.status === 'running')
const form = reactive({ host: '', port: 22, username: 'root' })
const editing = ref(false), checked = ref(false), password = ref(''), removeFiles = ref(false), warning = ref(''), forceable = ref(false), allowRestart = ref(false)
const showForm = computed(() => !target.value || editing.value)
const formValid = computed(() => HOST_PATTERN.test(form.host.trim()) && USERNAME_PATTERN.test(form.username.trim()) && Number.isInteger(Number(form.port)) && Number(form.port) >= 1 && Number(form.port) <= 65535)
const replaceable = computed(() => !!target.value && !target.value.client_key_installed && !running.value)
// 最近一次部署停在“启用 gVisor 要重启 Docker，但有 MCP 管理器在运行”
const needsRestart = computed(() => job.value?.kind === 'install' && job.value.status === 'failed' && job.value.error_code === 'gvisor_needs_docker_restart')

function put(next: State) { cache.setQueryData(key, next) }
// 任务结束后容器服务器的状态（在线、版本）也变了
watch(() => job.value?.status, (now, before) => { if (before === 'running' && now && now !== 'running') { notice.value = ''; void refresh() } })
// 换了服务器（地址、端口、账号、主机公钥）就清空输入过的密码：绝不把一台服务器的密码发给另一台；“允许重启 Docker”同样不带过去
watch(() => target.value ? [target.value.address, target.value.port, target.value.username, target.value.host_key_fingerprint].join('\n') : '', () => { password.value = ''; allowRestart.value = false })
// 勾选只对这一次失败有效：换了任务（或不再是这个原因）就取消
watch(() => needsRestart.value ? job.value?.id : null, () => { allowRestart.value = false })
// 成功提示在右下角停几秒（和智能体服务器页一样）
let hide: ReturnType<typeof setTimeout> | undefined
watch(notice, (text) => { clearTimeout(hide); if (text) hide = setTimeout(() => { if (notice.value === text) notice.value = '' }, 5000) })
onBeforeUnmount(() => clearTimeout(hide))

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
    try { const next = await effect(); if (next) put(next) } catch (cause) { failure = cause; throw cause }
  }, success)
  if (!ok) explain(failure)
  return ok
}
async function probe() {
  if (!formValid.value) return
  const t = target.value
  const body: Schema['AgentSshTargetSet'] = { host: form.host.trim(), port: Number(form.port), username: form.username.trim(), ...(t ? { version: t.version } : {}) }
  checked.value = false
  if (await call(async () => dataOf(await auth.slowApi.PUT('/api/v1/runner/hosts/{host_id}/ssh', { params: { header, path: { host_id: props.host.id } }, body })), '已读取主机公钥：请在服务器上核对指纹')) editing.value = false
}
async function confirm() {
  const t = target.value
  if (!t || !checked.value) return
  await call(async () => dataOf(await auth.api.POST('/api/v1/runner/hosts/{host_id}/ssh/confirm', { params: { header, path: { host_id: props.host.id } }, body: { version: t.version, fingerprint: t.host_key_fingerprint } })), '指纹已确认：之后只接受这把主机公钥')
}
async function install() {
  const t = target.value
  if (!t) return
  // 密码只随这一次请求发送，发送前就从页面上清掉
  const secret = password.value
  password.value = ''
  if (!secret && !t.client_key_installed) return
  // 只有上一次因为这个原因失败、并且勾选了，才带上“允许重启 Docker”；同样只随这一次请求
  const restart = needsRestart.value && allowRestart.value
  allowRestart.value = false
  await call(async () => dataOf(await auth.api.POST('/api/v1/runner/hosts/{host_id}/ssh/install', { params: { header, path: { host_id: props.host.id } }, body: { version: t.version, ...(secret ? { password: secret } : {}), allow_docker_restart: restart } })), '已开始部署：下面显示每一步的进度')
}
async function uninstall() {
  const t = target.value
  if (!t) return
  const files = removeFiles.value, secret = password.value
  if (!window.confirm(files ? '停止服务器上的 runner-agent 并删除接入包目录？Docker 与 gVisor 保留。' : '停止服务器上的 runner-agent？接入包目录保留，之后可以重新部署。')) return
  password.value = ''
  await call(async () => dataOf(await auth.api.POST('/api/v1/runner/hosts/{host_id}/ssh/uninstall', { params: { header, path: { host_id: props.host.id } }, body: { version: t.version, remove_files: files, ...(secret ? { password: secret } : {}) } })), '已开始卸载：下面显示每一步的进度')
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
      const next = dataOf(await auth.slowApi.POST('/api/v1/runner/hosts/{host_id}/ssh/revoke', { params: { header, path: { host_id: props.host.id } }, body: { version: t.version, force } }))
      put(next); warning.value = next.warning ?? ''
    } catch (cause) { failure = cause; throw cause }
  }, force ? '已删除工作台保存的专用私钥' : '专用密钥已吊销')
  if (!ok) { explain(failure); forceable.value = !force && failure instanceof ApiError && ['AGENT_SSH_UNREACHABLE', 'AGENT_SSH_KEY_INVALID'].includes(failure.code) }
}
async function forget() {
  const t = target.value
  if (!t || !window.confirm(`删除 SSH 地址 ${t.host}？之后可以重新填写。`)) return
  password.value = ''
  await call(async () => {
    await auth.api.DELETE('/api/v1/runner/hosts/{host_id}/ssh', { params: { header, path: { host_id: props.host.id }, query: { version: t.version } } })
    put({ target: null, job: null, warning: null })
  }, '已删除 SSH 地址')
}

// 手动部署：下载接入包（重新下载会签发新凭证，旧凭证立即失效）
async function bundle() {
  if (props.host.last_seen_at && !window.confirm('重新下载接入包会签发新凭证，旧凭证立即失效：容器服务器要用新的接入包重新部署才能连上。继续？')) return
  let file = ''
  if (await act(async () => { file = await downloadRunnerBundle(scope, props.host.id) })) notice.value = `已下载 ${file}：凭证只在这个 zip 里，传到容器服务器后删掉本地的副本`
}
async function remove() {
  const host = props.host
  if (!window.confirm(`移除容器服务器「${host.name}」？凭证立即失效。服务器上的 runner-agent 与 Docker 不会被卸载。`)) return
  if (await act(async () => {
    await auth.api.DELETE('/api/v1/runner/hosts/{host_id}', { params: { header, path: { host_id: host.id }, query: { version: host.version } } })
  }, '已移除容器服务器')) emit('removed')
}

// 页头的状态：部署到哪一步了
const phase = computed<{ label: string; tone: string }>(() => {
  const t = target.value, j = job.value
  if (running.value) return { label: `${SSH_JOB_KIND[j!.kind]}中`, tone: 'blue' }
  if (props.host.status === 'online') return { label: '已部署', tone: 'green' }
  if (j?.status === 'failed' || j?.status === 'interrupted') return { label: `${SSH_JOB_KIND[j.kind]}${j.status === 'failed' ? '失败' : '中断'}`, tone: 'red' }
  if (props.host.status === 'offline') return { label: '已部署 · 离线', tone: 'red' }
  if (!ssh.value || !owner.value) return { label: HOST_STATUS[props.host.status], tone: 'amber' }
  if (!t) return { label: '待填写服务器', tone: 'muted' }
  if (!t.host_key_confirmed) return { label: '待核对指纹', tone: 'amber' }
  return { label: '待部署', tone: 'amber' }
})
const subtitle = computed(() => {
  const t = target.value
  if (!ssh.value) return 'Kubernetes 节点由集群接入，不经 SSH 部署。'
  if (t) return `${t.username}@${t.host}:${t.port}${t.installed_at ? ` · 最近部署成功 ${formatTime(t.installed_at)}` : ''}`
  return '用一台专用的 Linux 服务器（x86_64 或 ARM64，内核 5.6 以上）：工作台经 SSH 装好 Docker、gVisor 与 runner-agent。'
})

// 右栏：部署前提
type Check = { key: string; label: string; value: string; note?: string; tone: 'ok' | 'warn' | 'bad' | 'todo' }
const CHECK_ICON = { ok: CircleCheck, warn: CircleAlert, bad: CircleX, todo: CircleDashed } as const
const checks = computed<Check[]>(() => {
  const t = target.value, h = props.host
  const agent: Check = {
    key: 'agent', label: 'runner-agent',
    value: h.status === 'online' ? '已连上工作台' : h.status === 'pending' ? '还没有连上' : HOST_STATUS[h.status],
    note: [h.agent_version ? `版本 ${h.agent_version}` : '', h.last_seen_at ? `最近联系 ${ago(h.last_seen_at)}` : ''].filter(Boolean).join(' · '),
    tone: h.status === 'online' ? 'ok' : h.status === 'offline' ? 'bad' : h.status === 'disabled' ? 'warn' : 'todo',
  }
  const ingress: Check = { key: 'ingress', label: '连工作台的入口', value: INGRESS[h.ingress], note: h.token_prefix ? `凭证 ${h.token_prefix}…` : '部署时签发凭证', tone: 'ok' }
  if (!ssh.value) return [agent, ingress]
  return [
    { key: 'address', label: 'SSH 地址', value: t ? `${t.username}@${t.host}:${t.port}` : '还没有填写', note: t && t.address !== t.host ? `解析为 ${t.address}` : '', tone: t ? 'ok' : 'todo' },
    { key: 'fingerprint', label: '主机指纹', value: !t ? '填写地址后读取（不登录）' : t.host_key_confirmed ? '已确认' : '待核对', note: t?.host_key_confirmed ? t.host_key_fingerprint : '', tone: !t ? 'todo' : t.host_key_confirmed ? 'ok' : 'warn' },
    { key: 'key', label: '工作台专用密钥', value: t?.client_key_installed ? '已装在服务器上' : '部署时安装（密码不保存）', note: t?.client_key_fingerprint ?? '', tone: t?.client_key_installed ? 'ok' : 'todo' },
    agent, ingress,
  ]
})
const passed = computed(() => checks.value.filter(c => c.tone === 'ok').length)

// 逐步进度：步骤名按容器服务器的部署写（和智能体服务器共用一套任务记录）
const STEP: Record<string, string> = {
  connect: '登录服务器（这次输入的密码或工作台的专用密钥）',
  inspect: '检测系统、架构与 sudo 权限',
  authorize_key: '装上工作台的专用公钥',
  upload: '签发凭证，生成并上传接入包',
  install: '运行安装脚本（Docker、gVisor、runner-agent）',
  online: '等待 runner-agent 连上工作台',
  stop: '停止并删除 runner-agent 容器',
  remove_files: '删除接入包目录',
}
const steps = computed(() => (job.value?.steps ?? []).map(s => ({ key: s.key, label: STEP[s.key] ?? (SSH_STEP as Record<string, string>)[s.key] ?? s.key, detail: s.detail, status: s.status })))
const SUCCESS = { prepare: '完成。', install: '部署完成，容器服务器已连上工作台。', uninstall: '卸载完成。' } as const
const PLAN = [
  '用这次输入的密码登录服务器，核对主机公钥',
  '装上工作台的专用公钥（之后用它登录；密码不保存）',
  '签发容器服务器凭证，上传接入包',
  '安装 Docker 29.9.1 与 gVisor release-20261019.0（核对 sha512）',
  '启动 runner-agent，等它主动连回工作台（服务器不用开入站端口）',
]
</script>

<template>
  <section class="ws-panel asv-init" aria-label="连接与部署">
    <div class="asv-init-head">
      <div class="asv-init-title">
        <h3>连接与部署 <span class="asv-state" :class="`tone-${phase.tone}`">{{ phase.label }}</span></h3>
        <p class="asv-init-sub">{{ subtitle }}</p>
      </div>
    </div>
    <p v-if="error" class="asv-init-error" role="alert">{{ error }}</p>

    <div class="asv-init-body">
      <div class="asv-init-main">
        <p v-if="!ssh" class="form-help">这个节点由 Kubernetes 集群接入：runner-agent 随集群部署，这里不需要 SSH。</p>
        <div v-else-if="!owner" class="ws-callout" role="note"><strong><KeyRound aria-hidden="true" />只有 owner 能部署</strong><p>通过 SSH 部署要输入服务器的登录密码，只有 owner 能操作；你可以查看状态与容器。</p></div>
        <p v-else-if="state.isPending.value" role="status" class="ws-state-line">正在加载…</p>
        <p v-else-if="state.error.value" role="alert">{{ sshError(state.error.value) }}</p>
        <template v-else>
          <form v-if="showForm" class="cs-ssh-form" aria-label="容器服务器的 SSH 地址" @submit.prevent="probe">
            <p class="form-help">填写服务器的 SSH 地址与账号（root，或能用 sudo 的账号）。先只读取服务器的主机公钥（不登录），你核对指纹后，再输入一次登录密码开始部署。</p>
            <div class="cs-ssh-fields">
              <label class="form-field"><span>服务器 IP 或域名 *</span><input v-model.trim="form.host" maxlength="253" autocomplete="off" placeholder="203.0.113.20" /></label>
              <label class="form-field"><span>SSH 端口 *</span><input v-model.number="form.port" type="number" min="1" max="65535" /></label>
              <label class="form-field"><span>SSH 账号 *</span><input v-model.trim="form.username" maxlength="32" autocomplete="off" /></label>
            </div>
            <div class="form-actions">
              <button type="submit" class="primary-button" :disabled="busy || !formValid">读取主机公钥</button>
              <button v-if="target" type="button" class="secondary-button" :disabled="busy" @click="editing = false">取消</button>
            </div>
          </form>

          <div v-else-if="target && !target.host_key_confirmed" class="asv-verify" role="group" aria-label="核对主机公钥">
            <strong class="asv-verify-title"><ShieldAlert aria-hidden="true" />下一步：核对主机指纹</strong>
            <p>工作台读到的主机公钥（{{ target.host_key_algorithm }}）指纹：</p>
            <p><code class="asv-fingerprint" data-testid="runner-ssh-fingerprint">{{ target.host_key_fingerprint }}</code></p>
            <p class="form-help">用云服务商的网页控制台（或其他你信任的方式）登录这台服务器，运行下面的命令，SHA256: 开头的那一段必须完全一致；不一致时不要确认。</p>
            <pre class="ai-arguments">{{ hostKeyCommand(target.host_key_algorithm) }}</pre>
            <label class="check-field"><input v-model="checked" type="checkbox" /> 我已在服务器上核对，指纹完全一致</label>
            <div class="form-actions">
              <button type="button" class="primary-button" :disabled="busy || !checked" @click="confirm">确认指纹</button>
              <button type="button" class="secondary-button" :disabled="busy" @click="edit">更换服务器</button>
              <button type="button" class="secondary-button" :disabled="busy" @click="forget">删除</button>
            </div>
          </div>

          <JobProgress
            v-if="job"
            :label="`${SSH_JOB_KIND[job.kind]}进度`" :title="SSH_JOB_KIND[job.kind]" :status="job.status"
            :started-at="job.started_at" :finished-at="job.finished_at" :updated-at="job.updated_at"
            :steps="steps" :log="job.log" log-note="密码、凭证、私钥已替换成 ***"
            :error-message="job.error_message" :error-code="job.error_code" :success-text="SUCCESS[job.kind]"
          />
          <section v-else-if="!target?.installed_at" class="asv-plan" aria-label="部署将完成">
            <header><h4><ListChecks aria-hidden="true" />部署将完成</h4><span>可以重复执行</span></header>
            <ol>
              <li v-for="(item, i) in PLAN" :key="item"><span class="asv-plan-num" aria-hidden="true">{{ i + 1 }}</span><span>{{ item }}</span><span class="asv-plan-state">待执行</span></li>
            </ol>
          </section>

          <form v-if="target?.host_key_confirmed && !editing && !running" class="cs-deploy" aria-label="部署容器服务器" @submit.prevent="install">
            <label class="form-field"><span>{{ target.client_key_installed ? '登录密码（root 账号可以不填；其他账号要用 sudo，必须填）' : '登录密码 *' }}</span><input v-model="password" type="password" autocomplete="off" maxlength="1024" /></label>
            <p class="form-help">密码只随这一次请求发送，用于登录、安装公钥和 sudo，工作台不保存；提交后输入框会清空。部署会签发新的容器服务器凭证（旧的立即失效），已有的环境不受影响；第一次要下载，可能需要几分钟。</p>
            <template v-if="needsRestart">
              <label class="check-field"><input v-model="allowRestart" type="checkbox" /> 允许重启 Docker</label>
              <p class="form-help">上一次部署停在启用 gVisor（原因见上面的进度）：勾选后重新部署会重启 Docker，这台服务器上的 MCP 管理器和其他容器短暂中断，之后自己恢复；不勾选时仍会停在这一步。</p>
            </template>
            <div class="form-actions">
              <button type="submit" class="primary-button" :disabled="busy || (!target.client_key_installed && !password)">{{ target.installed_at ? '重新部署' : '开始部署' }}</button>
              <button v-if="replaceable" type="button" class="secondary-button" :disabled="busy" @click="edit">更换服务器</button>
              <button v-if="replaceable" type="button" class="secondary-button" :disabled="busy" @click="forget">删除</button>
            </div>
          </form>

          <div v-if="target?.client_key_installed && !running" class="cs-manage" role="group" aria-label="卸载与吊销">
            <strong><KeyRound aria-hidden="true" />卸载与吊销</strong>
            <p>卸载停止服务器上的 runner-agent（Docker 与 gVisor 保留），要先删掉这台服务器上的所有环境；不是 root 账号时，在上面的部署表单里填登录密码。吊销会删掉服务器上工作台的专用公钥。</p>
            <label class="check-field"><input v-model="removeFiles" type="checkbox" /> 卸载时同时删除接入包目录（含容器服务器凭证）</label>
            <div class="form-actions">
              <button type="button" class="secondary-button im-danger" :disabled="busy" @click="uninstall">卸载 runner-agent</button>
              <button type="button" class="secondary-button im-danger" :disabled="busy" @click="revoke()">吊销专用密钥</button>
              <button v-if="forceable" type="button" class="secondary-button im-danger" :disabled="busy" @click="revoke(true)">强制吊销（只删除工作台保存的私钥）</button>
            </div>
          </div>
          <p v-if="warning" class="ws-callout tone-amber" role="alert">{{ warning }}</p>
        </template>
      </div>

      <div class="asv-init-side">
        <section class="asv-prereq" aria-label="部署前提">
          <header>
            <h4><KeyRound aria-hidden="true" />部署前提</h4>
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
              <button v-if="c.key === 'address' && target && !editing && replaceable" type="button" class="asv-link" :disabled="busy" @click="edit"><Pencil aria-hidden="true" />更换</button>
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
            <strong><ShieldCheck aria-hidden="true" />服务器最好专用</strong>
            <p>部署只装 Docker、gVisor 与 runner-agent，不动服务器上其他容器。runner-agent 主动连工作台的接入入口，服务器不用开入站端口。升级 runner-agent 也是重新部署一次，容器与数据卷不受影响。</p>
          </template>
        </div>
        <details class="asv-note cs-manual">
          <summary>不用 SSH：手动部署</summary>
          <p>下载接入包，传到容器服务器上解压，以 root 运行 <code>install.sh</code>（装 Docker 29.9.1、gVisor release-20261019.0 并核对 sha512、启动 runner-agent）。</p>
          <button type="button" class="secondary-button" :disabled="busy" @click="bundle"><Download aria-hidden="true" />{{ host.last_seen_at ? '下载新的接入包' : '下载接入包' }}</button>
        </details>
      </div>
    </div>

    <div class="asv-danger">
      <p><b>移除容器服务器</b><span>凭证立即失效；服务器上的 runner-agent 与 Docker 不会被卸载（要卸载先用上面的“卸载 runner-agent”）。还有没删除的环境时不能移除。</span></p>
      <button type="button" class="secondary-button im-danger" :disabled="busy || running" @click="remove"><Trash2 aria-hidden="true" />移除容器服务器</button>
    </div>
    <p v-if="notice" class="asv-toast" role="status">{{ notice }}</p>
  </section>
</template>
