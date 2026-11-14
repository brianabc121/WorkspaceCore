<script setup lang="ts">
import { computed, nextTick, ref, toRef } from 'vue'
import { Archive, ArrowLeft, Info, Pencil, Send, Square } from '@lucide/vue'
import DataState from '../../../components/DataState.vue'
import { useMediaQuery } from '../../../composables/useMediaQuery'
import { MODES, platformName, type Bridge } from '../api'
import { chatError, needsTakeover, newRequestId, ownerText, type LlmChatApi } from './chatApi'
import { useLiveConversation } from './useLiveConversation'
import ChatMessage from './ChatMessage.vue'
import ChatInfoPanel from './ChatInfoPanel.vue'
import ChatQueue from './ChatQueue.vue'

// 中间一栏：一个会话的对话。父组件按“管理器 + 会话”给 key，换会话时整个重建。
// 内容和进度（长轮询读事件）在 useLiveConversation 里。
const props = defineProps<{ chat: LlmChatApi; bridge: Bridge; sessionId: string; narrow: boolean; initialDraft?: string; initialError?: string }>()
const emit = defineEmits<{ back: []; changed: [] }>()

const bridgeId = computed(() => props.bridge.id)

// ---- 滚动：人在底部附近时，新内容出来就跟到底部；往上翻着看时不打扰 ----
const box = ref<HTMLElement | null>(null)
let stick = true
function onScroll() { const el = box.value; if (el) stick = el.scrollHeight - el.scrollTop - el.clientHeight < 80 }
function scrollDown(force = false) {
  if (force) stick = true
  void nextTick(() => { const el = box.value; if (el && stick) el.scrollTop = el.scrollHeight })
}
const { conversation, session, messages, lost, activeTurn, poll, sent } = useLiveConversation(
  props.chat, bridgeId, toRef(props, 'sessionId'), { activity: () => scrollDown(), changed: () => emit('changed') },
)

// ---- 发送 ----
const draft = ref(props.initialDraft ?? ''), sending = ref(false), stopping = ref(false), sendError = ref(props.initialError ?? '')
const confirmed = ref(false)
// 结果未知时再点发送：同一段文字用同一个幂等号，不会发两次
let pending: { text: string; id: string } | null = null
const foreign = computed(() => !!session.value && session.value.owner_kind !== 'me')
const takeoverText = computed(() => {
  const s = session.value
  if (!s) return ''
  if (s.owner_kind === 'external') return '这是官方程序里已有的会话。接着发消息会写进那个会话里。\n\n确定要接着发吗？'
  const who = s.owner_kind === 'agent' ? `智能体「${ownerText(s)}」` : `「${ownerText(s)}」`
  return `这是${who}建的会话。接着发消息会改变它的上下文：对方下次用这个会话时会看到你发的内容和回答。\n\n确定要接着发吗？`
})
const blocked = computed(() => {
  if (!session.value) return ''
  if (session.value.status === 'archived') return '这个会话已归档，不能再发送。'
  if (!props.bridge.enabled) return '这个 MCP 管理器已停用：可以查看，不能发送。到“管理器与账号”里启用后再试。'
  return ''
})
function allowTakeover() {
  if (confirmed.value) return true
  confirmed.value = window.confirm(takeoverText.value)
  return confirmed.value
}
async function submit() {
  const text = draft.value.trim()
  if (!text || sending.value || blocked.value) return
  if (foreign.value && !allowTakeover()) return
  if (pending?.text !== text) pending = { text, id: newRequestId() }
  const body = { text, request_id: pending.id }
  sending.value = true; sendError.value = ''
  try {
    let turn
    try { turn = await props.chat.send(props.bridge.id, props.sessionId, { ...body, takeover: foreign.value }) }
    catch (e) {
      // 列表里显示的“谁建的”可能已经过时：服务器说要确认，就在这里补问一次
      if (!needsTakeover(e) || !allowTakeover()) throw e
      turn = await props.chat.send(props.bridge.id, props.sessionId, { ...body, takeover: true })
    }
    pending = null; draft.value = ''
    stick = true; sent(turn, text); emit('changed')
    void props.chat.invalidateQueue(props.bridge.id, props.sessionId)
  } catch (e) {
    sendError.value = needsTakeover(e) ? '没有发送：接着别人建的会话发消息前需要确认。' : chatError(e)
  } finally { sending.value = false }
}
function onKey(event: KeyboardEvent) {
  // Enter 发送，Shift + Enter 换行；输入法还在选字时的 Enter 不算
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  void submit()
}
async function stop() {
  const turn = activeTurn.value?.turn_id
  if (!turn || stopping.value) return
  stopping.value = true; sendError.value = ''
  try { await props.chat.cancel(props.bridge.id, turn); void poll() }
  catch (e) { sendError.value = chatError(e) }
  finally { stopping.value = false }
}

