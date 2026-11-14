<script setup lang="ts">
import { computed } from 'vue'
import { seconds } from '../api'
import { splitText, type ChatMessage } from './chatApi'

// 一条消息。回答按纯文字显示：只把三个反引号围起来的部分排成代码块，不渲染 HTML、不执行任何内容。
// 开发会话的“过程”（中间说的话、命令、改动的文件）收在可以展开的一栏里；回答还没完时默认展开。
const props = defineProps<{ message: ChatMessage; platform: string }>()

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
</script>

<template>
  <li class="lc-msg" :class="[reply ? 'is-reply' : 'is-ask', { 'is-mine': message.mine }]" :data-status="status || undefined">
    <div class="lc-msg-head">
      <strong>{{ who }}</strong>
      <time v-if="message.at">{{ seconds(message.at) }}</time>
      <span v-if="status === 'cancelled'" class="lc-tag">已停止</span>
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
  </li>
</template>
