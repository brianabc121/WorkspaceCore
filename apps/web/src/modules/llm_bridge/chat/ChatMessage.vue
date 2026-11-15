<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { CHAT_NO_TOOLS, duration, seconds } from '../api'
import { splitText, type ChatMessage, type ChatStep, type LlmChatApi } from './chatApi'
import ChatAttachments from './ChatAttachments.vue'
import { answerHtml } from './markdown'
import '../../../features/kb/kb.css'

// 一条消息。回答按纯文字显示：只把三个反引号围起来的部分排成代码块，不渲染 HTML、不执行任何内容。
// 开发会话的“过程”（中间说的话、命令、改动的文件）收在可以展开的一栏里；回答还没完时默认展开。
// 被额度打断的回合（T-0237）显示什么时候自动继续，可以“现在就试”或“不要自动继续”。
// source：从哪个管理器的哪个会话取附件的内容；不给就不显示附件。
// noTools：对话模式的会话。模型有时把“调用工具”当成文字写出来，这里标明它并没有执行。
const props = defineProps<{ message: ChatMessage; platform: string; noTools?: boolean; target?: boolean; last?: boolean; busyAction?: boolean; resuming?: string; source?: { chat: LlmChatApi; bridgeId: string; sessionId: string } }>()
const emit = defineEmits<{ resume: [turn: string, now: boolean]; again: [turn: string] }>()

