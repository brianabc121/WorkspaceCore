<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useId } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { Eye, EyeOff, Info, TriangleAlert } from '@lucide/vue'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf } from '../../services/api'
import { STATUS_LABELS, aiError, executionKey, ownKeys, type Execution } from './queries'
import './approval-center.css'

// Approve / reject one pending request (OC-1 contract §6; 审批中心改版 T-0191): session + CSRF + If-Match (the version
// shown here); critical actions need the current user's workbench password (1–128, write-only, cleared after every
// attempt and when the record changes); rejecting never needs it. Approving runs the stored arguments once; nothing
// typed in IM counts as approval. A lost response is never retried blindly: the same record is read again first.
// Used by the approval detail and by approval cards in IM.
const props = defineProps<{ scope: string; execution: Execution; expired?: boolean }>()
const emit = defineEmits<{ notice: [text: string] }>()
const auth = useAuthStore(), cache = useQueryClient(), user = auth.user?.id
const password = ref(''), reveal = ref(false), reason = ref(''), rejecting = ref(false)
const busy = ref(false), checking = ref(false), error = ref('')
const uid = useId()
const critical = computed(() => props.execution.risk_level === 'critical')
const blocked = computed(() => busy.value || checking.value || !!props.expired)
const active = () => auth.user?.id === user && auth.workspaceId === props.scope
const refresh = () => cache.invalidateQueries({ predicate: ownKeys(user, props.scope) })
// 状态变了（别处已处理、版本变了、失权）：重新读取，不重复执行
const REFRESH_CODES = ['VERSION_CONFLICT', 'EXECUTION_NOT_PENDING', 'FORBIDDEN', 'FORBIDDEN_ROLE', 'NOT_FOUND']
onBeforeUnmount(() => { password.value = '' })

function clearSecret() { password.value = ''; reveal.value = false }
function say(text: string) { error.value = text; emit('notice', text) }

/** 重新读取同一条记录（只读）。先说明结果再写回缓存：状态一变，这个表单就会卸载 */
async function readAgain() {
  try {
    return dataOf(await auth.api.GET('/api/v1/ai/tool-executions/{execution_id}', { params: { header: { 'X-Workspace-Id': props.scope }, path: { execution_id: props.execution.id } } }))
  } catch {
    return null
  }
}
async function settle(prefix: string) {
  const fresh = await readAgain()
  if (!active()) return
  // 读不到就保持锁定，不让人在结果未知时再批准一次
  checking.value = !fresh
  if (!fresh) say(`${prefix}无法确认结果。请稍后点“重新读取状态”核对，不要重复批准。`)
  else say(fresh.status === 'pending_approval' ? `${prefix}已重新读取：这条请求仍在等待审批，确认后可以再次处理。` : `${prefix}已重新读取：${STATUS_LABELS[fresh.status]}。`)
  if (fresh) cache.setQueryData(executionKey(user, props.scope, fresh.id), fresh)
  await refresh()
}

async function decide(verb: 'approve' | 'reject') {
  if (blocked.value || !active()) return
  error.value = ''
  if (verb === 'approve' && critical.value) {
    if (!password.value) { error.value = '高危操作需要输入你的登录密码。'; return }
    if (password.value.length > 128) { clearSecret(); error.value = '密码最长 128 个字符。'; return }
  }
  if (verb === 'reject' && reason.value.trim().length > 500) { error.value = '拒绝原因最多 500 个字符。'; return }
  const secret = password.value
  clearSecret()
  busy.value = true
  const params = { header: { 'X-Workspace-Id': props.scope, 'If-Match': `"${props.execution.version}"` }, path: { execution_id: props.execution.id } }
  try {
    const result = verb === 'approve'
      ? dataOf(await auth.api.POST('/api/v1/ai/tool-executions/{execution_id}/approve', { params, body: critical.value ? { password: secret } : {} }))
      : dataOf(await auth.api.POST('/api/v1/ai/tool-executions/{execution_id}/reject', { params, body: { reason: reason.value.trim() } }))
    if (!active()) return
    // 批准不等于成功：如实写回服务端返回的状态（执行中 / 成功 / 失败 / 结果未知）
    cache.setQueryData(executionKey(user, props.scope, result.id), result)
    await refresh()
  } catch (cause) {
    if (!active()) return
    if (cause instanceof ApiError && (cause.status === 0 || cause.status >= 500)) {
      // 响应丢了或网关出错：请求可能已经执行。先读同一条记录，不盲目重试
      error.value = '连接中断，结果未知；正在重新读取这条请求的状态…'
      await settle('连接中断。')
      return
    }
    say(aiError(cause))
    if (cause instanceof ApiError && REFRESH_CODES.includes(cause.code)) await refresh()
  } finally { busy.value = false }
}

async function recheck() {
  if (busy.value || !active()) return
  busy.value = true
  try { await settle('') } finally { busy.value = false }
}
</script>

<template>
  <form class="ai-actions ac-actions" :aria-label="`处理：${execution.summary}`" @submit.prevent="decide('approve')">
    <template v-if="critical">
      <p class="ac-callout tone-red"><TriangleAlert aria-hidden="true" />高危操作需验证你的工作台登录密码。</p>
      <div class="ac-password-row">
        <label class="form-field ac-password">
          <span>你的登录密码</span>
          <span class="ac-password-box">
            <input v-model="password" :type="reveal ? 'text' : 'password'" autocomplete="current-password" maxlength="128" :disabled="blocked" spellcheck="false" />
            <button type="button" class="ac-reveal" :aria-label="reveal ? '隐藏密码' : '显示密码'" :aria-pressed="reveal" :disabled="blocked" @click="reveal = !reveal"><EyeOff v-if="reveal" aria-hidden="true" /><Eye v-else aria-hidden="true" /></button>
          </span>
        </label>
        <button type="button" class="asv-link ac-reason-toggle" :aria-expanded="rejecting" :disabled="blocked" @click="rejecting = !rejecting">拒绝时填写原因</button>
      </div>
    </template>
    <button v-else type="button" class="asv-link ac-reason-toggle" :aria-expanded="rejecting" :disabled="blocked" @click="rejecting = !rejecting">拒绝时填写原因</button>
    <div v-if="rejecting" class="form-field ac-reason">
      <label :for="`${uid}-reason`">拒绝原因（可选）</label>
      <textarea :id="`${uid}-reason`" v-model="reason" rows="2" maxlength="500" placeholder="例如：不需要" :disabled="blocked" :aria-describedby="`${uid}-reason-help`" />
      <span :id="`${uid}-reason-help`" class="asv-sub">{{ reason.trim().length }} / 500；拒绝不需要密码。</span>
    </div>
    <div class="form-actions ac-buttons">
      <button type="button" class="secondary-button" :disabled="blocked" @click="decide('reject')">拒绝申请</button>
      <button type="submit" class="primary-button" :disabled="blocked">批准并执行</button>
    </div>
    <p v-if="error" role="alert" class="ac-error">{{ error }}</p>
    <button v-if="checking && !busy" type="button" class="secondary-button" @click="recheck">重新读取状态</button>
    <p class="ac-note"><Info aria-hidden="true" />审批只授权这一次操作。</p>
  </form>
</template>
