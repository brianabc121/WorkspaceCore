import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, errorMessage, type Schema } from '../../services/api'

// Gmail 邮件（T-0130，docs/v2/MAIL_PLAN.md 第 6 节；接口见 T-0128）。只有 owner / admin 能用。
// 邮件的发件人、主题、正文、附件名都是外部输入：页面只按纯文字显示，不渲染 HTML、不自动打开附件。
// 查询键都以 'mail' 开头。
export type MailAccount = Schema['MailAccountOut']
export type MailSummary = Schema['MailSummary']
export type MailMessage = Schema['MailMessageOut']
export type MailSend = Schema['MailSendOut']

export const SEND_STATUS: Record<MailSend['status'], string> = { sending: '发送中', sent: '已发出', failed: '失败', unknown: '结果未知' }
export const LABELS: { id: string; name: string }[] = [
  { id: 'INBOX', name: '收件箱' }, { id: 'UNREAD', name: '未读' }, { id: 'STARRED', name: '星标' }, { id: 'SENT', name: '已发送' },
]

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
  'SECRET_BLOCKED', 'TIMEOUT_UNKNOWN', 'PAYLOAD_TOO_LARGE',
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

export function useMailList(scope: string, account: Ref<string>, filters: Ref<{ q: string; label: string; page: string | null }>) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => mailKey(c.user, scope, 'list', account.value, { ...filters.value })),
    enabled: computed(() => c.enabled() && !!account.value),
    queryFn: async ({ signal }) => {
      const f = filters.value
      const query = { limit: 25, ...(f.q.trim() ? { q: f.q.trim() } : {}), ...(f.label ? { label: f.label } : {}), ...(f.page ? { page_token: f.page } : {}) }
      return dataOf(await c.auth.api.GET('/api/v1/mail/accounts/{account_id}/messages', { params: { header: c.header, path: { account_id: account.value }, query }, signal }))
    },
  })
}

export function useMailThread(scope: string, account: Ref<string>, thread: Ref<string | null>) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => mailKey(c.user, scope, 'thread', account.value, thread.value)),
    enabled: computed(() => c.enabled() && !!account.value && !!thread.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/mail/accounts/{account_id}/threads/{thread_id}', { params: { header: c.header, path: { account_id: account.value, thread_id: thread.value! } }, signal })),
  })
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
