import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../../services/api'
import { bridgeError, type BridgeStatus, type Platform } from '../api'

// 大模型聚合聊天的页面接口（docs/v2/LLM_CHAT_PLAN.md 第 4.2 节；后端 app/modules/llm_bridge/chat_api.py）。
// 都在 /api/v1/llm_bridge/bridges/{bridge_id}/chat 下，owner / admin 可用（后端同样校验）。
// 读事件用长轮询：服务器最多挂 25 秒，所以走超时更长的 slowApi；读模型、新建会话要等官方程序，也走它。
// 查询键都以 'llm_bridge' 开头：['llm_bridge', userId, workspaceId, 'chat', bridgeId, ...]。
export type ChatSession = Schema['LlmChatSessionOut']
export type ChatMessage = Schema['LlmChatMessageOut']
export type ChatStep = Schema['LlmChatStepOut']
export type ChatConversation = Schema['LlmChatConversationOut']
export type ChatEvent = Schema['LlmChatEventOut']
export type ChatOptions = Schema['LlmChatOptionsOut']
export type ChatCreateBody = Schema['LlmChatCreateIn']
export type ChatTurn = Schema['LlmChatTurnOut']
export type SessionFilter = { q: string; platform: Platform | null; status: 'active' | 'archived' }
export type ChatGroup = Schema['LlmChatGroupOut']
export type ChatGroupMember = Schema['LlmChatGroupMemberOut']
export type ChatTarget = Schema['LlmChatTargetIn']
export type ChatGroupResult = Schema['LlmChatGroupResult']
/** 整组刚发出的那一条在某一栏的结果：回合，或者没发出去的原因 */
export interface ColumnSend { id: number; text: string; turn: ChatTurn | null; error: string }
/** 一组同问最少、最多几个目标（后端同样校验） */
export const GROUP_TARGETS = { min: 2, max: 6 }

export const EVENT_WAIT_SECONDS = 20
/** 回合已经结束的事件：收到后重新读会话内容 */
export const TERMINAL_EVENTS = ['completed', 'failed', 'cancelled', 'unknown']
export const TURN_STATUS: Record<string, string> = { queued: '排队中', running: '正在回答', completed: '已完成', failed: '失败', cancelled: '已停止', unknown: '结果未知' }
/** 发送用的幂等号。页面不是 HTTPS 时浏览器不给 randomUUID，退回到随机字节 */
export function newRequestId() {
  if (typeof crypto.randomUUID === 'function') return `web-${crypto.randomUUID()}`
  return `web-${Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('')}`
}

