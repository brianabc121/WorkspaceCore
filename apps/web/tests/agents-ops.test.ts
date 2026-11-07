import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// T-0114：实例卡片里的“运维”面板（固定动作、二次认证、输出、记录）。
const at = '2026-11-04T04:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const instance: Schema['AgentInstanceOut'] = { id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read', max_role: 'member', status: 'online', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: at, reported_version: '2026.9.36', plugin_version: '0.3.3', bundle_issued_at: at, im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null }, mail: { accounts: [], read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 }, bundle_outdated: false, server_id: null, server_manual: false, limits: { mem_limit_mb: null, cpus: null }, version: 3, created_at: at, updated_at: at, credentials: [], secret_grants: [] }
const action = (over: Partial<Schema['AgentOpsActionOut']> = {}): Schema['AgentOpsActionOut'] => ({ id: 'op-1', action: 'status', service: null, status: 'succeeded', exit_code: 0, output: null, truncated: false, error_code: null, error_message: null, started_by: 'user-a', started_at: at, finished_at: at, expires_at: '2026-11-11T04:00:00Z', ...over })
const state = (over: Partial<Schema['AgentOpsState']> = {}): Schema['AgentOpsState'] => ({ available: true, reason: null, running: null, items: [], ...over })
const fail = (status: number, code: string) => Response.json({ error: { code, message: 'x', request_id: 'req', details: {} } }, { status })

function setup(handler: (r: Request) => Response | Promise<Response | undefined> | undefined) {
  return mockApi(async r => await handler(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/instances' ? Response.json({ items: [instance], next_cursor: null, total_estimate: 1 }) :
    path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
    path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) : undefined
  ))
}
async function panel() {
  await openApp('/agents')
  const card = await screen.findByRole('article', { name: '实例：alpha' })
  return within(card).getByRole('region', { name: '运维' })
}
const posted = async (requests: Request[]) => {
  await vi.waitFor(() => expect(requests.some(r => r.method === 'POST' && path(r).endsWith('/ops'))).toBe(true))
  return await requests.filter(r => r.method === 'POST' && path(r).endsWith('/ops')).at(-1)!.clone().json() as unknown
}
afterEach(() => vi.restoreAllMocks())

