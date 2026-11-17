<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, toRef, watch } from 'vue'
import { Archive, ArrowLeft, BookOpen, ChevronDown, ChevronUp, Code2, Cpu, Download, Eraser, Layers, MessageSquare, MoreHorizontal, PanelRight, Paperclip, Pencil, RefreshCw, Search, Send, Square, X } from '@lucide/vue'
import DataState from '../../../components/DataState.vue'
import { useMediaQuery } from '../../../composables/useMediaQuery'
import { CHAT_NO_TOOLS, MODES, platformName, seconds, type Bridge, type Platform, type Quota } from '../api'
import { ATTACHMENTS, IMAGE_ACCEPT, PLATFORM_MARK, chatError, fileSize, hasContext, isClearCommand, needsTakeover, newRequestId, ownerText, slashCommand, type ChatAttachment, type ChatMessage as Message, type ChatNoteHit, type LlmChatApi } from './chatApi'
import { useLiveConversation } from './useLiveConversation'
import ChatMessage from './ChatMessage.vue'
import ChatInfoPanel from './ChatInfoPanel.vue'
import ChatQueue from './ChatQueue.vue'
import ChatKnowledge from './ChatKnowledge.vue'
import ChatSaveNote from './ChatSaveNote.vue'

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
  props.chat, bridgeId, toRef(props, 'sessionId'), { activity: () => { scrollDown(); void nextTick(jump) }, changed: () => { badge(); emit('changed') } },
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
// 草稿按会话存在浏览器里：切换会话、刷新页面都还在；发出去就清掉
const draftKey = `llm_chat_draft:${props.bridge.id}:${props.sessionId}`
function loadDraft() { try { return window.localStorage.getItem(draftKey) ?? '' } catch { return '' } }
function saveDraft(value: string) {
  try { if (value) window.localStorage.setItem(draftKey, value.slice(0, 100000)); else window.localStorage.removeItem(draftKey) } catch { /* 存不了就算了 */ }
}
if (!draft.value) draft.value = loadDraft()
watch(draft, saveDraft)
// 回答到了而人不在这个页面：在标签页标题上标出来，回来就恢复
let titleBefore: string | null = null
function badge() { if (document.hidden && titleBefore === null) { titleBefore = document.title; document.title = `● 有新回答 · ${titleBefore}` } }
function unbadge() { if (!document.hidden && titleBefore !== null) { document.title = titleBefore; titleBefore = null } }
document.addEventListener('visibilitychange', unbadge)
onBeforeUnmount(() => { document.removeEventListener('visibilitychange', unbadge); if (titleBefore !== null) document.title = titleBefore })
async function submit() {
  // 清空上下文还没做完（页面上可能已经画出分隔线）就按了发送：等它做完再发，不能把这条吞掉
  if (clearRun) await clearRun
  const text = draft.value.trim()
  if (!text || sending.value || blocked.value) return
  // 只输入 /clear：清空上下文，不发给模型（官方程序在这里不执行斜杠命令，T-0255）
  if (isClearCommand(text)) { await clearContext(true); return }
  if (foreign.value && !allowTakeover()) return
  if (uploading.value) { sendError.value = '附件还在上传，传完再发。'; return }
  if (pending?.text !== text) pending = { text, id: newRequestId() }
  const files = [...attached.value], notes = [...picked.value]
  const body = { text, request_id: pending.id, ...(files.length ? { attachments: files.map(a => a.id) } : {}), ...(notes.length ? { knowledge_notes: notes.map(n => n.id) } : {}), ...(inserting.value ? { mode: 'insert' as const } : {}) }
  sending.value = true; sendError.value = sendNotice.value = ''
  try {
    let turn
    try { turn = await props.chat.send(props.bridge.id, props.sessionId, { ...body, takeover: foreign.value }) }
    catch (e) {
      // 列表里显示的“谁建的”可能已经过时：服务器说要确认，就在这里补问一次
      if (!needsTakeover(e) || !allowTakeover()) throw e
      turn = await props.chat.send(props.bridge.id, props.sessionId, { ...body, takeover: true })
    }
    pending = null; draft.value = ''; attached.value = []; picked.value = []; sendMode.value = 'queue'
    stick = true
    if (turn.delivered === 'inserted') {
      // 没有新回合：消息进了正在处理的那一回合，重新读一次把它显示在那一问后面
      sendNotice.value = '已插入：这条消息送进了正在处理的回合。'
      await reload()
    } else {
      if (turn.delivered === 'merged') sendNotice.value = '平台没有收下插入的消息：已中断上一回合，这条消息会带着“接着刚才没做完的继续”重新开始。'
      sent(turn, text, files, notes.map(n => ({ note_id: n.id, title: n.title })))
      if (turn.delivered === 'merged') void reload()
    }
    emit('changed')
    void props.chat.invalidateQueue(props.bridge.id, props.sessionId)
  } catch (e) {
    sendError.value = needsTakeover(e) ? '没有发送：接着别人建的会话发消息前需要确认。' : chatError(e)
  } finally { sending.value = false }
}
// ---- 清空上下文（T-0255）：管理器给会话换一个新的官方会话，之后的消息从头开始，上面的记录保留 ----
const clearing = ref(false)
let clearRun: Promise<void> | null = null
const canClear = computed(() => !!session.value && session.value.origin === 'bridge' && session.value.status === 'active' && props.bridge.enabled)
function clearText(other: boolean) {
  const s = session.value
  const lines = ['清空这个会话的上下文？', '', '之后发的消息从头开始：模型不再记得上面的内容，每条消息用的 token 也回到开头的水平。上面的记录还在，可以照常查看和导出。']
  if (s?.mode === 'dev') lines.push('开发会话工作目录里的文件不受影响。')
  if (other) {
    const who = s && s.owner_kind === 'agent' ? `智能体「${ownerText(s)}」` : s && s.owner_kind !== 'me' ? `「${ownerText(s)}」` : '别人'
    lines.push('', `这是${who}建的会话：清空后对方再用这个会话时也是从头开始。`)
  }
  return lines.join('\n')
}
// typed：在输入框里输入了 /clear。那是明确要清空，自己的会话不再问；按钮和别人的会话先确认
async function clearContext(typed = false) {
  if (!session.value || clearRun) return
  clearRun = runClear(typed)
  try { await clearRun } finally { clearRun = null }
}
async function runClear(typed: boolean) {
  sendError.value = sendNotice.value = ''
  if (activeTurn.value) { sendError.value = '还有消息在排队或进行中：等它们做完或停止后再清空。'; return }
  if (!hasContext(messages.value)) {
    sendNotice.value = '上次清空之后还没有发过消息，不用再清空。'
    if (typed) draft.value = ''
    return
  }
  if ((!typed || foreign.value) && !window.confirm(clearText(foreign.value))) return
  clearing.value = true
  try {
    let out
    try { out = await props.chat.clearContext(props.bridge.id, props.sessionId, foreign.value) }
    catch (e) {
      // 列表里显示的“谁建的”可能已经过时：服务器说要确认，就在这里补问一次
      if (!needsTakeover(e) || !window.confirm(clearText(true))) throw e
      out = await props.chat.clearContext(props.bridge.id, props.sessionId, true)
    }
    if (typed) draft.value = ''
    sendNotice.value = out.cleared ? '已清空上下文：之后的消息从头开始，上面的记录还在。' : '上次清空之后还没有发过消息，不用再清空。'
    stick = true
    await reload()
    emit('changed')
  } catch (e) {
    sendError.value = needsTakeover(e) ? '没有清空：清空别人建的会话前需要确认。' : `没能清空：${chatError(e)}`
  } finally { clearing.value = false }
}

