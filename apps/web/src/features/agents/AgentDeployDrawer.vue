<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, useId, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { ArrowRight, Info, Layers, Lock } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'
import { useAgentAction } from './action'
import { agentError, useInstance } from './queries'
import { blockReason, deployable, deploymentKey, initStatus, mib, resources, useDeployment, type Server } from './servers'
import { sshError } from './ssh'
import { forgetDeployment, getAgentDeploymentReceipt, rememberDeployment, startAgentDeployment, type DeploymentRecord } from './deploymentRequests'
import SshJob from './SshJob.vue'
import ModelConnectionPicker from './ModelConnectionPicker.vue'
import { bindingError, type ModelChoice } from './modelBinding'

// 部署 OpenClaw（T-0182，效果图 02 右侧；改造规范第 6 节）：名称、部署服务器（只能选可部署的）、CPU / 内存上限（默认不限制）、
// 平台推荐版本（已锁定）。没有 IP、账号或密码输入：用服务器页保存的连接信息。
// 提交走一次点击的部署命令（request_id = Idempotency-Key）；结果未知、页面刷新后先查收据，不换新的 request_id；
// 接纳后按现有 2 秒轮询显示任务的真实步骤；失败 / 中断时“继续部署原实例”（POST /agents/instances/{id}/deploy），不再新建。
const props = defineProps<{ servers: Server[]; serversLoading?: boolean; preselect?: string | null; recovery?: DeploymentRecord | null }>()
const emit = defineEmits<{ close: []; view: [instanceId: string] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id, cache = useQueryClient()
const active = () => auth.user?.id === user && auth.workspaceId === scope
const { busy, error: actionError, act, refresh, header } = useAgentAction(scope, sshError)
const placementId = useId()
const mode = ref<'auto' | 'specified'>(props.preselect ? 'specified' : 'auto')
const ids = { name: useId(), server: useId(), cpus: useId(), mem: useId() }

type Phase = 'form' | 'sending' | 'checking' | 'progress' | 'missing'
const phase = ref<Phase>(props.recovery ? 'checking' : 'form')
const requestId = ref<string | null>(props.recovery?.request_id ?? null)
const instanceId = ref<string | null>(props.recovery?.instance_id ?? null)
const form = reactive({ name: '', server_id: '', cpus: '1', mem: '2048' })
// 主模型（T-0207）：必选；选择即授权给新智能体（与创建同一个事务），不再落回固定的 openai_api_key
const model = ref<ModelChoice | null>(null)
const error = ref(''), notice = ref(''), interrupted = ref(false), acceptedName = ref('')
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false
onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })

// 服务器：只列可部署的；不可部署的单独说明原因与去服务器页的链接。地址栏指定的服务器不可用时不偷选别的
const targets = computed(() => props.servers.filter(deployable))
const blocked = computed(() => props.servers.filter(s => !deployable(s)))
const chosen = computed(() => targets.value.find(s => s.id === form.server_id) ?? null)
const preselected = computed(() => props.preselect ? props.servers.find(s => s.id === props.preselect) ?? null : null)
const preselectIssue = computed(() => {
  if (!props.preselect || props.serversLoading) return ''
  if (!preselected.value) return '地址里指定的服务器不存在或你已无权访问：请从下面选择一台。'
  return deployable(preselected.value) ? '' : `「${preselected.value.name}」暂不能部署：${blockReason(preselected.value)}。`
})
let seeded = false
watch([() => props.preselect, targets], () => {
  if (seeded || !props.preselect) return
  if (targets.value.some(s => s.id === props.preselect)) { form.server_id = props.preselect; seeded = true }
}, { immediate: true })
const allocated = computed(() => {
  const s = chosen.value
  if (!s) return null
  if (!s.managed_deployments) return { text: '还没有平台部署的智能体', percent: null }
  if (s.allocated_mem_limit_mb === null) return { text: '已分配内存上限：含不限制的智能体（不是实际占用）', percent: null }
  const total = s.mem_total ? s.mem_total / 1024 ** 2 : null
  return { text: `已分配内存上限 ${mib(s.allocated_mem_limit_mb)}${total ? ` / ${mib(Math.round(total))}` : ''}（不是实际占用）`, percent: total ? Math.min(100, (100 * s.allocated_mem_limit_mb) / total) : null }
})

const cpus = computed(() => form.cpus.trim() === '' ? null : Number(form.cpus))
const mem = computed(() => form.mem.trim() === '' ? null : Number(form.mem))
const cpuValid = computed(() => cpus.value !== null && (Number.isFinite(cpus.value) && cpus.value >= 0.1 && cpus.value <= 256))
const memValid = computed(() => mem.value !== null && (Number.isInteger(mem.value) && mem.value >= 256 && mem.value <= 1048576))
const nameValid = computed(() => form.name.trim().length >= 1 && form.name.trim().length <= 64)
const valid = computed(() => nameValid.value && (mode.value === 'auto' || !!chosen.value) && !!model.value && cpuValid.value && memValid.value)
const FLOW = ['检查服务器环境', '拉取镜像', '创建独立容器与数据目录', '接入工作台', '等待心跳并确认上线']

