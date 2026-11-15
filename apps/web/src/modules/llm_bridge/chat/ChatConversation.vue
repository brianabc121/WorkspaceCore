<script setup lang="ts">
import { computed, nextTick, ref, toRef } from 'vue'
import { Archive, ArrowLeft, Info, Paperclip, Pencil, RefreshCw, Send, Square, X } from '@lucide/vue'
import DataState from '../../../components/DataState.vue'
import { useMediaQuery } from '../../../composables/useMediaQuery'
import { CHAT_NO_TOOLS, MODES, platformName, type Bridge, type Quota } from '../api'
import { ATTACHMENTS, IMAGE_ACCEPT, chatError, fileSize, needsTakeover, newRequestId, ownerText, type ChatAttachment, type LlmChatApi } from './chatApi'
import { useLiveConversation } from './useLiveConversation'
import ChatMessage from './ChatMessage.vue'
import ChatInfoPanel from './ChatInfoPanel.vue'
import ChatQueue from './ChatQueue.vue'

// 中间一栏：一个会话的对话。父组件按“管理器 + 会话”给 key，换会话时整个重建。
// 内容和进度（长轮询读事件）在 useLiveConversation 里。
const props = defineProps<{ chat: LlmChatApi; bridge: Bridge; sessionId: string; narrow: boolean; initialDraft?: string; initialError?: string; quotas?: Record<string, Quota> | null; targetTurn?: string | null }>()
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
// 从通知、执行记录、会话记录点过来时带着回合编号：第一次读到内容后滚到那一回合并标出来
let jumped = false
function jump() {
  if (jumped || !props.targetTurn) return
  const el = box.value?.querySelector<HTMLElement>(`li.is-reply[data-turn="${props.targetTurn}"]`)
  if (!el) return
  jumped = true; stick = false
  el.scrollIntoView?.({ block: 'center' })
}
const { conversation, session, messages, lost, activeTurn, poll, reload, sent, resume, resuming, resumeError } = useLiveConversation(
  props.chat, bridgeId, toRef(props, 'sessionId'), { activity: () => { scrollDown(); void nextTick(jump) }, changed: () => emit('changed') },
)

