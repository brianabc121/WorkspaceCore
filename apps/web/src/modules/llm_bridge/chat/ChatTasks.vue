<script setup lang="ts">
import { computed, ref, toRef, watch } from 'vue'
import { Activity, AlarmClock, ChevronDown, CircleStop, FileText, Terminal, X } from '@lucide/vue'
import { formatTime } from '../../../features/time'
import { chatError, duration, liveTasks, type ChatJob, type ChatTimer, type LlmChatApi } from './chatApi'

// 后台任务（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 5.9；样子参考 Claude App 的“后台任务”）：
// - 正在跑的回合在做什么：命令、跑了多久、多久没动静、输出末尾、官方程序报的后台任务；
// - 后台作业：运行中的可以停、能看日志；结束后默认自动给会话发消息、模型接着做；
// - 预约：到点给会话发一条消息（人也能在这里预约），没发出的可以取消；
// - 自动接着做（循环）：到了上限暂停时标红，可以继续或停止。
// 作业由会话里的模型（llm-task 命令）、智能体或人开；这里不开作业，只看和停。
const props = defineProps<{ chat: LlmChatApi; bridgeId: string; sessionId: string; busy: boolean; active: boolean; canWrite: boolean }>()
const emit = defineEmits<{ changed: [] }>()

const query = props.chat.useTasks(toRef(props, 'bridgeId'), toRef(props, 'sessionId'), toRef(props, 'busy'))
const tasks = computed(() => query.data.value ?? null)
const jobs = computed(() => tasks.value?.jobs ?? [])
const timers = computed(() => tasks.value?.timers ?? [])
const running = computed(() => jobs.value.filter(j => j.status === 'running'))
const pending = computed(() => timers.value.filter(t => t.status === 'pending'))
const finishedJobs = computed(() => jobs.value.filter(j => j.status !== 'running'))
const pastTimers = computed(() => timers.value.filter(t => t.status !== 'pending'))
const chain = computed(() => tasks.value?.chain ?? null)
const paused = computed(() => chain.value?.status === 'paused')
const activity = computed(() => (props.busy ? tasks.value?.activity ?? null : null))
const finished = computed(() => finishedJobs.value.length + pastTimers.value.length)
const hasAnything = computed(() => !!activity.value?.current || jobs.value.length > 0 || timers.value.length > 0 || paused.value)

const summary = computed(() => {
  const parts: string[] = []
  if (running.value.length) parts.push(`${running.value.length} 个作业运行中`)
  if (pending.value.length) parts.push(`${pending.value.length} 个预约`)
  if (paused.value) parts.push('自动接着做已暂停')
  if (!parts.length && finished.value) parts.push(`已完成 ${finished.value}`)
  return parts.join(' · ') || '没有后台任务'
})
// 有在跑的、在等的、暂停了的：默认展开。手机上地方小，只有暂停了（要人决定）才自动展开
const narrow = () => typeof window !== 'undefined' && !!window.matchMedia?.('(max-width: 640px)').matches
const open = ref(false)
watch(() => (narrow() ? paused.value : liveTasks(tasks.value) || paused.value), live => { if (live) open.value = true }, { immediate: true })

const working = ref(''), error = ref('')
async function act(id: string, action: () => Promise<unknown>) {
  if (working.value) return
  working.value = id; error.value = ''
  try { await action(); emit('changed') } catch (e) { error.value = chatError(e) } finally { working.value = '' }
}
const stop = (job: ChatJob) => {
  if (!window.confirm(`停掉后台作业「${job.name}」？停掉后不会再自动叫会话接着做。`)) return
  return act(job.id, () => props.chat.stopJob(props.bridgeId, job.id))
}
const cancel = (timer: ChatTimer) => act(timer.id, () => props.chat.cancelTimer(props.bridgeId, timer.id))
const chainAction = (action: 'resume' | 'stop') => {
  if (action === 'stop' && !window.confirm('停止自动接着做？没发出的预约会取消，在跑的作业结束后不再叫会话。')) return
  return act(`chain:${action}`, () => props.chat.chainControl(props.bridgeId, props.sessionId, action))
}

// 日志：一次只看一个作业；运行中的每次点开重新取
const logJob = ref<string | null>(null), logText = ref(''), logLoading = ref(false)
async function toggleLog(job: ChatJob) {
  if (logJob.value === job.id) { logJob.value = null; return }
  logJob.value = job.id; logText.value = ''; logLoading.value = true
  try { logText.value = (await props.chat.jobLog(props.bridgeId, job.id)).text || '（还没有输出）' } catch (e) { logText.value = chatError(e) } finally { logLoading.value = false }
}

