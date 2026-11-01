<script setup lang="ts">
import { ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError } from '../../services/api'
import { aiError, type Execution } from './queries'

// Approve / reject one pending request (OC-1 contract §6): session + CSRF + If-Match (the version shown here);
// critical actions need the current user's password. Approving runs the stored arguments; nothing typed in IM
// counts as approval. Used by the approval center and by approval cards in IM.
const props = defineProps<{ scope: string; execution: Execution }>()
const auth = useAuthStore(), cache = useQueryClient(), user = auth.user?.id
const password = ref(''), reason = ref(''), busy = ref(false), error = ref(''), rejecting = ref(false)
const critical = () => props.execution.risk_level === 'critical'
const active = () => auth.user?.id === user && auth.workspaceId === props.scope
const refresh = () => cache.invalidateQueries({ predicate: q => ['ai-tool-executions', 'im'].includes(String(q.queryKey[0])) && q.queryKey[1] === user && q.queryKey[2] === props.scope })

async function decide(verb: 'approve' | 'reject') {
  if (busy.value || !active()) return
  if (verb === 'approve' && critical() && !password.value) { error.value = '高危操作需要输入你的登录密码。'; return }
  busy.value = true; error.value = ''
  const params = { header: { 'X-Workspace-Id': props.scope, 'If-Match': `"${props.execution.version}"` }, path: { execution_id: props.execution.id } }
  try {
    if (verb === 'approve') await auth.api.POST('/api/v1/ai/tool-executions/{execution_id}/approve', { params, body: critical() ? { password: password.value } : {} })
    else await auth.api.POST('/api/v1/ai/tool-executions/{execution_id}/reject', { params, body: { reason: reason.value.trim() } })
    password.value = ''
    if (active()) await refresh()
  } catch (cause) {
    if (!active()) return
    error.value = aiError(cause)
    // The record changed (or was decided elsewhere): show its current state.
    if (cause instanceof ApiError && ['VERSION_CONFLICT', 'EXECUTION_NOT_PENDING'].includes(cause.code)) await refresh()
  } finally { busy.value = false }
}
</script>

<template>
  <form class="ai-actions" :aria-label="`处理：${execution.summary}`" @submit.prevent="decide('approve')">
    <label v-if="critical()" class="form-field"><span>高危操作，请输入你的登录密码</span><input v-model="password" type="password" autocomplete="current-password" maxlength="256" /></label>
    <label v-if="rejecting" class="form-field"><span>拒绝原因（可选）</span><input v-model="reason" maxlength="500" placeholder="例如：不需要" /></label>
    <div class="form-actions">
      <button type="submit" class="primary-button" :disabled="busy">批准并执行</button>
      <button v-if="!rejecting" type="button" class="secondary-button" :disabled="busy" @click="rejecting = true">拒绝…</button>
      <button v-else type="button" class="secondary-button" :disabled="busy" @click="decide('reject')">确认拒绝</button>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
  </form>
</template>
