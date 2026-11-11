<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { dataOf, type Schema } from '../../services/api'
import { useAuthStore } from '../../stores/auth'
import { runnerError, useRunnerAction } from './api'

const props = defineProps<{ environmentId: string }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const active = computed(() => auth.workspaceId === scope && auth.user?.id === user && auth.canManage)
const { act, busy, error, header } = useRunnerAction(scope)
const state = useQuery({
  queryKey: computed(() => ['runner', user, scope, 'network', props.environmentId]),
  enabled: active,
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/runner/environments/{environment_id}/network', {
    params: { header, path: { environment_id: props.environmentId } }, signal,
  })),
  refetchInterval: 30_000,
})
const data = computed(() => active.value ? state.data.value : undefined)
const name = ref(''), port = ref(''), protocol = ref<'TCP' | 'UDP'>('TCP')
const states: Record<Schema['RunnerServiceOut']['state'], string> = {
  approved_pending_apply: '等待节点应用', active: '已生效', revoked: '已撤销', expired: '已过期', apply_failed: '节点应用失败',
}
async function register() {
  const number = Number(port.value)
  if (!active.value || !auth.isOwner || !/^[a-z][a-z0-9-]{0,39}$/.test(name.value) || !Number.isInteger(number) || number < 1 || number > 65535) {
    error.value = '请输入有效的服务名称和 1–65535 的端口'; return
  }
  if (await act(async () => dataOf(await auth.api.POST('/api/v1/runner/environments/{environment_id}/services', {
    params: { header, path: { environment_id: props.environmentId } },
    body: { name: name.value, port: number, protocol: protocol.value },
  })))) { name.value = ''; port.value = ''; await state.refetch() }
}
</script>

<template>
  <section class="cs-block" aria-label="容器网络与服务">
    <h3>网络与服务</h3>
    <p class="form-help">同一智能体的受管容器可跨服务器互通；跨智能体仍需来源授权及目标允许。公网入口默认关闭。</p>
    <p v-if="state.isPending.value" role="status">正在获取节点网络状态…</p>
    <div v-if="state.error.value" role="alert"><p>{{ runnerError(state.error.value) }}</p><p v-if="data">显示上次读取的规则，当前生效情况待确认。</p><button class="secondary-button" type="button" @click="state.refetch()">重试网络状态</button></div>
    <template v-if="data">
      <p v-if="data.runtime_status === 'unsupported'" role="status">当前容器使用兼容运行后端，受管跨节点网络尚不可用。</p>
      <p v-else-if="data.runtime_status === 'active'" role="status">内部服务已收到节点生效回执。</p>
      <p v-else role="status">规则保存与节点生效分别记录，当前网络尚待确认。</p>
      <ul v-if="data.services.length" class="cs-runs">
        <li v-for="service in data.services" :key="service.id">
          <strong>{{ service.name }}</strong> · {{ service.protocol }} / {{ service.port }} · {{ states[service.state] }}
          <p v-if="service.accepting_new_connections && service.connect_host"><code>{{ service.connect_host }}:{{ service.connect_port }}</code></p>
          <p v-else class="form-help">{{ service.desired_dns ? `期望内部地址：${service.desired_dns}` : '等待分配内部地址' }}；尚未确认可连接。</p>
          <p class="form-help">期望修订 {{ service.desired_revision }} / 已应用 {{ service.applied_revision }}</p>
          <p v-if="service.apply_error" class="form-help">节点尚未确认当前规则，请检查容器服务器的网络状态。</p>
        </li>
      </ul>
      <p v-else class="form-help">尚未登记内部服务。</p>
      <form v-if="auth.isOwner && data.runtime_status !== 'unsupported'" class="runner-filters" aria-label="登记内部服务" @submit.prevent="register">
        <label class="form-field"><span>服务名称</span><input v-model="name" pattern="[a-z][a-z0-9-]{0,39}" maxlength="40" placeholder="例如 database" required :disabled="busy" /></label>
        <label class="form-field"><span>服务端口</span><input v-model="port" type="number" min="1" max="65535" required :disabled="busy" /></label>
        <label class="form-field"><span>协议</span><select v-model="protocol" :disabled="busy"><option>TCP</option><option>UDP</option></select></label>
        <button class="secondary-button" type="submit" :disabled="busy">登记内部服务</button>
      </form>
      <h4>跨智能体与公网入口</h4>
      <p class="form-help">只有经过身份校验、来源限制和撤销验证的网关才能启用入口；当前版本不会把待生效记录显示为可连接。</p>
      <p v-for="rule in data.ingress_rules" :key="rule.id">跨智能体入站：{{ states[rule.state] }} · 修订 {{ rule.desired_revision }} / {{ rule.applied_revision }}</p>
      <p v-for="endpoint in data.published_endpoints" :key="endpoint.id">公网入口：{{ states[endpoint.state] }} · 修订 {{ endpoint.desired_revision }} / {{ endpoint.applied_revision }}</p>
      <p v-if="!data.ingress_rules.length && !data.published_endpoints.length" class="form-help">没有跨智能体或公网入口。</p>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>