export function useLlmChat(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id, cache = useQueryClient()
  const header = { 'X-Workspace-Id': scope }
  const active = () => user === auth.user?.id && scope === auth.workspaceId && auth.canManage
  const key = (bridge: string | null, ...parts: unknown[]) => ['llm_bridge', user, scope, 'chat', bridge, ...parts]
  const at = (bridge_id: string) => ({ header, path: { bridge_id } })
  const sessionAt = (bridge_id: string, session_id: string) => ({ header, path: { bridge_id, session_id } })

  return {
    useSessions: (bridge: Ref<string | null>, filter: Ref<SessionFilter>) => useQuery({
      queryKey: computed(() => key(bridge.value, 'sessions', filter.value)), enabled: computed(() => active() && !!bridge.value),
      // 智能体也在用同一个管理器：隔一会儿重新读一次，列表里能看到它们新开的会话
      refetchInterval: 20000,
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions', {
        params: { ...at(bridge.value!), query: { q: filter.value.q, status: filter.value.status, limit: 100, ...(filter.value.platform ? { platform: filter.value.platform } : {}) } }, signal,
      })),
    }),
    useConversation: (bridge: Ref<string | null>, session: Ref<string | null>) => useQuery({
      queryKey: computed(() => key(bridge.value, 'session', session.value)), enabled: computed(() => active() && !!bridge.value && !!session.value),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}', { params: sessionAt(bridge.value!, session.value!), signal })),
    }),
    useOptions: (bridge: Ref<string | null>, platform: Ref<Platform | null>, mode: Ref<'chat' | 'dev'>) => useQuery({
      queryKey: computed(() => key(bridge.value, 'options', platform.value, mode.value)), enabled: computed(() => active() && !!bridge.value && !!platform.value),
      queryFn: async ({ signal }) => dataOf(await auth.slowApi.GET('/api/v1/llm_bridge/bridges/{bridge_id}/chat/options', { params: { ...at(bridge.value!), query: { platform: platform.value!, mode: mode.value } }, signal })),
    }),
    // 多模型同问（docs/v2/LLM_CHAT_PLAN.md 第 4.3 节）：分组登记在工作台，可以跨管理器
    useGroups: (status: Ref<'active' | 'archived'>, wanted: Ref<boolean>) => useQuery({
      queryKey: computed(() => key('groups', 'list', status.value)), enabled: computed(() => active() && wanted.value), refetchInterval: 30000,
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/chat/groups', { params: { header, query: { status: status.value } }, signal })).groups,
    }),
    useGroup: (id: Ref<string | null>) => useQuery({
      queryKey: computed(() => key('groups', 'one', id.value)), enabled: computed(() => active() && !!id.value),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/chat/groups/{group_id}', { params: { header, path: { group_id: id.value! } }, signal })),
    }),
    async createGroup(body: Schema['LlmChatGroupCreateIn']) {
      const out = dataOf(await auth.slowApi.POST('/api/v1/llm_bridge/chat/groups', { params: { header }, body }))
      cache.setQueryData(key('groups', 'one', out.group.id), out.group)
      await cache.invalidateQueries({ queryKey: key('groups', 'list') })
      return out
    },
    async sendGroup(id: string, body: Schema['LlmChatGroupSendIn']) {
      const out = dataOf(await auth.slowApi.POST('/api/v1/llm_bridge/chat/groups/{group_id}/messages', { params: { header, path: { group_id: id } }, body }))
      cache.setQueryData(key('groups', 'one', id), out.group)
      void cache.invalidateQueries({ queryKey: key('groups', 'list') })
      return out
    },
    async archiveGroup(id: string) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/chat/groups/{group_id}/archive', { params: { header, path: { group_id: id } } }))
      cache.setQueryData(key('groups', 'one', id), out)
      await cache.invalidateQueries({ queryKey: key('groups', 'list') })
      return out
    },
    invalidateSessions: (bridge: string) => cache.invalidateQueries({ queryKey: key(bridge, 'sessions') }),
    invalidateConversation: (bridge: string, session: string) => cache.invalidateQueries({ queryKey: key(bridge, 'session', session) }),

    async events(bridge: string, session: string, after: number, signal?: AbortSignal) {
      return dataOf(await auth.slowApi.GET('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/events', {
        params: { ...sessionAt(bridge, session), query: { after, wait_seconds: EVENT_WAIT_SECONDS } }, signal,
      }))
    },
    async syncSessions(bridge: string) {
      const out = dataOf(await auth.slowApi.GET('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions', { params: { ...at(bridge), query: { sync: true, limit: 1 } } }))
      await cache.invalidateQueries({ queryKey: key(bridge, 'sessions') })
      return out
    },
    async create(bridge: string, body: ChatCreateBody) {
      const out = dataOf(await auth.slowApi.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions', { params: at(bridge), body }))
      await cache.invalidateQueries({ queryKey: key(bridge, 'sessions') })
      return out
    },
    async send(bridge: string, session: string, body: Schema['LlmChatSendIn']) {
      return dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/messages', { params: sessionAt(bridge, session), body }))
    },
    async cancel(bridge: string, turn: string) {
      return dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/turns/{turn_id}/cancel', { params: { header, path: { bridge_id: bridge, turn_id: turn } } }))
    },
    async rename(bridge: string, session: string, title: string) {
      const out = dataOf(await auth.api.PATCH('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}', { params: sessionAt(bridge, session), body: { title } }))
      await Promise.all([cache.invalidateQueries({ queryKey: key(bridge, 'sessions') }), cache.invalidateQueries({ queryKey: key(bridge, 'session', session) })])
      return out
    },
    async archive(bridge: string, session: string) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/archive', { params: sessionAt(bridge, session) }))
      await Promise.all([cache.invalidateQueries({ queryKey: key(bridge, 'sessions') }), cache.invalidateQueries({ queryKey: key(bridge, 'session', session) })])
      return out
    },
  }
}
export type LlmChatApi = ReturnType<typeof useLlmChat>

