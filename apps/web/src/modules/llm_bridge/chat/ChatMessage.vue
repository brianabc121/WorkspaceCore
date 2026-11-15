<script setup lang="ts">
import { computed } from 'vue'
import { CHAT_NO_TOOLS, seconds } from '../api'
import { splitText, type ChatMessage, type ChatStep, type LlmChatApi } from './chatApi'
import ChatAttachments from './ChatAttachments.vue'

// 一条消息。回答按纯文字显示：只把三个反引号围起来的部分排成代码块，不渲染 HTML、不执行任何内容。
// 开发会话的“过程”（中间说的话、命令、改动的文件）收在可以展开的一栏里；回答还没完时默认展开。
// 被额度打断的回合（T-0237）显示什么时候自动继续，可以“现在就试”或“不要自动继续”。
// source：从哪个管理器的哪个会话取附件的内容；不给就不显示附件。
// noTools：对话模式的会话。模型有时把“调用工具”当成文字写出来，这里标明它并没有执行。
const props = defineProps<{ message: ChatMessage; platform: string; noTools?: boolean; resuming?: string; source?: { chat: LlmChatApi; bridgeId: string; sessionId: string } }>()
const emit = defineEmits<{ resume: [turn: string, now: boolean] }>()

const STEP: Record<string, string> = { message: '说明', command: '命令', files: '改动的文件', notice: '提示', tool: '工具', tool_result: '结果', image: '图片', file: '文件' }
const stepLabel = (step: ChatStep) => `${STEP[step.kind] ?? step.kind}${step.name && (step.kind === 'tool' || step.kind === 'tool_result') ? ` ${step.name}` : ''}`
const CODE_STEPS = ['command', 'files', 'tool', 'tool_result']
const FAILURE: Record<string, string> = { failed: '这一回合失败了', unknown: '这一回合的结果未知' }
const parts = computed(() => splitText(props.message.text))
const fakeCall = computed(() => !!props.noTools && props.message.role === 'assistant' && /<tool_call>|<function_calls>|<invoke\b|<tool_use>/.test(props.message.text ?? ''))
const reply = computed(() => props.message.role === 'assistant')
const status = computed(() => (reply.value ? props.message.status ?? 'completed' : ''))
const pending = computed(() => status.value === 'queued' || status.value === 'running')
const who = computed(() => {
  if (reply.value) return props.platform
  if (props.message.mine) return '我'
  return props.message.by || '用户'
})
const waiting = computed(() => {
  if (status.value === 'queued') {
    const ahead = props.message.ahead_in_session
    return ahead ? `排队中：前面还有 ${ahead} 条` : '排队中…'
  }
  return parts.value.length ? '还在继续…' : '正在回答…'
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
  <li class="lc-msg" :class="[reply ? 'is-reply' : 'is-ask', { 'is-mine': message.mine }]" :data-status="status || undefined">
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
        </li>
      </ol>
    </details>
    <div v-if="parts.length || pending" class="lc-bubble">
      <template v-for="(part, i) in parts" :key="i">
        <pre v-if="part.code" class="lc-code"><code>{{ part.text }}</code></pre>
        <p v-else class="lc-text">{{ part.text }}</p>
      </template>
      <p v-if="pending" class="lc-wait" role="status">{{ waiting }}</p>
    </div>
    <p v-else-if="reply && status === 'completed'" class="lc-bubble lc-muted">（没有文字回答）</p>
    <ChatAttachments v-if="source && message.attachments?.length" :chat="source.chat" :bridge-id="source.bridgeId" :session-id="source.sessionId" :items="message.attachments" />
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
