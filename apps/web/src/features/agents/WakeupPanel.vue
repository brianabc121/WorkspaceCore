<script setup lang="ts">
import { computed, reactive } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'
import { formatTime } from '../time'
import { useAgentAction } from './action'
import { agentError } from './queries'

// 实例卡片里的“唤醒”（T-0121，AGENT_MESSAGING_PLAN 7.2）：到点叫醒智能体跑一轮。
// 智能体自己用 schedule_wakeup 安排的，和管理员在这里建的（可以每天、工作日、每周重复）。
// 接入包每分钟运行 wakeup.mjs 领取到期的唤醒；没配那一行定时任务时一直显示“待执行”。
type Wakeup = Schema['AgentWakeupOut']
const props = defineProps<{ instance: Schema['AgentInstanceOut'] }>()
const auth = useAuthStore(), scope = auth.workspaceId!, user = auth.user?.id
const { busy, error, notice, act, header } = useAgentAction(scope, agentError)
const wakeups = useQuery({
  queryKey: computed(() => ['agents', user, scope, 'wakeups', props.instance.id] as const),
  enabled: computed(() => user === auth.user?.id && scope === auth.workspaceId && auth.isOwner),
  queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/agents/instances/{instance_id}/wakeups', { params: { header, path: { instance_id: props.instance.id } }, signal })),
  refetchInterval: query => query.state.data?.items.some(w => w.status === 'running') ? 10000 : false,
})
const data = computed(() => wakeups.data.value)
const STATUS: Record<Wakeup['status'], string> = { pending: '待执行', running: '执行中', done: '已完成', failed: '失败', cancelled: '已取消' }
const REPEAT: Record<Wakeup['repeat'], string> = { none: '一次', daily: '每天', weekdays: '工作日', weekly: '每周' }
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const form = reactive({ prompt: '', target: '', at: '', repeat: 'none' as Wakeup['repeat'] })
const label = (target: string | null) => target ? (data.value?.targets.find(t => t.target === target)?.label ?? target) : '独立会话'

async function create() {
  if (!form.prompt.trim() || !form.at) { error.value = '填写要它做的事和时间。'; return }
  const runAt = new Date(form.at)
  if (Number.isNaN(runAt.getTime())) { error.value = '时间不对。'; return }
  const ok = await act(async () => {
    dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/wakeups', {
      params: { header, path: { instance_id: props.instance.id } },
      body: { prompt: form.prompt.trim(), target: form.target || null, run_at: runAt.toISOString(), repeat: form.repeat, time_zone: zone },
    }))
  }, '已建唤醒')
  if (ok) { form.prompt = ''; form.at = '' }
}
async function cancel(item: Wakeup) {
  await act(async () => {
    dataOf(await auth.api.POST('/api/v1/agents/instances/{instance_id}/wakeups/{wakeup_id}/cancel', { params: { header, path: { instance_id: props.instance.id, wakeup_id: item.id } } }))
  }, '已取消唤醒')
}
</script>

<template>
  <section class="agent-section" aria-label="唤醒">
    <h3>唤醒</h3>
    <p class="form-help">到点叫醒它跑一轮，把要做的事交给它（例如“工作日 9 点在项目群发昨天的进展”）。它要发消息时自己用 im_send。接入包每分钟领取一次，要在服务器上配好那一行定时任务（SSH 自动部署会自动写入）。</p>
    <p v-if="wakeups.isPending.value" role="status">正在加载…</p>
    <p v-else-if="wakeups.error.value" role="alert">{{ agentError(wakeups.error.value) }}</p>
    <template v-else-if="data">
      <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <ul v-if="data.items.length" class="im-contact-list" aria-label="唤醒列表">
        <li v-for="item in data.items" :key="item.id">
          <span>
            {{ formatTime(item.run_at) }} · {{ REPEAT[item.repeat] }} · {{ label(item.target) }} · <span class="badge">{{ STATUS[item.status] }}</span>
            · {{ item.source === 'agent' ? '它自己安排的' : '管理员建的' }}
            <template v-if="item.last_error"> · <span class="form-help">{{ item.last_error }}</span></template>
            <span class="preserve-text"> · {{ item.prompt }}</span>
          </span>
          <button v-if="item.status === 'pending' || item.status === 'running'" type="button" class="secondary-button" :disabled="busy" :aria-label="`取消唤醒：${item.prompt}`" @click="cancel(item)">取消</button>
        </li>
      </ul>
      <p v-else class="form-help">还没有唤醒。</p>
      <form class="im-group-add" aria-label="新建唤醒" @submit.prevent="create">
        <label class="form-field"><span>要它做的事</span><textarea v-model="form.prompt" rows="2" maxlength="2000"></textarea></label>
        <label class="form-field"><span>时间（{{ zone }}）</span><input v-model="form.at" type="datetime-local" /></label>
        <label class="form-field"><span>重复</span><select v-model="form.repeat"><option value="none">一次</option><option value="daily">每天</option><option value="weekdays">工作日</option><option value="weekly">每周</option></select></label>
        <label class="form-field"><span>在哪个会话里</span><select v-model="form.target"><option value="">独立会话</option><option v-for="t in data.targets" :key="t.target" :value="t.target">{{ t.label }}</option></select></label>
        <button type="submit" class="primary-button" :disabled="busy || !!instance.disabled_at">建唤醒</button>
      </form>
    </template>
  </section>
</template>
