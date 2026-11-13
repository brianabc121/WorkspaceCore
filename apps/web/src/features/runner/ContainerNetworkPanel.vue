<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { ApiError, dataOf, isAccessError, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { useInstances, unique } from '../agents/queries'
import { formatTime } from '../time'
import { useRunnerAction } from './api'

type NetworkRecord = Schema['RunnerServiceOut'] | Schema['RunnerIngressOut'] | Schema['RunnerPublishedEndpointOut']
type Action = { kind: 'ingress' | 'publish'; environmentId: string; serviceId: string; label: string }
  | { kind: 'service' | 'rule' | 'endpoint'; environmentId: string; recordId: string; label: string }
  | { kind: 'edit_service' | 'edit_rule' | 'edit_endpoint'; environmentId: string; recordId: string; revision: number; label: string }
const props = defineProps<{ environmentId: string }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const disposed = ref(false), clock = ref(Date.now())
const clockTimer = setInterval(() => { clock.value = Date.now() }, 1000)
const active = computed(() => !disposed.value && auth.workspaceId === scope && auth.user?.id === user && auth.canManage)
const { act, busy, error, header } = useRunnerAction(scope, networkError)
const state = useQuery({
  queryKey: computed(() => ['runner', user, scope, 'network', props.environmentId]),
  enabled: active,
  queryFn: async ({ queryKey, signal }) => dataOf(await auth.api.GET('/api/v1/runner/environments/{environment_id}/network', {
    params: { header, path: { environment_id: queryKey[4] as string } }, signal,
  })),
  refetchInterval: 30_000,
})
const data = computed(() => active.value && !isAccessError(state.error.value) && state.data.value?.environment_id === props.environmentId ? state.data.value : undefined)
const fresh = computed(() => !!data.value && !state.error.value && !state.isFetching.value)
const canCreate = computed(() => fresh.value && auth.isOwner && data.value?.runtime_status !== 'unsupported')
const instances = useInstances(scope)
const sources = computed(() => active.value && auth.isOwner && !isAccessError(instances.error.value) ? unique(instances.data.value?.pages).filter(i => i.id !== data.value?.owner_instance_id && i.status !== 'disabled') : [])
const name = ref(''), port = ref(''), protocol = ref<'TCP' | 'UDP'>('TCP')
const action = ref<Action | null>(null), password = ref(''), source = ref(''), expires = ref(''), cidrs = ref('')
const editName = ref(''), editPort = ref(''), editProtocol = ref<'TCP' | 'UDP'>('TCP')
const editing = computed(() => action.value?.kind.startsWith('edit_') ?? false)
const publicAction = computed(() => action.value?.kind === 'publish' || action.value?.kind === 'edit_endpoint')
const leaseAction = computed(() => publicAction.value || action.value?.kind === 'ingress' || action.value?.kind === 'edit_rule')
const formLabel = computed(() => ({ ingress: '允许跨智能体来源', publish: '申请受限公网入口', edit_service: '编辑内部服务', edit_rule: '编辑跨智能体规则', edit_endpoint: '编辑受限公网入口', service: '确认撤销网络规则', rule: '确认撤销网络规则', endpoint: '确认撤销网络规则' })[action.value?.kind ?? 'service'])
const sourceReady = computed(() => !instances.error.value && !instances.isFetching.value && sources.value.some(i => i.id === source.value))
const submitLabel = computed(() => editing.value ? '保存修改' : action.value?.kind === 'ingress' ? '保存来源规则' : action.value?.kind === 'publish' ? '保存公网入口' : '确认撤销')
const errorEnvironment = ref<string | null>(null), notice = ref('')
const visibleError = computed(() => active.value && errorEnvironment.value === props.environmentId ? error.value : '')
const states: Record<NetworkRecord['state'], string> = {
  approved_pending_apply: '等待节点应用', active: '已生效', revoked: '已撤销', expired: '已过期', apply_failed: '节点应用失败',
}
function clearAction() { action.value = null; password.value = ''; source.value = ''; expires.value = ''; cidrs.value = ''; editName.value = ''; editPort.value = ''; editProtocol.value = 'TCP' }
watch(fresh, value => { if (!value) password.value = '' }, { flush: 'sync' })
watch([instances.error, instances.isFetching], () => {
  if (action.value?.kind === 'ingress') password.value = ''
  if (isAccessError(instances.error.value)) source.value = ''
}, { flush: 'sync' })
watch(data, value => { if (!value) { clearAction(); notice.value = '' } }, { flush: 'sync' })
watch([() => props.environmentId, active, () => auth.isOwner], () => {
  clearAction(); name.value = ''; port.value = ''; protocol.value = 'TCP'; notice.value = ''; error.value = ''; errorEnvironment.value = null
}, { flush: 'sync' })
onBeforeUnmount(() => { clearInterval(clockTimer); disposed.value = true; clearAction() })
function expired(record: NetworkRecord) { return !!record.expires_at && !(new Date(record.expires_at).getTime() > clock.value) }
function status(record: NetworkRecord) {
  if (expired(record) && !['revoked', 'expired'].includes(record.state)) return '已到期，等待刷新'
  const value = ['revoked', 'expired'].includes(record.state) ? `${states[record.state]}，${record.revocation_confirmed ? '现有连接关闭已确认' : '等待现有连接关闭确认'}` : states[record.state]
  return !fresh.value ? `上次状态：${value}（待确认）` : value
}
function usable(record: NetworkRecord) {
  return fresh.value && !expired(record) && record.state === 'active' && record.desired_revision === record.applied_revision
    && record.desired_generation === record.applied_generation && record.accepting_new_connections && record.connect_host && record.connect_port
}
function revocable(record: NetworkRecord) { return auth.isOwner && fresh.value && !['revoked', 'expired'].includes(record.state) }
function sourceName(id: string) { return sources.value.find(s => s.id === id)?.name ?? id }
function applyMessage(record: NetworkRecord) {
  return record.apply_error === 'REVOCATION_PENDING_CONFIRMATION' ? '撤销已记录，等待节点确认连接关闭。' : record.apply_error ? '节点尚未确认当前规则，请检查容器服务器的网络状态。' : ''
}
function networkError(cause: unknown): string {
  // Network writes contain step-up passwords. Never display server-supplied messages.
  const messages: Record<string, string> = {
    VERSION_CONFLICT: '已被其他人修改，请刷新后重新打开编辑；当前输入已保留。',
    WRITE_UNKNOWN: '连接中断，结果未知：已刷新，请看最新状态再决定要不要重试。',
    FORBIDDEN: '没有权限执行这次网络操作。', NOT_FOUND: '记录不存在，或你已无权访问。',
    VALIDATION_FAILED: '输入不符合要求，请检查服务、来源和有效期后重试。',
    STEP_UP_FAILED: '密码验证失败，请重新输入登录密码。',
    STEP_UP_REQUIRED: '请重新输入登录密码确认这次网络操作。',
    RATE_LIMITED: '密码验证次数过多，请稍后重试。',
    NETWORK_PROTOCOL_UNSUPPORTED: '跨智能体和公网网关仅支持 TCP；改为 UDP 前请先撤销现有授权和入口。',
    INVALID_PASSWORD: '密码验证失败，请重新输入登录密码。',
    NETWORK_BACKEND_UNAVAILABLE: '当前服务器未使用受管网络后端。',
    NETWORK_VALIDATION_REQUIRED: '运行节点尚未通过跨节点网络与隔离验证。',
    NETWORK_IDENTITY_UNAVAILABLE: '环境当前没有有效的运行实例身份。',
    SERVICE_NAME_EXISTS: '环境内已存在同名服务，请修改名称。',
    SERVICE_UNAVAILABLE: '服务身份已变化或已撤销，请刷新后检查。',
    SAME_INSTANCE_PRIVATE: '同一智能体使用私网服务，无需跨智能体授权。',
    SOURCE_CAPABILITY_REQUIRED: '来源智能体需要已生效的跨智能体通信权限。',
  }
  return cause instanceof ApiError ? messages[cause.code] ?? (cause.status === 0 ? '网络连接失败，请检查连接后重试。' : '请求失败，请稍后重试。') : '请求失败，请稍后重试。'
}
function localTime(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16) : ''
}
function openEdit(kind: 'edit_service' | 'edit_rule' | 'edit_endpoint', record: NetworkRecord, label: string) {
  if (!canCreate.value || !revocable(record) || busy.value) return
  openAction({ kind, environmentId: props.environmentId, recordId: record.id, revision: record.desired_revision, label })
  if ('name' in record) { editName.value = record.name; editPort.value = String(record.port); editProtocol.value = record.protocol }
  if ('source_cidrs' in record) cidrs.value = record.source_cidrs.join('\n')
  expires.value = localTime(record.expires_at)
}
function serviceName(id: string) { return data.value?.services.find(s => s.id === id)?.name ?? id }
function openAction(value: Action) {
  if (!active.value || !auth.isOwner || busy.value || !fresh.value) return
  clearAction(); error.value = ''; errorEnvironment.value = props.environmentId; notice.value = ''; action.value = value
}
function fail(message: string) { password.value = ''; errorEnvironment.value = props.environmentId; error.value = message }
function lease(): string | null {
  const date = new Date(expires.value), delta = date.getTime() - Date.now()
  return Number.isFinite(delta) && delta > 0 && delta <= 30 * 86_400_000 ? date.toISOString() : null
}
function publicSources(): string[] | null {
  const values = [...new Set(cidrs.value.split(/[\s,]+/).filter(Boolean))]
  if (!values.length || values.length > 32) return null
  for (const value of values) {
    const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/.exec(value)
    if (!match) return null
    const [a, b, c, d, prefix] = match.slice(1).map(Number) as [number, number, number, number, number]
    if ([a, b, c, d].some(n => n > 255) || prefix < 1 || prefix > 32 || a === 0 || a === 10 || a === 127 || a >= 224 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)) return null
    const ip = ((a << 24) | (b << 16) | (c << 8) | d) >>> 0
    const hostMask = prefix === 32 ? 0 : 0xffffffff >>> prefix
    if ((ip & hostMask) !== 0) return null
    // Reject ranges that overlap private networks, even if their first address is public.
    const end = (ip | hostMask) >>> 0
    if ([[0x0a000000, 0x0affffff], [0xac100000, 0xac1fffff], [0xc0a80000, 0xc0a8ffff]].some(([lo, hi]) => ip <= hi! && end >= lo!)) return null
  }
  return values
}
async function register() {
  if (!canCreate.value || busy.value) return
  const number = Number(port.value), environmentId = props.environmentId
  errorEnvironment.value = environmentId; notice.value = ''
  if (!/^[a-z][a-z0-9-]{0,39}$/.test(name.value) || !Number.isInteger(number) || number < 1 || number > 65535) { fail('请输入有效的服务名称和 1–65535 的端口'); return }
  if (await act(async () => dataOf(await auth.api.POST('/api/v1/runner/environments/{environment_id}/services', {
    params: { header, path: { environment_id: environmentId } }, body: { name: name.value, port: number, protocol: protocol.value },
  }))) && active.value && environmentId === props.environmentId) { name.value = ''; port.value = ''; notice.value = '内部服务已登记，连接状态以节点回执为准。' }
}
async function submitAction() {
  const selected = action.value
  if (!selected || !active.value || !auth.isOwner || !fresh.value || busy.value || selected.environmentId !== props.environmentId) return
  const secret = password.value
  password.value = ''; errorEnvironment.value = selected.environmentId; notice.value = ''
  if (!secret) { fail('请输入登录密码确认这次网络操作'); return }
  const path = { environment_id: selected.environmentId }
  let effect: () => Promise<unknown>
  if (selected.kind === 'edit_service') {
    const number = Number(editPort.value)
    if (!canCreate.value || !data.value?.services.some(s => s.id === selected.recordId && revocable(s))) return
    if (!/^[a-z][a-z0-9-]{0,39}$/.test(editName.value) || !Number.isInteger(number) || number < 1 || number > 65535) { fail('请输入有效的服务名称和 1–65535 的端口'); return }
    effect = async () => dataOf(await auth.api.PATCH('/api/v1/runner/environments/{environment_id}/services/{service_id}', { params: { header, path: { ...path, service_id: selected.recordId } }, body: { name: editName.value, port: number, protocol: editProtocol.value, expected_revision: selected.revision, password: secret } }))
  } else if (selected.kind === 'edit_rule' || selected.kind === 'edit_endpoint') {
    const records = selected.kind === 'edit_rule' ? data.value?.ingress_rules : data.value?.published_endpoints
    if (!canCreate.value || !records?.some(r => r.id === selected.recordId && revocable(r))) return
    const expiresAt = lease()
    if (!expiresAt) { fail('有效期必须在未来且不超过 30 天'); return }
    if (selected.kind === 'edit_rule') {
      effect = async () => dataOf(await auth.api.PATCH('/api/v1/runner/environments/{environment_id}/ingress/{rule_id}', { params: { header, path: { ...path, rule_id: selected.recordId } }, body: { expires_at: expiresAt, expected_revision: selected.revision, password: secret } }))
    } else {
      const values = publicSources()
      if (!values) { fail('请填写 1–32 个明确的 IPv4 公网来源 CIDR；不接受私网、无限来源或 IPv6'); return }
      effect = async () => dataOf(await auth.api.PATCH('/api/v1/runner/environments/{environment_id}/published-endpoints/{endpoint_id}', { params: { header, path: { ...path, endpoint_id: selected.recordId } }, body: { source_cidrs: values, expires_at: expiresAt, expected_revision: selected.revision, password: secret } }))
    }
  } else if (selected.kind === 'ingress' || selected.kind === 'publish') {
    if (!canCreate.value || !data.value?.services.some(s => s.id === selected.serviceId && s.protocol === 'TCP' && revocable(s))) { fail('跨智能体和公网网关仅支持 TCP；UDP 仅用于同域私网'); return }
    const expiresAt = lease()
    if (!expiresAt) { fail('有效期必须在未来且不超过 30 天'); return }
    if (selected.kind === 'ingress') {
      if (!sourceReady.value) { fail('请选择当前工作空间内可用的来源智能体'); return }
      effect = async () => dataOf(await auth.api.POST('/api/v1/runner/environments/{environment_id}/services/{service_id}/ingress', { params: { header, path: { ...path, service_id: selected.serviceId } }, body: { source_instance_id: source.value, expires_at: expiresAt, password: secret } }))
    } else {
      const values = publicSources()
      if (!values) { fail('请填写 1–32 个明确的 IPv4 公网来源 CIDR；不接受私网、无限来源或 IPv6'); return }
      effect = async () => dataOf(await auth.api.POST('/api/v1/runner/environments/{environment_id}/services/{service_id}/publish', { params: { header, path: { ...path, service_id: selected.serviceId } }, body: { source_cidrs: values, expires_at: expiresAt, password: secret } }))
    }
  } else if (selected.kind === 'service') {
    effect = async () => dataOf(await auth.api.POST('/api/v1/runner/environments/{environment_id}/services/{service_id}/revoke', { params: { header, path: { ...path, service_id: selected.recordId } }, body: { password: secret } }))
  } else if (selected.kind === 'rule') {
    effect = async () => dataOf(await auth.api.POST('/api/v1/runner/environments/{environment_id}/ingress/{rule_id}/revoke', { params: { header, path: { ...path, rule_id: selected.recordId } }, body: { password: secret } }))
  } else if (selected.kind === 'endpoint') {
    effect = async () => dataOf(await auth.api.POST('/api/v1/runner/environments/{environment_id}/published-endpoints/{endpoint_id}/revoke', { params: { header, path: { ...path, endpoint_id: selected.recordId } }, body: { password: secret } }))
  } else {
    return
  }
  if (await act(effect) && active.value && selected.environmentId === props.environmentId) {
    notice.value = selected.kind === 'ingress' || selected.kind === 'publish' || selected.kind.startsWith('edit_') ? '访问规则已保存；尚未确认生效，请以节点回执为准。' : '撤销请求已保存；节点确认前不能认定现有连接已断开。'
    clearAction()
  }
}
</script>