// ---- 发送 ----
const draft = ref(props.initialDraft ?? ''), sending = ref(false), stopping = ref(false), sendError = ref(props.initialError ?? '')
const confirmed = ref(false)
// 会话在处理别的消息时：排队（默认，不打断）还是插入（送进正在处理的回合；平台做不到就中断它、合并重发）
const sendMode = ref<'queue' | 'insert'>('queue'), sendNotice = ref('')
const inserting = computed(() => sendMode.value === 'insert' && activeTurn.value?.status === 'running')
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
  if (uploading.value) { sendError.value = '附件还在上传，传完再发。'; return }
  if (pending?.text !== text) pending = { text, id: newRequestId() }
  const files = [...attached.value]
  const body = { text, request_id: pending.id, ...(files.length ? { attachments: files.map(a => a.id) } : {}), ...(inserting.value ? { mode: 'insert' as const } : {}) }
  sending.value = true; sendError.value = sendNotice.value = ''
  try {
    let turn
    try { turn = await props.chat.send(props.bridge.id, props.sessionId, { ...body, takeover: foreign.value }) }
    catch (e) {
      // 列表里显示的“谁建的”可能已经过时：服务器说要确认，就在这里补问一次
      if (!needsTakeover(e) || !allowTakeover()) throw e
      turn = await props.chat.send(props.bridge.id, props.sessionId, { ...body, takeover: true })
    }
    pending = null; draft.value = ''; attached.value = []; sendMode.value = 'queue'
    stick = true
    if (turn.delivered === 'inserted') {
      // 没有新回合：消息进了正在处理的那一回合，重新读一次把它显示在那一问后面
      sendNotice.value = '已插入：这条消息送进了正在处理的回合。'
      await reload()
    } else {
      if (turn.delivered === 'merged') sendNotice.value = '平台没有收下插入的消息：已中断上一回合，这条消息会带着“接着刚才没做完的继续”重新开始。'
      sent(turn, text, files)
      if (turn.delivered === 'merged') void reload()
    }
    emit('changed')
    void props.chat.invalidateQueue(props.bridge.id, props.sessionId)
  } catch (e) {
    sendError.value = needsTakeover(e) ? '没有发送：接着别人建的会话发消息前需要确认。' : chatError(e)
  } finally { sending.value = false }
}
// ---- 附件：先上传到管理器，发送时带上编号；对话会话只能带图片 ----
const attached = ref<ChatAttachment[]>([]), uploading = ref(0), picker = ref<HTMLInputElement | null>(null)
const imagesOnly = computed(() => session.value?.mode !== 'dev')
function pick(event: Event) {
  const input = event.target as HTMLInputElement, chosen = Array.from(input.files ?? [])
  input.value = ''
  void addFiles(chosen)
}
// 点按钮选、粘贴、拖进来，都走这里
async function addFiles(chosen: File[]) {
  if (!chosen.length || blocked.value) return
  sendError.value = ''
  for (const file of chosen) {
    if (imagesOnly.value && !IMAGE_ACCEPT.split(',').includes(file.type)) { sendError.value = `「${file.name}」不是图片：对话会话只能带图片，别的文件要在开发会话里发。`; continue }
    if (attached.value.length + uploading.value >= ATTACHMENTS.max) { sendError.value = `一条消息最多带 ${ATTACHMENTS.max} 个附件。`; break }
    if (file.size > ATTACHMENTS.bytes) { sendError.value = `「${file.name}」太大：单个最多 20 MB。`; continue }
    uploading.value += 1
    try { attached.value.push(await props.chat.upload(props.bridge.id, props.sessionId, file)) }
    catch (e) { sendError.value = `「${file.name}」没有传上去：${chatError(e)}` }
    finally { uploading.value -= 1 }
  }
}
// 粘贴：剪贴板里有文件（截图、复制的图片或文件）就当附件加上；只有文字时照常粘贴
function onPaste(event: ClipboardEvent) {
  const pasted = Array.from(event.clipboardData?.files ?? [])
  if (!pasted.length) return
  event.preventDefault()
  void addFiles(pasted)
}
// 拖拽：只理会拖进来的是文件的情况；进出子元素会各触发一次，用计数判断是不是真的离开了
const dragging = ref(0)
const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files')
function onDragEnter(event: DragEvent) { if (hasFiles(event) && !blocked.value) { event.preventDefault(); dragging.value += 1 } }
function onDragOver(event: DragEvent) { if (hasFiles(event) && !blocked.value) event.preventDefault() }
function onDragLeave(event: DragEvent) { if (hasFiles(event)) dragging.value = Math.max(0, dragging.value - 1) }
function onDrop(event: DragEvent) {
  if (!hasFiles(event)) return
  event.preventDefault()
  dragging.value = 0
  void addFiles(Array.from(event.dataTransfer?.files ?? []))
}
async function detach(item: ChatAttachment) {
  attached.value = attached.value.filter(a => a.id !== item.id)
  // 管理器那边的文件一起去掉；去不掉也没关系，没发出去的附件过一天会自动清
  try { await props.chat.discardUpload(props.bridge.id, props.sessionId, item.id) } catch { /* 见上 */ }
}
const source = computed(() => ({ chat: props.chat, bridgeId: props.bridge.id, sessionId: props.sessionId }))
function reimport() {
  if (!window.confirm('重新向官方程序读一次这个会话的内容？\n\n官方程序里又有了新内容时用。经工作台发的消息不受影响。')) return
  return act(() => props.chat.reimport(props.bridge.id, props.sessionId))
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
            <span v-if="session.mode === 'chat'" class="lc-tag" :title="CHAT_NO_TOOLS">不能执行命令</span>
            <span v-if="session.status === 'archived'" class="lc-tag">已归档</span>
          </p>
        </div>
        <div v-if="session" class="lc-head-actions">
          <button v-if="session.origin === 'external'" type="button" class="lc-icon" aria-label="重新导入" title="重新向官方程序读一次内容" :disabled="working" @click="reimport"><RefreshCw aria-hidden="true" /></button>
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
            <ChatMessage v-for="(m, i) in messages" :key="`${m.turn_id ?? ''}:${m.role}:${i}`" :message="m" :platform="platformName(session.platform)" :no-tools="session.mode === 'chat'" :target="!!targetTurn && m.turn_id === targetTurn" :resuming="resuming" :source="source" @resume="resume" />
          </ol>
          <p v-if="resumeError" class="lc-note is-red" role="alert">{{ resumeError }}</p>
          <p v-if="lost" class="lc-note is-red" role="alert">{{ lost }} <button type="button" class="asv-link" @click="poll()">重试</button></p>
        </template>
      </div>

      <ChatQueue v-if="session" :chat="chat" :bridge-id="bridge.id" :session-id="sessionId" :busy="!!activeTurn" @changed="poll()" />
      <form
        v-if="session" class="lc-composer" :class="{ 'is-dropping': dragging > 0 }" aria-label="发送消息" @submit.prevent="submit"
        @dragenter="onDragEnter" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop"
      >
        <p v-if="blocked" class="lc-note" role="note">{{ blocked }}</p>
        <template v-else>
          <p v-if="foreign" class="lc-note" role="note">{{ session.owner_kind === 'external' ? '这是官方程序里已有的会话' : `这是${session.owner_kind === 'agent' ? '智能体' : ''}「${ownerText(session)}」建的会话` }}：可以看；接着发消息前会让你确认一次。</p>
          <p v-if="dragging > 0" class="lc-note" role="status">松开鼠标，把{{ imagesOnly ? '图片' : '图片或文件' }}加到这条消息里。</p>
          <ul v-if="attached.length || uploading" class="lc-attached" aria-label="要带上的附件">
            <li v-for="a in attached" :key="a.id"><span class="lc-file-name">{{ a.name }}</span><span class="lc-file-size">{{ fileSize(a.size) }}</span><button type="button" class="lc-icon" :aria-label="`去掉附件：${a.name}`" @click="detach(a)"><X aria-hidden="true" /></button></li>
            <li v-if="uploading" role="status">正在上传 {{ uploading }} 个…</li>
          </ul>
          <div class="lc-compose-row">
            <input ref="picker" type="file" multiple hidden :accept="imagesOnly ? IMAGE_ACCEPT : undefined" aria-label="选择要带上的图片或文件" @change="pick" />
            <button type="button" class="lc-icon" :aria-label="imagesOnly ? '添加图片' : '添加图片或文件'" :title="imagesOnly ? '添加图片（对话会话只能带图片）' : '添加图片或文件（文件会放进工作目录）'" :disabled="sending" @click="picker?.click()"><Paperclip aria-hidden="true" /></button>
            <textarea v-model="draft" rows="2" maxlength="100000" aria-label="消息内容" :placeholder="`输入消息：Enter 发送，Shift + Enter 换行；可以粘贴或拖入${imagesOnly ? '图片' : '图片、文件'}`" @keydown="onKey" @paste="onPaste" />
            <button v-if="activeTurn" type="button" class="secondary-button" :disabled="stopping" @click="stop"><Square aria-hidden="true" />{{ stopping ? '正在停止…' : '停止' }}</button>
            <button type="submit" class="primary-button" :disabled="sending || !draft.trim()"><Send aria-hidden="true" />{{ sending ? '正在发送…' : '发送' }}</button>
          </div>
          <fieldset v-if="activeTurn?.status === 'running'" class="lc-mode" aria-label="上一条还没答完时怎么发">
            <label class="check-field"><input v-model="sendMode" type="radio" name="lc-send-mode" value="queue" />排队</label>
            <label class="check-field"><input v-model="sendMode" type="radio" name="lc-send-mode" value="insert" />插入</label>
          </fieldset>
          <p v-if="activeTurn" class="lc-hint">{{ inserting ? '插入：这条消息直接送进正在处理的回合。平台做不到时，会中断正在处理的回合，把这条消息和“接着刚才没做完的继续”一起重新发。' : '上一条还没答完：现在发的不会打断它，会进队列，等前面的做完再发给模型。' }}</p>
          <p v-if="sendNotice" class="lc-note" role="status">{{ sendNotice }}</p>
        </template>
        <p v-if="sendError" class="lc-note is-red" role="alert">{{ sendError }}</p>
      </form>
    </div>
    <ChatInfoPanel v-if="infoOpen && session" :session="session" :messages="messages" :manager-name="bridge.name" :quota="quotas?.[session.platform] ?? null" @close="infoOpen = false" />
  </div>
</template>
