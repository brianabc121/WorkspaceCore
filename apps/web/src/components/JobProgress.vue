<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useSlots, watch } from 'vue'
import { ChevronDown, ChevronRight, CircleCheck, CircleDashed, CircleMinus, CircleX, Copy, LoaderCircle, SquareTerminal, TriangleAlert } from '@lucide/vue'
import { formatTime } from '../features/time'
import './job-progress.css'

// 后台任务的逐步进度（T-0269 效果图；T-0272 抽成共用组件）：智能体服务器的初始化 / 部署 / 卸载（features/agents/SshJob.vue）
// 和 MCP 管理器的部署 / 重新部署 / 重启 / 卸载（modules/llm_bridge/DeployJob.vue）都用它，外观一致。
// 调用方把自己的任务记录换成下面这些参数（步骤名、成功文字、命令输出的脱敏说明等），这里不认识任何业务字段。
// 布局：标题 + 状态 + 开始时间与用时（+ 一行在哪台服务器）→ 按步骤分格的进度条 → 步骤时间线（失败原因挂在失败那一步下面，
// 进行中那一步下面显示最新几行输出）→ 默认折叠的命令输出。成功后收起成一条成功条，可以展开每一步。
interface JobProgressStep { key: string; label: string; detail?: string | null; status: string }
const props = withDefaults(defineProps<{
  /** 区块的可访问名称，例如“初始化服务器进度”“部署「2 号」的进度” */
  label: string
  title: string
  /** running / succeeded / failed / interrupted */
  status: string
  startedAt: string
  finishedAt?: string | null
  /** 没有结束时间又不在进行中（例如中断）时，用最后更新的时间算用时 */
  updatedAt?: string | null
  /** 标题下面一行，例如在哪台服务器、怎么连 */
  meta?: string
  steps: JobProgressStep[]
  log?: string | null
  /** 命令输出里替换掉了什么，例如“密码、令牌、私钥已替换成 ***” */
  logNote: string
  errorMessage?: string | null
  errorCode?: string | null
  successText: string
  /** 成功条上列出的要点 */
  chips?: string[]
  /** 成功后是否把步骤收起来（只删除登记的卸载要一直显示那一步的说明） */
  collapse?: boolean
}>(), { finishedAt: null, updatedAt: null, meta: '', log: null, errorMessage: null, errorCode: null, chips: () => [], collapse: true })
const slots = useSlots()

const STATUS: Record<string, string> = { running: '进行中', succeeded: '成功', failed: '失败', interrupted: '已中断' }
const STEP_STATUS: Record<string, string> = { pending: '等待', running: '进行中', done: '完成', skipped: '跳过', failed: '失败' }
const TONE: Record<string, string> = { running: 'tone-blue', succeeded: 'tone-green', failed: 'tone-red', interrupted: 'tone-red' }
const STEP_ICON: Record<string, unknown> = { done: CircleCheck, skipped: CircleMinus, failed: CircleX, running: LoaderCircle, pending: CircleDashed }

const running = computed(() => props.status === 'running')
const succeeded = computed(() => props.status === 'succeeded')
const total = computed(() => props.steps.length)
const finished = computed(() => props.steps.filter(s => s.status === 'done' || s.status === 'skipped').length)
const failedIndex = computed(() => props.steps.findIndex(s => s.status === 'failed'))
const runningIndex = computed(() => props.steps.findIndex(s => s.status === 'running'))
const notRun = computed(() => props.steps.filter(s => s.status === 'pending').length)
// 失败原因：有失败的那一步就挂在它下面，否则（例如没走到任何一步就失败）单独显示
const reason = computed(() => props.errorMessage ?? '失败，详见命令输出')

const progress = computed(() => {
  const n = total.value
  if (running.value) return runningIndex.value >= 0 ? `正在执行第 ${runningIndex.value + 1} / ${n} 步` : `已完成 ${finished.value} / ${n} 步`
  if (props.status === 'failed') return failedIndex.value >= 0 ? `完成 ${finished.value} / ${n} 步，在第 ${failedIndex.value + 1} 步失败` : `完成 ${finished.value} / ${n} 步后失败`
  if (props.status === 'interrupted') return `完成 ${finished.value} / ${n} 步，任务被中断`
  return `${finished.value} / ${n} 步完成`
})
const progressNote = computed(() => running.value ? `完成 ${finished.value} 步` : notRun.value ? `后面 ${notRun.value} 步未执行` : '')
// 任务结束后，没轮到的步骤是“未执行”而不是“等待”
const stepState = (step: JobProgressStep) => step.status === 'pending' && !running.value ? '未执行' : STEP_STATUS[step.status] ?? step.status
const stepIcon = (step: JobProgressStep) => STEP_ICON[step.status] ?? CircleDashed

const collapsed = computed(() => succeeded.value && props.collapse)
const showSteps = ref(false), logOpen = ref(false)
watch(() => [props.label, props.startedAt], () => { showSteps.value = false; logOpen.value = false })

// 用时：进行中每秒走一次；结束后用结束时间
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
watch(running, (on) => {
  if (timer) { clearInterval(timer); timer = undefined }
  if (on) { now.value = Date.now(); timer = setInterval(() => { now.value = Date.now() }, 1000) }
}, { immediate: true })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
const elapsed = computed(() => {
  const start = Date.parse(props.startedAt)
  const endAt = props.finishedAt ?? (running.value ? null : props.updatedAt)
  const end = endAt ? Date.parse(endAt) : running.value ? now.value : NaN
  const seconds = Math.max(0, Math.round((end - start) / 1000))
  if (!Number.isFinite(seconds)) return ''
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, '0')} 秒`
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`
})

