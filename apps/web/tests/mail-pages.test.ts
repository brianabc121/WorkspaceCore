// Gmail 邮件页面（T-0130，docs/v2/MAIL_PLAN.md 第 6 节）：设置 → 邮件、邮件页、实例卡片上的邮件权限。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

const at = '2026-11-06T08:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const account: Schema['MailAccountOut'] = { id: 'acc-1', email: 'owner.mail@gmail.com', display_name: 'owner.mail@gmail.com', status: 'active', can_read: true, can_send: true, agents: [], agent_signature: true, last_error: null, connected_at: at, version: 2 }
const settings: Schema['MailSettingsOut'] = { configured: true, client_id: '1234567890-fake.apps.googleusercontent.com', client_secret_set: true, redirect_uri: 'https://ws.example.test/api/v1/mail/oauth/callback', version: 1 }
const summary = (over: Partial<Schema['MailSummary']> = {}): Schema['MailSummary'] => ({ id: 'm1', thread_id: 't1', sender: 'Alice <alice@example.com>', to: 'owner.mail@gmail.com', cc: '', subject: '合同', date: at, snippet: '请看附件', unread: true, labels: ['INBOX', 'UNREAD'], ...over })
const message = (over: Partial<Schema['MailMessageOut']> = {}): Schema['MailMessageOut'] => ({ ...summary(), text: '第一行\n<script>alert(1)</script>', truncated: false, attachments: [{ attachment_id: 'att-1', filename: '报价.pdf', mime_type: 'application/pdf', size: 2048 }], ...over })
const item = (over: Partial<Schema['MailAggregateItem']> = {}): Schema['MailAggregateItem'] => ({ ...summary(), account_id: 'acc-1', account_email: account.email, account_display_name: account.display_name, ...over })
const state: Schema['MailAggregateAccount'] = { account_id: 'acc-1', email: account.email, display_name: account.display_name, status: 'ok', error_code: null, loaded: 2, exhausted: true, unread: 1, retry_at: null }
const sent: Schema['MailSendOut'] = { id: 'send-1', account_id: 'acc-1', actor_kind: 'user', actor_name: 'Owner', sender: account.email, to: ['alice@example.com'], cc: [], bcc_count: 0, subject: 'Re: 合同', status: 'sent', error_code: null, gmail_message_id: 'g1', gmail_thread_id: 't1', created_at: at, finished_at: at }
const owner = () => Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] })

