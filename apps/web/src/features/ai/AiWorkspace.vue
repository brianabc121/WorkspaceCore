<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import ExecutionItem from './ExecutionItem.vue'
import { RISK_LABELS, STATUS_LABELS, aiError, useExecution, useExecutions, type ExecutionFilter, type ExecutionStatus, type RiskLevel } from './queries'

// Approval center (pending requests) and activity log (every MCP tool call) — OC-1 contract §6.
// `?id=` (notification links, IM cards) brings one record to the top.
const auth = useAuthStore(), route = useRoute(), router = useRouter()
const scope = auth.workspaceId!
const view = computed(() => route.query.view === 'activity' ? 'activity' : 'pending')
const filter = ref<ExecutionFilter>({ status: '', risk_level: '' })
const effective = () => view.value === 'pending' ? { status: 'pending_approval' as const, risk_level: '' as const } : filter.value
const list = useExecutions(scope, effective)
const focusedId = computed(() => typeof route.query.id === 'string' ? route.query.id : null)
const focused = useExecution(scope, () => focusedId.value)
const items = computed(() => [...new Map((list.data.value?.pages ?? []).flatMap(p => p.items).map(e => [e.id, e])).values()].filter(e => e.id !== focusedId.value))
const pendingCount = computed(() => list.data.value?.pages[0]?.pending_count ?? null)
const statuses = Object.entries(STATUS_LABELS) as [ExecutionStatus, string][]
const risks = Object.entries(RISK_LABELS) as [RiskLevel, string][]

function show(next: 'pending' | 'activity') { void router.replace({ query: { ...route.query, view: next === 'activity' ? 'activity' : undefined } }) }
</script>

<template>
  <div class="im-tabs" role="group" aria-label="AI 审批视图">
    <button type="button" class="secondary-button" :aria-pressed="view === 'pending'" @click="show('pending')">待审批<span v-if="pendingCount" class="im-unread">{{ pendingCount }}</span></button>
    <button type="button" class="secondary-button" :aria-pressed="view === 'activity'" @click="show('activity')">活动记录</button>
  </div>
  <p class="form-help">OpenClaw 实例的写操作和高危操作都要在这里批准后才执行，按实例发起时存下的参数执行；在 IM 里回复“同意”不算批准。15 分钟内没人处理会自动拒绝。</p>
  <section v-if="view === 'activity'" class="panel ai-filters" aria-label="筛选">
    <label class="form-field"><span>状态</span><select v-model="filter.status"><option value="">全部</option><option v-for="[value, label] in statuses" :key="value" :value="value">{{ label }}</option></select></label>
    <label class="form-field"><span>类型</span><select v-model="filter.risk_level"><option value="">全部</option><option v-for="[value, label] in risks" :key="value" :value="value">{{ label }}</option></select></label>
  </section>
  <ul class="ai-list" aria-label="工具调用记录">
    <ExecutionItem v-if="focused.data.value" :key="`focused-${focused.data.value.id}-${focused.data.value.version}`" :scope="scope" :execution="focused.data.value" focused />
    <ExecutionItem v-for="execution in items" :key="`${execution.id}-${execution.version}`" :scope="scope" :execution="execution" />
  </ul>
  <p v-if="focused.error.value" role="alert">{{ aiError(focused.error.value) }}</p>
  <p v-if="list.isPending.value" role="status">正在加载…</p>
  <p v-else-if="list.error.value" role="alert">{{ aiError(list.error.value) }}</p>
  <p v-else-if="!items.length && !focused.data.value" class="empty-state">{{ view === 'pending' ? '没有待审批的请求。' : '还没有工具调用记录。' }}</p>
  <button v-if="list.hasNextPage.value" type="button" class="secondary-button" :disabled="list.isFetchingNextPage.value" @click="list.fetchNextPage()">更多</button>
  <p v-if="!auth.canManage" class="form-help">你只能看到以自己身份执行的记录；审批由 owner 或 admin 处理。</p>
</template>
