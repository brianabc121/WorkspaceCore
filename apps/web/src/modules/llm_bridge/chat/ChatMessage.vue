<script setup lang="ts">
import { computed } from 'vue'
import { seconds } from '../api'
import { splitText, type ChatMessage } from './chatApi'

// 一条消息。回答按纯文字显示：只把三个反引号围起来的部分排成代码块，不渲染 HTML、不执行任何内容。
// 开发会话的“过程”（中间说的话、命令、改动的文件）收在可以展开的一栏里；回答还没完时默认展开。
// 被额度打断的回合（T-0237）显示什么时候自动继续，可以“现在就试”或“不要自动继续”。
const props = defineProps<{ message: ChatMessage; platform: string; resuming?: string }>()
const emit = defineEmits<{ resume: [turn: string, now: boolean] }>()

const STEP: Record<string, string> = { message: '说明', command: '命令', files: '改动的文件', notice: '提示' }
const FAILURE: Record<string, string> = { failed: '这一回合失败了', unknown: '这一回合的结果未知' }
const parts = computed(() => splitText(props.message.text))
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
    </div>
    <details v-if="reply && message.steps?.length" class="lc-steps" :open="pending">
      <summary>过程（{{ message.steps.length }} 步）</summary>
      <ol>
        <li v-for="(step, i) in message.steps" :key="i">
          <span class="lc-step-kind">{{ STEP[step.kind] ?? step.kind }}</span>
          <code v-if="step.kind === 'command' || step.kind === 'files'">{{ step.text }}</code>
          <span v-else>{{ step.text }}</span>
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