function setup(handler?: (r: Request) => Response | undefined) {
  return mockApi(async r => {
    const result = handler?.(r)
    if (result) return result
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return owner()
    if (p === '/api/v1/mail/settings' && r.method === 'GET') return Response.json(settings)
    if (p === '/api/v1/mail/accounts' && r.method === 'GET') return Response.json({ items: [account] })
    if (p === '/api/v1/mail/agents' && r.method === 'GET') return Response.json({ items: [{ id: 'inst-a', name: '甲' }, { id: 'inst-b', name: '乙' }] })
    if (p === '/api/v1/mail/messages') return Response.json({ items: [item(), item({ id: 'm2', thread_id: 't2', subject: '周报', unread: false })], accounts: [state], outcome: 'ok', partial: false, next_cursor: null, total_estimate: 2 })
    if (p === '/api/v1/mail/accounts/acc-1/threads/t1') return Response.json({ thread_id: 't1', messages: [message()] })
    if (p === '/api/v1/mail/sends') return Response.json({ items: [sent, { ...sent, id: 'send-2', actor_kind: 'instance', actor_name: '甲', status: 'unknown', error_code: 'TIMEOUT_UNKNOWN', subject: '进展' }] })
    return undefined
  })
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('mail settings', () => {
  it('saves the OAuth client without echoing the secret and starts the Google consent', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign, origin: window.location.origin })
    const requests = setup(r => {
      if (path(r) === '/api/v1/mail/settings' && r.method === 'PUT') return Response.json({ ...settings, version: 2 })
      if (path(r) === '/api/v1/mail/connect') return Response.json({ authorize_url: 'https://accounts.google.com/o/oauth2/v2/auth?state=s' })
      return undefined
    })
    await openApp('/settings/mail')
    expect(await screen.findByRole('heading', { level: 1, name: '邮件设置' })).toBeVisible()
    const client = await screen.findByRole('region', { name: 'Google OAuth 客户端' })
    expect(await within(client).findByText(settings.redirect_uri)).toBeVisible()
    // 改版（T-0194）：已配置的客户端点“修改客户端”才打开表单
    await fireEvent.click(within(client).getByRole('button', { name: '修改客户端' }))
    await fireEvent.update(within(client).getByLabelText('客户端密钥'), 'GOCSPX-typed-secret')
    await fireEvent.click(within(client).getByRole('button', { name: '保存 OAuth 客户端' }))
    await screen.findByText('OAuth 客户端已保存')
    const put = requests.find(r => r.method === 'PUT')!
    expect(await put.clone().json()).toEqual({ client_id: settings.client_id, client_secret: 'GOCSPX-typed-secret', version: 1 })
    expect(document.body).not.toHaveTextContent('GOCSPX-typed-secret')

    // 改版（T-0194）：页头“连接邮箱”打开连接表单
    await fireEvent.click(screen.getByRole('button', { name: '连接邮箱' }))
    const box = screen.getByRole('form', { name: '连接 Gmail 邮箱' })
    await fireEvent.update(within(box).getByLabelText('要连接的邮箱（可不填）'), 'owner.mail@gmail.com')
    await fireEvent.click(within(box).getByRole('button', { name: '前往 Google 授权' }))
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith('https://accounts.google.com/o/oauth2/v2/auth?state=s'))
    const connect = requests.find(r => path(r) === '/api/v1/mail/connect')!
    expect(await connect.clone().json()).toEqual({ login_hint: 'owner.mail@gmail.com' })
    expect(connect.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })

  it('shows the callback result, assigns the mailbox to agents and manages the account', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup(r => {
      if (r.method === 'PUT' && path(r) === '/api/v1/mail/accounts/acc-1/agents') return Response.json({ ...account, agents: [{ id: 'inst-a', name: '甲' }], version: 3 })
      if (r.method === 'PATCH') return Response.json({ ...account, agent_signature: false, version: 3 })
      if (r.method === 'DELETE') return new Response(null, { status: 204 })
      return undefined
    })
    const { router } = await openApp('/settings/mail?mail=MAIL_SCOPE_MISSING')
    expect(await screen.findByText(/没有授权读取邮件/)).toBeVisible()
    await vi.waitFor(() => expect(router.currentRoute.value.query.mail).toBeUndefined())
    expect(screen.getByRole('button', { name: '连接邮箱' })).toBeEnabled()
    // 改版（T-0194）：邮箱在表格里一行，分配、署名、断开在右侧的所选邮箱里（默认选中第一个）
    expect(await screen.findByRole('row', { name: `邮箱：${account.email}` })).toBeVisible()
    const item = await screen.findByRole('complementary', { name: '所选邮箱' })
    const assign = await within(item).findByRole('group', { name: `分配 ${account.email} 给智能体` })
    expect(within(assign).getByText(/还没有分配给智能体/)).toBeVisible()
    const save = within(assign).getByRole('button', { name: '保存分配' })
    expect(save).toBeDisabled()
    await fireEvent.click(await within(assign).findByLabelText('甲'))
    await fireEvent.click(save)
    await screen.findByText('邮箱分配已保存')
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining(`把 ${account.email} 分配给 甲`))
    const put = requests.find(r => r.method === 'PUT' && path(r).endsWith('/agents'))!
    expect(await put.clone().json()).toEqual({ version: 2, instance_ids: ['inst-a'] })

    const signature = within(item).getByRole('switch', { name: '智能体发信署名' })
    expect(signature).toHaveAttribute('aria-checked', 'true')
    await fireEvent.click(signature)
    await screen.findByText('邮箱设置已保存')
    const patch = requests.find(r => r.method === 'PATCH')!
    expect(path(patch)).toBe('/api/v1/mail/accounts/acc-1')
    expect(await patch.clone().json()).toEqual({ version: 2, agent_signature: false })
    await fireEvent.click(within(item).getByRole('button', { name: '断开邮箱' }))
    await screen.findByText('邮箱已断开')
    expect(requests.some(r => r.method === 'DELETE' && path(r) === '/api/v1/mail/accounts/acc-1')).toBe(true)
  })

  it('hides mail from members', async () => {
    mockApi(r => (path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'member' }] }) : undefined))
    const { router } = await openApp('/')
    const nav = screen.getByRole('navigation', { name: '主导航' })
    expect(nav.querySelector('a[href="/mail"]')).toBeNull()
    expect(nav.querySelector('a[href="/settings/mail"]')).toBeNull()
    await router.push('/mail')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
  })
})

