<script setup lang="ts">
import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { agentError } from './queries'

// 实例卡片里的“终止正在执行的任务”（T-0126，AGENT_MESSAGING_PLAN 7.3）：智能体深陷在一件事里时叫停它。
// 立即生效的是软停止（5 分钟内它调用工作台工具都被拒绝）；接入包每分钟运行 stop-runs.mjs，
// 在它的 OpenClaw 里中止正在运行的会话并报回结果。没配那一行定时任务时终止令 10 分钟后记为过期。
type Stop = Schema['AgentRunStopOut']
const props = defineProps<{ instance: Schema['AgentInstanceOut']; names: Map<string, string> }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const { busy, error, notice, act, header } = useAgentAction(scope, agentError)
const stops = useQuery({
  queryKey: computed(() => ['agents', user, scope, 'stops', props.instance.id] as const),
  enabled: computed(() => user === auth.user?.id && scope === auth.workspaceId && auth.canManage),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/agents/instances/{instance_id}/stops', { params: { header, path: { instance_id: props.instance.id } }, signal })),
  refetchInterval: query => query.state.data?.items.some(s => s.status === 'pending' || s.status === 'running') ? 10000 : false,
})
const items = computed(() => stops.data.value?.items ?? [])
const STATUS: Record<Stop['status'], string> = { pending: '等实例领取', running: '实例正在中止', done: '已中止', failed: '失败', expired: '过期（实例没领取）' }
const reason = ref('')
const by = (s: Stop) => s.requested_by_instance ? `智能体「${props.names.get(s.requested_by_instance) ?? '已删除的实例'}」` : '管理员'

async function stop() {
  const text = reason.value.trim()
  if (!text) { error.value = '写一句叫停的原因，它会看到。'; return }
  if (!window.confirm(`叫停「${props.instance.name}」正在执行的任务？接下来 5 分钟它调用工作台工具都会被拒绝，1 分钟内它的 OpenClaw 会中止正在运行的会话。`)) return
  const ok = await act(async () => {
    dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/stop', { params: { header, path: { instance_id: props.instance.id } }, body: { reason: text } }))
  }, '已叫停：它接下来调用工作台工具会被拒绝，1 分钟内中止正在运行的会话')
  if (ok) reason.value = ''
}
</script>

<template>
  <section class="agent-section" aria-label="终止正在执行的任务">
    <h3>终止正在执行的任务</h3>
    <p class="form-help">它在一件事上反复调用工具停不下来时用：立即生效，接下来 5 分钟它调用工作台工具都会被拒绝并看到你写的原因；接入包每分钟领取一次终止令，在它的 OpenClaw 里中止正在运行和排队的会话（要配好那一行定时任务，SSH 自动部署会自动写入）。任务不会被收回。</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <form class="im-group-add" aria-label="叫停" @submit.prevent="stop">
      <label class="form-field"><span>原因（它会看到）</span><input v-model="reason" maxlength="500" placeholder="例如：同一个查询已经调了 40 次" /></label>
      <button type="submit" class="secondary-button im-danger" :disabled="busy || !!instance.disabled_at">叫停</button>
    </form>
    <p v-if="stops.isPending.value" role="status">正在加载…</p>
    <p v-else-if="stops.error.value" role="alert">{{ agentError(stops.error.value) }}</p>
    <ul v-else-if="items.length" class="im-contact-list" aria-label="最近的终止记录">
      <li v-for="item in items" :key="item.id">
        <span>
          {{ formatTime(item.created_at) }} · {{ by(item) }} · <span class="badge">{{ STATUS[item.status] }}</span>
          <template v-if="item.status === 'done' && item.aborted !== null"> · 中止了 {{ item.aborted }} 个会话</template>
          <template v-if="item.error"> · <span class="form-help">{{ item.error }}</span></template>
          <span class="preserve-text"> · {{ item.reason }}</span>
        </span>
      </li>
    </ul>
  </section>
</template>
