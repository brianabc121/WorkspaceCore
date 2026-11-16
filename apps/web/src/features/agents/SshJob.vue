<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, CircleCheck, CircleDashed, CircleMinus, CircleX, Copy, LoaderCircle, SquareTerminal, TriangleAlert } from '@lucide/vue'
import type { Schema } from '../../services/api'
import { formatTime } from '../time'
import { SSH_JOB_KIND, SSH_JOB_STATUS, SSH_STEP, SSH_STEP_STATUS } from './ssh'
import './ssh-job.css'

// 一次 SSH 任务（初始化服务器、部署、卸载）的逐步进度与脱敏后的命令输出（T-0090、T-0147；按效果图重排 T-0269）。
// 步骤与失败原因都来自任务记录本身；没登记文字的新步骤直接显示步骤名，不猜。
// 布局：标题 + 状态 + 开始时间与用时 → 按步骤分格的进度条 → 步骤时间线（失败原因挂在失败那一步下面，
// 进行中那一步下面显示最新几行输出）→ 默认折叠的命令输出。成功后收起成一条成功条，可以展开每一步。
const props = defineProps<{ job: Schema['AgentSshJobOut'] }>()
type Step = Schema['AgentSshJobStep']

const label = (key: string) => (SSH_STEP as Record<string, string>)[key] ?? key
const running = computed(() => props.job.status === 'running')
const succeeded = computed(() => props.job.status === 'succeeded')
const steps = computed(() => props.job.steps)
const total = computed(() => steps.value.length)
const finished = computed(() => steps.value.filter(s => s.status === 'done' || s.status === 'skipped').length)
const failedIndex = computed(() => steps.value.findIndex(s => s.status === 'failed'))
const runningIndex = computed(() => steps.value.findIndex(s => s.status === 'running'))
const notRun = computed(() => steps.value.filter(s => s.status === 'pending').length)
// 失败原因：有失败的那一步就挂在它下面，否则（例如没走到任何一步就失败）单独显示
const reason = computed(() => `${props.job.error_message ?? '失败，详见日志'}`)
const TONE: Record<Schema['AgentSshJobOut']['status'], string> = { running: 'tone-blue', succeeded: 'tone-green', failed: 'tone-red', interrupted: 'tone-red' }

const progress = computed(() => {
  const n = total.value
  if (running.value) return runningIndex.value >= 0 ? `正在执行第 ${runningIndex.value + 1} / ${n} 步` : `已完成 ${finished.value} / ${n} 步`
  if (props.job.status === 'failed') return failedIndex.value >= 0 ? `完成 ${finished.value} / ${n} 步，在第 ${failedIndex.value + 1} 步失败` : `完成 ${finished.value} / ${n} 步后失败`
  if (props.job.status === 'interrupted') return `完成 ${finished.value} / ${n} 步，任务被中断`
  return `${finished.value} / ${n} 步完成`
})
const progressNote = computed(() => running.value ? `完成 ${finished.value} 步` : notRun.value ? `后面 ${notRun.value} 步未执行` : '')
// 任务结束后，没轮到的步骤是“未执行”而不是“等待”
const stepState = (step: Step) => step.status === 'pending' && !running.value ? '未执行' : SSH_STEP_STATUS[step.status]
const STEP_ICON = { done: CircleCheck, skipped: CircleMinus, failed: CircleX, running: LoaderCircle, pending: CircleDashed } as const

// 成功条上的要点（只对初始化列出它准备好的东西）
const READY: Partial<Record<Step['key'], string>> = { docker: 'Docker 与 Compose', workspace: '部署目录', verify: '平台连通' }
const chips = computed(() => props.job.kind === 'prepare' ? steps.value.filter(s => s.status === 'done' && READY[s.key]).map(s => READY[s.key]!) : [])
const SUCCESS: Record<Schema['AgentSshJobOut']['kind'], string> = {
  prepare: '服务器初始化完成：可以在这台服务器上部署智能体了。',
  install: '部署完成，实例已连上工作台。',
  uninstall: '卸载完成。',
}
const showSteps = ref(false), logOpen = ref(false)
watch(() => props.job.id, () => { showSteps.value = false; logOpen.value = false })

// 用时：进行中每秒走一次；结束后用结束时间
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
watch(running, (on) => {
  if (timer) { clearInterval(timer); timer = undefined }
  if (on) { now.value = Date.now(); timer = setInterval(() => { now.value = Date.now() }, 1000) }
}, { immediate: true })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
const elapsed = computed(() => {
  const start = Date.parse(props.job.started_at)
  const end = props.job.finished_at ? Date.parse(props.job.finished_at) : running.value ? now.value : Date.parse(props.job.updated_at)
  const seconds = Math.max(0, Math.round((end - start) / 1000))
  if (!Number.isFinite(seconds)) return ''
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, '0')} 秒`
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`
})

