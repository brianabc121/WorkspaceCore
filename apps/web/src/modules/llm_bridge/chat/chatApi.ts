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
export type ChatAttachment = Schema['LlmChatAttachmentOut']
/** 附件的上限（和后端一致）：一条消息最多几个、单个多大 */
export const ATTACHMENTS = { max: 8, bytes: 20 * 1024 * 1024 }
export const IMAGE_ACCEPT = 'image/png,image/jpeg,image/gif,image/webp'
export const isImage = (a: { media_type: string }) => IMAGE_ACCEPT.split(',').includes(a.media_type)
export const fileSize = (bytes: number) => (bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`)
export type ChatConversation = Schema['LlmChatConversationOut']
export type ChatEvent = Schema['LlmChatEventOut']
export type ChatOptions = Schema['LlmChatOptionsOut']
export type ChatCreateBody = Schema['LlmChatCreateIn']
export type ChatTurn = Schema['LlmChatTurnOut']
export type ChatQueue = Schema['LlmChatQueueOut']
export type ChatQueueItem = Schema['LlmChatQueueItem']
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
export const TERMINAL_EVENTS = ['completed', 'failed', 'cancelled', 'unknown', 'interrupted']
export const TURN_STATUS: Record<string, string> = { queued: '排队中', running: '正在回答', completed: '已完成', failed: '失败', cancelled: '已停止', unknown: '结果未知', interrupted: '额度用完，已中断' }
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
    useSessions: (bridge: Ref<string | null>, filter: Ref<SessionFilter>, limit: Ref<number>) => useQuery({
      queryKey: computed(() => key(bridge.value, 'sessions', filter.value, limit.value)), enabled: computed(() => active() && !!bridge.value),
      // 智能体也在用同一个管理器：隔一会儿重新读一次，列表里能看到它们新开的会话
      refetchInterval: 20000,
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions', {
        params: { ...at(bridge.value!), query: { q: filter.value.q, status: filter.value.status, limit: limit.value, ...(filter.value.platform ? { platform: filter.value.platform } : {}) } }, signal,
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
    /** 推送通道：事件一条条到（onEvent），直到会话里没有回合在跑或到了时限；返回结束时的状态。
     *  用带请求头的 fetch 读流（EventSource 带不了 X-Workspace-Id）。读不了就抛错，调用方退回 events() 的长轮询 */
    async stream(bridge: string, session: string, after: number, onEvent: (event: ChatEvent) => void | Promise<void>, signal?: AbortSignal): Promise<{ busy: boolean; last_id: number }> {
      const url = `${window.location.origin}/api/v1/llm_bridge/bridges/${bridge}/chat/sessions/${session}/stream?after=${after}`
      const response = await fetch(new Request(url, { headers: { ...header, Accept: 'text/event-stream' }, credentials: 'same-origin', signal }))
      if (!response.ok || !response.body || !(response.headers.get('content-type') ?? '').startsWith('text/event-stream')) throw new Error('stream unavailable')
      const reader = response.body.getReader(), decoder = new TextDecoder()
      let buffer = '', end: { busy: boolean; last_id: number } | null = null
      for (;;) {
        const { value, done } = await reader.read()
        buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
        // 一帧以空行结束：event: 名字，data: JSON
        let cut = buffer.search(/\r?\n\r?\n/)
        while (cut >= 0) {
          const frame = buffer.slice(0, cut)
          buffer = buffer.slice(cut).replace(/^\r?\n\r?\n/, '')
          let name = 'message'; const data: string[] = []
          for (const line of frame.split(/\r?\n/)) {
            if (line.startsWith('event:')) name = line.slice(6).trim()
            else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
          }
          if (data.length) {
            let parsed: unknown = null
            try { parsed = JSON.parse(data.join('\n')) } catch { /* 读不懂的一帧跳过 */ }
            if (parsed && name === 'chat') await onEvent(parsed as ChatEvent)
            else if (parsed && name === 'end') end = parsed as { busy: boolean; last_id: number }
          }
          cut = buffer.search(/\r?\n\r?\n/)
        }
        if (done) break
      }
      if (!end) throw new Error('stream ended early')
      return end
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
    // attachments、mode 没有就不传（生成的类型把有默认值的字段也标成必填）
    async send(bridge: string, session: string, body: Omit<Schema['LlmChatSendIn'], 'attachments' | 'mode' | 'knowledge_notes'> & { attachments?: string[]; mode?: Schema['LlmChatSendIn']['mode']; knowledge_notes?: string[] }) {
      return dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/messages', { params: sessionAt(bridge, session), body: body as Schema['LlmChatSendIn'] }))
    },
    // ---- 附件与旧会话的导入（docs/v2/LLM_BRIDGE_QUEUE_PLAN.md 第 3 节）----
    // 上传一个要随下一条消息发出去的图片 / 文件：请求体就是文件内容
    async upload(bridge: string, session: string, file: File) {
      return dataOf(await auth.slowApi.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/attachments', {
        params: { ...sessionAt(bridge, session), query: { name: file.name } }, body: file as unknown as string, bodySerializer: (raw: unknown) => raw as File, headers: { 'Content-Type': 'application/octet-stream' },
      }))
    },
    async discardUpload(bridge: string, session: string, attachment: string) {
      await auth.api.DELETE('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/attachments/{attachment_id}', { params: { header, path: { bridge_id: bridge, session_id: session, attachment_id: attachment } } })
    },
    // 附件的内容：要带工作空间请求头，所以不能直接当图片地址或链接用
    /** 把一个会话导出成 Markdown（提问、回答、每回合的状态和用时） */
    exportBlob(bridge: string, session: string): Promise<Blob> {
      return this.fileBlob(`/api/v1/llm_bridge/bridges/${encodeURIComponent(bridge)}/chat/sessions/${encodeURIComponent(session)}/export`)
    },
    attachmentBlob(bridge: string, session: string, attachment: string, signal?: AbortSignal): Promise<Blob> {
      return this.fileBlob(`/api/v1/llm_bridge/bridges/${encodeURIComponent(bridge)}/chat/sessions/${encodeURIComponent(session)}/attachments/${encodeURIComponent(attachment)}`, signal)
    },
    async fileBlob(path: string, signal?: AbortSignal): Promise<Blob> {
      let response: Response
      try { response = await fetch(new Request(`${window.location.origin}${path}`, { credentials: 'same-origin', headers: header, signal })) }
      catch { throw new ApiError(0, 'NETWORK_ERROR', '网络连接失败') }
      if (!response.ok) {
        let detail: Partial<Schema['ErrorBody']> = {}
        try { detail = ((await response.json()) as Schema['ErrorResponse']).error ?? {} } catch { /* 网关返回的不是 JSON */ }
        throw new ApiError(response.status, detail.code ?? 'HTTP_ERROR', detail.message ?? '请求失败', detail.request_id ?? null)
      }
      return response.blob()
    },
    async reimport(bridge: string, session: string) {
      const out = dataOf(await auth.slowApi.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/reimport', { params: sessionAt(bridge, session) }))
      cache.setQueryData(key(bridge, 'session', session), out)
      return out
    },
    // 会话的消息队列（docs/v2/LLM_BRIDGE_QUEUE_PLAN.md 第 4 节）：有回合在跑时才读，跑着的时候每 5 秒看一次
    useQueue: (bridge: Ref<string | null>, session: Ref<string | null>, busy: Ref<boolean>) => useQuery({
      queryKey: computed(() => key(bridge.value, 'queue', session.value)), enabled: computed(() => active() && !!bridge.value && !!session.value && busy.value),
      refetchInterval: 5000,
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/queue', { params: sessionAt(bridge.value!, session.value!), signal })),
    }),
    async editQueued(bridge: string, turn: string, text: string) {
      const out = dataOf(await auth.api.PATCH('/api/v1/llm_bridge/bridges/{bridge_id}/chat/turns/{turn_id}', { params: { header, path: { bridge_id: bridge, turn_id: turn } }, body: { text } }))
      cache.setQueryData(key(bridge, 'queue', out.session_id), out)
      return out
    },
    async moveQueued(bridge: string, turn: string, to: 'first' | 'last') {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/turns/{turn_id}/move', { params: { header, path: { bridge_id: bridge, turn_id: turn } }, body: { to } }))
      cache.setQueryData(key(bridge, 'queue', out.session_id), out)
      return out
    },
    invalidateQueue: (bridge: string, session: string) => cache.invalidateQueries({ queryKey: key(bridge, 'queue', session) }),
    // 被额度打断的回合：现在就试着继续（now），或者不要自动继续
    async resume(bridge: string, turn: string, now: boolean) {
      const params = { header, path: { bridge_id: bridge, turn_id: turn } }
      const path = '/api/v1/llm_bridge/bridges/{bridge_id}/chat/turns/{turn_id}/resume'
      return dataOf(now ? await auth.api.POST(path, { params }) : await auth.api.DELETE(path, { params }))
    },
    async cancel(bridge: string, turn: string) {
      return dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/turns/{turn_id}/cancel', { params: { header, path: { bridge_id: bridge, turn_id: turn } } }))
    },
    /** 换这个会话之后的回合用的模型（Claude / CodeBuddy 的会话） */
    async setModel(bridge: string, session: string, model: string) {
      const out = dataOf(await auth.api.PUT('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/model', { params: sessionAt(bridge, session), body: { model } }))
      await Promise.all([cache.invalidateQueries({ queryKey: key(bridge, 'sessions') }), cache.invalidateQueries({ queryKey: key(bridge, 'session', session) })])
      return out
    },
    /** 清空上下文：之后的消息从头开始，之前的记录保留（T-0255）。别人建的会话要 takeover */
    async clearContext(bridge: string, session: string, takeover = false) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/clear', { params: sessionAt(bridge, session), body: { takeover } }))
      await Promise.all([cache.invalidateQueries({ queryKey: key(bridge, 'sessions') }), cache.invalidateQueries({ queryKey: key(bridge, 'session', session) })])
      return out
    },
    // ---- 手动压缩、带着摘要另开会话（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.2）----
    /** 手动压缩：排一个回合，轮到时让平台压缩上下文。别人建的会话要 takeover */
    async compact(bridge: string, session: string, instructions = '', takeover = false) {
      const out = dataOf(await auth.api.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/compact', { params: sessionAt(bridge, session), body: { instructions, request_id: newRequestId(), takeover } }))
      await Promise.all([cache.invalidateQueries({ queryKey: key(bridge, 'sessions') }), cache.invalidateQueries({ queryKey: key(bridge, 'session', session) })])
      return out
    },
    /** 带着摘要另开会话：先要摘要，再清空上下文，把摘要作为新上下文的第一条发出（同一个会话） */
    async continueWithSummary(bridge: string, session: string, takeover = false) {
      const out = dataOf(await auth.slowApi.POST('/api/v1/llm_bridge/bridges/{bridge_id}/chat/sessions/{session_id}/continue', { params: sessionAt(bridge, session), body: { request_id: newRequestId(), takeover } }))
      await Promise.all([cache.invalidateQueries({ queryKey: key(bridge, 'sessions') }), cache.invalidateQueries({ queryKey: key(bridge, 'session', session) })])
      return out
    },
    // ---- 提问前用上知识库、会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 第 5、6 节）----
    /** 输入框里的文字在知识库和以前的会话里找相近的（只在工作台里找，不发给任何平台） */
    async related(bridge: string, q: string, session?: string, signal?: AbortSignal) {
      return dataOf(await auth.api.GET('/api/v1/llm_bridge/bridges/{bridge_id}/chat/related', {
        params: { ...at(bridge), query: { q: q.slice(0, 2000), ...(session ? { session_id: session } : {}) } }, signal,
      }))
    },
    useDigests: (state: Ref<string | null>, wanted: Ref<boolean>) => useQuery({
      queryKey: computed(() => key('digests', state.value)), enabled: computed(() => active() && wanted.value),
      queryFn: async ({ signal }) => dataOf(await auth.api.GET('/api/v1/llm_bridge/digests', { params: { header, query: { limit: 200, ...(state.value ? { state: state.value } : {}) } }, signal })),
    }),
    /** 会话纪要的操作：关掉 / 打开、现在整理、转成正式笔记、删除（进回收站） */
    async digest(bridge: string, session: string, action: 'on' | 'off' | 'write' | 'promote' | 'delete') {
      const params = { header, path: { bridge_id: bridge, session_id: session } }
      if (action === 'on' || action === 'off') await auth.api.PUT('/api/v1/llm_bridge/digests/{bridge_id}/{session_id}', { params, body: { enabled: action === 'on' } }).then(dataOf)
      else if (action === 'write') await auth.slowApi.POST('/api/v1/llm_bridge/digests/{bridge_id}/{session_id}/write', { params }).then(dataOf)
      else if (action === 'promote') await auth.api.POST('/api/v1/llm_bridge/digests/{bridge_id}/{session_id}/promote', { params, body: { type: 'record' } }).then(dataOf)
      else await auth.api.DELETE('/api/v1/llm_bridge/digests/{bridge_id}/{session_id}', { params })
      await Promise.all([cache.invalidateQueries({ queryKey: key(bridge, 'session', session) }), cache.invalidateQueries({ queryKey: key('digests') })])
    },
    // 把一条回答存进知识库（5.2 的第二层：人写的笔记，走人的权限）
    async kbSpaces() { return dataOf(await auth.api.GET('/api/v1/knowledge/spaces', { params: { header } })).items },
    /** 同一个知识库里标题或开头相近的笔记：保存前提示“已有这几篇，是更新它还是另存” */
    async kbSimilar(space: string, q: string) {
      return dataOf(await auth.api.GET('/api/v1/knowledge/notes', { params: { header, query: { space_id: space, q: q.slice(0, 100), limit: 5 } } })).items
    },
    async kbCreate(body: Schema['NoteCreate']) { return dataOf(await auth.api.POST('/api/v1/knowledge/notes', { params: { header }, body })) },
    /** 接在一篇已有笔记的后面（带版本号，别人刚改过时 409） */
    async kbAppend(noteId: string, text: string, reason: string) {
      const note = dataOf(await auth.api.GET('/api/v1/knowledge/notes/{note_id}', { params: { header, path: { note_id: noteId } } }))
      return dataOf(await auth.api.PATCH('/api/v1/knowledge/notes/{note_id}', { params: { header, path: { note_id: noteId } }, body: { version: note.version, body: `${note.body.trimEnd()}\n\n---\n\n${text}`, reason } }))
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
/** token 数写成 124k / 1.2M 这样，看得快 */
export function tokens(count: number) {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(count >= 10_000_000 ? 0 : 1)}M`
  if (count >= 1000) return `${Math.round(count / 1000)}k`
  return String(count)
}
export type ChatContext = Schema['LlmChatContextOut']
export type ChatRelated = Schema['LlmChatRelatedOut']
export type ChatNoteHit = Schema['LlmChatNoteHit']
export type ChatDigest = Schema['LlmChatDigestOut']
export type DigestItem = Schema['LlmBridgeDigestItem']
export const DIGEST_STATE: Record<string, string> = { active: '照常更新', off: '已关掉', converted: '已转成正式笔记', edited: '人改过，不再自动更新', deleted: '已删除（在回收站里）', purged: '随会话内容按保留期清掉' }
export const DIGEST_REASON: Record<string, string> = { compaction: '平台压缩了上下文', archived: '会话归档', idle: '24 小时没有新回合', continued: '带着摘要另开会话', manual: '管理员要求现在整理' }
/** 一条消息最多带几篇笔记（和后端一致） */
export const MAX_REFERENCES = 3
/** 特殊回合的说明（4.2）：要交接摘要、带着摘要另开、手动压缩 */
export const TURN_KIND: Record<string, { label: string; title: string }> = {
  summary: { label: '要交接摘要', title: '带着摘要另开会话的第一步：在老的上下文里要一份交接摘要' },
  handoff: { label: '带着摘要另开', title: '清空了上下文，把上一步的摘要作为新上下文的第一条发出' },
  compact: { label: '手动压缩', title: '让平台把之前的内容压成摘要' },
}
/** 会话的上下文用了多少、压缩过几次（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.1）；平台没报就说没有 */
export function contextText(context: ChatContext | null | undefined) {
  if (!context) return ''
  const used = context.percent != null && context.used && context.window
    ? `已用 ${context.percent}%（${tokens(context.used)} / ${tokens(context.window)}）`
    : context.used ? `已用约 ${tokens(context.used)} token（平台没有报窗口大小）` : '还没有数据（跑过一个回合后显示）'
  const times = context.compactions ? `，压缩过 ${context.compactions} 次` : ''
  return `${used}${times}`
}
// 输入框里的斜杠命令：官方程序在管理器调用它的方式下不执行这些命令（T-0255）。
// 只有 /clear 由工作台自己处理（清空上下文）；别的会原样发给模型，发之前提示一句
export const isClearCommand = (text: string) => /^\/clear$/i.test(text.trim())
export const slashCommand = (text: string) => /^\/[A-Za-z][\w-]*(?:\s|$)/.test(text.trim()) && !isClearCommand(text)
/** 清空上下文之后（最后一条分隔之后）有没有发过消息：没有就不用再清空 */
export const hasContext = (messages: { role: string }[]) => {
  const last = messages.map(m => m.role).lastIndexOf('context_cleared')
  return messages.slice(last + 1).some(m => m.role === 'user')
}

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
export function splitText(text: string): { code: boolean; text: string; lang?: string }[] {
  const parts: { code: boolean; text: string; lang?: string }[] = []
  const fence = /```([^\n`]*)\n([\s\S]*?)(?:\n```|```|$)/g
  let last = 0, match: RegExpExecArray | null
  while ((match = fence.exec(text))) {
    if (match.index > last) parts.push({ code: false, text: text.slice(last, match.index) })
    parts.push({ code: true, text: match[2] ?? '', lang: (match[1] ?? '').trim().slice(0, 30) })
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
