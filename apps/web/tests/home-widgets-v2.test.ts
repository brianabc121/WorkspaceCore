import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace, fail } from './helpers'
import { overview, widget } from './dashboard-fixtures'
import type { Schema } from '../src/services/api'

const path = (r: Request) => new URL(r.url).pathname
const at = '2026-11-01T04:00:00Z'
const layout = widget.layout
const progress: Schema['WidgetOut'] = { ...widget, id: 'w-progress', widget_type: 'project_progress', config: { limit: 5 }, layout }
const quick: Schema['WidgetOut'] = { ...widget, id: 'w-quick', widget_type: 'im_quick_chat', config: { instance_id: null }, layout }
const ok = { status: 'ok' as const, as_of: at, retryable: false }
function setup(role: Schema['WorkspaceOut']['role'], handler?: (r: Request) => Response | undefined) {
  return mockApi(r => handler?.(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) :
    path(r) === '/api/v1/dashboard/overview' ? Response.json({ ...overview, widgets: [progress, quick], modules: { ...overview.modules, tasks: ok } }) :
    path(r) === '/api/v1/projects/progress' ? Response.json({ items: [{ project_id: 'p1', title: '发布计划', source_url: 'https://tasks.example.com/projects/7', open_tasks: 3, done_tasks: 1 }], sync: { status: 'ok', as_of: at, reason: null } }) :
    path(r) === '/api/v1/im/status' ? Response.json({ available: true, account_ready: true, im_user_id: 'ws_owner' }) :
    path(r) === '/api/v1/im/contacts' ? Response.json({ items: [{ im_user_id: 'oc_a', nickname: '甲', instance_id: 'inst-a', conversation_id: null, status: 'online', contact_group_ids: [] }] }) : undefined))
}
afterEach(() => vi.restoreAllMocks())

describe('V2 home widgets', () => {
  it('shows project progress from the task projection', async () => {
    setup('viewer')
    await openApp('/')
    const card = await screen.findByRole('article', { name: '项目进度' })
    expect(await within(card).findByText('发布计划')).toBeVisible()
    expect(within(card).getByText('未完成 3 · 已完成 1（25%）')).toBeVisible()
    expect(within(card).getByRole('progressbar', { name: '发布计划 完成 25%' })).toBeInTheDocument()
  })

  it('sends a quick question to an instance and opens the conversation', async () => {
    const requests = setup('owner', r =>
      r.method === 'POST' && path(r) === '/api/v1/im/conversations' ? Response.json({ conversation_id: 'si_a', kind: 'single', title: '甲', peer_instance_id: 'inst-a', group_id: null, pinned: false, pinned_at: null, muted: false, unread_count: 0, read_seq: 0, max_seq: 0, last_message: null, last_message_at: null }) :
      r.method === 'POST' && path(r).endsWith('/messages') ? Response.json({ server_msg_id: 's1', client_msg_id: 'c1', conversation_id: 'si_a', seq: 1, sender_im_id: 'ws_owner', sender_instance_id: null, kind: 'text', text: '今天有什么告警？', image: null, at_im_ids: [], approval_execution_id: null, status: 'sent', sent_at: at, pinned: false }) :
      path(r) === '/api/v1/im/conversations' ? Response.json({ items: [], next_cursor: null, total_estimate: 0, unread_total: 0 }) :
      path(r).endsWith('/messages') ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
      path(r).endsWith('/pins') ? Response.json({ items: [] }) :
      path(r) === '/api/v1/im/contact-groups' ? Response.json({ items: [] }) : undefined)
    const { router } = await openApp('/')
    const form = await screen.findByRole('form', { name: '快捷提问' })
    expect(within(form).getByLabelText('实例')).toHaveValue('inst-a')
    await fireEvent.update(within(form).getByLabelText('问题'), '今天有什么告警？')
    await fireEvent.click(within(form).getByRole('button', { name: '发送并打开会话' }))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/im?c=si_a'))
    const sent = requests.find(r => r.method === 'POST' && path(r).endsWith('/messages'))!
    expect(sent.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await sent.clone().json()).toEqual({ kind: 'text', text: '今天有什么告警？', at_im_ids: [] })
  })

  it('explains IM errors and keeps the draft', async () => {
    setup('owner', r => r.method === 'POST' ? fail(503, 'IM_UNAVAILABLE') : undefined)
    await openApp('/')
    const form = await screen.findByRole('form', { name: '快捷提问' })
    await fireEvent.update(within(form).getByLabelText('问题'), '在吗')
    await fireEvent.click(within(form).getByRole('button', { name: '发送并打开会话' }))
    expect(await within(form).findByText('IM 服务暂时不可用，请稍后重试。')).toBeVisible()
    expect(within(form).getByLabelText('问题')).toHaveValue('在吗')
  })

  it('tells members that IM is for owner and admin', async () => {
    const requests = setup('member')
    await openApp('/')
    const card = await screen.findByRole('article', { name: '快捷提问（IM）' })
    expect(within(card).getByText('IM 只对 owner 与 admin 开放。')).toBeVisible()
    expect(requests.some(r => path(r).startsWith('/api/v1/im/'))).toBe(false)
  })
})
