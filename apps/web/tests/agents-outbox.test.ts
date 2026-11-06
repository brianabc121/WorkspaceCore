import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// T-0118：实例卡片里的“发件记录”（智能体主动发的消息、取消排队的、被拒收的自动回复）。
const at = '2026-11-04T04:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const instance: Schema['AgentInstanceOut'] = { id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read_write', max_role: 'member', status: 'online', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: at, reported_version: '2026.9.36', plugin_version: '0.3.3', bundle_issued_at: at, im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null }, mail: { accounts: [], read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 }, bundle_outdated: false, version: 3, created_at: at, updated_at: at, credentials: [], secret_grants: [] }
const item = (over: Partial<Schema['ImOutboxItem']> = {}): Schema['ImOutboxItem'] => ({ message_id: 'm-1', target: 'group:g1', kind: 'text', status: 'sent', source: 'tool', created_at: at, sent_at: at, next_attempt_at: null, code: null, reason: null, untrusted: { text: '周报写好了' }, ...over })

function setup(outbox: () => Schema['ImOutboxOutput']) {
  return mockApi(async r =>
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/instances' ? Response.json({ items: [instance], next_cursor: null, total_estimate: 1 }) :
    path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
    path(r) === '/api/v1/agents/instances/inst-a/ssh' ? Response.json({ target: null, job: null, warning: null }) :
    path(r).endsWith('/ops') ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) :
    path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) :
    path(r) === '/api/v1/agents/instances/inst-a/outbox' ? Response.json(outbox()) :
    path(r) === '/api/v1/agents/instances/inst-a/outbox/cancel' ? Response.json({ cancelled: ['m-2'], not_cancellable: [] }) : undefined)
}
async function panel() {
  await openApp('/agents')
  const card = await screen.findByRole('article', { name: '实例：alpha' })
  return within(card).getByRole('region', { name: '发件记录' })
}
afterEach(() => vi.restoreAllMocks())

describe('agent outbox panel', () => {
  it('lists sent, queued and rejected messages and cancels a queued one', async () => {
    let cancelled = false
    const requests = setup(() => ({
      items: [
        item(),
        item({ message_id: 'm-2', status: cancelled ? 'cancelled' : 'queued', next_attempt_at: at, untrusted: { text: '排队中的' } }),
        item({ message_id: 'm-3', status: 'rejected', code: 5001, reason: '群里智能体连续发言已达上限，自动接力已暂停', untrusted: { text: '被拒的' } }),
      ],
      rejected_replies: [{ conversation_id: 'sg_g1', code: 5002, reason: '你在这个群里每分钟的条数已达上限', retried: true, created_at: at, untrusted: { text: '自动回复' } }],
    }))
    const box = await panel()
    expect(await within(box).findByText(/已发出 1 · 待发 1 · 被拒收 2/)).toBeVisible()
    const list = within(box).getByRole('list', { name: '主动发的消息' })
    expect(within(list).getByText(/周报写好了/)).toBeVisible()
    expect(within(list).getByText(/连续发言已达上限/)).toBeVisible()
    expect(within(box).getByText(/被拒收的自动回复（1/)).toBeVisible()
    expect(within(box).getByText(/已在限额恢复后补发/)).toBeInTheDocument()
    const buttons = within(list).getAllByRole('button', { name: '取消' })
    expect(buttons).toHaveLength(1)
    cancelled = true
    await fireEvent.click(buttons[0]!)
    await vi.waitFor(() => expect(requests.some(r => r.method === 'POST' && path(r).endsWith('/outbox/cancel'))).toBe(true))
    const body = await requests.filter(r => r.method === 'POST' && path(r).endsWith('/outbox/cancel')).at(-1)!.clone().json() as unknown
    expect(body).toEqual({ message_ids: ['m-2'] })
    expect(await within(box).findByRole('status')).toHaveTextContent('已取消')
  })

  it('says so when nothing has been sent', async () => {
    setup(() => ({ items: [], rejected_replies: [] }))
    const box = await panel()
    expect(await within(box).findByText('还没有主动发过消息。')).toBeVisible()
  })
})
