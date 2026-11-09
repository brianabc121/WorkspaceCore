import { computed, type Ref } from 'vue'
import { useInfiniteQuery, useQuery, useQueryClient, type QueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'

// Gmail 邮件（T-0130，docs/v2/MAIL_PLAN.md 第 6 节；接口见 T-0128；多邮箱聚合 T-0170 / T-0171）。只有 owner / admin 能用。
// 邮件的发件人、主题、正文、附件名都是外部输入：页面只按纯文字显示，不渲染 HTML、不自动打开附件。
// 查询键都以 'mail' 开头；邮件内容只在查询缓存（内存）里，离开邮件页、退出或失权时清掉，不写浏览器存储。
export type MailAccount = Schema['MailAccountOut']
export type MailSummary = Schema['MailSummary']
export type MailMessage = Schema['MailMessageOut']
export type MailSend = Schema['MailSendOut']
export type MailItem = Schema['MailAggregateItem']
export type MailPage = Schema['MailAggregatePage']
export type MailAccountState = Schema['MailAggregateAccount']

export const SEND_STATUS: Record<MailSend['status'], string> = { sending: '发送中', sent: '已发出', failed: '失败', unknown: '结果未知' }
// 邮件分类：只放 Gmail 系统标签里有查询入口的只读分类（“全部邮件”不含垃圾邮件与垃圾箱）。
// 草稿、已删除没有查询入口，本轮不展示；Gmail 只有 readonly + send 授权，不能归档、删除、标记。
export const CATEGORIES: { id: string; name: string }[] = [
  { id: '', name: '全部邮件' }, { id: 'INBOX', name: '收件箱' }, { id: 'STARRED', name: '星标' }, { id: 'SENT', name: '已发送' },
]
// 列表上方的筛选：拼在 Gmail 搜索语法后面。is:unread 见 Gmail API discovery（revision 20261102）messages.list 的 q 参数示例。
// TODO：“有附件”（has:attachment）要等能核对 Gmail 搜索语法的官方页面（support.google.com/mail/answer/7190，本环境访问被拦）后再加。
export const FILTERS = [
  { id: 'all', name: '全部', term: '' }, { id: 'unread', name: '未读', term: 'is:unread' },
] as const
export type MailFilter = (typeof FILTERS)[number]['id']
// 选中的一封邮件：消息 / 会话 ID 只在所属邮箱里唯一，所以总带着 accountId
export type SelectedMail = { accountId: string; messageId: string; threadId: string }
export const mailKeyOf = (item: { account_id: string; id: string }) => `${item.account_id}:${item.id}`

// 授权回调跳回 /settings/mail?mail=… 时的结果说明
export const CONNECT_RESULTS: Record<string, string> = {
  connected: '邮箱已连接。',
  MAIL_STATE_INVALID: '授权链接已失效或不是你发起的，请重新点“连接 Gmail 邮箱”。',
  MAIL_ACCESS_DENIED: '你在 Google 授权页上拒绝了授权，邮箱没有连接。',
  MAIL_OAUTH_FAILED: 'Google 没有返回授权结果，请重试。',
  MAIL_SCOPE_MISSING: '没有授权读取邮件：重新连接时请勾选“查看您的电子邮件和设置”。',
  MAIL_NO_REFRESH_TOKEN: 'Google 没有返回长期令牌：请在 Google 账号里移除这个应用的授权后重新连接。',
  MAIL_CLIENT_INVALID: 'Google OAuth 客户端 ID 或密钥不对，请检查后重新保存。',
  MAIL_NOT_CONFIGURED: '还没有设置 Google OAuth 客户端。',
  FORBIDDEN: '只有 owner / admin 能连接邮箱。',
}

const OWN_MESSAGES = new Set([
  'MAIL_NOT_CONFIGURED', 'MAIL_RECONNECT', 'MAIL_SCOPE_MISSING', 'MAIL_CLIENT_INVALID', 'MAIL_FORBIDDEN', 'MAIL_NO_REFRESH_TOKEN',
  'SECRET_BLOCKED', 'TIMEOUT_UNKNOWN', 'PAYLOAD_TOO_LARGE', 'MAIL_CURSOR_EXPIRED', 'MAIL_CURSOR_MISMATCH', 'MAIL_LABEL_NEEDS_ACCOUNT',
])
// 这些错误的说明是工作台自己写的（不含外部内容），直接显示
export function mailError(error: unknown): string {
  if (error instanceof ApiError && OWN_MESSAGES.has(error.code)) {
    return error.code === 'TIMEOUT_UNKNOWN' ? 'Gmail 没有及时响应，不知道有没有发出去。不会自动重发：请到 Gmail 的“已发送”里确认，再决定要不要重发。' : error.message
  }
  if (error instanceof ApiError && error.code === 'VALIDATION_FAILED' && error.message) return error.message
  return errorMessage(error)
}

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, active, enabled: () => active() && auth.canManage }
}
export const mailKey = (user: string | undefined, scope: string, ...parts: unknown[]) => ['mail', user, scope, ...parts]

