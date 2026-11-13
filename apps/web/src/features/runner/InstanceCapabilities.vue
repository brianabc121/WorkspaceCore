<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { runnerError, useRunnerAction } from './api'

const props = defineProps<{ instanceId: string; enabled: boolean }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const { busy, error, act, header } = useRunnerAction(scope)
const capabilities = useQuery({
  queryKey: computed(() => ['runner', user, scope, 'capabilities', props.instanceId]),
  enabled: computed(() => auth.user?.id === user && auth.workspaceId === scope && auth.canManage),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/agents/instances/{instance_id}/runner/capabilities', { params: { header, path: { instance_id: props.instanceId } }, signal })),
  refetchInterval: 30_000,
})
const data = computed(() => capabilities.data.value)
const names = { 'runner.unlimited_container_count': '免数量上限', 'runner.cross_agent_communication': '跨智能体通信权限' } as const
const states = { approved_pending_apply: '已批准，等待生效', active: '已生效', revoked: '已撤销', expired: '已过期', apply_failed: '应用失败' } as const
const capability = ref<Schema['RunnerCapabilityRequestInput']['capability']>('runner.unlimited_container_count')
const reason = ref(''), expires = ref(''), pending = ref<string | null>(null)
const revoking = ref<Schema['RunnerCapabilityGrantOut'] | null>(null), revokeReason = ref(''), password = ref('')
async function request() {
  if (!reason.value.trim() || !props.enabled) return
  let expiresAt: string | null = null
  if (expires.value) {
    const date = new Date(expires.value)
    if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) { error.value = '有效期必须是未来时间'; return }
    expiresAt = date.toISOString()
  }
  await act(async () => {
    const out = dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/runner/capabilities/request', { params: { header, path: { instance_id: props.instanceId } }, body: { capability: capability.value, reason: reason.value.trim(), expires_at: expiresAt } }))
    pending.value = out.action_id
  })
}
async function revoke() {
  if (!revoking.value || !revokeReason.value.trim() || !password.value) return
  const grantId = revoking.value.id, secret = password.value
  password.value = ''
  if (await act(async () => dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/runner/capabilities/{grant_id}/revoke', { params: { header, path: { instance_id: props.instanceId, grant_id: grantId } }, body: { reason: revokeReason.value.trim(), password: secret } })))) {
    revoking.value = null; revokeReason.value = ''
  }
}
</script>

<template>
  <section class="agent-section" aria-label="容器数量与高级权限">
    <h3>容器数量与高级权限</h3>
    <p v-if="capabilities.isPending.value" role="status">正在加载配额与授权…</p>
    <p v-if="capabilities.error.value" role="alert">{{ runnerError(capabilities.error.value) }}</p>
    <template v-if="data">
      <p>保有环境：{{ data.held_count }} / {{ data.unlimited_active ? '免数量上限' : data.default_limit }}</p>
      <p v-if="!data.unlimited_active && data.held_count > data.default_limit" class="feedback">已超额，阻止新增；现有环境可以继续停止、清理，不会自动删除数据。</p>
      <p class="form-help">停止、未知、待删除的环境仍占名额；数量豁免不提高 CPU、内存、磁盘预算。跨智能体通信仍需目标允许，并遵守工作空间与平台边界。</p>
      <ul class="im-contact-list">
        <li v-for="grant in data.grants" :key="grant.id">
          <strong>{{ names[grant.capability] }}</strong>：{{ states[grant.state] }}
          <p class="form-help">{{ grant.reason }} · {{ grant.expires_at ? `到期 ${formatTime(grant.expires_at)}` : '持续到撤销' }} · 期望修订 {{ grant.desired_revision }} / 已应用 {{ grant.applied_revision }}</p>
          <RouterLink :to="{ path: '/approvals', query: { id: grant.approval_id } }">查看授权审批</RouterLink>
          <button v-if="auth.isOwner && !['revoked', 'expired'].includes(grant.state)" type="button" class="secondary-button" :disabled="busy" @click="revoking = grant; password = ''; revokeReason = ''">撤销{{ names[grant.capability] }}</button>
        </li>
      </ul>
      <p v-if="!data.grants.length" class="form-help">尚未批准高级权限。</p>
      <p v-for="approval in data.pending_approval_ids" :key="approval"><RouterLink :to="{ path: '/approvals', query: { id: approval } }">查看待审批权限申请</RouterLink></p>
      <p v-if="pending" role="status">已提交，等待 owner 审批；批准后还需策略生效。<RouterLink :to="{ path: '/approvals', query: { id: pending } }">查看本次审批</RouterLink></p>
      <form class="runner-filters" aria-label="申请容器高级权限" @submit.prevent="request">
        <label class="form-field"><span>申请能力</span><select v-model="capability" :disabled="busy"><option v-for="(name, key) in names" :key="key" :value="key">{{ name }}</option></select></label>
        <label class="form-field"><span>申请原因</span><input v-model="reason" maxlength="2000" required :disabled="busy" /></label>
        <label class="form-field"><span>有效期（可选）</span><input v-model="expires" type="datetime-local" :disabled="busy" /></label>
        <button type="submit" class="secondary-button" :disabled="busy || !enabled || !reason.trim()">申请权限</button>
      </form>
      <p v-if="!enabled" class="form-help">先打开运行环境权限后才能申请高级能力。</p>
      <form v-if="revoking" class="runner-filters" aria-label="撤销高级权限" @submit.prevent="revoke">
        <p>撤销{{ names[revoking.capability] }}；已超额的环境保留，后续新增重新检查配额。</p>
        <label class="form-field"><span>撤销原因</span><input v-model="revokeReason" required maxlength="2000" /></label>
        <label class="form-field"><span>确认登录密码</span><input v-model="password" type="password" autocomplete="current-password" required /></label>
        <button type="submit" class="secondary-button im-danger" :disabled="busy || !revokeReason.trim() || !password">确认撤销</button>
        <button type="button" class="secondary-button" :disabled="busy" @click="revoking = null; password = ''">取消</button>
      </form>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>
