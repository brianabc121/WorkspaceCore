import { computed, type Ref } from 'vue'
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'

// IM queries (V2 OC-4; OC-1 contract §5). All keys start with 'im' so the SSE topic `im` refreshes them.
export type ConversationKind = Schema['ImConversationOut']['kind']
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
export const UPLOAD_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'] as const

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, active }
}

export function useImStatus(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: ['im', c.user, scope, 'status'], enabled: computed(() => c.active() && c.auth.canManage),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/im/status', { params: { header: c.header }, signal })) })
}

export function useImBadges(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: ['im', c.user, scope, 'badges'], enabled: computed(() => c.active() && c.auth.canManage), retry: false,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/im/badges', { params: { header: c.header }, signal })) })
}

export function useConversations(scope: string, filter: Ref<{ kind: ConversationKind | ''; q: string; group: string }>) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => ['im', c.user, scope, 'conversations', filter.value.kind, filter.value.q, filter.value.group]),
    enabled: computed(() => c.active() && c.auth.canManage), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const { kind, q, group } = filter.value
      const query = { limit: 50, ...(kind ? { kind } : {}), ...(q ? { q } : {}), ...(group ? { contact_group_id: group } : {}), ...(pageParam ? { cursor: pageParam } : {}) }
      return dataOf(await c.auth.api.GET('/api/v1/im/conversations', { params: { header: c.header, query }, signal }))
    },
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

export function useMessages(scope: string, conversation: Ref<string | null>) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => ['im', c.user, scope, 'messages', conversation.value]),
    enabled: computed(() => c.active() && c.auth.canManage && !!conversation.value), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await c.auth.api.GET('/api/v1/im/conversations/{conversation_id}/messages', {
      params: { header: c.header, path: { conversation_id: conversation.value! }, query: { limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: p => p.next_cursor ?? undefined,
  })
}

export function usePins(scope: string, conversation: Ref<string | null>) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => ['im', c.user, scope, 'pins', conversation.value]),
    enabled: computed(() => c.active() && c.auth.canManage && !!conversation.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/im/conversations/{conversation_id}/pins', {
      params: { header: c.header, path: { conversation_id: conversation.value! } }, signal })),
  })
}

export function useContacts(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: ['im', c.user, scope, 'contacts'], enabled: computed(() => c.active() && c.auth.canManage),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/im/contacts', { params: { header: c.header }, signal })) })
}

export function useContactGroups(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: ['im', c.user, scope, 'contact-groups'], enabled: computed(() => c.active() && c.auth.canManage),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/im/contact-groups', { params: { header: c.header }, signal })) })
}

export function useGroup(scope: string, groupId: Ref<string | null>) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => ['im', c.user, scope, 'group', groupId.value]),
    enabled: computed(() => c.active() && c.auth.canManage && !!groupId.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/im/groups/{group_id}', { params: { header: c.header, path: { group_id: groupId.value! } }, signal })),
  })
}

export const STATUS_LABELS: Record<Schema['ImContactOut']['status'], string> = { pending: '准备中', online: '在线', offline: '离线', disabled: '已停用' }

// Messages come newest first; the chat shows them oldest first and drops duplicates across pages.
export function chronological(pages: Schema['ImMessagePage'][] | undefined): Schema['ImMessageOut'][] {
  const seen = new Map<string, Schema['ImMessageOut']>()
  for (const item of (pages ?? []).flatMap(p => p.items)) {
    const key = item.server_msg_id ?? item.client_msg_id
    if (!seen.has(key)) seen.set(key, item)
  }
  return [...seen.values()].sort((a, b) => (a.seq ?? Number.MAX_SAFE_INTEGER) - (b.seq ?? Number.MAX_SAFE_INTEGER) || a.sent_at.localeCompare(b.sent_at))
}

export function checkUpload(file: File): string {
  if (!(UPLOAD_TYPES as readonly string[]).includes(file.type)) return '只能发送 PNG、JPEG、GIF 或 WebP 图片。'
  if (file.size > MAX_UPLOAD_BYTES) return '图片不能超过 10 MiB。'
  return ''
}

export function imError(error: unknown): string {
  if (!(error instanceof ApiError)) return '请求失败，请稍后重试。'
  const messages: Record<string, string> = {
    IM_UNAVAILABLE: 'IM 服务暂时不可用，请稍后重试。',
    IM_NOT_READY: '对方的 IM 账号还没就绪，请稍后再试。',
    IM_SEND_REJECTED: error.message || '消息被拒收。',
    PAYLOAD_TOO_LARGE: '图片不能超过 10 MiB。',
    VALIDATION_FAILED: '内容不符合要求：文字不能为空，图片只支持 PNG、JPEG、GIF、WebP。',
    RESULT_UNKNOWN: '发送结果未知，请稍后刷新会话确认，不要重复发送。',
    OPERATION_NOT_APPLIED: '上一条消息确认没有发出，可以重新发送。',
    NOT_FOUND: '会话不存在，或你已无权访问。',
    FORBIDDEN: '只有 owner 与 admin 可以使用 IM。',
    CONTACT_GROUP_EXISTS: '已有同名分组。',
    VERSION_CONFLICT: '已被其他人修改，请刷新后再操作。',
  }
  if (error.status === 429) return error.retryAfter === null ? '发送过于频繁，请稍后重试。' : `发送过于频繁，请在 ${error.retryAfter} 秒后重试。`
  return messages[error.code] ?? (error.status === 0 ? '网络连接失败，请检查连接后重试。' : '请求失败，请稍后重试。')
}
