import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import { project, task, sync } from './sprint3-fixtures'
import type { Schema } from '../src/services/api'

// 规范 §8.1 右侧 AI / 详情抽屉与 §8.4 Task Drawer（T-0066）
const at = '2026-11-01T04:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const conversation: Schema['ImConversationOut'] = { conversation_id: 'si_a', kind: 'single', title: '甲', peer_instance_id: 'inst-a', group_id: null, pinned: false, pinned_at: null, muted: false, unread_count: 0, read_seq: 0, max_seq: 1, last_message: null, last_message_at: null }
const message = (over: Partial<Schema['ImMessageOut']>): Schema['ImMessageOut'] => ({ server_msg_id: 's1', client_msg_id: 'c1', conversation_id: 'si_a', seq: 1, sender_im_id: 'oc_a', sender_instance_id: 'inst-a', kind: 'text', text: '今天一切正常', image: null, at_im_ids: [], approval_execution_id: null, status: 'sent', sent_at: at, pinned: false, ...over })
function setup(role: Schema['WorkspaceOut']['role'] = 'owner', handler?: (r: Request) => Response | undefined) {
  return mockApi(r => handler?.(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) :
    path(r) === '/api/v1/im/status' ? Response.json({ available: true, account_ready: true, im_user_id: 'ws_owner' }) :
    path(r) === '/api/v1/im/contacts' ? Response.json({ items: [{ im_user_id: 'oc_a', nickname: '甲', instance_id: 'inst-a', conversation_id: 'si_a', status: 'online', contact_group_ids: [] }] }) :
    path(r) === '/api/v1/im/conversations' && r.method === 'POST' ? Response.json(conversation) :
    path(r) === '/api/v1/im/conversations/si_a/messages' && r.method === 'GET' ? Response.json({ items: [message({})], next_cursor: null, total_estimate: 1 }) :
    path(r) === '/api/v1/tasks' ? Response.json({ items: [task], sync, next_cursor: null, total_estimate: 1 }) :
    path(r) === `/api/v1/tasks/${task.id}` ? Response.json(task) :
    path(r) === '/api/v1/projects' ? Response.json({ items: [project], sync, next_cursor: null, total_estimate: 1 }) : undefined))
}
const drawer = () => screen.findByRole('complementary', { name: 'AI 与详情' })
afterEach(() => vi.restoreAllMocks())

describe('right AI / detail drawer', () => {
  it('opens from the top bar, remembers the choice and collapses again', async () => {
    setup()
    await openApp('/')
    const toggle = screen.getByRole('button', { name: 'AI 助手' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('complementary', { name: 'AI 与详情' })).not.toBeInTheDocument()
    await fireEvent.click(toggle)
    expect(await drawer()).toBeVisible()
    expect(screen.getByRole('button', { name: '收起 AI 助手' })).toHaveAttribute('aria-expanded', 'true')
    expect(localStorage.getItem('workspace-drawer-open')).toBe('1')
    await fireEvent.click(screen.getByRole('button', { name: '收起 AI 与详情抽屉' }))
    expect(screen.queryByRole('complementary', { name: 'AI 与详情' })).not.toBeInTheDocument()
    expect(localStorage.getItem('workspace-drawer-open')).toBe('0')
  })

  it('restores the drawer on the next visit and chats with an instance through the workspace IM API', async () => {
    localStorage.setItem('workspace-drawer-open', '1')
    const requests = setup('owner', r => r.method === 'POST' && path(r).endsWith('/messages') ? Response.json(message({ server_msg_id: 's2', client_msg_id: 'c2', seq: 2, sender_im_id: 'ws_owner', sender_instance_id: null, text: '磁盘还够吗？' })) : undefined)
    await openApp('/')
    const panel = await drawer()
    expect(await within(panel).findByText('今天一切正常')).toBeVisible()
    expect(within(panel).getByLabelText('对话的实例')).toHaveValue('inst-a')
    await fireEvent.update(within(panel).getByLabelText('消息'), '磁盘还够吗？')
    await fireEvent.click(within(panel).getByRole('button', { name: '发送' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'POST' && path(r).endsWith('/messages'))).toBe(true))
    const sent = requests.find(r => r.method === 'POST' && path(r).endsWith('/messages'))!
    expect(path(sent)).toBe('/api/v1/im/conversations/si_a/messages')
    expect(sent.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await sent.clone().json()).toEqual({ kind: 'text', text: '磁盘还够吗？', at_im_ids: [] })
    expect(within(panel).getByRole('link', { name: '在消息页打开 →' })).toHaveAttribute('href', '/im?c=si_a')
    // The browser never talks to OpenIM directly.
    expect(requests.every(r => new URL(r.url).pathname.startsWith('/api/'))).toBe(true)
  })

  it('explains to members that the assistant is for owner and admin', async () => {
    localStorage.setItem('workspace-drawer-open', '1')
    const requests = setup('member')
    await openApp('/')
    expect(await within(await drawer()).findByText(/只对 owner 与 admin 开放/)).toBeVisible()
    expect(requests.some(r => path(r).startsWith('/api/v1/im/'))).toBe(false)
  })

  it('opens task details in the drawer (Task Drawer) and restores the previous drawer state when closed', async () => {
    setup()
    await openApp('/tasks')
    await fireEvent.click(await screen.findByRole('button', { name: task.title }))
    const panel = await drawer()
    const detail = within(panel).getByRole('dialog', { name: '任务详情' })
    expect(await within(detail).findByLabelText('任务标题')).toHaveValue(task.title)
    expect(within(panel).getByRole('tab', { name: '详情' })).toHaveAttribute('aria-selected', 'true')
    await fireEvent.click(within(detail).getByRole('button', { name: '关闭对话框' }))
    // The drawer was closed before the task was opened, so it closes again.
    await vi.waitFor(() => expect(screen.queryByRole('complementary', { name: 'AI 与详情' })).not.toBeInTheDocument())

    // With the drawer already open on the assistant, closing the task returns to the assistant.
    await fireEvent.click(screen.getByRole('button', { name: 'AI 助手' }))
    await fireEvent.click(screen.getByRole('button', { name: task.title }))
    await within(await drawer()).findByRole('dialog', { name: '任务详情' })
    await fireEvent.keyDown(within(await drawer()).getByLabelText('任务标题'), { key: 'Escape' })
    await vi.waitFor(() => expect(screen.queryByRole('dialog', { name: '任务详情' })).not.toBeInTheDocument())
    expect(await drawer()).toBeVisible()
    expect(within(await drawer()).getByRole('tab', { name: 'AI 助手' })).toHaveAttribute('aria-selected', 'true')
  })

  it('uses a slide-over sheet on narrow screens', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }))
    // A drawer remembered as open on a wide screen does not pop over the page on a narrow one.
    localStorage.setItem('workspace-drawer-open', '1')
    setup()
    await openApp('/tasks')
    expect(screen.queryByRole('dialog', { name: 'AI 助手' })).not.toBeInTheDocument()
    await fireEvent.click(await screen.findByRole('button', { name: task.title }))
    const sheet = await screen.findByRole('dialog', { name: '任务详情' })
    expect(await within(sheet).findByLabelText('任务标题')).toHaveValue(task.title)
    expect(screen.queryByRole('complementary', { name: 'AI 与详情' })).not.toBeInTheDocument()
    await fireEvent.click(within(sheet).getByRole('button', { name: '关闭对话框' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog', { name: '任务详情' })).not.toBeInTheDocument())
  })
})