describe('mail page', () => {
  it('lists mail, shows plain text only and replies in the thread', async () => {
    const requests = setup(r => (path(r) === '/api/v1/mail/accounts/acc-1/send' ? Response.json(sent) : undefined))
    await openApp('/mail')
    const list = await screen.findByRole('region', { name: '邮件列表' })
    await fireEvent.click(await within(list).findByRole('button', { name: /合同/ }))
    const mail = await screen.findByRole('article', { name: '邮件：合同' })
    expect(mail.querySelector('script')).toBeNull()
    expect(within(mail).getByText(/<script>alert\(1\)<\/script>/)).toBeVisible()
    expect(within(mail).getByRole('button', { name: /报价\.pdf · 2 KB/ })).toBeVisible()
    // 默认全部邮箱 + 全部邮件：聚合接口不带邮箱、不带标签
    const listQuery = new URL(requests.find(r => path(r) === '/api/v1/mail/messages')!.url).searchParams
    expect([...listQuery.keys()]).toEqual([])
    expect(requests.some(r => path(r) === '/api/v1/mail/accounts/acc-1/threads/t1')).toBe(true)

    await fireEvent.click(within(mail).getByRole('button', { name: '回复' }))
    const form = await screen.findByRole('form', { name: '写邮件' })
    expect(within(form).getByLabelText('发信邮箱')).toHaveValue('acc-1')
    expect(within(form).getByLabelText('收件人')).toHaveValue('alice@example.com')
    await fireEvent.update(within(form).getByLabelText('正文（纯文字）'), '收到，谢谢')
    await fireEvent.click(within(form).getByRole('button', { name: '发送' }))
    await screen.findByText('已发出：Re: 合同')
    const send = requests.find(r => path(r) === '/api/v1/mail/accounts/acc-1/send')!
    expect(await send.clone().json()).toEqual({ to: ['alice@example.com'], cc: [], bcc: [], subject: '', body: '收到，谢谢', reply_to_message_id: 'm1' })
  })

  it('explains an unknown send result and highlights it in the log', async () => {
    setup(r => (path(r) === '/api/v1/mail/accounts/acc-1/send'
      ? Response.json({ error: { code: 'TIMEOUT_UNKNOWN', message: 'Gmail 没有及时响应，结果未知', request_id: 'req_1', details: {} } }, { status: 504 })
      : undefined))
    await openApp('/mail')
    await fireEvent.click(await screen.findByRole('button', { name: '写邮件' }))
    const form = await screen.findByRole('form', { name: '写邮件' })
    // 全部邮箱视图写信：发信邮箱没有默认值，要自己选
    expect(within(form).getByLabelText('发信邮箱')).toHaveValue('')
    await fireEvent.update(within(form).getByLabelText('发信邮箱'), 'acc-1')
    await fireEvent.update(within(form).getByLabelText('收件人'), 'a@example.com, b@example.com')
    await fireEvent.update(within(form).getByLabelText('主题'), '通知')
    await fireEvent.update(within(form).getByLabelText('正文（纯文字）'), '正文')
    await fireEvent.click(within(form).getByRole('button', { name: '发送' }))
    expect(await screen.findByText(/不会自动重发：请到 Gmail 的“已发送”里确认/)).toBeVisible()

    await fireEvent.click(screen.getByRole('tab', { name: '发件记录' }))
    const log = await screen.findByRole('region', { name: '发件记录' })
    const row = (await within(log).findByText('进展')).closest('tr')!
    expect(row).toHaveClass('mail-unknown')
    expect(row).toHaveTextContent('智能体「甲」')
    expect(row).toHaveTextContent('结果未知')
  })
})

