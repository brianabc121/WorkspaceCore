import { describe, it, expect, afterEach, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// T-0126：终止智能体正在执行的任务 —— 实例的“终止其他智能体任务”权限开关与“终止正在执行的任务”。
const at = '2026-11-06T01:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const instance = (over: Partial<Schema['AgentInstanceOut']> = {}): Schema['AgentInstanceOut'] => ({ id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read_write', max_role: 'member', status: 'online', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: at, reported_version: '2026.9.36', plugin_version: '0.3.3+myworkspace.1', bundle_issued_at: at, im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null }, mail: { accounts: [], read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 }, bundle_outdated: false, server_id: null, server_manual: false, limits: { mem_limit_mb: null, cpus: null }, version: 3, created_at: at, updated_at: at, credentials: [], secret_grants: [], ...over })
const stop = (over: Partial<Schema['AgentRunStopOut']> = {}): Schema['AgentRunStopOut'] => ({ id: 's-1', instance_id: 'inst-a', requested_by: null, requested_by_instance: 'inst-b', task_id: null, reason: '同一个查询调了 40 次', status: 'done', soft_until: at, aborted: 2, error: null, created_at: at, finished_at: at, ...over })

function setup(stops: () => Schema['AgentRunStopList'], override?: (r: Request) => Response | undefined) {
  return mockApi(async r =>
    override?.(r) ??
    (path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/instances' ? Response.json({ items: [instance(), instance({ id: 'inst-b', name: 'beta', stop_others: { enabled: true, granted_by: 'user-a', granted_at: at } })], next_cursor: null, total_estimate: 2 }) :
    path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
    path(r).endsWith('/ssh') ? Response.json({ target: null, job: null, warning: null }) :
    path(r).endsWith('/ops') ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) :
    path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) :
    path(r).endsWith('/wakeups') ? Response.json({ items: [], targets: [] }) :
    path(r) === '/api/v1/agents/instances/inst-a/stops' ? Response.json(stops()) :
    path(r) === '/api/v1/agents/instances/inst-b/stops' ? Response.json({ items: [] }) :
    path(r) === '/api/v1/agents/instances/inst-a/stop' && r.method === 'POST' ? Response.json(stop({ id: 's-2', status: 'pending', requested_by: 'user-a', requested_by_instance: null, aborted: null, finished_at: null }), { status: 201 }) : undefined))
}
afterEach(() => vi.restoreAllMocks())

describe('stopping agent runs (T-0126)', () => {
  it('grants the stop-others permission after confirmation with the instance version', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup(() => ({ items: [] }), r => path(r) === '/api/v1/agents/instances/inst-a/stop-others' && r.method === 'PUT' ? Response.json({ enabled: true, granted_by: 'user-a', granted_at: at }) : undefined)
    // 详情一次显示一个智能体（T-0182）：先看已经打开权限的 beta，再回到 alpha
    const { router } = await openApp('/agents?instance=inst-b')
    const other = within(await screen.findByRole('article', { name: '实例：beta' })).getByRole('region', { name: '终止其他智能体任务的权限' })
    expect(within(other).getByText('终止其他智能体任务的权限：已打开')).toBeVisible()
    await router.push('/agents?instance=inst-a')
    const card = await screen.findByRole('article', { name: '实例：alpha' })
    const section = within(card).getByRole('region', { name: '终止其他智能体任务的权限' })
    expect(within(section).getByText('终止其他智能体任务的权限：关闭')).toBeVisible()
    await fireEvent.click(within(section).getByRole('button', { name: '允许它叫停别的智能体' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'PUT' && path(r).endsWith('/stop-others'))).toBe(true))
    expect(await requests.find(r => r.method === 'PUT' && path(r).endsWith('/stop-others'))!.clone().json()).toEqual({ enabled: true, version: 3 })
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('叫停直接生效、不审批'))
    expect(await screen.findByText('已打开终止其他智能体任务的权限')).toBeVisible()
  })

  it('lists recent stops with who asked and stops a run with a reason', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup(() => ({ items: [stop(), stop({ id: 's-0', requested_by: 'user-a', requested_by_instance: null, status: 'expired', aborted: null, reason: '跑偏了' }), stop({ id: 's-x', status: 'failed', aborted: 0, error: 'sessions.abort failed: 1', reason: '第三次' })] }))
    await openApp('/agents?instance=inst-a')
    const card = await screen.findByRole('article', { name: '实例：alpha' })
    const panel = within(card).getByRole('region', { name: '终止正在执行的任务' })
    const list = await within(panel).findByRole('list', { name: '最近的终止记录' })
    expect(list).toHaveTextContent('智能体「beta」 · 已中止 · 中止了 2 个会话 · 同一个查询调了 40 次')
    expect(list).toHaveTextContent('管理员 · 过期（实例没领取） · 跑偏了')
    expect(list).toHaveTextContent('sessions.abort failed: 1')
    const form = within(panel).getByRole('form', { name: '叫停' })
    await fireEvent.click(within(form).getByRole('button', { name: '叫停' }))
    expect(await within(panel).findByRole('alert')).toHaveTextContent('写一句叫停的原因')
    expect(requests.some(r => r.method === 'POST' && path(r).endsWith('/stop'))).toBe(false)
    await fireEvent.update(within(form).getByLabelText('原因（它会看到）'), '  同一个查询调了 40 次 ')
    await fireEvent.click(within(form).getByRole('button', { name: '叫停' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'POST' && path(r) === '/api/v1/agents/instances/inst-a/stop')).toBe(true))
    expect(await requests.find(r => r.method === 'POST' && path(r).endsWith('/stop'))!.clone().json()).toEqual({ reason: '同一个查询调了 40 次' })
    expect(await within(panel).findByText(/已叫停/)).toBeVisible()
  })

  it('does not stop when the confirmation is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const requests = setup(() => ({ items: [] }))
    await openApp('/agents?instance=inst-a')
    const panel = within(await screen.findByRole('article', { name: '实例：alpha' })).getByRole('region', { name: '终止正在执行的任务' })
    await fireEvent.update(within(panel).getByLabelText('原因（它会看到）'), '停一下')
    await fireEvent.click(within(panel).getByRole('button', { name: '叫停' }))
    expect(window.confirm).toHaveBeenCalled()
    expect(requests.some(r => r.method === 'POST' && path(r).endsWith('/stop'))).toBe(false)
  })
})
