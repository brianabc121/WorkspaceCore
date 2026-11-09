<script setup lang="ts">
import { computed } from 'vue'
import { formatTime } from '../time'
import { SEND_STATUS, mailError, useMailSends } from './api'

// 发件记录（T-0130）：人和智能体经工作台发的信（不含正文）；“结果未知”高亮，不自动重发。
const props = defineProps<{ scope: string }>()
const sends = useMailSends(props.scope)
const items = computed(() => sends.data.value?.items ?? [])
</script>

<template>
  <section class="ws-panel ws-panel-body" aria-label="发件记录">
    <p class="form-help">经工作台发的信（人和智能体），不含正文。“结果未知”是 Gmail 没有及时响应：不会自动重发，请到 Gmail 的“已发送”里确认。</p>
    <p v-if="sends.isPending.value" role="status">正在加载…</p>
    <p v-else-if="sends.error.value" role="alert">{{ mailError(sends.error.value) }}</p>
    <p v-else-if="!items.length" class="empty-state">还没有发件记录。</p>
    <div v-else class="ws-table-wrap">
      <table class="ws-table mail-sends">
        <thead><tr><th>时间</th><th>谁发的</th><th>发信邮箱</th><th>收件人</th><th>主题</th><th>结果</th></tr></thead>
        <tbody>
          <tr v-for="s in items" :key="s.id" :class="{ 'mail-unknown': s.status === 'unknown' }">
            <td>{{ formatTime(s.created_at) }}</td>
            <td>{{ s.actor_kind === 'instance' ? `智能体「${s.actor_name}」` : s.actor_name }}</td>
            <td>{{ s.sender }}</td>
            <td>{{ [...s.to, ...s.cc].join('、') }}<template v-if="s.bcc_count">（密送 {{ s.bcc_count }}）</template></td>
            <td>{{ s.subject }}</td>
            <td>{{ SEND_STATUS[s.status] }}<template v-if="s.error_code">（{{ s.error_code }}）</template></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