describe('instance mail permission', () => {
  const at2 = at
  const instance = (over: Partial<Schema['AgentInstanceOut']> = {}): Schema['AgentInstanceOut'] => ({ id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read_write', max_role: 'member', status: 'online', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: at2, reported_version: '2026.9.36', plugin_version: '0.3.3+myworkspace.1', bundle_issued_at: at2, im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null }, mail: { accounts: [], read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 }, bundle_outdated: false, server_id: null, server_manual: false, limits: { mem_limit_mb: null, cpus: null }, version: 3, created_at: at2, updated_at: at2, credentials: [], secret_grants: [], ...over })
  function agents(handler?: (r: Request) => Response | undefined) {
    return mockApi(async r => handler?.(r) ?? (
      path(r) === '/api/v1/me' && r.method === 'GET' ? owner() :
      path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
      path(r) === '/api/v1/agents/instances' ? Response.json({ items: [instance()], next_cursor: null, total_estimate: 1 }) :
      path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
      path(r).endsWith('/ssh') ? Response.json({ target: null, job: null, warning: null }) :
      path(r).endsWith('/ops') ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) :
      path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) :
      path(r) === '/api/v1/mail/accounts' ? Response.json({ items: [account] }) :
      path(r).endsWith('/stops') ? Response.json({ items: [] }) : undefined))
  }

  it('defaults to read only, assigns a mailbox and opens sending with a recipient scope after confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = agents(r => (path(r) === '/api/v1/agents/instances/inst-a/mail' && r.method === 'PUT'
      ? Response.json({ accounts: [{ id: 'acc-1', email: account.email, status: 'active', can_send: true }], read_enabled: true, send_enabled: true, send_scope: 'listed', recipients: ['@partner.com'], send_by: 'user-a', send_at: at, version: 4 })
      : undefined))
    await openApp('/agents?instance=inst-a')
    const card = await screen.findByRole('article', { name: '实例：alpha' })
    const section = within(card).getByRole('region', { name: '邮件权限' })
    expect(within(section).getByText('邮件权限：没有分配邮箱')).toBeVisible()
    expect(within(section).getByLabelText(/允许查询邮件/)).toBeChecked()
    expect(within(section).getByLabelText(/允许发送邮件/)).not.toBeChecked()
    const mailbox = await within(section).findByLabelText(account.email)
    expect(mailbox).not.toBeChecked()
    await fireEvent.click(mailbox)

    await fireEvent.click(within(section).getByLabelText(/允许发送邮件/))
    await fireEvent.click(within(section).getByRole('button', { name: '保存邮件权限' }))
    expect(within(section).getByRole('alert')).toHaveTextContent('至少要填一个地址或域名')
    await fireEvent.update(within(section).getByLabelText(/每行一个/), 'not-an-address')
    await fireEvent.click(within(section).getByRole('button', { name: '保存邮件权限' }))
    expect(within(section).getByRole('alert')).toHaveTextContent('不是有效的地址或域名：not-an-address')
    await fireEvent.update(within(section).getByLabelText(/每行一个/), '@partner.com')
    await fireEvent.click(within(section).getByRole('button', { name: '保存邮件权限' }))
    await screen.findByText('邮件权限已保存')
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('收件人范围：@partner.com'))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining(`把邮箱 ${account.email} 分配给「alpha」`))
    const put = requests.find(r => r.method === 'PUT' && path(r).endsWith('/mail'))!
    expect(await put.clone().json()).toEqual({ read_enabled: true, send_enabled: true, send_scope: 'listed', recipients: ['@partner.com'], account_ids: ['acc-1'], version: 3 })
  })
})