// 命令输出：$ 开头的是命令，！开头的是报错
const lines = computed(() => props.job.log.replace(/\n+$/, '').split('\n'))
const lineKind = (line: string) => line.startsWith('$ ') ? 'is-cmd' : /^[！!]/.test(line) ? 'is-err' : ''
const tail = computed(() => lines.value.filter(l => l.trim()).slice(-4))
const logEl = ref<HTMLDetailsElement | null>(null)
function openLog() {
  logOpen.value = true
  requestAnimationFrame(() => logEl.value?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' }))
}
const copied = ref(false)
async function copy() {
  try { await navigator.clipboard.writeText(props.job.log); copied.value = true; setTimeout(() => { copied.value = false }, 2000) }
  catch { copied.value = false }
}
</script>

<template>
  <section class="ssh-job" :class="`is-${job.status}`" :aria-label="`${SSH_JOB_KIND[job.kind]}进度`" :data-status="job.status">
    <header class="ssh-job-head">
      <h4><SquareTerminal aria-hidden="true" />{{ SSH_JOB_KIND[job.kind] }}</h4>
      <span class="asv-state" :class="TONE[job.status]">{{ SSH_JOB_STATUS[job.status] }}</span>
      <span class="ssh-job-time"><time :datetime="job.started_at">{{ formatTime(job.started_at) }}</time> 开始<template v-if="elapsed"> · {{ running ? '已用时' : '用时' }} {{ elapsed }}</template></span>
    </header>

    <div v-if="succeeded" class="ssh-job-done">
      <strong role="status"><CircleCheck aria-hidden="true" />{{ SUCCESS[job.kind] }}</strong>
      <span v-if="chips.length" class="ssh-job-chips"><span v-for="chip in chips" :key="chip">{{ chip }}</span></span>
      <button type="button" class="ssh-link" :aria-expanded="showSteps" @click="showSteps = !showSteps">
        {{ showSteps ? '收起每一步' : `查看每一步（${progress}）` }}<ChevronDown aria-hidden="true" :class="{ 'is-open': showSteps }" />
      </button>
    </div>
    <div v-else class="ssh-job-progress">
      <div class="ssh-job-progress-text"><span>{{ progress }}</span><span v-if="progressNote">{{ progressNote }}</span></div>
      <div class="ssh-job-segs" aria-hidden="true"><i v-for="step in steps" :key="step.key" :data-status="step.status" /></div>
    </div>

    <ol v-show="!succeeded || showSteps" class="ssh-job-steps">
      <li v-for="step in steps" :key="step.key" :data-status="step.status">
        <span class="ssh-step-dot"><component :is="STEP_ICON[step.status]" aria-hidden="true" /></span>
        <div class="ssh-step-main">
          <span class="ssh-step-title">{{ label(step.key) }}</span>
          <span v-if="step.detail" class="ssh-step-detail">{{ step.detail }}</span>
        </div>
        <span class="ssh-step-state">{{ stepState(step) }}</span>
        <div v-if="(step.status === 'failed' && job.status === 'failed') || (step.status === 'running' && running && tail.length)" class="ssh-step-extra">
          <div v-if="step.status === 'failed'" class="ssh-job-fail" role="alert">
            <strong><TriangleAlert aria-hidden="true" />失败原因</strong>
            <p>{{ reason }}</p>
            <div v-if="job.error_code || job.log" class="ssh-job-fail-foot">
              <code v-if="job.error_code">{{ job.error_code }}</code>
              <button v-if="job.log" type="button" class="secondary-button" @click="openLog">查看命令输出</button>
            </div>
          </div>
          <div v-else class="ssh-job-live" aria-label="最新输出">
            <span class="ssh-job-live-head">最新输出 · 实时</span>
            <span v-for="(line, i) in tail" :key="i" :class="lineKind(line)">{{ line }}</span>
          </div>
        </div>
      </li>
    </ol>

    <div v-if="job.status === 'failed' && failedIndex < 0" class="ssh-job-fail is-alone" role="alert">
      <strong><TriangleAlert aria-hidden="true" />失败原因</strong>
      <p>{{ reason }}</p>
      <div v-if="job.error_code" class="ssh-job-fail-foot"><code>{{ job.error_code }}</code></div>
    </div>
    <p v-else-if="job.status === 'interrupted'" class="ssh-job-fail is-alone" role="alert">任务被中断（工作台重启过），可以重新开始。</p>

    <details v-if="job.log" ref="logEl" class="ssh-job-log" :open="logOpen" @toggle="logOpen = ($event.target as HTMLDetailsElement).open">
      <summary>
        <ChevronRight aria-hidden="true" /><b>命令输出</b><span class="ssh-job-log-hint">{{ lines.length }} 行 · 密码、令牌、私钥已替换成 ***</span>
      </summary>
      <div class="ssh-job-log-body">
        <button type="button" class="secondary-button ssh-job-copy" @click="copy"><Copy aria-hidden="true" />{{ copied ? '已复制' : '复制' }}</button>
        <pre class="ssh-job-term"><span v-for="(line, i) in lines" :key="i" :class="lineKind(line)">{{ line }}</span></pre>
      </div>
    </details>
  </section>
</template>
