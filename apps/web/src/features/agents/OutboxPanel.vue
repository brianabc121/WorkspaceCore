<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { agentError } from './queries'
import { OUTBOX_KIND, OUTBOX_SOURCE, OUTBOX_STATUS, targetLabel, useAgentOutbox } from './outbox'

// 实例卡片里的“发件记录”（T-0118）：智能体经 im_send 主动发的消息（排队、定时的可以取消）
// 与最近 7 天被拒收的自动回复。正文是智能体写的，按纯文本显示。
const props = defineProps<{ instance: Schema['AgentInstanceOut'] }>()
const auth = useAuthStore(), scope = auth.workspaceId!
const outbox = useAgentOutbox(scope, () => props.instance.id)
const { busy, error, notice, act, header } = useAgentAction(scope, agentError)
const data = computed(() => outbox.data.value)
const cancellable = (status: Schema['ImOutboxItem']['status']) => status === 'queued' || status === 'scheduled'
const counts = computed(() => {
  const items = data.value?.items ?? []
  return { sent: items.filter(i => i.status === 'sent').length, pending: items.filter(i => ['scheduled', 'queued', 'sending', 'unknown'].includes(i.status)).length, rejected: items.filter(i => i.status === 'rejected').length + (data.value?.rejected_replies.length ?? 0) }
})

async function cancel(item: Schema['ImOutboxItem']) {
  await act(async () => {
    dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/outbox/cancel', { params: { header, path: { instance_id: props.instance.id } }, body: { message_ids: [item.message_id] } }))
  }, '已取消')
}
</script>

<template>
  <section class="agent-section outbox-panel" aria-label="发件记录">
    <h3>发件记录</h3>
    <p v-if="outbox.isPending.value" role="status">正在加载…</p>
    <p v-else-if="outbox.error.value" role="alert">{{ agentError(outbox.error.value) }}</p>
    <template v-else-if="data">
      <p class="form-help">智能体用 im_send 主动发的消息与被拒收的自动回复。已发出 {{ counts.sent }} · 待发 {{ counts.pending }} · 被拒收 {{ counts.rejected }}</p>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <ul v-if="data.items.length" class="im-contact-list" aria-label="主动发的消息">
        <li v-for="item in data.items" :key="item.message_id">
          <span>
            {{ formatTime(item.created_at) }} · {{ targetLabel(item.target) }} · {{ OUTBOX_KIND[item.kind] }} · <span class="badge">{{ OUTBOX_STATUS[item.status] }}</span>
            <template v-if="item.source !== 'tool'"> · {{ OUTBOX_SOURCE[item.source] }}</template>
            <template v-if="item.next_attempt_at && item.status !== 'sent'"> · 预计 {{ formatTime(item.next_attempt_at) }}</template>
            <span v-if="item.reason" class="form-help"> · {{ item.reason }}</span>
            <span v-if="item.untrusted.text" class="preserve-text"> · {{ item.untrusted.text }}</span>
          </span>
          <button v-if="cancellable(item.status)" type="button" class="secondary-button" :disabled="busy" @click="cancel(item)">取消</button>
        </li>
      </ul>
      <p v-else class="form-help">还没有主动发过消息。</p>
      <details v-if="data.rejected_replies.length" class="ops-history">
        <summary>被拒收的自动回复（{{ data.rejected_replies.length }}，保留 7 天）</summary>
        <ul class="im-contact-list">
          <li v-for="(reply, index) in data.rejected_replies" :key="index">
            <span>{{ formatTime(reply.created_at) }} · {{ reply.reason }}（{{ reply.code }}）<template v-if="reply.retried"> · 已在限额恢复后补发</template><span v-if="reply.untrusted.text" class="preserve-text"> · {{ reply.untrusted.text }}</span></span>
          </li>
        </ul>
      </details>
    </template>
  </section>
</template>