// ---- 管得了上下文（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.2）：手动压缩、带着摘要另开会话 ----
// 两件事都排在会话队列里（有消息在跑也可以点）；别人建的会话先确认一次，和发送一样
const shaping = ref('')
const CONTEXT_ACTIONS = {
  compact: { ask: '压缩这个会话的上下文？\n\n平台会把之前的内容压成摘要（这一步本身要花一次模型调用），之后每回合花的 token 变少，但前面的细节只剩摘要。', done: '已排上手动压缩：轮到时平台会把之前的内容压成摘要。', fail: '没能压缩' },
  continue: { ask: '带着摘要另开会话？\n\n先在现在的上下文里要一份交接摘要，然后清空上下文，把摘要作为新上下文的第一条发出去。上面的记录都还在；之后的消息接着在这个会话里发。比一直在长会话里聊省 token。', done: '已排上“带着摘要另开会话”：先要摘要，再清空上下文、发出摘要。', fail: '没能带着摘要另开会话' },
} as const
async function shapeContext(action: keyof typeof CONTEXT_ACTIONS) {
  if (!session.value || shaping.value) return
  const text = CONTEXT_ACTIONS[action]
  sendError.value = sendNotice.value = ''
  if (!window.confirm(foreign.value ? `${text.ask}\n\n${takeoverText.value}` : text.ask)) return
  shaping.value = action
  const run = (takeover: boolean) => (action === 'compact' ? props.chat.compact(props.bridge.id, props.sessionId, '', takeover) : props.chat.continueWithSummary(props.bridge.id, props.sessionId, takeover))
  try {
    let out: Awaited<ReturnType<typeof run>>
    try { out = await run(foreign.value) }
    catch (e) {
      if (!needsTakeover(e) || !window.confirm(takeoverText.value)) throw e
      out = await run(true)
    }
    // 平台压缩时写过摘要（打开了压缩钩子，4.3）：直接用它，不再先问一次
    sendNotice.value = out && 'summary_from' in out && out.summary_from === 'compaction' ? '已排上“带着摘要另开会话”：直接用平台压缩时写的摘要，清空上下文后发出（不再先问一次）。' : text.done
    stick = true
    await reload()
    void poll()
    emit('changed')
  } catch (e) {
    sendError.value = needsTakeover(e) ? `${text.fail}：接着别人建的会话操作前需要确认。` : `${text.fail}：${chatError(e)}`
  } finally { shaping.value = '' }
}

