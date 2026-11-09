// 邮件页：默认全部邮箱、单邮箱三栏（T-0171，四页改造规范第 7 节，效果图 04 / 05）。
// 两个邮箱故意用相同的消息 / 会话 ID，检查读信、附件、回复都按所属邮箱走，不串号。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { me, mockApi, openApp, workspace } from './helpers'
import type { Schema } from '../src/services/api'

const at = '2026-11-06T08:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const params = (r: Request) => Object.fromEntries(new URL(r.url).searchParams)
const owner = () => Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] })
type Account = Schema['MailAccountOut']
type Item = Schema['MailAggregateItem']
type State = Schema['MailAggregateAccount']

const mailbox = (id: string, email: string, display_name: string, over: Partial<Account> = {}): Account => ({ id, email, display_name, status: 'active', can_read: true, can_send: true, agents: [], agent_signature: true, last_error: null, connected_at: at, version: 1, ...over })
const A = mailbox('acc-a', 'work@example.com', '工作邮箱')
const B = mailbox('acc-b', 'me@example.com', '个人邮箱')
const item = (account: Account, over: Partial<Item> = {}): Item => ({ id: 'm1', thread_id: 't1', sender: 'Alice <alice@example.com>', to: account.email, cc: '', subject: '合同', date: at, snippet: '摘要', unread: true, labels: ['INBOX', 'UNREAD'], account_id: account.id, account_email: account.email, account_display_name: account.display_name, ...over })
const state = (account: Account, over: Partial<State> = {}): State => ({ account_id: account.id, email: account.email, display_name: account.display_name, status: 'ok', error_code: null, loaded: 1, exhausted: true, unread: null, retry_at: null, ...over })
const page = (items: Item[], accounts: State[], over: Partial<Schema['MailAggregatePage']> = {}): Schema['MailAggregatePage'] => ({ items, accounts, outcome: 'ok', partial: false, next_cursor: null, total_estimate: items.length, ...over })
// 同一个消息 ID / 会话 ID（m1 / t1）在两个邮箱里各有一封
const itemA = item(A, { subject: 'A 合同', date: '2026-11-06T08:00:00Z' })
const itemB = item(B, { subject: 'B 合同', date: '2026-11-06T07:00:00Z', sender: 'Bob <bob@example.com>' })
const itemB2 = item(B, { id: 'm2', thread_id: 't2', subject: 'B 周报', date: '2026-11-05T07:00:00Z', unread: false, labels: ['INBOX'] })
const full = (account: Account, text: string, file: string): Schema['MailMessageOut'] => ({
  ...(account === A ? itemA : itemB), text, truncated: false, attachments: [{ attachment_id: `att-${account.id}`, filename: file, mime_type: 'application/pdf', size: 2048 }],
})
const sent = (account: Account): Schema['MailSendOut'] => ({ id: `send-${account.id}`, account_id: account.id, actor_kind: 'user', actor_name: 'Owner', sender: account.email, to: ['alice@example.com'], cc: [], bcc_count: 0, subject: 'Re: 合同', status: 'sent', error_code: null, gmail_message_id: 'g1', gmail_thread_id: 't1', created_at: at, finished_at: at })