const STEP: Record<string, string> = { message: '说明', command: '命令', files: '改动的文件', notice: '提示', tool: '工具', tool_result: '结果', image: '图片', file: '文件' }
const stepLabel = (step: ChatStep) => `${STEP[step.kind] ?? step.kind}${step.name && (step.kind === 'tool' || step.kind === 'tool_result') ? ` ${step.name}` : ''}`
const CODE_STEPS = ['command', 'files', 'tool', 'tool_result']
const FAILURE: Record<string, string> = { failed: '这一回合失败了', unknown: '这一回合的结果未知' }
// 正在写的那一段（逐字的增量）接在已经写完的后面一起显示
const shown = computed(() => [props.message.text, props.message.partial].filter(Boolean).join('\n\n'))
const parts = computed(() => splitText(shown.value))
const fakeCall = computed(() => !!props.noTools && props.message.role === 'assistant' && /<tool_call>|<function_calls>|<invoke\b|<tool_use>/.test(props.message.text ?? ''))
const reply = computed(() => props.message.role === 'assistant')
const status = computed(() => (reply.value ? props.message.status ?? 'completed' : ''))
const pending = computed(() => status.value === 'queued' || status.value === 'running')
const who = computed(() => {
  if (reply.value) return props.platform
  if (props.message.mine) return '我'
  return props.message.by || '用户'
})
// 等待时每秒走一下，显示已经用了多久
const now = ref(Date.now() / 1000)
let ticker: ReturnType<typeof setInterval> | undefined
watch(pending, on => {
  clearInterval(ticker); ticker = undefined
  if (on) { now.value = Date.now() / 1000; ticker = setInterval(() => { now.value = Date.now() / 1000 }, 1000) }
}, { immediate: true })
onBeforeUnmount(() => clearInterval(ticker))
const elapsed = computed(() => {
  const started = Number(props.message.timing?.started_at ?? 0)
  return status.value === 'running' && started ? duration(Math.max(0, now.value - started)) : ''
})
// 开发回合正在做什么：最近的一步（命令、工具、提示）
const doing = computed(() => {
  if (status.value !== 'running') return ''
  const last = [...(props.message.steps ?? [])].reverse().find(s => ['command', 'tool', 'notice', 'files'].includes(s.kind))
  return last ? `${stepLabel(last)}：${last.text.split('\n')[0]!.slice(0, 80)}` : ''
})
const waiting = computed(() => {
  if (status.value === 'queued') {
    const wait = props.message.waiting, ahead = props.message.ahead_in_session
    // 说清楚在等什么：会话里前面的消息、平台因额度暂停、平台的名额
    if (wait?.reason === 'quota') return `排队中：这个平台因额度用完暂停了${wait.until ? `，${wait.estimated ? '预计 ' : ''}${seconds(Number(wait.until))} 恢复后开始` : ''}。`
    if (wait?.reason === 'platform_slot') return '排队中：在等这个平台的名额（同时进行的会话数到上限了）。'
    return ahead ? `排队中：前面还有 ${ahead} 条` : '排队中…'
  }
  const base = parts.value.length ? '还在继续…' : '正在回答…'
  return [base, elapsed.value && `已用 ${elapsed.value}`, doing.value].filter(Boolean).join(' · ')
})
// 复制：整条回答，或者一段代码
const copied = ref('')
async function copy(text: string, what: string) {
  try { await navigator.clipboard.writeText(text); copied.value = what; setTimeout(() => { if (copied.value === what) copied.value = '' }, 1500) } catch { /* 浏览器不让复制：不打扰 */ }
}
// 回答下面的操作：复制；没做成的可以把这一问再发一次；最后一条回答可以重新生成（再问一次，新的回答接在后面）
const actions = computed(() => {
  if (!reply.value || pending.value || props.message.imported) return []
  const list: { key: 'copy' | 'again'; label: string; title: string }[] = []
  if (props.message.text) list.push({ key: 'copy', label: '复制', title: '复制这条回答的原文' })
  if (!props.message.turn_id) return list
  const failed = ['failed', 'unknown', 'cancelled'].includes(status.value) || (status.value === 'interrupted' && !['pending', 'forced'].includes(props.message.resume?.state ?? ''))
  if (failed) list.push({ key: 'again', label: '重试', title: '把这一问原样再发一次（附件不会再带上）' })
  else if (props.last && status.value === 'completed') list.push({ key: 'again', label: '重新生成', title: '把这一问原样再发一次：新的回答接在后面，原来的还在' })
  return list
})
function act(key: 'copy' | 'again') {
  if (key === 'copy') void copy(props.message.text, 'all')
  else if (props.message.turn_id) emit('again', props.message.turn_id)
}
// 回答完之后：用了多久、多少 token（官方程序报的数）
const meta = computed(() => {
  if (!reply.value || pending.value) return ''
  const ran = Number(props.message.timing?.ran_seconds ?? 0), usage = props.message.usage as Record<string, unknown> | null | undefined
  const tokens = usage ? Number(usage.total_tokens ?? 0) || Number(usage.input_tokens ?? 0) + Number(usage.output_tokens ?? 0) : 0
  return [ran >= 1 && `用时 ${duration(ran)}`, tokens > 0 && `${tokens.toLocaleString()} token`].filter(Boolean).join(' · ')
})
const failure = computed(() => {
  if (status.value !== 'failed' && status.value !== 'unknown') return ''
  const detail = props.message.failure?.message
  return `${FAILURE[status.value]}${typeof detail === 'string' && detail ? `：${detail}` : '。'}`
})
// 额度用完被打断：按续跑的状态说明接下来会怎样
const interrupted = computed(() => {
  if (status.value !== 'interrupted') return null
  // 被后面插入的消息打断（不是额度）：新回合里会接着做，没有什么可点的
  if (props.message.failure?.kind === 'superseded') return { text: '这一回合被后面插入的消息打断了；新消息里会接着做。', canNow: false, canCancel: false }
  const resume = props.message.resume, at = resume?.at ? seconds(resume.at) : ''
  const state = resume?.state ?? null
  const text = state === 'pending' ? `额度用完，这一回合中断了。${at ? `${at} ` : '额度重置后'}自动继续。`
    : state === 'forced' ? '额度用完，这一回合中断了。正在试着继续…'
    : state === 'done' ? '额度用完，这一回合中断了；已经在下面自动继续。'
    : state === 'cancelled' ? '额度用完，这一回合中断了。已设成不自动继续。'
    : state === 'exhausted' ? '额度用完，这一回合中断了。自动继续的次数已经用完，不再自动继续。'
    : '额度用完，这一回合中断了。没有安排自动继续。'
  return { text, canNow: state !== 'done' && state !== 'forced', canCancel: state === 'pending' }
})
const busy = computed(() => !!props.resuming && props.resuming === props.message.turn_id)
</script>

