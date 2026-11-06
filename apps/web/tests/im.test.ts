import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import { checkFileUpload, checkUpload, chronological, imError } from '../src/features/im/queries'
import { ApiError, type Schema } from '../src/services/api'

const at = '2026-11-01T04:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const self = 'ws_owner', peer = 'oc_alpha'
const conversation = (over: Partial<Schema['ImConversationOut']> = {}): Schema['ImConversationOut'] => ({ conversation_id: 'si_alpha', kind: 'single', title: '甲', peer_instance_id: 'inst-a', group_id: null, pinned: false, pinned_at: null, muted: false, unread_count: 2, read_seq: 1, max_seq: 3, last_message: { kind: 'text', sender_im_id: peer, sent_at: at, text_preview: '你好' }, last_message_at: at, ...over })
const message = (seq: number, over: Partial<Schema['ImMessageOut']> = {}): Schema['ImMessageOut'] => ({ server_msg_id: `srv-${seq}`, client_msg_id: `cli-${seq}`, conversation_id: 'si_alpha', seq, sender_im_id: peer, sender_instance_id: 'inst-a', kind: 'text', text: `第 ${seq} 条`, image: null, at_im_ids: [], approval_execution_id: null, status: 'sent', sent_at: at, pinned: false, ...over })

const group = (over: Partial<Schema['ImGroupOut']> = {}): Schema['ImGroupOut'] => ({ group_id: 'g1', conversation_id: 'sg_g1', name: '值班群', announcement: '', owner_im_id: self, created_at: at, dismissed: false,
  members: [{ im_user_id: self, instance_id: null, nickname: '测试用户', online: null, role: 'owner', user_id: 'user-a' }, { im_user_id: peer, instance_id: 'inst-a', nickname: '甲', online: true, role: 'member', user_id: null }],
  policy: { relay_enabled: true, max_agent_streak: 8, max_per_minute: 6, paused: true, version: 4 }, ...over })
function setup(handler?: (r: Request) => Response | Promise<Response | undefined> | undefined, conversations = [conversation()], messages = [message(3), message(2), message(1)]) {
  return mockApi(async r => await handler?.(r) ?? (
    path(r) === '/api/v1/im/status' ? Response.json({ available: true, account_ready: true, im_user_id: self }) :
    path(r) === '/api/v1/im/contacts' ? Response.json({ items: [{ im_user_id: peer, nickname: '甲', instance_id: 'inst-a', conversation_id: 'si_alpha', status: 'online', contact_group_ids: [] }] }) :
    path(r) === '/api/v1/im/conversations' && r.method === 'GET' ? Response.json({ items: conversations, next_cursor: null, total_estimate: conversations.length, unread_total: 2 }) :
    path(r).endsWith('/messages') && r.method === 'GET' ? Response.json({ items: messages, next_cursor: null, total_estimate: messages.length }) :
    path(r).endsWith('/pins') && r.method === 'GET' ? Response.json({ items: [] }) :
    path(r).endsWith('/read') ? Response.json(conversation({ unread_count: 0, read_seq: 3 })) :
    path(r) === '/api/v1/im/contact-groups' && r.method === 'GET' ? Response.json({ items: [{ id: 'cg-1', name: '运维', instance_ids: [], sort_order: 0, version: 3 }] }) :
    path(r) === '/api/v1/im/groups/g1' && r.method === 'GET' ? Response.json(group()) :
    path(r) === '/api/v1/im/badges' ? Response.json({ im_unread: 5, pending_approvals: 2 }) : undefined
  ))
}
afterEach(() => vi.restoreAllMocks())

describe('im helpers', () => {
  it('orders newest-first pages oldest first and drops duplicates', () => {
    const pages = [{ items: [message(3), message(2)], next_cursor: 'c' }, { items: [message(2), message(1)], next_cursor: null }]
    expect(chronological(pages).map(m => m.seq)).toEqual([1, 2, 3])
  })
  it('checks uploads by type and size before sending', () => {
    expect(checkUpload(new File(['x'], 'a.svg', { type: 'image/svg+xml' }))).toContain('PNG')
    expect(checkUpload(new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'a.png', { type: 'image/png' }))).toContain('10 MiB')
    expect(checkUpload(new File(['x'], 'a.png', { type: 'image/png' }))).toBe('')
  })
  it('explains IM errors', () => {
    expect(imError(new ApiError(503, 'IM_UNAVAILABLE', ''))).toContain('暂时不可用')
    expect(imError(new ApiError(429, 'RATE_LIMITED', '', null, 12))).toContain('12 秒')
    expect(imError(new ApiError(0, 'WRITE_UNKNOWN', ''))).toContain('网络')
  })
})

