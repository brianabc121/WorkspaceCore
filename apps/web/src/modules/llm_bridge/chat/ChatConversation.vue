<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, toRef, watch } from 'vue'
import { Archive, ArrowLeft, Info, Pencil, Send, Square } from '@lucide/vue'
import DataState from '../../../components/DataState.vue'
import { useMediaQuery } from '../../../composables/useMediaQuery'
import { MODES, platformName, type Bridge } from '../api'
import { TERMINAL_EVENTS, chatError, needsTakeover, newRequestId, ownerText, type ChatConversation, type ChatEvent, type ChatMessage as Message, type LlmChatApi } from './chatApi'
import ChatMessage from './ChatMessage.vue'
import ChatInfoPanel from './ChatInfoPanel.vue'

// 中间一栏：一个会话的对话。父组件按“管理器 + 会话”给 key，换会话时整个重建。
// 进度：有回合没做完时用长轮询读事件（一段回答、一条命令、改动的文件），在本地接到对应的回答上；
// 回合结束、或出现这里不认识的回合（别人或智能体发的）时重新读整段会话，以服务器给的为准。
const props = defineProps<{ chat: LlmChatApi; bridge: Bridge; sessionId: string; narrow: boolean; initialDraft?: string; initialError?: string }>()
const emit = defineEmits<{ back: []; changed: [] }>()

const bridgeId = computed(() => props.bridge.id)
const conversation = props.chat.useConversation(bridgeId, toRef(props, 'sessionId'))
const session = computed(() => conversation.data.value?.session ?? null)
const messages = ref<Message[]>([])
const lost = ref('')
let cursor = 0, alive = true, polling = false
const abort = new AbortController()

// ---- 滚动：人在底部附近时，新内容出来就跟到底部；往上翻着看时不打扰 ----
const box = ref<HTMLElement | null>(null)
let stick = true
function onScroll() { const el = box.value; if (el) stick = el.scrollHeight - el.scrollTop - el.clientHeight < 80 }
function scrollDown(force = false) {
  if (force) stick = true
  void nextTick(() => { const el = box.value; if (el && stick) el.scrollTop = el.scrollHeight })
}

function load(data: ChatConversation) {
  messages.value = data.messages.map(m => ({ ...m, steps: [...(m.steps ?? [])] }))
  cursor = data.last_event_id
  scrollDown()
  if (data.session.busy) void poll()
}
watch(() => conversation.data.value, data => { if (data) load(data) }, { immediate: true })
async function reload() {
  const { data } = await conversation.refetch()
  if (data && alive) load(data)
}

/** 把新事件接到对应的回答上；返回 true 表示要重新读整段会话 */
function apply(events: ChatEvent[]): boolean {
  let stale = false
  for (const e of events) {
    if (!e.turn_id) continue
    const reply = messages.value.find(m => m.role === 'assistant' && m.turn_id === e.turn_id)
    if (!reply || TERMINAL_EVENTS.includes(e.kind)) { stale = true; continue }
    const steps = (reply.steps ??= [])
    if (e.kind === 'started') { reply.status = 'running'; reply.ahead_in_session = null }
    else if (e.kind === 'message' && e.text) reply.text = reply.text ? `${reply.text}\n\n${e.text}` : e.text
    else if (e.kind === 'notice' && e.text) steps.push({ kind: 'notice', text: e.text, at: e.at })
    else if (e.kind === 'command' && e.command) steps.push({ kind: 'command', text: e.command, at: e.at })
    else if (e.kind === 'files' && e.paths?.length) steps.push({ kind: 'files', text: e.paths.join('、'), at: e.at })
  }
  if (events.length) scrollDown()
  return stale
}
const pause = (ms: number) => new Promise<void>(resolve => { setTimeout(resolve, ms) })
async function poll() {
  if (polling || !alive) return
  polling = true
  let failures = 0
  try {
    while (alive) {
      let out
      const asked = Date.now()
      try {
        out = await props.chat.events(props.bridge.id, props.sessionId, cursor, abort.signal)
        failures = 0; lost.value = ''
      } catch (e) {
        if (!alive) return
        if (++failures >= 3) { lost.value = `读不到新的进度：${chatError(e)}`; return }
        await pause(1500 * failures)
        continue
      }
      if (!alive) return
      const fresh = out.events.filter(e => e.id > cursor)
      cursor = Math.max(cursor, out.last_id)
      if (apply(fresh)) { await reload(); emit('changed') }
      if (!out.busy) return
      // 服务器本该等到有新事件才返回：万一立刻空着回来，歇一下再问，不空转
      if (!fresh.length && Date.now() - asked < 1000) await pause(1000)
    }
  } finally { polling = false }
}
// 智能体可能随时往这个会话里发消息：空闲时隔一会儿重新读一次
const idle = setInterval(() => { if (!polling && alive && !conversation.isFetching.value) void reload() }, 15000)
onBeforeUnmount(() => { alive = false; abort.abort(); clearInterval(idle) })

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
const activeTurn = computed(() => {
  const open = messages.value.filter(m => m.role === 'assistant' && (m.status === 'running' || m.status === 'queued'))
  return open.find(m => m.status === 'running') ?? open[0] ?? null
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
    if (!messages.value.some(m => m.turn_id === turn.turn_id)) {
      const at = Date.now() / 1000
      messages.value.push(
        { role: 'user', turn_id: turn.turn_id, text, at, mine: true, steps: [] },
        { role: 'assistant', turn_id: turn.turn_id, text: '', at, status: turn.status, steps: [], ahead_in_session: turn.ahead_in_session ?? null },
      )
    }
    scrollDown(true); emit('changed'); void poll()
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

      <form v-if="session" class="lc-composer" aria-label="发送消息" @submit.prevent="submit">
        <p v-if="blocked" class="lc-note" role="note">{{ blocked }}</p>
        <template v-else>
          <p v-if="foreign" class="lc-note" role="note">{{ session.owner_kind === 'external' ? '这是官方程序里已有的会话' : `这是${session.owner_kind === 'agent' ? '智能体' : ''}「${ownerText(session)}」建的会话` }}：可以看；接着发消息前会让你确认一次。</p>
          <div class="lc-compose-row">
            <textarea v-model="draft" rows="2" maxlength="100000" aria-label="消息内容" placeholder="输入消息：Enter 发送，Shift + Enter 换行" @keydown="onKey" />
            <button v-if="activeTurn" type="button" class="secondary-button" :disabled="stopping" @click="stop"><Square aria-hidden="true" />{{ stopping ? '正在停止…' : '停止' }}</button>
            <button type="submit" class="primary-button" :disabled="sending || !draft.trim()"><Send aria-hidden="true" />{{ sending ? '正在发送…' : '发送' }}</button>
          </div>
          <p v-if="activeTurn" class="lc-hint">上一条还没答完：现在发的会排在它后面。</p>
        </template>
        <p v-if="sendError" class="lc-note is-red" role="alert">{{ sendError }}</p>
      </form>
    </div>
    <ChatInfoPanel v-if="infoOpen && session" :session="session" :messages="messages" :manager-name="bridge.name" @close="infoOpen = false" />
  </div>
</template>
