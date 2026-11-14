<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import DataState from '../../../components/DataState.vue'
import { platformName } from '../api'
import { PLATFORM_MARK, TURN_STATUS, chatError, type ChatGroupMember, type ColumnSend, type LlmChatApi } from './chatApi'
import { useLiveConversation } from './useLiveConversation'
import ChatMessage from './ChatMessage.vue'

// 多模型同问里的一栏：一个模型的会话。它就是一个普通会话，内容和进度和单会话一样读。
// latest：整组刚发出的那一条在这一栏的结果（回合，或者没发出去的原因）。
const props = defineProps<{ chat: LlmChatApi; member: ChatGroupMember; latest: ColumnSend | null }>()

// 管理器的登记被删除后读不到内容：不发请求
const bridgeId = computed(() => props.member.bridge_id ?? null)
const sessionId = computed(() => (props.member.bridge_id ? props.member.session_id ?? null : null))
const box = ref<HTMLElement | null>(null)
const scrollDown = () => { void nextTick(() => { const el = box.value; if (el) el.scrollTop = el.scrollHeight }) }
const { conversation, session, messages, lost, activeTurn, poll, sent } = useLiveConversation(props.chat, bridgeId, sessionId, { activity: scrollDown })

const sendProblem = ref('')
watch(() => props.latest?.id, () => {
  const latest = props.latest
  if (!latest) return
  sendProblem.value = latest.error
  if (latest.turn) sent(latest.turn, latest.text)
})

const name = computed(() => platformName(props.member.platform))
const state = computed(() => {
  if (activeTurn.value) return activeTurn.value.status === 'queued' ? '排队中' : '正在回答'
  const last = [...messages.value].reverse().find(m => m.role === 'assistant')
  return last ? TURN_STATUS[last.status ?? 'completed'] ?? last.status ?? '' : ''
})
const lastAnswer = computed(() => [...messages.value].reverse().find(m => m.role === 'assistant' && m.text)?.text ?? '')

const stopping = ref(false), problem = ref(''), copied = ref(false)
async function stop() {
  const turn = activeTurn.value?.turn_id, bridge = props.member.bridge_id
  if (!turn || !bridge || stopping.value) return
  stopping.value = true; problem.value = ''
  try { await props.chat.cancel(bridge, turn); void poll() }
  catch (e) { problem.value = chatError(e) }
  finally { stopping.value = false }
}
async function copy() {
  problem.value = ''
  try {
    await navigator.clipboard.writeText(lastAnswer.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch { problem.value = '复制不了：浏览器没有允许访问剪贴板。' }
}
</script>

<template>
  <section class="lc-col" :aria-label="`${name}（${member.bridge_name}）`">
    <header class="lc-col-head">
      <span class="lc-mark" :data-platform="member.platform" aria-hidden="true">{{ PLATFORM_MARK[member.platform] ?? '?' }}</span>
      <div class="lc-col-title"><strong>{{ name }}</strong><span>{{ member.model || '平台默认模型' }} · {{ member.bridge_name }}</span></div>
      <span v-if="state && sessionId" class="lc-tag" :data-status="activeTurn ? 'running' : undefined">{{ state }}</span>
    </header>
    <div ref="box" class="lc-col-body">
      <p v-if="!member.session_id" class="lc-note is-red" role="alert">这个模型的会话没有建成：{{ member.error || '原因未知' }}</p>
      <p v-else-if="!member.bridge_id" class="lc-note" role="note">这个 MCP 管理器的登记已删除，读不到这一栏的内容了。</p>
      <DataState v-else-if="conversation.isPending.value || (conversation.error.value && !session)" :loading="conversation.isPending.value" :error="conversation.error.value" :empty="false" empty-text="" @retry="conversation.refetch()" />
      <template v-else>
        <p v-if="member.error" class="lc-note is-red" role="alert">{{ member.error }}</p>
        <ol class="lc-messages" :aria-label="`${name}的消息`">
          <ChatMessage v-for="(m, i) in messages" :key="`${m.turn_id ?? i}:${m.role}`" :message="m" :platform="name" />
        </ol>
        <p v-if="lost" class="lc-note is-red" role="alert">{{ lost }} <button type="button" class="asv-link" @click="poll()">重试</button></p>
      </template>
      <p v-if="sendProblem" class="lc-note is-red" role="alert">这一个没有发出去：{{ sendProblem }}</p>
      <p v-if="problem" class="lc-note is-red" role="alert">{{ problem }}</p>
    </div>
    <footer v-if="sessionId" class="lc-col-foot">
      <button v-if="activeTurn" type="button" class="asv-link lb-danger" :disabled="stopping" @click="stop">{{ stopping ? '正在停止…' : '停止这一个' }}</button>
      <RouterLink class="asv-link" :to="{ path: '/llm_bridge', query: { m: member.bridge_id!, s: sessionId } }">在这个会话里继续</RouterLink>
      <button v-if="lastAnswer" type="button" class="asv-link" @click="copy">{{ copied ? '已复制' : '复制回答' }}</button>
    </footer>
  </section>
</template>