// ---- 标题、归档、会话信息 ----
const headError = ref(''), working = ref(false), renaming = ref(false), title = ref('')
const wide = useMediaQuery('(min-width: 1280px)')
const infoOpen = ref(wide.value)
async function act(action: () => Promise<unknown>) {
  if (working.value) return
  working.value = true; headError.value = ''
  try { await action(); emit('changed') } catch (e) { headError.value = chatError(e) } finally { working.value = false }
}
function startRename() { title.value = session.value?.title ?? ''; renaming.value = true; headError.value = '' }
function saveTitle() {
  const value = title.value.trim()
  if (!value) { headError.value = '标题不能为空。'; return }
  return act(async () => { await props.chat.rename(props.bridge.id, props.sessionId, value); renaming.value = false })
}
function archive() {
  if (!window.confirm('归档这个会话？\n\n归档后不能再往里发消息，记录保留，可以在“看已归档的”里找到。')) return
  return act(() => props.chat.archive(props.bridge.id, props.sessionId))
}
</script>

<template>
  <div class="lc-chat" :class="{ 'has-info': infoOpen && !!session }">
    <div class="lc-thread">
      <header class="lc-head">
        <button v-if="narrow" type="button" class="lc-icon" aria-label="返回会话列表" @click="emit('back')"><ArrowLeft aria-hidden="true" /></button>
        <div class="lc-head-main">
          <form v-if="renaming" class="lc-rename" aria-label="改会话标题" @submit.prevent="saveTitle">
            <input v-model="title" maxlength="120" aria-label="会话标题" />
            <button type="submit" class="primary-button" :disabled="working">保存</button>
            <button type="button" class="secondary-button" :disabled="working" @click="renaming = false">取消</button>
          </form>
          <h2 v-else>{{ session ? session.title || '（没有标题）' : '会话' }}</h2>
          <p v-if="session" class="lc-head-meta">
            {{ platformName(session.platform) }} · {{ session.model || '平台默认模型' }} · {{ MODES[session.mode] ?? session.mode }}{{ session.environment ? `（${session.environment.name}）` : '' }} · {{ bridge.name }} · {{ ownerText(session) }}建的
            <span v-if="session.status === 'archived'" class="lc-tag">已归档</span>
          </p>
        </div>
        <div v-if="session" class="lc-head-actions">
          <button v-if="!renaming && session.origin !== 'external'" type="button" class="lc-icon" aria-label="改标题" title="改标题" :disabled="working" @click="startRename"><Pencil aria-hidden="true" /></button>
          <button v-if="session.status !== 'archived'" type="button" class="lc-icon" aria-label="归档会话" title="归档" :disabled="working" @click="archive"><Archive aria-hidden="true" /></button>
          <button type="button" class="lc-icon" :aria-pressed="infoOpen" aria-label="会话信息" title="会话信息" @click="infoOpen = !infoOpen"><Info aria-hidden="true" /></button>
        </div>
      </header>
      <p v-if="headError" class="lc-note is-red" role="alert">{{ headError }}</p>

      <div ref="box" class="lc-scroll" @scroll.passive="onScroll">
        <DataState v-if="conversation.isPending.value || (conversation.error.value && !session)" :loading="conversation.isPending.value" :error="conversation.error.value" :empty="false" empty-text="" @retry="conversation.refetch()" />
        <template v-else-if="session">
          <p v-if="!messages.length" class="lc-none">这个会话里还没有消息。</p>
          <ol v-else class="lc-messages" aria-label="消息">
            <ChatMessage v-for="(m, i) in messages" :key="`${m.turn_id ?? i}:${m.role}`" :message="m" :platform="platformName(session.platform)" />
          </ol>
          <p v-if="lost" class="lc-note is-red" role="alert">{{ lost }} <button type="button" class="asv-link" @click="poll()">重试</button></p>
        </template>
      </div>

      <ChatQueue v-if="session" :chat="chat" :bridge-id="bridge.id" :session-id="sessionId" :busy="!!activeTurn" @changed="poll()" />
      <form v-if="session" class="lc-composer" aria-label="发送消息" @submit.prevent="submit">
        <p v-if="blocked" class="lc-note" role="note">{{ blocked }}</p>
        <template v-else>
          <p v-if="foreign" class="lc-note" role="note">{{ session.owner_kind === 'external' ? '这是官方程序里已有的会话' : `这是${session.owner_kind === 'agent' ? '智能体' : ''}「${ownerText(session)}」建的会话` }}：可以看；接着发消息前会让你确认一次。</p>
          <div class="lc-compose-row">
            <textarea v-model="draft" rows="2" maxlength="100000" aria-label="消息内容" placeholder="输入消息：Enter 发送，Shift + Enter 换行" @keydown="onKey" />
            <button v-if="activeTurn" type="button" class="secondary-button" :disabled="stopping" @click="stop"><Square aria-hidden="true" />{{ stopping ? '正在停止…' : '停止' }}</button>
            <button type="submit" class="primary-button" :disabled="sending || !draft.trim()"><Send aria-hidden="true" />{{ sending ? '正在发送…' : '发送' }}</button>
          </div>
          <p v-if="activeTurn" class="lc-hint">上一条还没答完：现在发的不会打断它，会进队列，等前面的做完再发给模型。</p>
        </template>
        <p v-if="sendError" class="lc-note is-red" role="alert">{{ sendError }}</p>
      </form>
    </div>
    <ChatInfoPanel v-if="infoOpen && session" :session="session" :messages="messages" :manager-name="bridge.name" @close="infoOpen = false" />
  </div>
</template>