function setup(handler?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => {
    const result = await handler?.(r)
    if (result) return result
    const p = path(r), q = params(r)
    if (p === '/api/v1/me' && r.method === 'GET') return owner()
    if (p === '/api/v1/mail/accounts' && r.method === 'GET') return Response.json({ items: [A, B] })
    if (p === '/api/v1/mail/messages') {
      if (q.account_id === A.id) return Response.json(page([itemA], [state(A, { unread: 5 })]))
      if (q.account_id === B.id) return Response.json(page([itemB, itemB2], [state(B, { unread: 2 })]))
      return Response.json(page([itemB2, itemA, itemB], [state(A, { unread: 5 }), state(B, { unread: 2 })]))
    }
    if (p === '/api/v1/mail/accounts/acc-a/threads/t1') return Response.json({ thread_id: 't1', messages: [full(A, 'A 的正文', 'a.pdf')] })
    if (p === '/api/v1/mail/accounts/acc-b/threads/t1') return Response.json({ thread_id: 't1', messages: [full(B, 'B 的正文', 'b.pdf')] })
    if (p === '/api/v1/mail/accounts/acc-a/send') return Response.json(sent(A))
    if (p === '/api/v1/mail/accounts/acc-b/send') return Response.json(sent(B))
    if (p === '/api/v1/mail/sends') return Response.json({ items: [] })
    return undefined
  })
}
const lists = (requests: Request[]) => requests.filter((r) => path(r) === '/api/v1/mail/messages')
const lastList = (requests: Request[]) => params(lists(requests).at(-1)!)
const nav = () => screen.getByRole('navigation', { name: '邮箱与分类' })
const listRegion = () => screen.getByRole('region', { name: '邮件列表' })
const reader = () => screen.getByRole('region', { name: '阅读' })
async function openRow(name: RegExp) {
  await fireEvent.click(await within(await screen.findByRole('region', { name: '邮件列表' })).findByRole('button', { name }))
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('mail page: all mailboxes by default', () => {
  it('mixes A and B on first open without picking an account', async () => {
    const requests = setup()
    await openApp('/mail')
    const list = await screen.findByRole('region', { name: '邮件列表' })
    expect(await within(list).findByRole('button', { name: /A 合同/ })).toBeVisible()
    expect(within(list).getByRole('button', { name: /B 合同/ })).toBeVisible()
    // 每行标明所属邮箱；按时间倒序
    const rows = within(list).getAllByRole('button', { name: /合同|周报/ })
    expect(rows.map((row) => row.textContent)).toEqual([expect.stringContaining('A 合同'), expect.stringContaining('B 合同'), expect.stringContaining('B 周报')])
    expect(rows[0]).toHaveTextContent('所属邮箱：工作邮箱')
    expect(rows[1]).toHaveTextContent('所属邮箱：个人邮箱')
    // 第一次请求就是全部邮箱：不带 account_id，也没有去读单个邮箱的列表
    expect(lastList(requests)).toEqual({})
    expect(requests.some((r) => /\/mail\/accounts\/[^/]+\/messages$/.test(path(r)))).toBe(false)
    expect(within(nav()).getByRole('button', { name: /全部邮箱/ })).toHaveAttribute('aria-pressed', 'true')
    expect(within(nav()).getByRole('button', { name: /工作邮箱/ })).toHaveAttribute('aria-pressed', 'false')
    expect(within(list).getByRole('searchbox')).toHaveAttribute('placeholder', '搜索所有邮箱的邮件')
    expect(within(list).getByRole('heading', { name: /全部邮件/ })).toHaveTextContent('来自 2 个邮箱')
    // 有依据的未读数（labels.get INBOX）；已加载数与估算数分开写
    expect(within(nav()).getByRole('button', { name: /工作邮箱/ })).toHaveTextContent('收件箱未读 5')
    expect(list).toHaveTextContent('已加载 3 封 · 约 3 封')
    expect(reader()).toHaveTextContent('选一封邮件阅读')
  })

  it('keeps account, category, search, filter, refresh and load more on the same scope', async () => {
    const requests = setup((r) => {
      const q = params(r)
      if (path(r) !== '/api/v1/mail/messages' || q.account_id !== A.id) return undefined
      return Response.json(page(q.cursor ? [item(A, { id: 'm9', subject: 'A 更早' })] : [itemA], [state(A, { exhausted: !q.cursor })], { next_cursor: q.cursor ? null : 'cursor-000000000001', total_estimate: null }))
    })
    await openApp('/mail')
    await within(await screen.findByRole('region', { name: '邮件列表' })).findByRole('button', { name: /B 合同/ })

    await fireEvent.click(within(nav()).getByRole('button', { name: /工作邮箱/ }))
    expect(await within(listRegion()).findByRole('heading', { name: /工作邮箱/ })).toBeVisible()
    expect(await within(listRegion()).findByRole('button', { name: /A 合同/ })).toBeVisible()
    expect(within(listRegion()).queryByRole('button', { name: /B 合同/ })).toBeNull()
    expect(lastList(requests)).toEqual({ account_id: A.id })
    expect(within(listRegion()).getByRole('searchbox')).toHaveAttribute('placeholder', '搜索 工作邮箱 的邮件')
    expect(within(nav()).getByRole('button', { name: /工作邮箱/ })).toHaveAttribute('aria-pressed', 'true')
    expect(listRegion()).toHaveTextContent('已加载 1 封')
    expect(listRegion()).not.toHaveTextContent('约')

    await fireEvent.click(within(nav()).getByRole('button', { name: '星标' }))
    await vi.waitFor(() => expect(lastList(requests)).toEqual({ account_id: A.id, label: 'STARRED' }))
    await fireEvent.update(within(listRegion()).getByRole('searchbox'), '合同')
    await fireEvent.submit(within(listRegion()).getByRole('search'))
    await vi.waitFor(() => expect(lastList(requests)).toEqual({ account_id: A.id, label: 'STARRED', q: '合同' }))
    await fireEvent.click(within(listRegion()).getByRole('button', { name: '未读' }))
    await vi.waitFor(() => expect(lastList(requests)).toEqual({ account_id: A.id, label: 'STARRED', q: '合同 is:unread' }))

    // 加载更多：同一个范围 + 游标
    await fireEvent.click(await within(listRegion()).findByRole('button', { name: '加载更多' }))
    expect(await within(listRegion()).findByRole('button', { name: /A 更早/ })).toBeVisible()
    expect(lastList(requests)).toEqual({ account_id: A.id, label: 'STARRED', q: '合同 is:unread', cursor: 'cursor-000000000001' })
    expect(listRegion()).toHaveTextContent('已加载 2 封')

    // 刷新：同一个范围从第一批重来（不带旧游标）
    const before = lists(requests).length
    await fireEvent.click(screen.getByRole('button', { name: '刷新当前邮箱' }))
    await vi.waitFor(() => expect(lists(requests).length).toBeGreaterThan(before))
    expect(params(lists(requests)[before]!)).toEqual({ account_id: A.id, label: 'STARRED', q: '合同 is:unread' })

    // 返回全部邮箱：分类与搜索保留
    await fireEvent.click(within(listRegion()).getByRole('button', { name: '返回全部邮箱' }))
    await vi.waitFor(() => expect(lastList(requests)).toEqual({ label: 'STARRED', q: '合同 is:unread' }))
    expect(within(nav()).getByRole('button', { name: /全部邮箱/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('cancels the old request and ignores its late response after quickly switching mailboxes', async () => {
    let release: (value: Response) => void = () => {}
    const late = new Promise<Response>((resolve) => { release = resolve })
    const requests = setup((r) => (path(r) === '/api/v1/mail/messages' && params(r).account_id === A.id ? late : undefined))
    await openApp('/mail')
    await within(await screen.findByRole('region', { name: '邮件列表' })).findByRole('button', { name: /A 合同/ })
    await fireEvent.click(within(nav()).getByRole('button', { name: /工作邮箱/ }))
    await fireEvent.click(within(nav()).getByRole('button', { name: /个人邮箱/ }))
    expect(await within(listRegion()).findByRole('button', { name: /B 周报/ })).toBeVisible()
    const slow = lists(requests).find((r) => params(r).account_id === A.id)!
    expect(slow.signal.aborted).toBe(true)
    release(Response.json(page([item(A, { id: 'late', subject: '迟到的 A 邮件' })], [state(A)])))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(within(listRegion()).queryByRole('button', { name: /迟到的 A 邮件/ })).toBeNull()
    expect(within(listRegion()).queryByRole('button', { name: /A 合同/ })).toBeNull()
    expect(within(listRegion()).getByRole('heading', { name: /个人邮箱/ })).toBeVisible()
  })
})

describe('mail page: reading and replying stay on the owning mailbox', () => {
  it('keeps the same message id in A and B apart for body and attachments', async () => {
    const createObjectURL = vi.fn(() => 'blob:mail'), revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const requests = setup((r) => (/\/attachments\//.test(path(r)) ? new Response('PDF') : undefined))
    await openApp('/mail')
    await openRow(/A 合同/)
    const a = await within(reader()).findByRole('article', { name: '邮件：A 合同' })
    expect(a).toHaveTextContent('A 的正文')
    expect(reader()).toHaveTextContent('所属邮箱：工作邮箱 · work@example.com')
    await fireEvent.click(within(a).getByRole('button', { name: /a\.pdf/ }))
    await vi.waitFor(() => expect(requests.some((r) => path(r) === '/api/v1/mail/accounts/acc-a/messages/m1/attachments/att-acc-a')).toBe(true))

    await openRow(/B 合同/)
    const b = await within(reader()).findByRole('article', { name: '邮件：B 合同' })
    expect(b).toHaveTextContent('B 的正文')
    expect(reader()).not.toHaveTextContent('A 的正文')
    expect(reader()).toHaveTextContent('所属邮箱：个人邮箱 · me@example.com')
    expect(within(b).getByRole('button', { name: /b\.pdf/ })).toBeVisible()
    expect(requests.map(path).filter((p) => p.endsWith('/threads/t1'))).toEqual(['/api/v1/mail/accounts/acc-a/threads/t1', '/api/v1/mail/accounts/acc-b/threads/t1'])
    expect(within(listRegion()).getByRole('button', { name: /B 合同/ })).toHaveAttribute('aria-current', 'true')
    expect(within(listRegion()).getByRole('button', { name: /A 合同/ })).not.toHaveAttribute('aria-current')
    // 正文不写进浏览器存储
    expect(JSON.stringify({ ...localStorage })).not.toContain('正文')
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('正文')
  })

  it('replies to A only through the A send endpoint; switching the sender clears the reply link', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup()
    await openApp('/mail')
    await openRow(/A 合同/)
    const a = await within(reader()).findByRole('article', { name: '邮件：A 合同' })
    expect(a).toHaveTextContent('将使用 工作邮箱 work@example.com 回复。')
    await fireEvent.click(within(a).getByRole('button', { name: '回复' }))
    let form = await screen.findByRole('form', { name: '写邮件' })
    expect(within(form).getByLabelText('发信邮箱')).toHaveValue('acc-a')
    await fireEvent.update(within(form).getByLabelText('正文（纯文字）'), '收到')
    await fireEvent.click(within(form).getByRole('button', { name: '发送' }))
    await screen.findByText('已发出：Re: 合同')
    const sends = requests.filter((r) => path(r).endsWith('/send'))
    expect(sends.map(path)).toEqual(['/api/v1/mail/accounts/acc-a/send'])
    expect(await sends[0]!.clone().json()).toEqual({ to: ['alice@example.com'], cc: [], bcc: [], subject: '', body: '收到', reply_to_message_id: 'm1' })

    // 回复时换成 B 发信：先确认，确认后去掉回复关联，B 收到的是一封新邮件
    await fireEvent.click(within(await within(reader()).findByRole('article', { name: '邮件：A 合同' })).getByRole('button', { name: '回复' }))
    form = await screen.findByRole('form', { name: '写邮件' })
    confirm.mockReturnValueOnce(false)
    await fireEvent.update(within(form).getByLabelText('发信邮箱'), 'acc-b')
    expect(within(form).getByLabelText('发信邮箱')).toHaveValue('acc-a')
    expect(within(form).getByRole('heading', { name: '回复' })).toBeVisible()
    await fireEvent.update(within(form).getByLabelText('发信邮箱'), 'acc-b')
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('取消与原邮件的回复关联'))
    expect(within(form).getByRole('heading', { name: '写邮件' })).toBeVisible()
    expect(within(form).getByLabelText('主题')).toHaveValue('Re: A 合同')
    await fireEvent.update(within(form).getByLabelText('正文（纯文字）'), '另起一封')
    await fireEvent.click(within(form).getByRole('button', { name: '发送' }))
    await vi.waitFor(() => expect(requests.filter((r) => path(r).endsWith('/send'))).toHaveLength(2))
    const second = requests.filter((r) => path(r).endsWith('/send'))[1]!
    expect(path(second)).toBe('/api/v1/mail/accounts/acc-b/send')
    expect(await second.clone().json()).toEqual({ to: ['alice@example.com'], cc: [], bcc: [], subject: 'Re: A 合同', body: '另起一封' })
  })

  it('requires an explicit sending mailbox when writing from all mailboxes', async () => {
    const requests = setup()
    await openApp('/mail')
    await within(await screen.findByRole('region', { name: '邮件列表' })).findByRole('button', { name: /A 合同/ })
    await fireEvent.click(within(nav()).getByRole('button', { name: '写邮件' }))
    const form = await screen.findByRole('form', { name: '写邮件' })
    expect(within(form).getByLabelText('发信邮箱')).toHaveValue('')
    await fireEvent.update(within(form).getByLabelText('收件人'), 'x@example.com')
    await fireEvent.update(within(form).getByLabelText('主题'), '通知')
    await fireEvent.update(within(form).getByLabelText('正文（纯文字）'), '正文')
    await fireEvent.click(within(form).getByRole('button', { name: '发送' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('请先选择发信邮箱。')
    expect(requests.some((r) => path(r).endsWith('/send'))).toBe(false)
    await fireEvent.update(within(form).getByLabelText('发信邮箱'), 'acc-b')
    expect(form).toHaveTextContent('将以 个人邮箱 <me@example.com> 的身份发出。')
    await fireEvent.click(within(form).getByRole('button', { name: '发送' }))
    await vi.waitFor(() => expect(requests.filter((r) => path(r).endsWith('/send')).map(path)).toEqual(['/api/v1/mail/accounts/acc-b/send']))
  })

  it('offers no archive, delete, mark-read, star, reply-all or forward buttons', async () => {
    setup()
    await openApp('/mail')
    await openRow(/A 合同/)
    await within(reader()).findByRole('article', { name: '邮件：A 合同' })
    for (const name of ['归档', '删除', '标记已读', '标记未读', '加星标', '回复全部', '转发']) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    expect(screen.getByRole('link', { name: '管理邮箱' })).toHaveAttribute('href', '/settings/mail')
    // 只有有查询入口的分类；草稿、已删除不展示
    expect(within(nav()).getByRole('list', { name: '邮件分类' })).toHaveTextContent('全部邮件收件箱星标已发送')
  })
})

describe('mail page: failures', () => {
  it('explains partially unavailable mailboxes and a broken single mailbox without switching to another', async () => {
    setup((r) => {
      if (path(r) !== '/api/v1/mail/messages') return undefined
      if (params(r).account_id === A.id) return Response.json(page([], [state(A, { status: 'needs_reconnect', error_code: 'MAIL_RECONNECT', exhausted: false })], { outcome: 'no_access', partial: true, total_estimate: null }))
      return Response.json(page([itemB], [state(A, { status: 'rate_limited', error_code: 'RATE_LIMITED', exhausted: false, retry_at: '2026-11-06T09:00:00Z' }), state(B)], { outcome: 'partial', partial: true, total_estimate: null, next_cursor: 'cursor-000000000002' }))
    })
    await openApp('/mail')
    const list = await screen.findByRole('region', { name: '邮件列表' })
    const alert = await within(list).findByRole('alert')
    expect(alert).toHaveTextContent('部分邮箱暂不可用')
    expect(alert).toHaveTextContent('工作邮箱：Gmail 限流')
    expect(within(list).getByRole('button', { name: /B 合同/ })).toBeVisible()
    expect(list).toHaveTextContent('已加载 1 封')
    expect(list).not.toHaveTextContent('约')
    expect(within(nav()).getByRole('button', { name: /工作邮箱/ })).toHaveTextContent('Gmail 限流，稍后再试')

    await fireEvent.click(within(nav()).getByRole('button', { name: /工作邮箱/ }))
    expect(await within(listRegion()).findByText('这个邮箱需要重新连接')).toBeVisible()
    expect(within(listRegion()).getByRole('link', { name: '去重新连接' })).toHaveAttribute('href', '/settings/mail')
    expect(within(listRegion()).queryByRole('button', { name: /B 合同/ })).toBeNull()
    expect(within(listRegion()).getByRole('button', { name: '返回全部邮箱' })).toBeVisible()
  })

  it('says so when the chosen mailbox is gone instead of showing another one', async () => {
    setup((r) => (path(r) === '/api/v1/mail/messages' && params(r).account_id === 'acc-gone' ? Response.json({ error: { code: 'NOT_FOUND', message: '没有这个邮箱', request_id: 'r', details: {} } }, { status: 404 }) : undefined))
    await openApp('/mail?account=acc-gone')
    expect(await within(await screen.findByRole('region', { name: '邮件列表' })).findByText('这个邮箱已断开或不存在')).toBeVisible()
    expect(within(listRegion()).queryByRole('button', { name: /合同/ })).toBeNull()
    expect(within(nav()).getByRole('button', { name: /全部邮箱/ })).toHaveAttribute('aria-pressed', 'false')
  })

  it('forgets loaded mail when leaving the page', async () => {
    setup()
    const { router, queryClient } = await openApp('/')
    // 真实应用里查询缓存会保留一段时间：这里也保留，确认是邮件页自己清掉的
    queryClient.setDefaultOptions({ queries: { retry: false, gcTime: 10 * 60 * 1000 } })
    await router.push('/mail')
    await openRow(/A 合同/)
    await within(reader()).findByRole('article', { name: '邮件：A 合同' })
    const cached = () => queryClient.getQueryCache().findAll({ predicate: (q) => q.queryKey[0] === 'mail' && ['aggregate', 'thread', 'message'].includes(String(q.queryKey[3])) })
    expect(cached().length).toBeGreaterThan(0)
    await router.push('/')
    await vi.waitFor(() => expect(cached()).toHaveLength(0))
  })
})