describe('im page', () => {
  it('lists conversations, shows messages oldest first and marks them read once', async () => {
    const requests = setup()
    await openApp('/im')
    const list = await screen.findByRole('region', { name: '会话列表' })
    expect(await within(list).findByLabelText('2 条未读')).toBeVisible()
    await fireEvent.click(within(list).getByRole('button', { name: /甲/ }))
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    const texts = (await within(chat).findAllByText(/第 \d 条/)).map(n => n.textContent)
    expect(texts).toEqual(['第 1 条', '第 2 条', '第 3 条'])
    await vi.waitFor(() => expect(requests.filter(r => path(r).endsWith('/read'))).toHaveLength(1))
    expect(await requests.find(r => path(r).endsWith('/read'))!.clone().json()).toEqual({ seq: 3 })
  })
  it('sends text with an Idempotency-Key and reuses it when the result is unknown', async () => {
    let attempts = 0
    const requests = setup(r => {
      if (path(r).endsWith('/messages') && r.method === 'POST') { attempts++; return attempts === 1 ? fail(504, 'RESULT_UNKNOWN') : Response.json(message(4, { sender_im_id: self }), { status: 202 }) }
    })
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    await fireEvent.update(within(chat).getByLabelText('消息'), '在吗')
    await fireEvent.click(within(chat).getByRole('button', { name: '发送' }))
    expect(await within(chat).findByRole('alert')).toHaveTextContent('结果未知')
    await fireEvent.click(within(chat).getByRole('button', { name: '发送' }))
    await vi.waitFor(() => expect(attempts).toBe(2))
    const sends = requests.filter(r => path(r).endsWith('/messages') && r.method === 'POST')
    expect(sends[0].headers.get('Idempotency-Key')).toBeTruthy()
    expect(sends[1].headers.get('Idempotency-Key')).toBe(sends[0].headers.get('Idempotency-Key'))
    expect(await sends[1].clone().json()).toEqual({ kind: 'text', text: '在吗', at_im_ids: [] })
    await vi.waitFor(() => expect(within(chat).getByLabelText('消息')).toHaveValue(''))
    // The server list does not have it yet (projection lags); the sent message is shown anyway, as mine.
    expect(await within(chat).findByText('第 4 条')).toBeVisible()
    expect(within(chat).getAllByText('我').length).toBeGreaterThan(0)
  })
  it('shows a sent message once while the server still lists it as sending', async () => {
    // The send endpoint first stores a "sending" row (client_msg_id myws-<Idempotency-Key>, no server_msg_id) and
    // pushes an event, so the list can refetch before the send returns; the sent copy has OpenIM's IDs.
    let key: string | null = null
    setup(r => {
      if (path(r).endsWith('/messages') && r.method === 'POST') {
        key = r.headers.get('Idempotency-Key')
        return Response.json(message(4, { sender_im_id: self, client_msg_id: 'openim-4' }), { status: 202 })
      }
      if (path(r).endsWith('/messages') && r.method === 'GET' && key) {
        const sending = message(4, { sender_im_id: self, server_msg_id: null, client_msg_id: `myws-${key}`, seq: null, status: 'sending' })
        return Response.json({ items: [sending, message(3), message(2), message(1)], next_cursor: null, total_estimate: 4 })
      }
    })
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    await fireEvent.update(within(chat).getByLabelText('消息'), '在吗')
    await fireEvent.click(within(chat).getByRole('button', { name: '发送' }))
    await vi.waitFor(() => expect(within(chat).getByLabelText('消息')).toHaveValue(''))
    expect(await within(chat).findByText('第 4 条')).toBeVisible()
    expect(within(chat).getAllByText('第 4 条')).toHaveLength(1)
    expect((await within(chat).findAllByText(/第 \d 条/)).map(n => n.textContent)).toEqual(['第 1 条', '第 2 条', '第 3 条', '第 4 条'])
  })
  it('exports a conversation as an NDJSON download', async () => {
    const created = vi.fn(() => 'blob:export'), click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: created, revokeObjectURL: () => undefined }))
    const line = JSON.stringify(message(1)) + '\n'
    const requests = setup(r => path(r).endsWith('/export') ? new Response(line, { headers: { 'Content-Type': 'application/x-ndjson', 'Content-Disposition': 'attachment; filename="si_alpha.ndjson"' } }) : undefined)
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    await fireEvent.click(within(chat).getByRole('button', { name: '更多' }))
    await fireEvent.click(await within(chat).findByRole('menuitem', { name: '导出聊天记录' }))
    expect(await within(chat).findByText(/已导出会话/)).toBeVisible()
    const exported = requests.find(r => path(r) === '/api/v1/im/conversations/si_alpha/export')!
    expect(exported.method).toBe('GET')
    expect(exported.headers.get('X-Workspace-Id')).toBe(workspace.id)
    expect(created).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })

  it('explains a failed export', async () => {
    setup(r => path(r).endsWith('/export') ? fail(403, 'FORBIDDEN') : undefined)
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    await fireEvent.click(within(chat).getByRole('button', { name: '更多' }))
    await fireEvent.click(await within(chat).findByRole('menuitem', { name: '导出聊天记录' }))
    expect(await within(chat).findByText('只有 owner 与 admin 可以使用 IM。')).toBeVisible()
  })

  it('loads workspace images through the API and never loads external image links', async () => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:media-1', revokeObjectURL: () => undefined }))
    const requests = setup(r => path(r) === '/api/v1/im/media/media-1' ? new Response(new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' }), { headers: { 'Content-Type': 'image/png' } }) : undefined,
      [conversation()], [message(2, { kind: 'image', text: null, image: { media_id: null, external_url: 'https://evil.example.test/x.png', width: 10, height: 10 } }), message(1, { kind: 'image', text: null, image: { media_id: 'media-1', external_url: null, width: 40, height: 30 } })])
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    expect(await within(chat).findByRole('img', { name: '甲发送的图片' })).toHaveAttribute('src', 'blob:media-1')
    expect(within(chat).getByText(/外部图片未加载/)).toBeVisible()
    const media = requests.find(r => path(r) === '/api/v1/im/media/media-1')!
    expect(media.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(requests.some(r => r.url.includes('evil.example.test'))).toBe(false)
  })
  it('rejects unsupported images before uploading', async () => {
    const requests = setup()
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    const input = within(chat).getByLabelText('选择图片') as HTMLInputElement
    await fireEvent.change(input, { target: { files: [new File(['<svg/>'], 'a.svg', { type: 'image/svg+xml' })] } })
    expect(await within(chat).findByRole('alert')).toHaveTextContent('PNG')
    expect(requests.some(r => path(r) === '/api/v1/im/uploads')).toBe(false)
  })
  it('explains when OpenIM is not deployed', async () => {
    setup(r => path(r) === '/api/v1/im/status' ? Response.json({ available: false, account_ready: false, im_user_id: null }) : undefined)
    await openApp('/im')
    expect(await screen.findByText(/还没有部署 OpenIM/)).toBeVisible()
  })
  it('hides IM from members and keeps them out of the page', async () => {
    mockApi(r => path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'member' }] }) : undefined)
    const { router } = await openApp('/im')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
    expect(screen.queryByRole('link', { name: /消息/ })).not.toBeInTheDocument()
  })

  it('shows unread and pending approval counts in the top bar for admins', async () => {
    setup()
    await openApp('/')
    expect(await screen.findByRole('link', { name: '消息，5 条未读' })).toHaveAttribute('href', '/im')
    expect(screen.getByRole('link', { name: '2 项待审批' })).toHaveAttribute('href', '/ai')
  })
  it('manages contact groups with versions and starts a chat from contacts', async () => {
    const requests = setup(r => {
      if (path(r) === '/api/v1/im/contact-groups/cg-1' && r.method === 'PATCH') return Response.json({ id: 'cg-1', name: '值班', instance_ids: [], sort_order: 0, version: 4 })
      if (path(r) === '/api/v1/im/contact-groups/cg-1/members') return Response.json({ id: 'cg-1', name: '值班', instance_ids: ['inst-a'], sort_order: 0, version: 5 })
      if (path(r) === '/api/v1/im/conversations' && r.method === 'POST') return Response.json(conversation())
    })
    await openApp('/im?view=contacts')
    const panel = await screen.findByRole('region', { name: '通讯录' })
    const section = await within(panel).findByRole('region', { name: '分组：运维' })
    await fireEvent.click(within(section).getByRole('button', { name: '编辑分组' }))
    await fireEvent.update(within(section).getByLabelText('分组名称'), '值班')
    await fireEvent.click(within(section).getByLabelText('甲'))
    await fireEvent.click(within(section).getByRole('button', { name: '保存分组' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/members'))).toBe(true))
    expect(await requests.find(r => r.method === 'PATCH')!.clone().json()).toEqual({ name: '值班', version: 3 })
    expect(await requests.find(r => path(r).endsWith('/members'))!.clone().json()).toEqual({ instance_ids: ['inst-a'], version: 4 })
    await fireEvent.click(within(within(panel).getByRole('region', { name: '未分组' })).getByRole('button', { name: '发消息' }))
    expect(await screen.findByRole('region', { name: '与甲的会话' })).toBeVisible()
    expect(await requests.find(r => path(r) === '/api/v1/im/conversations' && r.method === 'POST')!.clone().json()).toEqual({ instance_id: 'inst-a' })
  })
  it('creates a group with an Idempotency-Key and opens it', async () => {
    const sg = conversation({ conversation_id: 'sg_g1', kind: 'group', title: '值班群', group_id: 'g1', peer_instance_id: null })
    let created = false
    const requests = setup(r => {
      if (path(r) === '/api/v1/im/groups' && r.method === 'POST') { created = true; return Response.json(group(), { status: 201 }) }
      if (path(r) === '/api/v1/im/conversations' && r.method === 'GET') return Response.json({ items: created ? [conversation(), sg] : [conversation()], next_cursor: null, total_estimate: 2, unread_total: 2 })
    })
    await openApp('/im')
    await fireEvent.click(await screen.findByRole('button', { name: '新建：与实例聊天或新建群聊' }))
    await fireEvent.click(await screen.findByRole('menuitem', { name: '新建群聊' }))
    const dialog = await screen.findByRole('dialog', { name: '建群' })
    await fireEvent.update(within(dialog).getByLabelText('群名称 *'), '值班群')
    await fireEvent.click(within(dialog).getByLabelText('甲'))
    await fireEvent.click(within(dialog).getByRole('button', { name: '建群' }))
    expect(await screen.findByRole('region', { name: '与值班群的会话' })).toBeVisible()
    const post = requests.find(r => path(r) === '/api/v1/im/groups')!
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await post.clone().json()).toEqual({ name: '值班群', announcement: '', relay_enabled: true, instance_ids: ['inst-a'] })
  })
  it('edits group anti-spam settings with the policy version and shows the paused state', async () => {
    const sg = conversation({ conversation_id: 'sg_g1', kind: 'group', title: '值班群', group_id: 'g1', peer_instance_id: null, unread_count: 0 })
    const requests = setup(r => path(r) === '/api/v1/im/groups/g1/policy' ? Response.json({ relay_enabled: false, max_agent_streak: 3, max_per_minute: 6, paused: false, version: 5 }) : undefined, [sg], [])
    await openApp('/im?c=sg_g1')
    const chat = await screen.findByRole('region', { name: '与值班群的会话' })
    await fireEvent.click(within(chat).getByRole('button', { name: '群信息' }))
    const panel = await screen.findByRole('region', { name: '群信息' })
    expect(await within(panel).findByText(/自动接力已暂停/)).toBeVisible()
    expect(within(panel).queryByRole('button', { name: /移出/ })).toBeInTheDocument()
    await fireEvent.click(within(panel).getByLabelText(/允许实例之间接力/))
    await fireEvent.update(within(panel).getByLabelText(/最多连续发言/), '3')
    await fireEvent.click(within(panel).getByRole('button', { name: '保存防刷屏设置' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/policy'))).toBe(true))
    expect(await requests.find(r => path(r).endsWith('/policy'))!.clone().json()).toEqual({ relay_enabled: false, max_agent_streak: 3, max_per_minute: 6, version: 4 })
  })
  it('@ mentions group members and marks messages that mention me', async () => {
    const sg = conversation({ conversation_id: 'sg_g1', kind: 'group', title: '值班群', group_id: 'g1', peer_instance_id: null, unread_count: 0 })
    const mentioned = message(1, { conversation_id: 'sg_g1', text: '@测试用户 看一下', at_im_ids: [self] })
    const requests = setup(r => path(r).endsWith('/messages') && r.method === 'POST' ? Response.json(message(2, { conversation_id: 'sg_g1', sender_im_id: self, text: '@甲 重启 nginx', at_im_ids: [peer] }), { status: 202 }) : undefined, [sg], [mentioned])
    await openApp('/im?c=sg_g1')
    const chat = await screen.findByRole('region', { name: '与值班群的会话' })
    expect(await within(chat).findByText('@ 了我')).toBeVisible()
    await fireEvent.click(await within(chat).findByRole('button', { name: '@ 成员' }))
    const picker = await within(chat).findByRole('menu', { name: '选择要 @ 的成员' })
    // Only other members can be mentioned.
    expect(within(picker).queryByRole('menuitem', { name: /测试用户/ })).not.toBeInTheDocument()
    await fireEvent.click(within(picker).getByRole('menuitem', { name: /甲/ }))
    expect(within(chat).getByLabelText('消息')).toHaveValue('@甲 ')
    expect(within(within(chat).getByRole('list', { name: '将 @ 的成员' })).getByText('@甲')).toBeVisible()
    await fireEvent.update(within(chat).getByLabelText('消息'), '@甲 重启 nginx')
    await fireEvent.click(within(chat).getByRole('button', { name: '发送' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/messages') && r.method === 'POST')).toBe(true))
    expect(await requests.find(r => path(r).endsWith('/messages') && r.method === 'POST')!.clone().json()).toEqual({ kind: 'text', text: '@甲 重启 nginx', at_im_ids: [peer] })
    await vi.waitFor(() => expect(within(chat).queryByRole('list', { name: '将 @ 的成员' })).not.toBeInTheDocument())
  })
  it('lets a mention be removed before sending', async () => {
    const sg = conversation({ conversation_id: 'sg_g1', kind: 'group', title: '值班群', group_id: 'g1', peer_instance_id: null, unread_count: 0 })
    const requests = setup(r => path(r).endsWith('/messages') && r.method === 'POST' ? Response.json(message(2, { conversation_id: 'sg_g1', sender_im_id: self }), { status: 202 }) : undefined, [sg], [])
    await openApp('/im?c=sg_g1')
    const chat = await screen.findByRole('region', { name: '与值班群的会话' })
    await fireEvent.click(await within(chat).findByRole('button', { name: '@ 成员' }))
    await fireEvent.click(await within(chat).findByRole('menuitem', { name: /甲/ }))
    await fireEvent.click(within(chat).getByRole('button', { name: '不再 @ 甲' }))
    await fireEvent.update(within(chat).getByLabelText('消息'), '大家好')
    await fireEvent.click(within(chat).getByRole('button', { name: '发送' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/messages') && r.method === 'POST')).toBe(true))
    expect(await requests.find(r => path(r).endsWith('/messages') && r.method === 'POST')!.clone().json()).toEqual({ kind: 'text', text: '大家好', at_im_ids: [] })
  })
})

describe('im standalone page (T-0095)', () => {
  const sg = (over: Partial<Schema['ImConversationOut']> = {}) => conversation({ conversation_id: 'sg_g1', kind: 'group', title: '值班群', group_id: 'g1', peer_instance_id: null, unread_count: 0, ...over })
  it('opens without the workspace shell and goes back to the workspace from the menu', async () => {
    setup()
    await openApp('/im')
    expect(await screen.findByRole('region', { name: '会话列表' })).toBeVisible()
    expect(screen.getByRole('heading', { level: 1, name: '消息' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: '主导航' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'AI 助手' })).not.toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '菜单' }))
    const menu = await screen.findByRole('dialog', { name: '菜单' })
    expect(within(menu).getByRole('link', { name: /返回工作台/ })).toHaveAttribute('href', '/')
    await fireEvent.click(within(menu).getByRole('button', { name: /通讯录/ }))
    expect(await screen.findByRole('region', { name: '通讯录' })).toBeVisible()
  })
  it('filters by folder and by search text, and counts conversations with unread messages', async () => {
    const notice = conversation({ conversation_id: 'sn_1', kind: 'notice', title: '审批通知', peer_instance_id: null, unread_count: 0 })
    const requests = setup(undefined, [conversation(), sg({ muted: true, unread_count: 4 }), notice])
    await openApp('/im')
    const folders = await screen.findByRole('group', { name: '会话分类' })
    const all = within(folders).getByRole('button', { name: /全部/ })
    expect(await within(all).findByLabelText('1 个会话有未读')).toBeVisible()
    // 群聊里只有开了免打扰的会话有未读：数字用灰色
    expect(within(within(folders).getByRole('button', { name: /群聊/ })).getByLabelText('1 个会话有未读')).toHaveClass('is-muted')
    expect(within(folders).getByRole('button', { name: /运维/ })).toBeVisible()
    await fireEvent.click(within(folders).getByRole('button', { name: /^单聊/ }))
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/im/conversations' && new URL(r.url).searchParams.get('kind') === 'single')).toBe(true))
    expect(within(folders).getByRole('button', { name: /^单聊/ })).toHaveAttribute('aria-pressed', 'true')
    await fireEvent.update(screen.getByLabelText('搜索会话'), '  甲 ')
    await vi.waitFor(() => expect(requests.some(r => new URL(r.url).searchParams.get('q') === '甲' && new URL(r.url).searchParams.get('kind') === 'single')).toBe(true), { timeout: 2000 })
  })
  it('separates messages by day and shows send states in the bubble', async () => {
    const now = Date.now(), yesterday = new Date(now - 86400000).toISOString(), today = new Date(now).toISOString()
    setup(undefined, [conversation({ unread_count: 0 })], [
      message(3, { sender_im_id: self, sent_at: today, text: '没发出去', status: 'failed', server_msg_id: null }),
      message(2, { sender_im_id: self, sent_at: today, text: '不知道发没发', status: 'unknown' }),
      message(1, { sent_at: yesterday, text: '昨天的' }),
    ])
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    expect(await within(chat).findByText('昨天')).toBeVisible()
    expect(within(chat).getByText('今天')).toBeVisible()
    expect(within(chat).getByText('发送失败')).toBeVisible()
    expect(within(chat).getByText('结果未知')).toBeVisible()
  })
  it('re-sends a failed text message as a new message', async () => {
    const requests = setup(r => path(r).endsWith('/messages') && r.method === 'POST' ? Response.json(message(9, { sender_im_id: self, text: '没发出去' }), { status: 202 }) : undefined,
      [conversation({ unread_count: 0 })], [message(3, { sender_im_id: self, text: '没发出去', status: 'failed', server_msg_id: null }), message(1)])
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    const item = (await within(chat).findByText('没发出去')).closest('li')!
    await fireEvent.click(within(item).getByRole('button', { name: '消息操作' }))
    await fireEvent.click(await within(item).findByRole('menuitem', { name: '重新发送' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/messages') && r.method === 'POST')).toBe(true))
    const post = requests.find(r => path(r).endsWith('/messages') && r.method === 'POST')!
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await post.clone().json()).toEqual({ kind: 'text', text: '没发出去', at_im_ids: [] })
  })
  it('opens conversation info with mute, pin and export for a single chat', async () => {
    const requests = setup(r => path(r) === '/api/v1/im/conversations/si_alpha' && r.method === 'PATCH' ? Response.json(conversation({ muted: true })) : undefined, [conversation({ unread_count: 0 })])
    const { router } = await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    expect(within(chat).getByText('实例 · 在线')).toBeVisible()
    await fireEvent.click(within(chat).getByRole('button', { name: '会话信息' }))
    const info = await screen.findByRole('region', { name: '会话信息' })
    expect(router.currentRoute.value.query).toMatchObject({ c: 'si_alpha', info: '1' })
    expect(within(info).getByText('OpenClaw 实例 · 在线')).toBeVisible()
    await fireEvent.click(within(info).getByRole('button', { name: '免打扰' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'PATCH')).toBe(true))
    expect(await requests.find(r => r.method === 'PATCH')!.clone().json()).toEqual({ muted: true })
    await fireEvent.click(within(info).getAllByRole('button', { name: '关闭会话信息' })[0])
    await vi.waitFor(() => expect(router.currentRoute.value.query.info).toBeUndefined())
  })
  it('shows the pinned message bar and unpins from it', async () => {
    const requests = setup(r => {
      if (path(r).endsWith('/pins') && r.method === 'GET') return Response.json({ items: [{ server_msg_id: 'srv-2', pinned_by: null, pinned_at: at, message: message(2, { pinned: true }) }] })
      if (path(r) === '/api/v1/im/conversations/si_alpha/pins/srv-2' && r.method === 'DELETE') return new Response(null, { status: 204 })
    }, [conversation({ unread_count: 0 })])
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    expect(await within(chat).findByRole('button', { name: '置顶消息：第 2 条' })).toBeVisible()
    await fireEvent.click(within(chat).getByRole('button', { name: '取消置顶这条消息' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'DELETE' && path(r).endsWith('/pins/srv-2'))).toBe(true))
  })
  it('keeps a dismissed group read-only', async () => {
    setup(r => path(r) === '/api/v1/im/groups/g1' && r.method === 'GET' ? Response.json(group({ dismissed: true })) : undefined, [sg()], [])
    await openApp('/im?c=sg_g1')
    const chat = await screen.findByRole('region', { name: '与值班群的会话' })
    expect(await within(chat).findByText('这个群已解散，只能查看记录。')).toBeVisible()
    expect(within(chat).queryByLabelText('消息')).not.toBeInTheDocument()
  })
})

describe('im composer keys (T-0095)', () => {
  it('sends on Enter, keeps Shift + Enter and IME confirmation as typing', async () => {
    let sends = 0
    setup(r => { if (path(r).endsWith('/messages') && r.method === 'POST') { sends++; return Response.json(message(4, { sender_im_id: self }), { status: 202 }) } }, [conversation({ unread_count: 0 })])
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    const box = within(chat).getByLabelText('消息')
    await fireEvent.update(box, '第一行')
    await fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })
    await fireEvent.keyDown(box, { key: 'Enter', isComposing: true })
    expect(sends).toBe(0)
    await fireEvent.keyDown(box, { key: 'Enter' })
    await vi.waitFor(() => expect(sends).toBe(1))
    await vi.waitFor(() => expect(box).toHaveValue(''))
  })
})

describe('im review fixes (T-0095)', () => {
  it('re-sending a failed message keeps the unsent draft key while its result is unknown', async () => {
    let posts = 0
    const requests = setup(r => {
      if (path(r).endsWith('/messages') && r.method === 'POST') { posts++; return posts === 1 ? fail(504, 'RESULT_UNKNOWN') : Response.json(message(10 + posts, { sender_im_id: self }), { status: 202 }) }
    }, [conversation({ unread_count: 0 })], [message(3, { sender_im_id: self, text: '旧的失败消息', status: 'failed', server_msg_id: null }), message(1)])
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    await fireEvent.update(within(chat).getByLabelText('消息'), '草稿')
    await fireEvent.click(within(chat).getByRole('button', { name: '发送' }))
    expect(await within(chat).findByRole('alert')).toHaveTextContent('结果未知')
    const item = (await within(chat).findByText('旧的失败消息')).closest('li')!
    await fireEvent.click(within(item).getByRole('button', { name: '消息操作' }))
    await fireEvent.click(await within(item).findByRole('menuitem', { name: '重新发送' }))
    await vi.waitFor(() => expect(posts).toBe(2))
    await vi.waitFor(() => expect(within(chat).getByRole('button', { name: '发送' })).toBeEnabled())
    expect(within(chat).getByLabelText('消息')).toHaveValue('草稿')
    await fireEvent.click(within(chat).getByRole('button', { name: '发送' }))
    await vi.waitFor(() => expect(posts).toBe(3))
    const keys = requests.filter(r => path(r).endsWith('/messages') && r.method === 'POST').map(r => r.headers.get('Idempotency-Key'))
    expect(keys[2]).toBe(keys[0])
    expect(keys[1]).not.toBe(keys[0])
  })
  it('on a phone, back stays in the messages page when the chat was opened from elsewhere', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }))
    try {
      setup(undefined, [conversation({ unread_count: 0 })])
      const { router } = await openApp('/')
      await router.push('/im?c=si_alpha')
      const chat = await screen.findByRole('region', { name: '与甲的会话' })
      await fireEvent.click(within(chat).getByRole('button', { name: '返回会话列表' }))
      await vi.waitFor(() => expect(router.currentRoute.value.query.c).toBeUndefined())
      expect(router.currentRoute.value.path).toBe('/im')
      // 在消息页里进入的会话：返回就是后退一步
      await fireEvent.click(within(await screen.findByRole('region', { name: '会话列表' })).getByRole('button', { name: /甲/ }))
      await vi.waitFor(() => expect(router.currentRoute.value.query.c).toBe('si_alpha'))
      expect(router.options.history.state.imParent).toBe('/im')
      await fireEvent.click(within(await screen.findByRole('region', { name: '与甲的会话' })).getByRole('button', { name: '返回会话列表' }))
      await vi.waitFor(() => expect(router.currentRoute.value.query.c).toBeUndefined())
      expect(router.currentRoute.value.path).toBe('/im')
    } finally { vi.unstubAllGlobals() }
  })
  it('loads older messages to reach a pinned message that is not shown yet', async () => {
    const requests = setup(r => {
      if (path(r).endsWith('/pins') && r.method === 'GET') return Response.json({ items: [{ server_msg_id: 'srv-1', pinned_by: null, pinned_at: at, message: message(1, { pinned: true }) }] })
      if (path(r).endsWith('/messages') && r.method === 'GET') {
        const older = new URL(r.url).searchParams.get('cursor') === 'c1'
        return Response.json(older ? { items: [message(1, { pinned: true })], next_cursor: null, total_estimate: 3 } : { items: [message(3), message(2)], next_cursor: 'c1', total_estimate: 3 })
      }
    }, [conversation({ unread_count: 0 })])
    const { container } = await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    await within(chat).findByText('第 3 条')
    expect(container.querySelector('[data-msg="srv-1"]')).toBeNull()
    await fireEvent.click(await within(chat).findByRole('button', { name: '置顶消息：第 1 条' }))
    await vi.waitFor(() => expect(requests.some(r => new URL(r.url).searchParams.get('cursor') === 'c1')).toBe(true))
    await vi.waitFor(() => expect(container.querySelector('[data-msg="srv-1"]')).not.toBeNull())
  })
})

describe('im files and videos (T-0119)', () => {
  const file = (over: Partial<NonNullable<Schema['ImMessageOut']['attachment']>> = {}): NonNullable<Schema['ImMessageOut']['attachment']> => ({ media_id: 'file-1', external_url: null, name: '周报.pdf', size: 2048, mime: 'application/pdf', duration_seconds: null, cover_media_id: null, ...over })
  it('checks files before uploading', () => {
    expect(checkFileUpload(new File(['x'], 'run.sh'))).toContain('可执行')
    expect(checkFileUpload(new File([], 'a.txt'))).toContain('空')
    expect(checkFileUpload(new File(['x'], 'a.csv'))).toBeNull()
  })
  it('downloads a workspace file with the workspace header and never loads external files', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:file-1', revokeObjectURL: () => undefined }))
    const requests = setup(r => path(r) === '/api/v1/im/media/file-1' ? new Response(new Blob(['%PDF'], { type: 'application/octet-stream' })) : undefined,
      [conversation()], [message(2, { kind: 'file', text: null, attachment: file({ media_id: null, external_url: 'https://evil.example.test/x.pdf' }) }), message(1, { kind: 'file', text: null, attachment: file() })])
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    expect(await within(chat).findByRole('button', { name: '下载 周报.pdf' })).toBeVisible()
    expect(within(chat).getByText(/外部文件未加载/)).toBeVisible()
    await fireEvent.click(within(chat).getByRole('button', { name: '下载 周报.pdf' }))
    await vi.waitFor(() => expect(click).toHaveBeenCalled())
    const media = requests.find(r => path(r) === '/api/v1/im/media/file-1')!
    expect(media.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(requests.some(r => r.url.includes('evil.example.test'))).toBe(false)
  })
  it('plays a video only after the play button is pressed', async () => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:video-1', revokeObjectURL: () => undefined }))
    const requests = setup(r => path(r) === '/api/v1/im/media/video-1' ? new Response(new Blob(['mp4'], { type: 'video/mp4' })) : undefined,
      [conversation()], [message(1, { kind: 'video', text: null, attachment: file({ media_id: 'video-1', name: 'demo.mp4', mime: 'video/mp4', duration_seconds: 12 }) })])
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    const play = await within(chat).findByRole('button', { name: /播放视频（12 秒）/ })
    expect(requests.some(r => path(r) === '/api/v1/im/media/video-1')).toBe(false)
    await fireEvent.click(play)
    expect(await within(chat).findByLabelText('甲发送的视频')).toHaveAttribute('src', 'blob:video-1')
  })
  it('uploads a file with its name and sends it as a file message', async () => {
    const requests = setup(r =>
      path(r) === '/api/v1/im/uploads' ? Response.json({ upload_id: 'up-1', kind: 'file', name: 'plan.csv', duration_seconds: null, mime: 'text/csv', size: 8, width: null, height: null, expires_at: at }, { status: 201 }) :
      path(r).endsWith('/messages') && r.method === 'POST' ? Response.json(message(4, { kind: 'file', text: null, sender_im_id: self, sender_instance_id: null, status: 'sending', attachment: file({ media_id: 'up-1', name: 'plan.csv' }) }), { status: 202 }) : undefined)
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    const input = within(chat).getByLabelText('选择文件或视频') as HTMLInputElement
    await fireEvent.change(input, { target: { files: [new File(['a,b\n1,2\n'], 'plan.csv', { type: 'text/csv' })] } })
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/messages') && r.method === 'POST')).toBe(true))
    const upload = requests.find(r => path(r) === '/api/v1/im/uploads')!
    expect(new URL(upload.url).searchParams.get('name')).toBe('plan.csv')
    const sent = requests.find(r => path(r).endsWith('/messages') && r.method === 'POST')!
    expect(await sent.clone().json()).toEqual({ kind: 'file', upload_id: 'up-1', at_im_ids: [] })
    expect(sent.headers.get('Idempotency-Key')).toBeTruthy()
  })
  it('blocks scripts before uploading', async () => {
    const requests = setup()
    await openApp('/im?c=si_alpha')
    const chat = await screen.findByRole('region', { name: '与甲的会话' })
    const input = within(chat).getByLabelText('选择文件或视频') as HTMLInputElement
    await fireEvent.change(input, { target: { files: [new File(['echo'], 'run.sh')] } })
    expect(await within(chat).findByRole('alert')).toHaveTextContent('可执行')
    expect(requests.some(r => path(r) === '/api/v1/im/uploads')).toBe(false)
  })
})