export function useMailSettings(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: mailKey(c.user, scope, 'settings'), enabled: computed(c.enabled),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/mail/settings', { params: { header: c.header }, signal })) })
}

export function useMailAccounts(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: mailKey(c.user, scope, 'accounts'), enabled: computed(c.enabled),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/mail/accounts', { params: { header: c.header }, signal })) })
}

// 设置 → 邮件的“分配给智能体”用：只有实例 ID 与名字（实例列表本身只有 owner 能看）
export function useMailAgents(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: mailKey(c.user, scope, 'agents'), enabled: computed(c.enabled),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/mail/agents', { params: { header: c.header }, signal })) })
}

// 全部邮箱（accountId 为 null）或单个邮箱的邮件：GET /mail/messages，“加载更多”用 next_cursor。
// 查询键含用户、工作空间、邮箱、分类与搜索词：换了任何一个就是新的查询，旧请求没有观察者后被取消（signal），
// 迟到的响应只会落在旧键上，不会回填到当前列表。请求参数从查询键里取，保证与键一致。
export type MailFilters = { accountId: string | null; label: string; q: string }
export const aggregateKey = (user: string | undefined, scope: string, f: MailFilters) => mailKey(user, scope, 'aggregate', f.accountId, f.label, f.q)
export function useMailAggregate(scope: string, filters: Ref<MailFilters>) {
  const c = context(scope)
  return useInfiniteQuery({
    queryKey: computed(() => aggregateKey(c.user, scope, filters.value)),
    enabled: computed(c.enabled),
    initialPageParam: null as string | null,
    queryFn: async ({ queryKey, pageParam, signal }) => {
      const [, , , , accountId, label, q] = queryKey as [string, string | undefined, string, string, string | null, string, string]
      const query = { ...(accountId ? { account_id: accountId } : {}), ...(label ? { label } : {}), ...(q ? { q } : {}), ...(pageParam ? { cursor: pageParam } : {}) }
      return dataOf(await c.auth.api.GET('/api/v1/mail/messages', { params: { header: c.header, query }, signal }))
    },
    getNextPageParam: (last: MailPage) => last.next_cursor ?? undefined,
  })
}

// 读会话：按所属邮箱 + 会话 ID（同一个会话 ID 在别的邮箱里是另一个会话）
export function useMailThreadOf(scope: string, selected: Ref<SelectedMail | null>) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => mailKey(c.user, scope, 'thread', selected.value?.accountId ?? null, selected.value?.threadId ?? null)),
    enabled: computed(() => c.enabled() && !!selected.value),
    queryFn: async ({ queryKey, signal }) => {
      const [, , , , account_id, thread_id] = queryKey as [string, string | undefined, string, string, string, string]
      return dataOf(await c.auth.api.GET('/api/v1/mail/accounts/{account_id}/threads/{thread_id}', { params: { header: c.header, path: { account_id, thread_id } }, signal }))
    },
  })
}

// 会话很长（只返回最近 20 封）时，选中的那封可能不在里面：单独读这一封
export function useMailMessageOf(scope: string, target: Ref<SelectedMail | null>) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => mailKey(c.user, scope, 'message', target.value?.accountId ?? null, target.value?.messageId ?? null)),
    enabled: computed(() => c.enabled() && !!target.value),
    queryFn: async ({ queryKey, signal }) => {
      const [, , , , account_id, message_id] = queryKey as [string, string | undefined, string, string, string, string]
      return dataOf(await c.auth.api.GET('/api/v1/mail/accounts/{account_id}/messages/{message_id}', { params: { header: c.header, path: { account_id, message_id } }, signal }))
    },
  })
}