describe('agent ops panel', () => {
  it('runs a fixed action and shows its redacted output', async () => {
    let done = false
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/instances/inst-a/ops' && r.method === 'GET') return Response.json(state({ items: done ? [action()] : [] }))
      if (path(r) === '/api/v1/agents/instances/inst-a/ops' && r.method === 'POST') return Response.json(action({ status: 'running', exit_code: null, finished_at: null, output: '' }), { status: 202 })
      if (path(r) === '/api/v1/agents/instances/inst-a/ops/op-1') { done = true; return Response.json(action({ output: '$ docker compose ps --all\nopenclaw running\ntoken mwa_***' })) }
    })
    const ops = await panel()
    await fireEvent.click(await within(ops).findByRole('button', { name: '查看状态' }))
    expect(await posted(requests)).toEqual({ action: 'status' })
    const result = await within(ops).findByRole('article', { name: '运维结果：查看状态' })
    expect(await within(result).findByLabelText(/输出/)).toHaveTextContent('openclaw running')
    expect(within(result).getByText('成功')).toBeVisible()
    expect(within(result).getByText(/退出码 0/)).toBeVisible()
  })

  it('asks for the password before restart, sends it once and keeps the form on a wrong password', async () => {
    let wrong = true
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/instances/inst-a/ops' && r.method === 'GET') return Response.json(state())
      if (path(r) === '/api/v1/agents/instances/inst-a/ops' && r.method === 'POST') {
        if (wrong) { wrong = false; return fail(403, 'STEP_UP_FAILED') }
        return Response.json(action({ action: 'restart', status: 'running', exit_code: null, finished_at: null, output: '' }), { status: 202 })
      }
      if (path(r) === '/api/v1/agents/instances/inst-a/ops/op-1') return Response.json(action({ action: 'restart', output: '$ docker compose restart openclaw' }))
    })
    const ops = await panel()
    await fireEvent.click(await within(ops).findByRole('button', { name: '重启 OpenClaw' }))
    let form = within(ops).getByRole('form', { name: '确认：重启 OpenClaw' })
    const input = within(form).getByLabelText('你的登录密码')
    expect(input).toHaveAttribute('type', 'password')
    expect(within(form).getByRole('button', { name: '确认执行' })).toBeDisabled()
    await fireEvent.update(input, 'not-it')
    await fireEvent.click(within(form).getByRole('button', { name: '确认执行' }))
    expect(await within(ops).findByText('密码错误。')).toBeVisible()
    expect(await posted(requests)).toEqual({ action: 'restart', password: 'not-it' })
    form = within(ops).getByRole('form', { name: '确认：重启 OpenClaw' })
    expect(within(form).getByLabelText('你的登录密码')).toHaveValue('')
    await fireEvent.update(within(form).getByLabelText('你的登录密码'), 'right-one')
    await fireEvent.click(within(form).getByRole('button', { name: '确认执行' }))
    await vi.waitFor(() => expect(within(ops).queryByRole('form', { name: '确认：重启 OpenClaw' })).not.toBeInTheDocument())
    expect(await posted(requests)).toEqual({ action: 'restart', password: 'right-one' })
    expect(await within(ops).findByRole('article', { name: '运维结果：重启 OpenClaw' })).toBeVisible()
  })

  it('sends the chosen log source, blocks actions while one runs, and lists recent records', async () => {
    let running = false
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/instances/inst-a/ops' && r.method === 'GET') return Response.json(state(running
        ? { running: action({ id: 'op-2', action: 'logs', service: 'tunnel', status: 'running', exit_code: null, finished_at: null }), items: [action({ id: 'op-2', action: 'logs', service: 'tunnel', status: 'running', exit_code: null }), action()] }
        : { items: [action()] }))
      if (path(r) === '/api/v1/agents/instances/inst-a/ops' && r.method === 'POST') { running = true; return Response.json(action({ id: 'op-2', action: 'logs', service: 'tunnel', status: 'running', exit_code: null, finished_at: null, output: '' }), { status: 202 }) }
      if (path(r) === '/api/v1/agents/instances/inst-a/ops/op-2') return Response.json(action({ id: 'op-2', action: 'logs', service: 'tunnel', status: 'running', exit_code: null, finished_at: null, output: 'tunnel | started' }))
      if (path(r) === '/api/v1/agents/instances/inst-a/ops/op-1') return Response.json(action({ output: 'earlier output' }))
    })
    const ops = await panel()
    await fireEvent.update(await within(ops).findByLabelText('日志来源'), 'tunnel')
    await fireEvent.click(within(ops).getByRole('button', { name: '查看日志' }))
    expect(await posted(requests)).toEqual({ action: 'logs', service: 'tunnel' })
    const result = await within(ops).findByRole('article', { name: '运维结果：查看日志' })
    expect(within(result).getByText('查看日志（本机隧道）')).toBeVisible()
    expect(await within(result).findByText('正在执行…')).toBeVisible()
    await vi.waitFor(() => expect(within(ops).getByRole('button', { name: '查看状态' })).toBeDisabled())
    expect(within(ops).getByRole('button', { name: '重启 OpenClaw' })).toBeDisabled()
    const history = within(ops).getByText(/最近的运维记录/).closest('details')!
    const rows = within(history).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    await fireEvent.click(within(rows[1]).getByRole('button', { name: '查看输出' }))
    expect(await within(ops).findByText('earlier output')).toBeVisible()
  })

  it('explains that manual instances need to be deployed to a server first', async () => {
    setup(r => path(r) === '/api/v1/agents/instances/inst-a/ops' ? Response.json(state({ available: false, reason: 'not_deployed' })) : undefined)
    const ops = await panel()
    expect(await within(ops).findByText(/只适用于部署到 SSH 服务器的实例/)).toBeVisible()
    expect(within(ops).queryByRole('button', { name: '查看状态' })).not.toBeInTheDocument()
  })
})