// 预约一条消息：几个常用的时间，或者自己选
const WHEN = [
  { id: '1800', label: '30 分钟后' },
  { id: '3600', label: '1 小时后' },
  { id: '10800', label: '3 小时后' },
  { id: '86400', label: '明天这个时候' },
  { id: 'custom', label: '自己选时间' },
] as const
const planning = ref(false), planText = ref(''), planWhen = ref<string>('3600'), planAt = ref('')
async function plan() {
  const text = planText.value.trim()
  if (!text) { error.value = '写一下到时候要发给会话的话。'; return }
  let body: { text: string; at?: number; after_seconds?: number }
  if (planWhen.value === 'custom') {
    const at = new Date(planAt.value).getTime() / 1000
    if (!planAt.value || Number.isNaN(at)) { error.value = '选一个时间。'; return }
    body = { text, at }
  } else body = { text, after_seconds: Number(planWhen.value) }
  await act('plan', async () => {
    await props.chat.createTimer(props.bridgeId, props.sessionId, body)
    planText.value = ''; planning.value = false
  })
}

const when = (ts?: number | null) => (ts ? formatTime(new Date(ts * 1000).toISOString()) : '—')
const quiet = (s?: number | null) => (s == null ? '' : s < 5 ? '刚有输出' : `${duration(s)}没有输出`)
const JOB_TONE: Record<string, string> = { succeeded: 'ok', failed: 'bad', timeout: 'bad', lost: 'bad', stopped: 'muted' }
</script>

