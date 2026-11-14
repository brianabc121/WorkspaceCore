import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { TERMINAL_EVENTS, chatError, type ChatConversation, type ChatEvent, type ChatMessage, type ChatTurn, type LlmChatApi } from './chatApi'

// 一个会话的内容和进度（单会话的对话、多模型同问的每一栏都用它）。要在组件的 setup 里调用。
// 有回合没做完时用长轮询读事件（一段回答、一条命令、改动的文件），在本地接到对应的回答上；
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
      if (e.kind === 'started') { reply.status = 'running'; reply.ahead_in_session = null }
      else if (e.kind === 'message' && e.text) reply.text = reply.text ? `${reply.text}\n\n${e.text}` : e.text
      else if (e.kind === 'notice' && e.text) steps.push({ kind: 'notice', text: e.text, at: e.at })
      else if (e.kind === 'command' && e.command) steps.push({ kind: 'command', text: e.command, at: e.at })
      else if (e.kind === 'files' && e.paths?.length) steps.push({ kind: 'files', text: e.paths.join('、'), at: e.at })
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
  function sent(turn: ChatTurn, text: string) {
    if (!messages.value.some(m => m.turn_id === turn.turn_id)) {
      const at = Date.now() / 1000
      messages.value.push(
        { role: 'user', turn_id: turn.turn_id, text, at, mine: true, steps: [] },
        { role: 'assistant', turn_id: turn.turn_id, text: '', at, status: turn.status, steps: [], ahead_in_session: turn.ahead_in_session ?? null },
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
  return { conversation, session, messages, lost, activeTurn, poll, reload, sent }
}
