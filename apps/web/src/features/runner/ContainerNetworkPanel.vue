<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { dataOf, isAccessError, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { useInstances, unique } from '../agents/queries'
import { formatTime } from '../time'
import { runnerError, useRunnerAction } from './api'

type NetworkRecord = Schema['RunnerServiceOut'] | Schema['RunnerIngressOut'] | Schema['RunnerPublishedEndpointOut']
type Action = { kind: 'ingress' | 'publish'; environmentId: string; serviceId: string; label: string }
  | { kind: 'service' | 'rule' | 'endpoint'; environmentId: string; recordId: string; label: string }
const props = defineProps<{ environmentId: string }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const disposed = ref(false), clock = ref(Date.now())
const clockTimer = setInterval(() => { clock.value = Date.now() }, 1000)
const active = computed(() => !disposed.value && auth.workspaceId === scope && auth.user?.id === user && auth.canManage)
const { act, busy, error, header } = useRunnerAction(scope)
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
const sources = computed(() => active.value && auth.isOwner ? unique(instances.data.value?.pages).filter(i => i.id !== data.value?.owner_instance_id && i.status !== 'disabled') : [])
const name = ref(''), port = ref(''), protocol = ref<'TCP' | 'UDP'>('TCP')
const action = ref<Action | null>(null), password = ref(''), source = ref(''), expires = ref(''), cidrs = ref('')
const errorEnvironment = ref<string | null>(null), notice = ref('')
const visibleError = computed(() => active.value && errorEnvironment.value === props.environmentId ? error.value : '')
const states: Record<NetworkRecord['state'], string> = {
  approved_pending_apply: '等待节点应用', active: '已生效', revoked: '已撤销', expired: '已过期', apply_failed: '节点应用失败',
}
function clearAction() { action.value = null; password.value = ''; source.value = ''; expires.value = ''; cidrs.value = '' }
watch(data, value => { if (!value) { clearAction(); notice.value = '' } }, { flush: 'sync' })
watch([() => props.environmentId, active, () => auth.isOwner], () => {
  clearAction(); name.value = ''; port.value = ''; notice.value = ''; error.value = ''; errorEnvironment.value = null
}, { flush: 'sync' })
onBeforeUnmount(() => { clearInterval(clockTimer); disposed.value = true; clearAction() })
function expired(record: NetworkRecord) { return !!record.expires_at && !(new Date(record.expires_at).getTime() > clock.value) }
function status(record: NetworkRecord) {
  if (expired(record) && !['revoked', 'expired'].includes(record.state)) return '已到期，等待刷新'
  return state.error.value ? `上次状态：${states[record.state]}（待确认）` : states[record.state]
}
function usable(record: NetworkRecord) {
  return fresh.value && !expired(record) && record.state === 'active' && record.desired_revision === record.applied_revision
    && record.desired_generation === record.applied_generation && record.accepting_new_connections && record.connect_host && record.connect_port
}
function revocable(record: NetworkRecord) { return auth.isOwner && fresh.value && !['revoked', 'expired'].includes(record.state) }
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
  if (selected.kind === 'ingress' || selected.kind === 'publish') {
    if (!canCreate.value || !data.value?.services.some(s => s.id === selected.serviceId && !['revoked', 'expired'].includes(s.state))) return
    const expiresAt = lease()
    if (!expiresAt) { fail('有效期必须在未来且不超过 30 天'); return }
    if (selected.kind === 'ingress') {
      if (!sources.value.some(i => i.id === source.value) || instances.error.value) { fail('请选择当前工作空间内可用的来源智能体'); return }
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
    notice.value = selected.kind === 'ingress' || selected.kind === 'publish' ? '访问规则已保存；尚未确认生效，请以节点回执为准。' : '撤销请求已保存；节点确认前不能认定现有连接已断开。'
    clearAction()
  }
}
</script>

<template>
  <section v-if="active" class="cs-block" aria-label="容器网络与服务">
    <h3>网络与服务</h3>
    <p class="form-help">受管私网验证生效后，同一智能体的容器可跨服务器互通。跨智能体需要来源授权及目标允许；公网入口默认关闭。</p>
    <p v-if="state.isPending.value" role="status">正在获取节点网络状态…</p>
    <div v-if="state.error.value" role="alert"><p>{{ runnerError(state.error.value) }}</p><p v-if="data">显示上次读取的规则，当前生效情况待确认。</p><button class="secondary-button" type="button" @click="state.refetch()">重试网络状态</button></div>
    <template v-if="data">
      <p v-if="data.runtime_status === 'unsupported'" role="status">当前容器使用兼容运行后端，受管跨节点网络尚不可用。</p>
      <p v-else-if="data.runtime_status === 'active' && fresh" role="status">内部服务已收到节点生效回执。</p>
      <p v-else role="status">规则保存与节点生效分别记录，当前网络尚待确认。</p>
      <p class="form-help">所属智能体：{{ data.owner_instance_id ?? '原智能体已删除' }} · 容器服务器：{{ data.host_id }}</p>
      <p v-if="data.runtime_ip && fresh" class="form-help">当前运行地址：<code>{{ data.runtime_ip }}</code>（重建后可能变化）</p>
      <h4>内部服务</h4>
      <ul v-if="data.services.length" class="cs-runs">
        <li v-for="service in data.services" :key="service.id">
          <strong>{{ service.name }}</strong> · {{ service.protocol }} / {{ service.port }} · {{ status(service) }}
          <p v-if="usable(service)"><code>{{ service.connect_host }}:{{ service.connect_port }}</code></p>
          <p v-else class="form-help">{{ service.desired_dns ? `期望内部地址：${service.desired_dns}` : '等待分配内部地址' }}；尚未确认可连接。</p>
          <p class="form-help">期望修订 {{ service.desired_revision }} / 已应用 {{ service.applied_revision }} · 分配代次 {{ service.desired_generation }} / {{ service.applied_generation ?? '尚未应用' }}</p>
          <p v-if="service.apply_error" class="form-help">{{ service.apply_error === 'REVOCATION_PENDING_CONFIRMATION' ? '撤销已记录，等待节点确认连接关闭。' : '节点尚未确认当前规则，请检查容器服务器的网络状态。' }}</p>
          <div v-if="auth.isOwner" class="runner-filters">
            <button v-if="canCreate && !['revoked', 'expired'].includes(service.state)" type="button" class="secondary-button" :disabled="busy" @click="openAction({ kind: 'ingress', environmentId, serviceId: service.id, label: service.name })">允许来源访问 {{ service.name }}</button>
            <button v-if="canCreate && !['revoked', 'expired'].includes(service.state)" type="button" class="secondary-button" :disabled="busy" @click="openAction({ kind: 'publish', environmentId, serviceId: service.id, label: service.name })">申请公网入口 {{ service.name }}</button>
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
      <p class="form-help">来源需要已生效的跨智能体通信权限，后端会重新校验；目标只开放这里允许的服务。规则保存不代表网络已开通。</p>
      <ul v-if="data.ingress_rules.length" class="cs-runs">
        <li v-for="rule in data.ingress_rules" :key="rule.id">
          <p>来源智能体：{{ rule.source_instance_id }} → {{ serviceName(rule.service_id) }} · {{ status(rule) }}</p>
          <p class="form-help">到期：{{ formatTime(rule.expires_at) }} · 期望修订 {{ rule.desired_revision }} / 已应用 {{ rule.applied_revision }}</p>
          <p v-if="usable(rule)"><code>{{ rule.connect_host }}:{{ rule.connect_port }}</code></p>
          <p v-if="rule.apply_error === 'REVOCATION_PENDING_CONFIRMATION'" class="form-help">撤销已记录，等待节点确认连接关闭。</p>
          <button v-if="revocable(rule)" type="button" class="secondary-button im-danger" :disabled="busy" @click="openAction({ kind: 'rule', environmentId, recordId: rule.id, label: `来源 ${rule.source_instance_id} → ${serviceName(rule.service_id)}` })">撤销入站规则</button>
        </li>
      </ul>
      <p v-else class="form-help">没有跨智能体入站规则。</p>
      <h4>受限公网入口</h4>
      <p class="form-help">只接受明确允许的 IPv4 来源。平台验证网关身份、来源限制和撤销后才会开放；未确认的入口不可连接。</p>
      <ul v-if="data.published_endpoints.length" class="cs-runs">
        <li v-for="endpoint in data.published_endpoints" :key="endpoint.id">
          <p>{{ serviceName(endpoint.service_id) }} · {{ status(endpoint) }} · 允许来源：{{ endpoint.source_cidrs.join('、') }}</p>
          <p class="form-help">到期：{{ formatTime(endpoint.expires_at) }} · 期望修订 {{ endpoint.desired_revision }} / 已应用 {{ endpoint.applied_revision }}</p>
          <p v-if="usable(endpoint)"><code>{{ endpoint.connect_host }}:{{ endpoint.connect_port }}</code></p>
          <p v-if="endpoint.apply_error === 'REVOCATION_PENDING_CONFIRMATION'" class="form-help">撤销已记录，等待节点确认连接关闭。</p>
          <button v-if="revocable(endpoint)" type="button" class="secondary-button im-danger" :disabled="busy" @click="openAction({ kind: 'endpoint', environmentId, recordId: endpoint.id, label: `公网入口 ${serviceName(endpoint.service_id)}` })">撤销公网入口</button>
        </li>
      </ul>
      <p v-else class="form-help">没有公网入口。</p>
      <form v-if="action && auth.isOwner" class="runner-filters" :aria-label="action.kind === 'ingress' ? '允许跨智能体来源' : action.kind === 'publish' ? '申请受限公网入口' : '确认撤销网络规则'" @submit.prevent="submitAction">
        <p>{{ action.kind === 'ingress' ? '允许来源访问' : action.kind === 'publish' ? '申请受限公网入口' : '确认撤销' }}：{{ action.label }}</p>
        <template v-if="action.kind === 'ingress'">
          <label class="form-field"><span>来源智能体</span><select v-model="source" :disabled="busy || !!instances.error.value" required><option value="">请选择来源智能体</option><option v-for="item in sources" :key="item.id" :value="item.id">{{ item.name }} · {{ item.id }}</option></select></label>
          <p v-if="instances.isPending.value" role="status">正在读取来源智能体…</p>
          <p v-if="instances.error.value" role="alert">来源列表读取失败，刷新后再选择。<button type="button" class="secondary-button" @click="instances.refetch()">重试来源列表</button></p>
          <button v-if="instances.hasNextPage.value" type="button" class="secondary-button" :disabled="busy || instances.isFetching.value" @click="instances.fetchNextPage()">加载更多来源</button>
        </template>
        <label v-if="action.kind === 'publish'" class="form-field"><span>允许的 IPv4 公网来源</span><textarea v-model="cidrs" rows="3" maxlength="2048" placeholder="每行一个 CIDR，如 198.51.100.0/24" required :disabled="busy" /></label>
        <label v-if="action.kind === 'publish' || action.kind === 'ingress'" class="form-field"><span>有效期至</span><input v-model="expires" type="datetime-local" required :disabled="busy" /></label>
        <p v-if="action.kind === 'publish' || action.kind === 'ingress'" class="form-help">有效期最长 30 天；保存后等待执行层确认。</p>
        <p v-else class="form-help">撤销后停止接受新连接；已有连接是否断开仍需节点回执确认。</p>
        <label class="form-field"><span>确认登录密码</span><input v-model="password" type="password" autocomplete="current-password" required :disabled="busy" /></label>
        <button class="secondary-button" type="submit" :disabled="busy || !fresh || !password">{{ action.kind === 'ingress' ? '保存来源规则' : action.kind === 'publish' ? '保存公网入口' : '确认撤销' }}</button>
        <button class="secondary-button" type="button" :disabled="busy" @click="clearAction">取消</button>
      </form>
    </template>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="visibleError" role="alert">{{ visibleError }}</p>
  </section>
</template>