// 接纳之后：这个实例的部署状态（任务进度每 2 秒取一次）与最新的实例（继续部署时用它的 version）
const state = useDeployment(scope, () => instanceId.value ?? '', () => phase.value === 'progress')
const instance = useInstance(scope, () => instanceId.value, () => phase.value === 'progress')
const job = computed(() => state.data.value?.job ?? null)
const finished = computed(() => !!job.value && job.value.status !== 'running')
const failed = computed(() => job.value?.status === 'failed' || job.value?.status === 'interrupted')
const deployedServer = computed(() => state.data.value?.server ?? null)
const record = (instance_id?: string): DeploymentRecord => ({ request_id: requestId.value!, user_id: user!, workspace_id: scope, ...(instance_id ? { instance_id } : {}) })

function accept(next: Schema['AgentInstanceOut'], deployment: Schema['AgentDeploymentState'] | null) {
  instanceId.value = next.id; acceptedName.value = next.name
  rememberDeployment(record(next.id))
  if (deployment) cache.setQueryData(deploymentKey(user, scope, next.id), deployment)
  phase.value = 'progress'
  notice.value = deployment?.capacity_warning ?? ''
  void refresh()
}
function startOver() {
  if (requestId.value) forgetDeployment(requestId.value)
  requestId.value = null; instanceId.value = null; error.value = ''; notice.value = ''; interrupted.value = false
  phase.value = 'form'
}
// 结果未知（连接中断、网关错误、原请求还在处理）：先查收据
const modelCodes = new Set(['MODEL_CONNECTION_DISABLED', 'MODEL_CONNECTION_NOT_READY', 'MODEL_PROTOCOL_UNSUPPORTED', 'MODEL_NOT_ALLOWED', 'CREDENTIAL_UNREADABLE'])
const unknown = (cause: unknown) => cause instanceof ApiError && (cause.code === 'WRITE_UNKNOWN' || cause.code === 'IDEMPOTENCY_IN_PROGRESS' || (cause.code === 'HTTP_ERROR' && cause.status >= 500))

async function submit() {
  if (!valid.value || phase.value !== 'form' || !user) return
  // 同一次填写的连点、重试都用同一个 request_id；被明确拒绝后（没有建实例）才换新的
  requestId.value ??= crypto.randomUUID()
  const body: Schema['AgentDeploymentCreateIn'] = { request_id: requestId.value, placement: mode.value === 'auto' ? { mode: 'auto' } : { mode: 'specified', server_id: chosen.value!.id }, ...(mode.value === 'specified' ? { server_version: chosen.value!.version } : {}), name: form.name.trim(), description: '', cpus: cpus.value!, mem_limit_mb: mem.value!, model: model.value! }
  rememberDeployment(record())
  phase.value = 'sending'; error.value = ''
  try {
    const out = await startAgentDeployment(scope, body)
    if (!active() || disposed) return
    accept(out.instance, out.deployment)
  } catch (cause) {
    if (!active() || disposed) return
    if (unknown(cause)) { await check(); return }
    // 明确被拒绝：没有创建实例，可以改了再提交（下一次是新的请求）
    forgetDeployment(requestId.value ?? undefined)
    requestId.value = null
    error.value = modelCodes.has(cause instanceof ApiError ? cause.code : '') ? bindingError(cause) : sshError(cause)
    phase.value = 'form'
    void refresh()
  }
}

async function check() {
  if (!requestId.value || disposed) return
  clearTimeout(timer)
  phase.value = 'checking'; error.value = ''
  try {
    const receipt = await getAgentDeploymentReceipt(scope, requestId.value)
    if (!active() || disposed) return
    if (receipt.instance && (receipt.state === 'accepted' || receipt.state === 'interrupted')) { accept(receipt.instance, receipt.deployment); return }
    if (receipt.state === 'processing') { timer = setTimeout(() => void check(), 2000); return }
    if (receipt.state === 'failed') {
      // 命令被拒绝、没有创建实例：回到表单（不保留 request_id）
      forgetDeployment(requestId.value)
      requestId.value = null
      error.value = receipt.error_code ? (modelCodes.has(receipt.error_code) ? bindingError(new ApiError(409, receipt.error_code, '')) : sshError(new ApiError(409, receipt.error_code, ''))) : '这次部署请求被拒绝，没有创建智能体。'
      phase.value = 'form'
      return
    }
    interrupted.value = true
    phase.value = 'missing'
  } catch (cause) {
    if (!active() || disposed) return
    // 收据过期了，但之前已经记下了接纳的实例：直接看这个实例的部署进度
    if (cause instanceof ApiError && cause.status === 404 && instanceId.value) { phase.value = 'progress'; return }
    if (cause instanceof ApiError && cause.status === 404) { phase.value = 'missing'; return }
    error.value = `查询部署请求失败：${agentError(cause)}`
  }
}
if (props.recovery) void check()

