<script setup lang="ts">
import { useAuthStore } from '../../stores/auth'
import ApprovalActions from './ApprovalActions.vue'
import { RISK_LABELS, STATUS_LABELS, aiError, useExecution } from './queries'

// Approval card in IM (sent by the workspace notification account). The card text is only a hint; the current
// state and the decision come from /ai/tool-executions, so replying "同意" in the chat never approves anything.
const props = defineProps<{ scope: string; executionId: string; text: string | null }>()
const auth = useAuthStore()
const execution = useExecution(props.scope, () => props.executionId)
</script>

<template>
  <div class="ai-card">
    <template v-if="execution.data.value">
      <p class="ai-card-heading">
        <strong class="preserve-text">{{ execution.data.value.summary }}</strong>
        <span class="badge" :class="`ai-risk-${execution.data.value.risk_level}`">{{ RISK_LABELS[execution.data.value.risk_level] }}</span>
        <span class="badge" :class="`ai-status-${execution.data.value.status}`">{{ STATUS_LABELS[execution.data.value.status] }}</span>
      </p>
      <p class="form-help">实例 {{ execution.data.value.instance_name }}<template v-if="execution.data.value.source.triggered_by_name"> · 由 {{ execution.data.value.source.triggered_by_name }} 触发</template> · <RouterLink :to="{ path: '/ai', query: { id: execution.data.value.id } }">在审批中心查看参数</RouterLink></p>
      <ApprovalActions v-if="execution.data.value.status === 'pending_approval' && auth.canManage" :key="execution.data.value.version" :scope="scope" :execution="execution.data.value" />
    </template>
    <template v-else>
      <p v-if="text" class="preserve-text">{{ text }}</p>
      <p v-if="execution.error.value" class="note-text">{{ aiError(execution.error.value) }}</p>
      <p v-else role="status" class="note-text">正在加载审批状态…</p>
    </template>
  </div>
</template>