<template>
  <section v-if="hasAnything || (active && canWrite)" class="lc-tasks" aria-label="后台任务">
    <button type="button" class="lc-tasks-head" :aria-expanded="open" @click="open = !open">
      <Activity aria-hidden="true" /><b>后台任务</b><span class="lc-tasks-sum" :class="{ 'is-red': paused }">{{ summary }}</span><ChevronDown class="lc-tasks-caret" aria-hidden="true" />
    </button>
    <div v-if="open" class="lc-tasks-body">
      <div v-if="activity?.current" class="lc-activity" role="status" aria-label="正在运行">
        <p class="lc-activity-line">
          <span class="lc-dot is-live" aria-hidden="true" /><b>正在运行</b>
          <code class="lc-activity-cmd">{{ activity.current.text }}</code>
          <span class="lc-sub">已 {{ duration(activity.current.seconds) }}<template v-if="activity.quiet_seconds >= 5"> · {{ duration(activity.quiet_seconds) }}没有动静</template></span>
        </p>
        <details v-if="activity.output_tail" class="lc-activity-tail"><summary>输出末尾</summary><pre>{{ activity.output_tail }}</pre></details>
        <ul v-if="activity.background.length" class="lc-activity-bg" aria-label="官方程序报的后台任务">
          <li v-for="b in activity.background" :key="b.task_id">{{ b.description || b.task_id }}<span class="lc-sub">{{ b.status === 'running' ? '运行中' : b.status }}</span></li>
        </ul>
      </div>

      <p v-if="paused" class="lc-note is-red lc-chain" role="alert">
        自动接着做已暂停：{{ chain?.paused_reason }}
        <button v-if="canWrite && active" type="button" class="asv-link" :disabled="!!working" @click="chainAction('resume')">继续自动接着做</button>
      </p>
      <p v-else-if="chain && chain.rounds" class="lc-sec-note lc-chain">
        已经自动接着做了 {{ chain.rounds }} 轮（最多 {{ chain.limits.rounds }} 轮，连续失败 {{ chain.limits.failures }} 次暂停）
        <button v-if="canWrite && active" type="button" class="asv-link lb-danger" :disabled="!!working" @click="chainAction('stop')">停止自动接着做</button>
      </p>

      <h4 class="lc-tasks-title">运行中<span class="lc-tab-count">{{ running.length }}</span></h4>
      <p v-if="!running.length" class="lc-sec-note">没有在跑的后台作业。</p>
      <ul v-else class="lc-task-list" aria-label="运行中的后台作业">
        <li v-for="job in running" :key="job.id" class="lc-task">
          <Terminal class="lc-task-icon" aria-hidden="true" />
          <div class="lc-task-main">
            <b>{{ job.name }}</b>
            <span class="lc-sub">已 {{ duration(job.seconds) }}<template v-if="quiet(job.quiet_seconds)"> · {{ quiet(job.quiet_seconds) }}</template> · {{ job.by }}{{ job.follow_up ? ' · 结束后自动接着做' : '' }}</span>
          </div>
          <button type="button" class="lc-icon" :aria-label="`看日志：${job.name}`" title="看日志" @click="toggleLog(job)"><FileText aria-hidden="true" /></button>
          <button v-if="canWrite" type="button" class="lc-icon lb-danger" :aria-label="`停止作业：${job.name}`" title="停止" :disabled="!!working" @click="stop(job)"><CircleStop aria-hidden="true" /></button>
          <pre v-if="logJob === job.id" class="lc-task-log" :aria-label="`日志：${job.name}`">{{ logLoading ? '正在读日志…' : logText }}</pre>
        </li>
      </ul>

      <h4 class="lc-tasks-title">预约<span class="lc-tab-count">{{ pending.length }}</span></h4>
      <p v-if="!pending.length" class="lc-sec-note">没有待发出的预约。</p>
      <ul v-else class="lc-task-list" aria-label="待发出的预约">
        <li v-for="timer in pending" :key="timer.id" class="lc-task">
          <AlarmClock class="lc-task-icon" aria-hidden="true" />
          <div class="lc-task-main">
            <b>{{ when(timer.run_at) }}<span class="lc-sub">（{{ duration(timer.in_seconds) }}后）</span></b>
            <span class="lc-task-text">{{ timer.text }}</span>
            <span class="lc-sub">{{ timer.by }}</span>
          </div>
          <button v-if="canWrite" type="button" class="lc-icon" :aria-label="`取消预约：${when(timer.run_at)}`" title="取消" :disabled="!!working" @click="cancel(timer)"><X aria-hidden="true" /></button>
        </li>
      </ul>
      <form v-if="canWrite && active && planning" class="lc-plan" aria-label="预约一条消息" @submit.prevent="plan">
        <textarea v-model="planText" rows="2" maxlength="4000" aria-label="到时候发给会话的话" placeholder="例如：去看 CI 结果，失败就看哪个用例挂了" />
        <div class="lc-plan-row">
          <select v-model="planWhen" aria-label="什么时候发"><option v-for="w in WHEN" :key="w.id" :value="w.id">{{ w.label }}</option></select>
          <input v-if="planWhen === 'custom'" v-model="planAt" type="datetime-local" aria-label="发出的时间" />
          <button type="submit" class="primary-button" :disabled="!!working">预约</button>
          <button type="button" class="secondary-button" :disabled="!!working" @click="planning = false">取消</button>
        </div>
      </form>
      <button v-else-if="canWrite && active" type="button" class="lc-soft lc-plan-open" @click="planning = true"><AlarmClock aria-hidden="true" />预约一条消息</button>

      <details v-if="finished" class="lc-tasks-done">
        <summary>已完成<span class="lc-tab-count">{{ finished }}</span></summary>
        <ul class="lc-task-list" aria-label="已完成的后台任务">
          <li v-for="job in finishedJobs" :key="job.id" class="lc-task">
            <Terminal class="lc-task-icon" aria-hidden="true" />
            <div class="lc-task-main">
              <b>{{ job.name }}</b>
              <span class="lc-sub"><span class="lc-task-state" :data-tone="JOB_TONE[job.status] ?? 'muted'">{{ job.status_label }}</span><template v-if="job.exit_code != null"> · 退出码 {{ job.exit_code }}</template> · 用时 {{ duration(job.seconds) }}<template v-if="job.error"> · {{ job.error }}</template></span>
            </div>
            <button type="button" class="lc-icon" :aria-label="`看日志：${job.name}`" title="看日志" @click="toggleLog(job)"><FileText aria-hidden="true" /></button>
            <pre v-if="logJob === job.id" class="lc-task-log" :aria-label="`日志：${job.name}`">{{ logLoading ? '正在读日志…' : logText }}</pre>
          </li>
          <li v-for="timer in pastTimers" :key="timer.id" class="lc-task">
            <AlarmClock class="lc-task-icon" aria-hidden="true" />
            <div class="lc-task-main">
              <b>{{ when(timer.run_at) }}</b>
              <span class="lc-task-text">{{ timer.text }}</span>
              <span class="lc-sub"><span class="lc-task-state" :data-tone="timer.status === 'fired' ? 'ok' : 'muted'">{{ timer.status_label }}</span><template v-if="timer.reason"> · {{ timer.reason }}</template></span>
            </div>
          </li>
        </ul>
      </details>

      <p v-if="tasks && !tasks.jobs_allowed && active" class="lc-sec-note lc-tasks-hint">后台作业：{{ tasks.jobs_blocked }}</p>
      <p v-else-if="tasks && active" class="lc-sec-note lc-tasks-hint">后台作业由会话里的模型（llm-task 命令）或智能体开：跑很久的命令交给管理器，回答结束了照样跑，结束后自动叫模型接着做。</p>
      <p v-if="error" class="lc-note is-red" role="alert">{{ error }}</p>
    </div>
  </section>
</template>