async function resume() {
  const target = instance.data.value, server = state.data.value?.deployment?.server_id ?? deployedServer.value?.id
  if (!target || !server) return
  // 继续原来的实例：沿用它的服务器与最新 version，不新建实例、不带密码
  await act(async () => {
    const next = dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/deploy', { params: { header, path: { instance_id: target.id } }, body: { server_id: server, version: target.version } }))
    cache.setQueryData(deploymentKey(user, scope, target.id), next)
  })
}

// 任务结束（成功、失败、中断）：刷新总览、实例、服务器；部署请求有了结论，不再需要找回
watch(() => job.value?.status, (now, before) => {
  if (!now || now === 'running' || phase.value !== 'progress') return
  if (requestId.value) forgetDeployment(requestId.value)
  if (before === 'running') void refresh()
}, { immediate: true })
</script>

<template>
  <div class="ag-deploy">
    <form v-if="phase === 'form' || phase === 'sending'" class="asv-form" aria-label="部署 OpenClaw" @submit.prevent="submit">
      <p class="form-help">平台在选中的服务器上自动创建独立的容器、数据目录与网络，部署 OpenClaw 并接入工作台。</p>
      <div class="form-field">
        <label :for="ids.name">智能体名称 *</label>
        <input :id="ids.name" v-model="form.name" maxlength="64" autocomplete="off" placeholder="例如：项目助手" :disabled="phase === 'sending'" />
      </div>
      <div class="form-field">
        <label :for="placementId">部署位置</label>
        <select :id="placementId" v-model="mode" :disabled="phase === 'sending'"><option value="auto">自动分配（推荐）</option><option value="specified">指定服务器</option></select>
        <p v-if="mode === 'auto'" class="form-help">平台按最新资源、负载与创建预约选择服务器，提交后显示实际节点。</p>
        <p v-else class="form-help">只在指定服务器部署；资源不足或不可用时会保留选择，不会自动换机。</p>
      </div>
      <div v-if="mode === 'specified'" class="form-field">
        <label :for="ids.server">部署服务器 *</label>
        <select :id="ids.server" v-model="form.server_id" :disabled="phase === 'sending'">
          <option value="" disabled>{{ serversLoading ? '正在加载服务器…' : targets.length ? '选择一台已初始化的服务器' : '没有可部署的服务器' }}</option>
          <option v-for="s in targets" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
        <p v-if="preselectIssue" class="feedback" role="alert">{{ preselectIssue }}<RouterLink v-if="preselected" :to="{ path: '/agent-servers', query: { server: preselected.id } }">去服务器页处理</RouterLink></p>
        <div v-if="chosen" class="ag-server-info" aria-label="所选服务器">
          <p><span class="asv-state" :class="`tone-${initStatus(chosen).tone}`">{{ initStatus(chosen).label }}</span> {{ resources(chosen) }} · 已有 {{ chosen.instances_total }} 个智能体 <RouterLink class="asv-go" :to="{ path: '/agent-servers', query: { server: chosen.id } }">管理服务器<ArrowRight aria-hidden="true" /></RouterLink></p>
          <p v-if="allocated" class="form-help">{{ allocated.text }}</p>
          <span v-if="allocated?.percent !== null && allocated?.percent !== undefined" class="host-bar" aria-hidden="true"><span :style="{ width: `${allocated.percent}%` }" /></span>
        </div>
        <p v-if="!serversLoading && !targets.length" class="form-help">还没有可部署的服务器：先到 <RouterLink to="/agent-servers">智能体服务器</RouterLink> 页添加一台、核对指纹并初始化。</p>
        <details v-if="blocked.length" class="ag-blocked">
          <summary>{{ blocked.length }} 台服务器暂不能部署</summary>
          <ul><li v-for="s in blocked" :key="s.id"><span>{{ s.name }}：{{ blockReason(s) }}</span> <RouterLink v-if="s.kind === 'ssh'" :to="{ path: '/agent-servers', query: { server: s.id } }">去处理</RouterLink></li></ul>
        </details>
        <p class="form-help ag-hint"><Info aria-hidden="true" />尚未初始化、待补密码或正在执行任务的服务器不可选择。</p>
      </div>
      <ModelConnectionPicker v-model="model" :instance-id="null" :disabled="phase === 'sending'" />
      <div class="ag-field-row">
        <div class="form-field">
          <label :for="ids.cpus">CPU 上限（核）</label>
          <input :id="ids.cpus" v-model.trim="form.cpus" inputmode="decimal" required :aria-invalid="!cpuValid" :disabled="phase === 'sending'" />
        </div>
        <div class="form-field">
          <label :for="ids.mem">内存上限（MiB）</label>
          <input :id="ids.mem" v-model.trim="form.mem" inputmode="numeric" required :aria-invalid="!memValid" :disabled="phase === 'sending'" />
        </div>
      </div>
      <p v-if="!cpuValid" class="feedback">CPU 上限是 0.1 到 256 之间的数，必须填写有限资源上限。</p>
      <p v-else-if="!memValid" class="feedback">内存上限是 256 到 1048576 之间的整数（MiB，例如 4096 = 4 GB），必须填写有限资源上限。</p>
      <p v-else class="form-help">默认 1 核、2048 MiB；资源上限参与服务器容量检查。{{ mem !== null && memValid ? `内存上限 ${mib(mem)}。` : '' }}</p>
      <div class="form-field">
        <span class="ag-label">OpenClaw 版本</span>
        <p class="ag-version"><Lock aria-hidden="true" />平台推荐版本（已锁定）</p>
        <p class="form-help">使用平台锁定并验证过的版本，不能自选镜像。新智能体为外网模式、只读工具权限、最高角色 member；其他权限部署后在详情里单独打开。</p>
      </div>
      <section class="asv-checklist" aria-label="自动部署流程">
        <h3><Layers aria-hidden="true" />自动部署流程</h3>
        <ol class="asv-steps"><li v-for="item in FLOW" :key="item">{{ item }}</li></ol>
        <p class="form-help">每个智能体独立的 Compose 项目、数据目录与网络，不影响同机其他智能体。</p>
      </section>
      <p v-if="error" role="alert">{{ error }}</p>
      <div class="form-actions asv-actions">
        <button type="button" class="secondary-button" @click="emit('close')">取消</button>
        <button type="submit" class="primary-button" :disabled="phase === 'sending' || !valid">{{ phase === 'sending' ? '正在提交…' : '开始部署' }}</button>
      </div>
    </form>

    <section v-else-if="phase === 'checking'" class="asv-form" aria-label="正在确认部署请求">
      <p role="status">正在确认上一次部署请求的结果…</p>
      <p class="form-help">请求号 <code class="asv-code">{{ requestId }}</code>：工作台按它找回同一个智能体，不会重复创建。</p>
      <template v-if="error">
        <p role="alert">{{ error }}</p>
        <div class="form-actions asv-actions"><button type="button" class="secondary-button" @click="emit('close')">关闭</button><button type="button" class="primary-button" @click="check">再查一次</button></div>
      </template>
    </section>

    <section v-else-if="phase === 'missing'" class="asv-form" aria-label="部署请求结果未知">
      <p role="alert">{{ interrupted ? '上一次部署请求的处理被中断，没有找到它创建的智能体。' : '找不到上一次部署请求的记录（可能已经过期）。' }}</p>
      <p class="form-help">请先在列表里查看是否已经有这个智能体，再决定是否重新部署；工作台不会自动换一个请求号重建。</p>
      <div class="form-actions asv-actions">
        <button type="button" class="secondary-button" @click="startOver">重新填写</button>
        <button type="button" class="primary-button" @click="startOver(); emit('close')">查看现有智能体</button>
      </div>
    </section>

    <section v-else class="asv-form" aria-label="新智能体的部署">
      <p><strong>{{ instance.data.value?.name ?? (acceptedName || '新智能体') }}</strong><template v-if="deployedServer"> → <RouterLink :to="{ path: '/agent-servers', query: { server: deployedServer.id } }">{{ deployedServer.name }}</RouterLink></template></p>
      <p v-if="notice" class="feedback" role="note">{{ notice }}</p>
      <SshJob v-if="job" :job="job" />
      <p v-else-if="state.isPending.value" role="status">正在取部署进度…</p>
      <p v-else class="form-help">已接纳，等待部署任务开始…</p>
      <p v-if="failed" class="form-help">实例已经保留：继续部署会在原来的服务器上重试这个实例，不会再新建一个。</p>
      <p v-if="actionError" role="alert">{{ actionError }}</p>
      <div class="form-actions asv-actions">
        <button v-if="finished" type="button" class="secondary-button" @click="startOver">再部署一个</button>
        <button v-if="instanceId" type="button" class="secondary-button" @click="emit('view', instanceId)">查看智能体</button>
        <button v-if="failed" type="button" class="primary-button" :disabled="busy || !instance.data.value" @click="resume">继续部署原实例</button>
        <button v-else type="button" class="primary-button" @click="emit('close')">{{ finished ? '完成' : '在后台继续' }}</button>
      </div>
    </section>
  </div>
</template>