// ---- 提问前用上知识库（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 第 6 节）：相近内容的提示、挑选笔记带上 ----
const picked = ref<ChatNoteHit[]>([])
const kbPanel = ref<InstanceType<typeof ChatKnowledge> | null>(null)
// 存进知识库（5.2）：一条回答 → 人确认后的正式笔记
const saving = ref<{ question: string; answer: string; link: string } | null>(null)
function saveAnswer(message: Message) {
  const question = messages.value.find(m => m.role === 'user' && m.turn_id === message.turn_id)?.text ?? ''
  const link = `/llm_bridge?m=${props.bridge.id}&s=${props.sessionId}${message.turn_id ? `&t=${message.turn_id}` : ''}`
  saving.value = { question, answer: message.text, link }
}
// 会话纪要的操作（5.2、5.3）：在会话信息里
async function digestAction(action: 'on' | 'off' | 'write' | 'promote' | 'delete') {
  if (action === 'delete' && !window.confirm('删除这个会话的纪要？\n\n纪要放进知识库的回收站（30 天内可以恢复），之后不再为这个会话整理纪要。')) return
  if (action === 'promote' && !window.confirm('把这篇会话纪要转成正式笔记？\n\n转成后它就是你确认过的普通笔记，放到知识库的根目录，之后不再自动更新。')) return
  await act(() => props.chat.digest(props.bridge.id, props.sessionId, action))
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
  if (event.isComposing) return
  // Esc：停止正在回答的回合
  if (event.key === 'Escape' && activeTurn.value?.status === 'running') { event.preventDefault(); void stop(); return }
  // 输入框空着时按 ↑：把自己上一条发的消息放回来改
  if (event.key === 'ArrowUp' && !draft.value) {
    const mine = [...messages.value].reverse().find(m => m.role === 'user' && m.mine && m.text)
    if (mine) { event.preventDefault(); draft.value = mine.text }
    return
  }
  // Enter 发送，Shift + Enter 换行；输入法还在选字时的 Enter 不算
  if (event.key !== 'Enter' || event.shiftKey) return
  event.preventDefault()
  void submit()
}
// 重试 / 重新生成：把那一回合的提问原样再发一次（新的回合，原来的还在）
async function again(turnId: string) {
  const asked = messages.value.find(m => m.role === 'user' && m.turn_id === turnId && m.delivery !== 'inserted')
  if (!asked?.text) return
  if (draft.value.trim()) { sendError.value = '输入框里还有没发的内容：先发出去或清空，再点重试。'; return }
  draft.value = asked.text
  await submit()
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
// 换模型：Claude / CodeBuddy 每个回合起一次官方程序，模型可以逐回合换；Codex 的会话换不了
const canSwitch = computed(() => !!session.value && ['claude', 'workbuddy'].includes(session.value.platform) && session.value.origin === 'bridge' && session.value.status === 'active' && props.bridge.enabled)
const optionPlatform = computed(() => (canSwitch.value ? (session.value!.platform as Platform) : null))
const optionMode = computed<'chat' | 'dev'>(() => (session.value?.mode === 'dev' ? 'dev' : 'chat'))
const options = props.chat.useOptions(bridgeId, optionPlatform, optionMode)
const models = computed(() => (canSwitch.value ? (options.data.value?.models ?? []).map(m => ({ id: m.id, name: m.name })) : []))
function setModel(id: string) {
  if (!id || id === session.value?.model) return
  return act(() => props.chat.setModel(props.bridge.id, props.sessionId, id))
}
// 导出：提问、回答、每回合的状态和用时，存成一个 Markdown 文件
async function exportSession() {
  if (working.value || !session.value) return
  working.value = true; headError.value = ''
  try {
    const blob = await props.chat.exportBlob(props.bridge.id, props.sessionId)
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = `${session.value.title || '会话'}.md`; link.rel = 'noopener'
    document.body.append(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) { headError.value = `没能导出：${chatError(e)}` } finally { working.value = false }
}
function archive() {
  if (!window.confirm('归档这个会话？\n\n归档后不能再往里发消息，记录保留，可以在左栏的“已归档”里找到。')) return
  return act(() => props.chat.archive(props.bridge.id, props.sessionId))
}

// ---- 头部的“更多操作”：改标题、清空上下文、导出、归档、重新导入。点外面、按 Esc、做了一个操作后收起 ----
const moreOpen = ref(false), moreBox = ref<HTMLElement | null>(null)
function outsideMore(event: MouseEvent) { if (moreOpen.value && moreBox.value && !moreBox.value.contains(event.target as Node)) moreOpen.value = false }
function escapeMore(event: KeyboardEvent) { if (event.key === 'Escape') moreOpen.value = false }
document.addEventListener('click', outsideMore, true)
document.addEventListener('keydown', escapeMore)
function menu(action: () => unknown) { moreOpen.value = false; return action() }

// ---- 会话内搜索：在这个会话的提问和回答里找一段文字，逐处跳过去 ----
// 命中的那条消息标出来；浏览器支持 CSS Custom Highlight 时把命中的文字也高亮（不改页面内容）
const searchOpen = ref(false), query = ref(''), hitAt = ref(0), searchInput = ref<HTMLInputElement | null>(null)
const needle = computed(() => query.value.trim().toLowerCase())
const hits = computed(() => (needle.value
  ? messages.value.flatMap((m, i) => ((m.role === 'user' || m.role === 'assistant') && [m.text, m.partial].filter(Boolean).join('\n').toLowerCase().includes(needle.value) ? [i] : []))
  : []))
const hitNow = computed(() => (hits.value.length ? hits.value[Math.min(hitAt.value, hits.value.length - 1)]! : -1))
function openSearch() {
  if (searchOpen.value) { closeSearch(); return }
  searchOpen.value = true
  void nextTick(() => searchInput.value?.focus())
}
function closeSearch() { searchOpen.value = false; query.value = ''; hitAt.value = 0; paint() }
function stepHit(delta: number) {
  const n = hits.value.length
  if (n) hitAt.value = (Math.min(hitAt.value, n - 1) + delta + n) % n
}
function onSearchKey(event: KeyboardEvent) {
  if (event.isComposing) return
  if (event.key === 'Enter') { event.preventDefault(); stepHit(event.shiftKey ? -1 : 1) }
  else if (event.key === 'Escape') { event.preventDefault(); closeSearch() }
}
type HighlightRegistry = { set(name: string, value: unknown): void; delete(name: string): void }
function paint() {
  const registry = (globalThis as { CSS?: { highlights?: HighlightRegistry } }).CSS?.highlights
  const Mark = (globalThis as { Highlight?: new (...ranges: Range[]) => unknown }).Highlight
  if (!registry || !Mark) return
  registry.delete('lc-hit'); registry.delete('lc-hit-now')
  const root = box.value
  if (!searchOpen.value || !needle.value || !root) return
  const all: Range[] = [], now: Range[] = []
  for (const bubble of Array.from(root.querySelectorAll<HTMLElement>('li[data-msg] .lc-bubble'))) {
    const current = bubble.closest('li')?.getAttribute('data-msg') === String(hitNow.value)
    const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = (node.textContent ?? '').toLowerCase()
      for (let at = text.indexOf(needle.value); at >= 0; at = text.indexOf(needle.value, at + needle.value.length)) {
        const range = document.createRange()
        range.setStart(node, at); range.setEnd(node, at + needle.value.length)
        ;(current ? now : all).push(range)
      }
    }
  }
  registry.set('lc-hit', new Mark(...all))
  registry.set('lc-hit-now', new Mark(...now))
}
watch(needle, () => { hitAt.value = 0 })
watch([hitNow, () => messages.value.length], () => {
  void nextTick(() => {
    paint()
    if (hitNow.value < 0) return
    stick = false
    box.value?.querySelector<HTMLElement>(`li[data-msg="${hitNow.value}"]`)?.scrollIntoView?.({ block: 'center' })
  })
})
onBeforeUnmount(() => {
  document.removeEventListener('click', outsideMore, true)
  document.removeEventListener('keydown', escapeMore)
  const registry = (globalThis as { CSS?: { highlights?: HighlightRegistry } }).CSS?.highlights
  registry?.delete('lc-hit'); registry?.delete('lc-hit-now')
})
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
        </div>
        <div v-if="session" class="lc-head-actions">
          <button type="button" class="lc-icon" :aria-pressed="searchOpen" aria-label="在会话里搜索" title="在这个会话里搜索" @click="openSearch"><Search aria-hidden="true" /></button>
          <div ref="moreBox" class="lc-menu-wrap">
            <button type="button" class="lc-icon" aria-label="更多操作" title="更多操作" aria-haspopup="true" :aria-expanded="moreOpen" @click="moreOpen = !moreOpen"><MoreHorizontal aria-hidden="true" /></button>
            <div v-if="moreOpen" class="lc-menu" role="group" aria-label="会话操作">
              <button v-if="!renaming && session.origin !== 'external'" type="button" aria-label="改标题" :disabled="working" @click="menu(startRename)"><Pencil aria-hidden="true" />改标题</button>
              <button v-if="canClear" type="button" aria-label="清空上下文" title="之后的消息从头开始，上面的记录保留（也可以在输入框里输入 /clear）" :disabled="clearing" @click="menu(() => clearContext())"><Eraser aria-hidden="true" />清空上下文</button>
              <button v-if="session.origin === 'external'" type="button" aria-label="重新导入" title="重新向官方程序读一次内容" :disabled="working" @click="menu(reimport)"><RefreshCw aria-hidden="true" />重新导入</button>
              <button type="button" aria-label="导出会话" title="导出成 Markdown 文件" :disabled="working" @click="menu(exportSession)"><Download aria-hidden="true" />导出</button>
              <button v-if="session.status !== 'archived'" type="button" aria-label="归档会话" :disabled="working" @click="menu(archive)"><Archive aria-hidden="true" />归档</button>
            </div>
          </div>
          <button type="button" class="lc-icon" :aria-pressed="infoOpen" aria-label="会话信息" title="会话详情" @click="infoOpen = !infoOpen"><PanelRight aria-hidden="true" /></button>
        </div>
        <ul v-if="session" class="lc-chips" aria-label="会话的平台、模型与用法" :title="`${bridge.name} · ${ownerText(session)}建的`">
          <li class="lc-chip"><span class="lc-chip-mark" :data-platform="session.platform" aria-hidden="true">{{ PLATFORM_MARK[session.platform] ?? '?' }}</span>{{ platformName(session.platform) }}</li>
          <li v-if="models.length" class="lc-chip is-select" title="换了之后从下一回合起用新的模型">
            <Cpu aria-hidden="true" />
            <select :value="session.model ?? ''" :disabled="working" aria-label="之后的回合用的模型" @change="setModel(($event.target as HTMLSelectElement).value)">
              <option v-if="!session.model" value="" disabled>平台默认模型</option>
              <option v-else-if="!models.some(m => m.id === session!.model)" :value="session.model">{{ session.model }}</option>
              <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name || m.id }}</option>
            </select>
            <ChevronDown aria-hidden="true" />
          </li>
          <li v-else class="lc-chip"><Cpu aria-hidden="true" />{{ session.model || '平台默认模型' }}</li>
          <li class="lc-chip"><component :is="session.mode === 'dev' ? Code2 : MessageSquare" aria-hidden="true" />{{ MODES[session.mode] ?? session.mode }}模式</li>
          <li v-if="session.environment" class="lc-chip"><Layers aria-hidden="true" />{{ session.environment.name }}</li>
          <li v-if="session.mode === 'chat'" class="lc-chip is-plain" :title="CHAT_NO_TOOLS">不能执行命令</li>
          <li v-if="session.status === 'archived'" class="lc-chip is-warn">已归档</li>
        </ul>
      </header>
      <div v-if="searchOpen && session" class="lc-search-bar" role="search" aria-label="在这个会话里搜索">
        <Search aria-hidden="true" />
        <input ref="searchInput" v-model="query" type="search" maxlength="200" aria-label="要找的文字" placeholder="在提问和回答里找…" @keydown="onSearchKey" />
        <span class="lc-search-count" role="status">{{ !needle ? '' : hits.length ? `第 ${Math.min(hitAt, hits.length - 1) + 1} 处 / 共 ${hits.length} 处` : '没有找到' }}</span>
        <button type="button" class="lc-icon" aria-label="上一处" :disabled="hits.length < 2" @click="stepHit(-1)"><ChevronUp aria-hidden="true" /></button>
        <button type="button" class="lc-icon" aria-label="下一处" :disabled="hits.length < 2" @click="stepHit(1)"><ChevronDown aria-hidden="true" /></button>
        <button type="button" class="lc-icon" aria-label="关闭搜索" @click="closeSearch"><X aria-hidden="true" /></button>
      </div>
      <p v-if="headError" class="lc-note is-red" role="alert">{{ headError }}</p>
      <p v-if="session?.purged_at" class="lc-note" role="note">这个会话的对话内容已经按保留期清除（{{ seconds(session.purged_at) }}）：只剩下每个回合的状态、用时和用量。</p>

      <div ref="box" class="lc-scroll" @scroll.passive="onScroll">
        <DataState v-if="conversation.isPending.value || (conversation.error.value && !session)" :loading="conversation.isPending.value" :error="conversation.error.value" :empty="false" empty-text="" @retry="conversation.refetch()" />
        <template v-else-if="session">
          <p v-if="!messages.length" class="lc-none">这个会话里还没有消息。</p>
          <ol v-else class="lc-messages" aria-label="消息">
            <ChatMessage v-for="(m, i) in messages" :key="`${m.turn_id ?? ''}:${m.role}:${i}`" :message="m" :platform="platformName(session.platform)" :platform-id="session.platform" :no-tools="session.mode === 'chat'" :target="!!targetTurn && m.turn_id === targetTurn" :last="i === messages.length - 1" :data-msg="i" :class="{ 'is-hit': hits.includes(i), 'is-hit-now': i === hitNow }" :busy-action="sending" :resuming="resuming" :source="source" @resume="resume" @again="again" @save="saveAnswer" />
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
          <div v-if="session.context?.near_limit && session.status === 'active'" class="lc-note lc-context-warn" role="note">
            <span>这个会话的上下文已用 {{ session.context.percent }}%，快到平台自动压缩的位置了：压缩后前面的细节会变成摘要。要接着做长任务，带着摘要另开会话更省。</span>
            <span v-if="canClear" class="lc-context-actions">
              <button type="button" class="asv-link" :disabled="!!shaping" @click="shapeContext('compact')">{{ shaping === 'compact' ? '正在排…' : '压缩一下' }}</button>
              <button type="button" class="asv-link" :disabled="!!shaping" @click="shapeContext('continue')">{{ shaping === 'continue' ? '正在排…' : '带着摘要另开会话' }}</button>
            </span>
          </div>
          <p v-if="dragging > 0" class="lc-note" role="status">松开鼠标，把{{ imagesOnly ? '图片' : '图片或文件' }}加到这条消息里。</p>
          <ul v-if="attached.length || uploading" class="lc-attached" aria-label="要带上的附件">
            <li v-for="a in attached" :key="a.id"><span class="lc-file-name">{{ a.name }}</span><span class="lc-file-size">{{ fileSize(a.size) }}</span><button type="button" class="lc-icon" :aria-label="`去掉附件：${a.name}`" @click="detach(a)"><X aria-hidden="true" /></button></li>
            <li v-if="uploading" role="status">正在上传 {{ uploading }} 个…</li>
          </ul>
          <ChatKnowledge ref="kbPanel" v-model:selected="picked" :chat="chat" :bridge-id="bridge.id" :session-id="sessionId" :draft="draft" :disabled="sending" />
          <div class="lc-compose-box">
            <textarea v-model="draft" rows="2" maxlength="100000" aria-label="消息内容" :placeholder="`输入消息，或${imagesOnly ? '粘贴、拖入图片' : '粘贴、拖入图片与文件'}…`" @keydown="onKey" @paste="onPaste" />
            <div class="lc-compose-tools">
              <input ref="picker" type="file" multiple hidden :accept="imagesOnly ? IMAGE_ACCEPT : undefined" aria-label="选择要带上的图片或文件" @change="pick" />
              <button type="button" class="lc-tool" :title="imagesOnly ? '添加图片（对话会话只能带图片）' : '添加图片或文件（文件会放进工作目录）'" :disabled="sending" @click="picker?.click()"><Paperclip aria-hidden="true" />附件</button>
              <button type="button" class="lc-tool" title="参考知识库：挑笔记随这条消息带上" :disabled="sending" @click="kbPanel?.toggle()"><BookOpen aria-hidden="true" />知识库</button>
              <span v-if="session.environment" class="lc-env-state" :title="`开发环境：${session.environment.name}`">{{ session.environment.name }}</span>
              <span class="lc-compose-hint" aria-hidden="true">Enter 发送 · Shift + Enter 换行</span>
              <button v-if="activeTurn" type="button" class="secondary-button lc-send" :disabled="stopping" @click="stop"><Square aria-hidden="true" />{{ stopping ? '正在停止…' : '停止' }}</button>
              <button type="submit" class="primary-button lc-send" :disabled="sending || !draft.trim()"><Send aria-hidden="true" />{{ sending ? '正在发送…' : clearing ? '正在清空…' : isClearCommand(draft) ? '清空' : '发送' }}</button>
            </div>
          </div>
          <p v-if="isClearCommand(draft)" class="lc-hint">发送后清空这个会话的上下文，不会发给模型：之后的消息从头开始，上面的记录保留。</p>
          <p v-else-if="slashCommand(draft)" class="lc-hint">以 / 开头的命令在这里不会执行，这段文字会原样发给模型。要清空上下文，只输入 /clear 发送，或点上面的“清空上下文”。</p>
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
    <ChatInfoPanel v-if="infoOpen && session" :session="session" :messages="messages" :manager-name="bridge.name" :quota="quotas?.[session.platform] ?? null" :changing="working" :digest="conversation.data.value?.digest ?? null" :can-write="bridge.enabled" @digest="digestAction" @shape="shapeContext" @close="infoOpen = false" />
    <ChatSaveNote v-if="saving && session" :chat="chat" :question="saving.question" :answer="saving.answer" :session-title="session.title" :link="saving.link" @close="saving = null" />
  </div>
</template>
