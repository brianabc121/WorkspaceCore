import { describe, it, expect, afterEach, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// T-0121：实例卡片里的“唤醒”（到点叫醒智能体跑一轮；智能体自己安排的和管理员建的）。
const at = '2026-11-06T01:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const instance: Schema['AgentInstanceOut'] = { id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read_write', max_role: 'member', status: 'online', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: at, reported_version: '2026.9.36', plugin_version: '0.3.3', bundle_issued_at: at, im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null }, mail: { read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 }, bundle_outdated: false, version: 3, created_at: at, updated_at: at, credentials: [], secret_grants: [] }
const wakeup = (over: Partial<Schema['AgentWakeupOut']> = {}): Schema['AgentWakeupOut'] => ({ id: 'w-1', source: 'admin', created_by: 'user-a', target: 'group:g1', prompt: '在项目群发昨天的进展', run_at: at, repeat: 'weekdays', time_zone: 'Asia/Shanghai', status: 'pending', runs: 0, last_run_at: null, last_error: null, created_at: at, ...over })

function setup(list: () => Schema['AgentWakeupList']) {
  return mockApi(async r =>
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/instances' ? Response.json({ items: [instance], next_cursor: null, total_estimate: 1 }) :
    path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
    path(r) === '/api/v1/agents/instances/inst-a/ssh' ? Response.json({ target: null, job: null, warning: null }) :
    path(r).endsWith('/ops') ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) :
    path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) :
    path(r) === '/api/v1/agents/instances/inst-a/wakeups' && r.method === 'GET' ? Response.json(list()) :
    path(r) === '/api/v1/agents/instances/inst-a/wakeups' && r.method === 'POST' ? Response.json(wakeup({ id: 'w-2' }), { status: 201 }) :
    path(r) === '/api/v1/agents/instances/inst-a/wakeups/w-1/cancel' ? Response.json(wakeup({ status: 'cancelled' })) : undefined)
}
afterEach(() => vi.restoreAllMocks())

describe('agent wakeups (T-0121)', () => {
  it('lists wakeups with their session and cancels a pending one', async () => {
    const requests = setup(() => ({ items: [wakeup(), wakeup({ id: 'w-3', source: 'agent', target: null, repeat: 'none', status: 'failed', last_error: '领取后 15 分钟没有报回结果', prompt: '提醒交周报' })], targets: [{ target: 'group:g1', label: '群 发布群' }] }))
    await openApp('/agents')
    const panel = await screen.findByRole('region', { name: '唤醒' })
    const list = await within(panel).findByRole('list', { name: '唤醒列表' })
    expect(list).toHaveTextContent('工作日 · 群 发布群')
    expect(list).toHaveTextContent('它自己安排的')
    expect(list).toHaveTextContent('领取后 15 分钟没有报回结果')
    await fireEvent.click(within(panel).getByRole('button', { name: '取消唤醒：在项目群发昨天的进展' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/w-1/cancel') && r.method === 'POST')).toBe(true))
    expect(within(panel).queryByRole('button', { name: '取消唤醒：提醒交周报' })).toBeNull()
  })
  it('creates a repeating wakeup in a chosen session with the browser time zone', async () => {
    const requests = setup(() => ({ items: [], targets: [{ target: 'group:g1', label: '群 发布群' }] }))
    await openApp('/agents')
    const form = await screen.findByRole('form', { name: '新建唤醒' })
    await fireEvent.update(within(form).getByLabelText('要它做的事'), '在项目群发昨天的进展')
    await fireEvent.update(within(form).getByLabelText(/^时间/), '2026-11-09T09:00')
    await fireEvent.update(within(form).getByLabelText('重复'), 'weekdays')
    await fireEvent.update(within(form).getByLabelText('在哪个会话里'), 'group:g1')
    await fireEvent.click(within(form).getByRole('button', { name: '建唤醒' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/wakeups') && r.method === 'POST')).toBe(true))
    const body = await requests.find(r => path(r).endsWith('/wakeups') && r.method === 'POST')!.clone().json()
    expect(body).toMatchObject({ prompt: '在项目群发昨天的进展', target: 'group:g1', repeat: 'weekdays' })
    expect(body.run_at).toBe(new Date('2026-11-09T09:00').toISOString())
    expect(body.time_zone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone)
    expect(await screen.findByText('已建唤醒')).toBeVisible()
  })
})