<template>
  <section v-if="active" class="cs-block" aria-label="容器网络与服务">
    <h3>网络与服务</h3>
    <p class="form-help">同一工作空间和同一智能体的受管容器共用私网，验证生效后可跨服务器直接使用 TCP / UDP 互通。跨智能体需来源授权及目标允许；公网入口默认关闭。</p>
    <p v-if="state.isPending.value" role="status">正在获取节点网络状态…</p>
    <div v-if="state.error.value" role="alert"><p>{{ networkError(state.error.value) }}</p><p v-if="data">显示上次读取的规则，当前生效情况待确认。</p><button class="secondary-button" type="button" @click="state.refetch()">重试网络状态</button></div>
    <template v-if="data">
      <p v-if="data.runtime_status === 'unsupported'" role="status">当前容器使用兼容运行后端，受管跨节点网络尚不可用。</p>
      <p v-else-if="data.runtime_status === 'active' && fresh" role="status">内部服务已收到节点生效回执。</p>
      <p v-else role="status">规则保存与节点生效分别记录，当前网络尚待确认。</p>
      <p class="form-help">所属智能体：{{ data.owner_instance_id ?? '原智能体已删除' }} · 容器服务器：{{ data.host_id }}</p>
      <p v-if="data.runtime_ip && fresh" class="form-help">当前私网运行地址：<code>{{ data.runtime_ip }}</code>（重建后可能变化；可用性以服务回执为准）</p>
      <h4>内部服务</h4>
      <ul v-if="data.services.length" class="cs-runs">
        <li v-for="service in data.services" :key="service.id">
          <strong>{{ service.name }}</strong> · {{ service.protocol }} / {{ service.port }} · {{ status(service) }}
          <p v-if="usable(service)"><code>{{ service.connect_host }}:{{ service.connect_port }}</code></p>
          <p v-else class="form-help">{{ service.desired_dns ? `期望内部地址：${service.desired_dns}` : '等待分配内部地址' }}；尚未确认可连接。</p>
          <p class="form-help">期望修订 {{ service.desired_revision }} / 已应用 {{ service.applied_revision }} · 分配代次 {{ service.desired_generation }} / {{ service.applied_generation ?? '尚未应用' }}</p>
          <p v-if="service.apply_error" class="form-help">{{ applyMessage(service) }}</p>
          <p v-if="service.protocol === 'UDP'" class="form-help">跨智能体和公网网关仅支持 TCP；UDP 仅用于同域私网。</p>
          <div v-if="auth.isOwner" class="runner-filters">
            <button v-if="canCreate && revocable(service)" type="button" class="secondary-button" :disabled="busy" @click="openEdit('edit_service', service, service.name)">编辑服务 {{ service.name }}</button>
            <button v-if="canCreate && service.protocol === 'TCP' && revocable(service)" type="button" class="secondary-button" :disabled="busy" @click="openAction({ kind: 'ingress', environmentId, serviceId: service.id, label: service.name })">允许来源访问 {{ service.name }}</button>
            <button v-if="canCreate && service.protocol === 'TCP' && revocable(service)" type="button" class="secondary-button" :disabled="busy" @click="openAction({ kind: 'publish', environmentId, serviceId: service.id, label: service.name })">申请公网入口 {{ service.name }}</button>
            <button v-if="revocable(service)" type="button" class="secondary-button im-danger" :disabled="busy" @click="openAction({ kind: 'service', environmentId, recordId: service.id, label: service.name })">撤销服务 {{ service.name }}</button>
          </div>
        </li>
      </ul>
      <p v-else class="form-help">尚未登记内部服务。</p>
      <form v-if="canCreate" class="runner-filters" aria-label="登记内部服务" @submit.prevent="register">
        <label class="form-field"><span>服务名称</span><input v-model="name" pattern="[a-z][a-z0-9-]{0,39}" maxlength="40" placeholder="例如 database" required :disabled="busy" /></label>
        <label class="form-field"><span>服务端口</span><input v-model="port" type="number" min="1" max="65535" required :disabled="busy" /></label>
        <label class="form-field"><span>协议</span><select v-model="protocol" :disabled="busy"><option>TCP</option><option>UDP</option></select></label>
        <button class="secondary-button" type="submit" :disabled="busy">登记内部服务</button>
      </form>
      <h4>跨智能体入站</h4>
      <p class="form-help">仅支持 TCP 网关连接。来源需要已生效的跨智能体通信权限，后端会重新校验；目标只开放这里允许的服务。规则保存不代表网络已开通。</p>
      <ul v-if="data.ingress_rules.length" class="cs-runs">
        <li v-for="rule in data.ingress_rules" :key="rule.id">
          <p>来源智能体：{{ sourceName(rule.source_instance_id) }}（{{ rule.source_instance_id }}） → {{ serviceName(rule.service_id) }} · {{ status(rule) }}</p>
          <p class="form-help">到期：{{ formatTime(rule.expires_at) }} · 期望修订 {{ rule.desired_revision }} / 已应用 {{ rule.applied_revision }} · 分配代次 {{ rule.desired_generation }} / {{ rule.applied_generation ?? '尚未应用' }}</p>
          <p v-if="usable(rule)"><code>{{ rule.connect_host }}:{{ rule.connect_port }}</code></p>
          <p v-if="rule.apply_error" class="form-help">{{ applyMessage(rule) }}</p>
          <button v-if="canCreate && revocable(rule)" type="button" class="secondary-button" :disabled="busy" @click="openEdit('edit_rule', rule, `来源 ${sourceName(rule.source_instance_id)} → ${serviceName(rule.service_id)}`)">编辑入站规则</button>
          <button v-if="revocable(rule)" type="button" class="secondary-button im-danger" :disabled="busy" @click="openAction({ kind: 'rule', environmentId, recordId: rule.id, label: `来源 ${rule.source_instance_id} → ${serviceName(rule.service_id)}` })">撤销入站规则</button>
        </li>
      </ul>
      <p v-else class="form-help">没有跨智能体入站规则。</p>
      <h4>受限公网入口</h4>
      <p class="form-help">仅支持 TLS 保护的 TCP 网关连接，只接受明确允许的 IPv4 来源。平台验证网关身份、来源限制和撤销后才会开放；未确认的入口不可连接。</p>
      <ul v-if="data.published_endpoints.length" class="cs-runs">
        <li v-for="endpoint in data.published_endpoints" :key="endpoint.id">
          <p>{{ serviceName(endpoint.service_id) }} · {{ status(endpoint) }} · 允许来源：{{ endpoint.source_cidrs.join('、') }}</p>
          <p class="form-help">到期：{{ formatTime(endpoint.expires_at) }} · 期望修订 {{ endpoint.desired_revision }} / 已应用 {{ endpoint.applied_revision }} · 分配代次 {{ endpoint.desired_generation }} / {{ endpoint.applied_generation ?? '尚未应用' }}</p>
          <p v-if="usable(endpoint)"><code>{{ endpoint.connect_host }}:{{ endpoint.connect_port }}</code></p>
          <p v-if="endpoint.apply_error" class="form-help">{{ applyMessage(endpoint) }}</p>
          <button v-if="canCreate && revocable(endpoint)" type="button" class="secondary-button" :disabled="busy" @click="openEdit('edit_endpoint', endpoint, `公网入口 ${serviceName(endpoint.service_id)}`)">编辑公网入口</button>
          <button v-if="revocable(endpoint)" type="button" class="secondary-button im-danger" :disabled="busy" @click="openAction({ kind: 'endpoint', environmentId, recordId: endpoint.id, label: `公网入口 ${serviceName(endpoint.service_id)}` })">撤销公网入口</button>
        </li>
      </ul>
      <p v-else class="form-help">没有公网入口。</p>
      <form v-if="action && auth.isOwner" class="runner-filters" :aria-label="formLabel" @submit.prevent="submitAction">
        <p>{{ formLabel }}：{{ action.label }}</p>
        <p v-if="editing && 'revision' in action" class="form-help">本次编辑基于期望修订 {{ action.revision }}；保存时校验版本。</p>
        <template v-if="action.kind === 'edit_service'">
          <label class="form-field"><span>服务名称</span><input v-model="editName" pattern="[a-z][a-z0-9-]{0,39}" maxlength="40" required :disabled="busy || !fresh" /></label>
          <label class="form-field"><span>服务端口</span><input v-model="editPort" type="number" min="1" max="65535" required :disabled="busy || !fresh" /></label>
          <label class="form-field"><span>协议</span><select v-model="editProtocol" :disabled="busy || !fresh"><option>TCP</option><option>UDP</option></select></label>
          <p class="form-help">服务变更需重新等待节点应用；已有授权的目标也可能改变。跨智能体及公网仅支持 TCP。</p>
        </template>
        <template v-if="action.kind === 'ingress'">
          <label class="form-field"><span>来源智能体</span><select v-model="source" :disabled="busy || !fresh || !!instances.error.value || instances.isFetching.value" required><option value="">请选择来源智能体</option><option v-for="item in sources" :key="item.id" :value="item.id">{{ item.name }} · {{ item.id }}</option></select></label>
          <p v-if="instances.isPending.value" role="status">正在读取来源智能体…</p>
          <p v-if="instances.error.value" role="alert">来源列表读取失败，刷新后再选择。<button type="button" class="secondary-button" @click="instances.refetch()">重试来源列表</button></p>
          <button v-if="instances.hasNextPage.value" type="button" class="secondary-button" :disabled="busy || instances.isFetching.value" @click="instances.fetchNextPage()">加载更多来源</button>
        </template>
        <label v-if="publicAction" class="form-field"><span>允许的 IPv4 公网来源</span><textarea v-model="cidrs" rows="3" maxlength="2048" placeholder="每行一个 CIDR，如 198.51.100.0/24" required :disabled="busy || !fresh" /></label>
        <label v-if="leaseAction" class="form-field"><span>有效期至</span><input v-model="expires" type="datetime-local" required :disabled="busy || !fresh" /></label>
        <p v-if="leaseAction" class="form-help">有效期最长 30 天；保存后等待执行层确认。</p>
        <p v-else-if="!editing" class="form-help">撤销后停止接受新连接；已有连接是否断开仍需节点回执确认。</p>
        <label class="form-field"><span>确认登录密码</span><input v-model="password" type="password" autocomplete="current-password" required :disabled="busy || !fresh" /></label>
        <button class="secondary-button" type="submit" :disabled="busy || !fresh || !password || (action.kind === 'ingress' && !sourceReady)">{{ submitLabel }}</button>
        <button class="secondary-button" type="button" :disabled="busy" @click="clearAction">取消</button>
      </form>
    </template>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="visibleError" role="alert">{{ visibleError }}</p>
  </section>
</template>