const MESSAGES: Record<string, string> = {
  BRIDGE_DISABLED: '这个 MCP 管理器已停用：可以查看，不能新建会话和发送。到“管理器与账号”里启用后再试。',
  EXTERNAL_TIMEOUT: 'MCP 管理器没有及时回应，结果未知：稍后看会话里有没有这条消息，再决定是否重发。',
  WRITE_UNKNOWN: '连接中断或超时，不确定有没有发出去：再点一次发送不会重复（同一条消息只会发一次）。',
}
export const chatError = (error: unknown) => (error instanceof ApiError && MESSAGES[error.code]) || bridgeError(error)
export const needsTakeover = (error: unknown) => error instanceof ApiError && error.code === 'TAKEOVER_REQUIRED'

/** 这个管理器上能新建会话的平台：只列它控制的；带是否已登录、能用哪几种用法 */
export function platformChoices(status: BridgeStatus | undefined) {
  const channels = (status?.channels ?? []).filter(c => c.enabled && (c.mode === 'chat' || c.mode === 'dev'))
  const order: Platform[] = ['chatgpt', 'codex', 'claude', 'workbuddy']
  return order.filter(id => channels.some(c => c.platform === id)).map(id => {
    const mine = channels.filter(c => c.platform === id)
    const account = status?.accounts.find(a => a.slot === mine[0]!.slot)
    return {
      id, loggedIn: account?.state === 'logged_in',
      modes: mine.map(c => ({ id: c.mode as 'chat' | 'dev', available: c.available, note: c.note })),
    }
  })
}
export type PlatformChoice = ReturnType<typeof platformChoices>[number]

/** 列表和标题里“谁建的” */
export function ownerText(session: Pick<ChatSession, 'owner_kind' | 'owner'>): string {
  if (session.owner_kind === 'me') return '我'
  if (session.owner_kind === 'external') return '官方程序里已有'
  return session.owner || (session.owner_kind === 'agent' ? '智能体' : '别的管理员')
}

/** 列表和各处用的平台标记（两个字母以内） */
export const PLATFORM_MARK: Record<string, string> = { chatgpt: 'G', codex: 'Cx', claude: 'Cl', workbuddy: 'W' }

/** 把回答按三个反引号拆成普通文字和代码块：只做这一种格式，其余按纯文字显示（不渲染 HTML） */
export function splitText(text: string): { code: boolean; text: string }[] {
  const parts: { code: boolean; text: string }[] = []
  const fence = /```[^\n`]*\n([\s\S]*?)(?:\n```|```|$)/g
  let last = 0, match: RegExpExecArray | null
  while ((match = fence.exec(text))) {
    if (match.index > last) parts.push({ code: false, text: text.slice(last, match.index) })
    parts.push({ code: true, text: match[1] ?? '' })
    last = fence.lastIndex
    if (match[0].length === 0) break
  }
  if (last < text.length) parts.push({ code: false, text: text.slice(last) })
  return parts.map(p => ({ ...p, text: p.code ? p.text : p.text.trim() })).filter(p => p.text)
}

/** 会话按最后活动的日子分组：今天 / 昨天 / 更早 */
export function groupByDay(sessions: ChatSession[], now = new Date()): { name: string; items: ChatSession[] }[] {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000
  const groups = [{ name: '今天', items: [] as ChatSession[] }, { name: '昨天', items: [] as ChatSession[] }, { name: '更早', items: [] as ChatSession[] }]
  for (const s of sessions) groups[s.updated_at >= start ? 0 : s.updated_at >= start - 86400 ? 1 : 2]!.items.push(s)
  return groups.filter(g => g.items.length)
}
