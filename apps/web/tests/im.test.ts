import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import { checkUpload, chronological, imError } from '../src/features/im/queries'
import { ApiError, type Schema } from '../src/services/api'

const at = '2026-11-01T04:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const self = 'ws_owner', peer = 'oc_alpha'
const conversation = (over: Partial<Schema['ImConversationOut']> = {}): Schema['ImConversationOut'] => ({ conversation_id: 'si_alpha', kind: 'single', title: '甲', peer_instance_id: 'inst-a', group_id: null, pinned: false, pinned_at: null, muted: false, unread_count: 2, read_seq: 1, max_seq: 3, last_message: { kind: 'text', sender_im_id: peer, sent_at: at, text_preview: '你好' }, last_message_at: at, ...over })
const message = (seq: number, over: Partial<Schema['ImMessageOut']> = {}): Schema['ImMessageOut'] => ({ server_msg_id: `srv-${seq}`, client_msg_id: `cli-${seq}`, conversation_id: 'si_alpha', seq, sender_im_id: peer, sender_instance_id: 'inst-a', kind: 'text', text: `第 ${seq} 条`, image: null, at_im_ids: [], approval_execution_id: null, status: 'sent', sent_at: at, pinned: false, ...over })

function setup(handler?: (r: Request) => Response | Promise<Response | undefined> | undefined, conversations = [conversation()], messages = [message(3), message(2), message(1)]) {
  return mockApi(async r => await handler?.(r) ?? (
    path(r) === '/api/v1/im/status' ? Response.json({ available: true, account_ready: true, im_user_id: self }) :
    path(r) === '/api/v1/im/contacts' ? Response.json({ items: [{ im_user_id: peer, nickname: '甲', instance_id: 'inst-a', conversation_id: 'si_alpha', status: 'online', contact_group_ids: [] }] }) :
    path(r) === '/api/v1/im/conversations' && r.method === 'GET' ? Response.json({ items: conversations, next_cursor: null, total_estimate: conversations.length, unread_total: 2 }) :
    path(r).endsWith('/messages') && r.method === 'GET' ? Response.json({ items: messages, next_cursor: null, total_estimate: messages.length }) :
    path(r).endsWith('/pins') && r.method === 'GET' ? Response.json({ items: [] }) :
    path(r).endsWith('/read') ? Response.json(conversation({ unread_count: 0, read_seq: 3 })) :
    path(r) === '/api/v1/im/badges' ? Response.json({ im_unread: 0, pending_approvals: 0 }) : undefined
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
})