// 邮件内容（列表、会话、单封）只留在内存：离开邮件页、失权时整个删掉
export function forgetMailContent(cache: QueryClient, user: string | undefined, scope: string) {
  cache.removeQueries({ predicate: q => q.queryKey[0] === 'mail' && q.queryKey[1] === user && q.queryKey[2] === scope && ['aggregate', 'thread', 'message'].includes(String(q.queryKey[3])) })
}

export function useMailSends(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: mailKey(c.user, scope, 'sends'), enabled: computed(c.enabled),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/mail/sends', { params: { header: c.header, query: { limit: 100 } }, signal })) })
}

export function useMailActions(scope: string) {
  const c = context(scope), cache = useQueryClient(), api = c.auth.api, header = c.header
  const refresh = () => cache.invalidateQueries({ predicate: q => q.queryKey[0] === 'mail' && q.queryKey[1] === c.user && q.queryKey[2] === scope })
  // 分配会改实例的 mail.accounts 与 version：实例页也要刷新
  const refreshAll = () => cache.invalidateQueries({ predicate: q => ['mail', 'agents'].includes(String(q.queryKey[0])) && q.queryKey[1] === c.user && q.queryKey[2] === scope })
  return {
    refresh,
    async saveSettings(body: Schema['MailSettingsPut']) {
      const saved = dataOf(await api.PUT('/api/v1/mail/settings', { params: { header }, body })); await refresh(); return saved
    },
    async connect(loginHint: string) {
      return dataOf(await api.POST('/api/v1/mail/connect', { params: { header }, body: loginHint ? { login_hint: loginHint } : {} })).authorize_url
    },
    async patch(account: MailAccount, body: Omit<Schema['MailAccountPatch'], 'version'>) {
      const saved = dataOf(await api.PATCH('/api/v1/mail/accounts/{account_id}', { params: { header, path: { account_id: account.id } }, body: { version: account.version, ...body } }))
      await refresh(); return saved
    },
    async assign(account: MailAccount, instanceIds: string[]) {
      const saved = dataOf(await api.PUT('/api/v1/mail/accounts/{account_id}/agents', { params: { header, path: { account_id: account.id } }, body: { version: account.version, instance_ids: instanceIds } }))
      await refreshAll(); return saved
    },
    async disconnect(account: MailAccount) {
      await api.DELETE('/api/v1/mail/accounts/{account_id}', { params: { header, path: { account_id: account.id } } })
      await refreshAll()
    },
    async send(account: string, body: Schema['MailSendIn']) {
      try { return dataOf(await api.POST('/api/v1/mail/accounts/{account_id}/send', { params: { header, path: { account_id: account } }, body })) }
      finally { await cache.invalidateQueries({ queryKey: mailKey(c.user, scope, 'sends') }) }
    },
  }
}

// 附件经工作台转发下载（要带工作空间请求头，所以不用普通链接）；以 octet-stream 保存，不在浏览器里打开
export async function downloadAttachment(scope: string, account: string, message: string, attachment: Schema['MailAttachmentOut']) {
  const query = new URLSearchParams({ filename: attachment.filename })
  const path = `/api/v1/mail/accounts/${encodeURIComponent(account)}/messages/${encodeURIComponent(message)}/attachments/${encodeURIComponent(attachment.attachment_id ?? '')}?${query}`
  let response: Response
  try { response = await fetch(new Request(`${window.location.origin}${path}`, { credentials: 'same-origin', headers: { 'X-Workspace-Id': scope } })) }
  catch { throw new ApiError(0, 'NETWORK_ERROR', '网络连接失败') }
  if (!response.ok) {
    let detail: Partial<Schema['ErrorBody']> = {}
    try { detail = ((await response.json()) as Schema['ErrorResponse']).error ?? {} } catch { /* 网关返回的不是 JSON */ }
    throw new ApiError(response.status, detail.code ?? 'HTTP_ERROR', detail.message ?? '请求失败', detail.request_id ?? null)
  }
  const url = URL.createObjectURL(await response.blob()), link = document.createElement('a')
  link.href = url; link.download = attachment.filename || 'attachment'; document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