// 命令输出：$ 开头的是命令，！开头的是报错
const lines = computed(() => (props.log ?? '').replace(/\n+$/, '').split('\n'))
const lineKind = (line: string) => line.startsWith('$ ') ? 'is-cmd' : /^[！!]/.test(line) ? 'is-err' : ''
const tail = computed(() => lines.value.filter(l => l.trim()).slice(-4))
const logEl = ref<HTMLDetailsElement | null>(null)
function openLog() {
  logOpen.value = true
  requestAnimationFrame(() => logEl.value?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' }))
}
const copied = ref(false)
async function copy() {
  try { await navigator.clipboard.writeText(props.log ?? ''); copied.value = true; setTimeout(() => { copied.value = false }, 2000) }
  catch { copied.value = false }
}
</script>

<template>
  <section class="ssh-job" :class="`is-${status}`" :aria-label="label" :data-status="status">
    <header class="ssh-job-head">
      <h4><SquareTerminal aria-hidden="true" />{{ title }}</h4>
      <span class="asv-state" :class="TONE[status] ?? 'tone-muted'">{{ STATUS[status] ?? status }}</span>
      <span class="ssh-job-time"><time :datetime="startedAt">{{ formatTime(startedAt) }}</time> 开始<template v-if="elapsed"> · {{ running ? '已用时' : '用时' }} {{ elapsed }}</template></span>
      <span v-if="meta" class="ssh-job-meta">{{ meta }}</span>
    </header>

    <div v-if="succeeded" class="ssh-job-done">
      <strong role="status"><CircleCheck aria-hidden="true" />{{ successText }}</strong>
      <span v-if="chips.length" class="ssh-job-chips"><span v-for="chip in chips" :key="chip">{{ chip }}</span></span>
      <button v-if="collapse && steps.length" type="button" class="ssh-link" :aria-expanded="showSteps" @click="showSteps = !showSteps">
        {{ showSteps ? '收起每一步' : `查看每一步（${progress}）` }}<ChevronDown aria-hidden="true" :class="{ 'is-open': showSteps }" />
      </button>
    </div>
    <div v-else class="ssh-job-progress">
      <div class="ssh-job-progress-text"><span>{{ progress }}</span><span v-if="progressNote">{{ progressNote }}</span></div>
      <div class="ssh-job-segs" aria-hidden="true"><i v-for="step in steps" :key="step.key" :data-status="step.status" /></div>
    </div>

    <ol v-show="!collapsed || showSteps" class="ssh-job-steps">
      <li v-for="step in steps" :key="step.key" :data-status="step.status">
        <span class="ssh-step-dot"><component :is="stepIcon(step)" aria-hidden="true" /></span>
        <div class="ssh-step-main">
          <span class="ssh-step-title">{{ step.label }}</span>
          <span v-if="step.detail" class="ssh-step-detail">{{ step.detail }}</span>
        </div>
        <span class="ssh-step-state">{{ stepState(step) }}</span>
        <div v-if="(step.status === 'failed' && status === 'failed') || (step.status === 'running' && running && tail.length)" class="ssh-step-extra">
          <div v-if="step.status === 'failed'" class="ssh-job-fail" role="alert">
            <strong><TriangleAlert aria-hidden="true" />失败原因</strong>
            <p>{{ reason }}</p>
            <div v-if="errorCode || log" class="ssh-job-fail-foot">
              <code v-if="errorCode">{{ errorCode }}</code>
              <button v-if="log" type="button" class="secondary-button" @click="openLog">查看命令输出</button>
            </div>
          </div>
          <div v-else class="ssh-job-live" aria-label="最新输出">
            <span class="ssh-job-live-head">最新输出 · 实时</span>
            <span v-for="(line, i) in tail" :key="i" :class="lineKind(line)">{{ line }}</span>
          </div>
        </div>
      </li>
    </ol>

    <div v-if="status === 'failed' && failedIndex < 0" class="ssh-job-fail is-alone" role="alert">
      <strong><TriangleAlert aria-hidden="true" />失败原因</strong>
      <p>{{ reason }}</p>
      <div v-if="errorCode" class="ssh-job-fail-foot"><code>{{ errorCode }}</code></div>
    </div>
    <p v-else-if="status === 'interrupted'" class="ssh-job-fail is-alone" role="alert">任务被中断（工作台重启过），可以重新开始。</p>
    <div v-if="slots.after" class="ssh-job-after"><slot name="after" /></div>

    <details v-if="log" ref="logEl" class="ssh-job-log" :open="logOpen" @toggle="logOpen = ($event.target as HTMLDetailsElement).open">
      <summary>
        <ChevronRight aria-hidden="true" /><b>命令输出</b><span class="ssh-job-log-hint">{{ lines.length }} 行 · {{ logNote }}</span>
      </summary>
      <div class="ssh-job-log-body">
        <button type="button" class="secondary-button ssh-job-copy" @click="copy"><Copy aria-hidden="true" />{{ copied ? '已复制' : '复制' }}</button>
        <pre class="ssh-job-term"><span v-for="(line, i) in lines" :key="i" :class="lineKind(line)">{{ line }}</span></pre>
      </div>
    </details>
  </section>
</template>
