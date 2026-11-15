import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { TERMINAL_EVENTS, chatError, type ChatAttachment, type ChatConversation, type ChatEvent, type ChatMessage, type ChatTurn, type LlmChatApi } from './chatApi'

const STEP_DEFAULTS = { truncated: false, error: false }

// 一个会话的内容和进度（单会话的对话、多模型同问的每一栏都用它）。要在组件的 setup 里调用。
// 有回合没做完时读事件（逐字的增量、一段回答、一条命令、改动的文件），在本地接到对应的回答上：
// 先用推送通道，一条条收；推送用不了（代理不支持、旧版本）就退回长轮询。断线后带着最后的编号接着读。
// 回合结束、或出现这里不认识的回合（别人或智能体发的）时重新读整段会话，以服务器给的为准。
// hooks.activity：内容有变化（调用方据此滚动到底部）；hooks.changed：有回合结束（调用方据此刷新列表）。
export function useLiveConversation(
  chat: LlmChatApi, bridgeId: Ref<string | null>, sessionId: Ref<string | null>,
  hooks: { activity?: () => void; changed?: () => void } = {},
) {
  const conversation = chat.useConversation(bridgeId, sessionId)
  const session = computed(() => conversation.data.value?.session ?? null)
  const messages = ref<ChatMessage[]>([])
  const lost = ref('')
  let cursor = 0, alive = true, polling = false
  // 推送通道能不能用：试一次不行，这个会话之后就一直用长轮询
  let pushing = true
  const abort = new AbortController()

  function load(data: ChatConversation) {
    messages.value = data.messages.map(m => ({ ...m, steps: [...(m.steps ?? [])] }))
    cursor = data.last_event_id
    hooks.activity?.()
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
      if (e.kind === 'started') { reply.status = 'running'; reply.ahead_in_session = null; reply.waiting = null; reply.timing = { ...(reply.timing ?? {}), started_at: e.at } }
      // 逐字的增量先攒在 partial 里边写边显示；这一段写完时 message 带来全文，替换掉攒的
      else if (e.kind === 'delta' && e.text) { reply.status = 'running'; reply.partial = (reply.partial ?? '') + e.text }
      else if (e.kind === 'message' && e.text) { reply.text = reply.text ? `${reply.text}\n\n${e.text}` : e.text; reply.partial = null }
      else if (e.kind === 'notice' && e.text) steps.push({ ...STEP_DEFAULTS, kind: 'notice', text: e.text, at: e.at })
      else if (e.kind === 'command' && e.command) steps.push({ ...STEP_DEFAULTS, kind: 'command', text: e.command, at: e.at })
      else if (e.kind === 'files' && e.paths?.length) steps.push({ ...STEP_DEFAULTS, kind: 'files', text: e.paths.join('、'), at: e.at })
      // 工具调用、工具结果、产出的图片：事件里不带全部内容，重新读整段会话
      else if (['tool', 'tool_result', 'image'].includes(e.kind)) stale = true
    }
    if (events.length) hooks.activity?.()
    return stale
  }
  const pause = (ms: number) => new Promise<void>(resolve => { setTimeout(resolve, ms) })
  async function poll() {
    const bridge = bridgeId.value, id = sessionId.value
    if (polling || !alive || !bridge || !id) return
    polling = true
    let failures = 0
    try {
      while (alive) {
        let out
        const asked = Date.now()
        if (pushing) {
          try {
            const end = await chat.stream(bridge, id, cursor, async e => {
              if (!alive || e.id <= cursor) return
              cursor = e.id
              if (apply([e])) { await reload(); hooks.changed?.() }
            }, abort.signal)
            if (!alive) return
            failures = 0; lost.value = ''
            cursor = Math.max(cursor, end.last_id)
            if (!end.busy) return
            continue
          } catch {
            if (!alive) return
            pushing = false
          }
        }
        try {
          out = await chat.events(bridge, id, cursor, abort.signal)
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
        if (apply(fresh)) { await reload(); hooks.changed?.() }
        if (!out.busy) return
        // 服务器本该等到有新事件才返回：万一立刻空着回来，歇一下再问，不空转
        if (!fresh.length && Date.now() - asked < 1000) await pause(1000)
      }
    } finally { polling = false }
  }
  // 智能体可能随时往这个会话里发消息：空闲时隔一会儿重新读一次
  const idle = setInterval(() => { if (!polling && alive && sessionId.value && !conversation.isFetching.value) void reload() }, 15000)
  onBeforeUnmount(() => { alive = false; abort.abort(); clearInterval(idle) })

  /** 发出去之后：先把这一问和等待中的回答放上去，然后开始读进度 */
  function sent(turn: ChatTurn, text: string, attachments: ChatAttachment[] = []) {
    if (!messages.value.some(m => m.turn_id === turn.turn_id)) {
      const at = Date.now() / 1000
      messages.value.push(
        { role: 'user', turn_id: turn.turn_id, text, at, mine: true, steps: [], attachments, imported: false },
        { role: 'assistant', turn_id: turn.turn_id, text: '', at, status: turn.status, steps: [], ahead_in_session: turn.ahead_in_session ?? null, attachments: [], imported: false },
      )
    }
    hooks.activity?.()
    void poll()
  }
  /** 正在回答的那个回合；没有的话是排在最前面的 */
  const activeTurn = computed(() => {
    const open = messages.value.filter(m => m.role === 'assistant' && (m.status === 'running' || m.status === 'queued'))
    return open.find(m => m.status === 'running') ?? open[0] ?? null
  })
  /** 被额度打断的回合：现在就试着继续（now），或者不要自动继续。现在就试时，系统替人发的那一条稍后才出现，过一会儿再读一次 */
  const resuming = ref(''), resumeError = ref('')
  async function resume(turn: string, now: boolean) {
    const bridge = bridgeId.value
    if (!bridge || resuming.value) return
    resuming.value = turn; resumeError.value = ''
    try {
      await chat.resume(bridge, turn, now)
      await reload()
      if (now) { await pause(1500); if (alive) await reload() }
      hooks.changed?.()
    } catch (e) { resumeError.value = chatError(e) } finally { resuming.value = '' }
  }
  return { conversation, session, messages, lost, activeTurn, poll, reload, sent, resume, resuming, resumeError }
}