<template>
  <li class="lc-msg" :class="[reply ? 'is-reply' : 'is-ask', { 'is-mine': message.mine, 'is-target': target }]" :data-status="status || undefined" :data-turn="message.turn_id || undefined">
    <div class="lc-msg-head">
      <strong>{{ who }}</strong>
      <time v-if="message.at">{{ seconds(message.at) }}</time>
      <span v-if="status === 'cancelled'" class="lc-tag">已停止</span>
      <span v-if="status === 'interrupted'" class="lc-tag">已中断</span>
      <span v-if="message.delivery === 'inserted'" class="lc-tag" title="在上一条还没答完时插进去的，进了正在处理的那一回合">插入</span>
      <span v-if="message.delivery === 'merged'" class="lc-tag" title="插入时平台没有收下：中断了上一回合，这条消息带着“接着刚才的做”重新开始">插入（合并重发）</span>
      <span v-if="message.imported" class="lc-tag" title="官方程序里原有的内容，第一次打开时导入的">导入的</span>
    </div>
    <details v-if="reply && message.steps?.length" class="lc-steps" :open="pending">
      <summary>过程（{{ message.steps.length }} 步）</summary>
      <ol>
        <li v-for="(step, i) in message.steps" :key="i">
          <span class="lc-step-kind" :class="{ 'is-error': step.error }">{{ stepLabel(step) }}{{ step.error ? '（出错）' : '' }}</span>
          <code v-if="CODE_STEPS.includes(step.kind)">{{ step.text }}</code>
          <span v-else>{{ step.text }}</span>
          <span v-if="step.truncated" class="lc-tag">已截断</span>
          <span v-if="step.exit_code != null && step.exit_code !== 0" class="lc-tag">退出码 {{ step.exit_code }}</span>
          <pre v-if="step.output" class="lc-code lc-step-output" :aria-label="`${stepLabel(step)}的输出`"><code>{{ step.output }}</code></pre>
        </li>
      </ol>
    </details>
    <div v-if="parts.length || pending" class="lc-bubble">
      <template v-for="(part, i) in parts" :key="i">
        <div v-if="part.code" class="lc-codeblock">
          <div class="lc-codehead"><span>{{ part.lang || '代码' }}</span><button type="button" class="asv-link" :aria-label="`复制这段${part.lang || '代码'}`" @click="copy(part.text, `c${i}`)">{{ copied === `c${i}` ? '已复制' : '复制' }}</button></div>
          <pre class="lc-code"><code>{{ part.text }}</code></pre>
        </div>
        <!-- eslint-disable-next-line vue/no-v-html -- answerHtml 已用 DOMPurify 消毒，不允许原始 HTML（chat/markdown.ts） -->
        <div v-else-if="reply" class="kb-md lc-md" v-html="answerHtml(part.text)"></div>
        <p v-else class="lc-text">{{ part.text }}</p>
      </template>
      <p v-if="pending" class="lc-wait" role="status">{{ waiting }}</p>
    </div>
    <p v-else-if="reply && status === 'completed'" class="lc-bubble lc-muted">（没有文字回答）</p>
    <ChatAttachments v-if="source && message.attachments?.length" :chat="source.chat" :bridge-id="source.bridgeId" :session-id="source.sessionId" :items="message.attachments" />
    <p v-if="meta || actions.length" class="lc-meta">
      <span v-if="meta">{{ meta }}</span>
      <button v-for="a in actions" :key="a.key" type="button" class="asv-link" :title="a.title" :disabled="a.key !== 'copy' && busyAction" @click="act(a.key)">{{ a.key === 'copy' && copied === 'all' ? '已复制' : a.label }}</button>
    </p>
    <p v-if="fakeCall" class="lc-note" role="note">上面像“调用工具”的内容只是模型写出来的文字，并没有执行。{{ CHAT_NO_TOOLS }}</p>
    <p v-if="failure" class="lc-failure" role="alert">{{ failure }}</p>
    <div v-if="interrupted" class="lc-interrupted" role="note">
      <p>{{ interrupted.text }}</p>
      <div v-if="message.turn_id && (interrupted.canNow || interrupted.canCancel)" class="lc-interrupted-actions">
        <button v-if="interrupted.canNow" type="button" class="secondary-button" :disabled="!!resuming" @click="emit('resume', message.turn_id, true)">{{ busy ? '正在处理…' : '现在就试' }}</button>
        <button v-if="interrupted.canCancel" type="button" class="asv-link" :disabled="!!resuming" @click="emit('resume', message.turn_id, false)">不要自动继续</button>
      </div>
    </div>
  </li>
</template>
