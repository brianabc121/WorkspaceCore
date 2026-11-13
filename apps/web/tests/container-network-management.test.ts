import { afterEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import ContainerNetworkPanel from '../src/features/runner/ContainerNetworkPanel.vue'
import { useAuthStore } from '../src/stores/auth'
import type { Schema } from '../src/services/api'
import { fail, me, workspace } from './helpers'
import { instance } from './agents-fixtures'

const ENV = '10000000-0000-4000-8000-000000000001', TARGET = '20000000-0000-4000-8000-000000000001', SOURCE = '20000000-0000-4000-8000-000000000002'
const SERVICE = '30000000-0000-4000-8000-000000000001', RULE = '40000000-0000-4000-8000-000000000001', ENDPOINT = '50000000-0000-4000-8000-000000000001'
const base = { workspace_id: workspace.id, environment_id: ENV, owner_instance_id: TARGET, host_id: 'host-one', state: 'approved_pending_apply' as const, desired_revision: 2, applied_revision: 0, desired_generation: 1, applied_generation: null, apply_error: 'NETWORK_ENFORCEMENT_UNAVAILABLE', expires_at: null, revoked_by: null, revoked_at: null, created_at: '2026-11-12T00:00:00Z', accepting_new_connections: false, revocation_confirmed: false, connect_host: null, connect_port: null }
const service = (): Schema['RunnerServiceOut'] => ({ ...base, id: SERVICE, name: 'database', port: 5432, protocol: 'TCP', desired_dns: 'database.internal.invalid', visibility: 'private' })
const network = (over: Partial<Schema['RunnerNetworkOut']> = {}): Schema['RunnerNetworkOut'] => ({ environment_id: ENV, workspace_id: workspace.id, owner_instance_id: TARGET, host_id: 'host-one', runtime_status: 'pending_apply', runtime_error: 'NETWORK_RUNTIME_UNCONFIRMED', runtime_ip: null, services: [service()], ingress_rules: [{ ...base, id: RULE, service_id: SERVICE, source_instance_id: SOURCE }], published_endpoints: [{ ...base, id: ENDPOINT, service_id: SERVICE, source_cidrs: ['198.51.100.0/24'] }], ...over })
const path = (r: Request) => new URL(r.url).pathname
const writes = (requests: Request[]) => requests.filter(r => r.method === 'POST')
const until = () => new Date(Date.now() + 24 * 60 * 60_000).toISOString().slice(0, 16)
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.cookie = 'ws_csrf=; Max-Age=0; path=/' })

async function open(options: { role?: 'owner' | 'admin'; data?: Schema['RunnerNetworkOut']; handler?: (r: Request) => Response | Promise<Response | undefined> | undefined } = {}) {
  const requests: Request[] = []
  vi.stubGlobal('fetch', async (r: Request) => {
    requests.push(r)
    const custom = await options.handler?.(r)
    if (custom) return custom
    if (r.method === 'GET' && path(r).endsWith('/network')) return Response.json(options.data ?? network())
    if (path(r) === '/api/v1/agents/instances') return Response.json({ items: [instance({ id: TARGET, name: '目标助手' }), instance({ id: SOURCE, name: '<b>来源助手</b>', status: 'online' }), instance({ id: 'disabled-source', status: 'disabled' })], next_cursor: null, total_estimate: 3 })
    if (r.method === 'POST') {
      const record = path(r).includes('/published-endpoints/') || path(r).endsWith('/publish') ? network().published_endpoints[0]!
        : path(r).includes('/ingress/') || path(r).endsWith('/ingress') ? network().ingress_rules[0]! : service()
      return Response.json(path(r).endsWith('/revoke') ? { ...record, state: 'revoked', desired_revision: 3, revoked_at: new Date().toISOString(), revoked_by: me.user.id, apply_error: 'REVOCATION_PENDING_CONFIRMATION' } : record)
    }
    throw new Error(`Unexpected request: ${r.method} ${path(r)}`)
  })
  document.cookie = 'ws_csrf=test-csrf; path=/'
  const pinia = createPinia(); setActivePinia(pinia)
  const auth = useAuthStore()
  auth.acceptSession({ ...me, workspaces: [{ ...workspace, role: options.role ?? 'owner' }] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const view = render(ContainerNetworkPanel, { props: { environmentId: ENV }, global: { plugins: [pinia, [VueQueryPlugin, { queryClient: client }]] } })
  await screen.findByText('database', { exact: true })
  return { ...view, requests, auth, client }
}

it('opens read-only and displays target/source/service mappings without asserting pending connectivity', async () => {
  const { requests } = await open({ role: 'admin' })
  expect(screen.getByText(SOURCE, { exact: false })).toBeVisible()
  expect(screen.getByText('198.51.100.0/24', { exact: false })).toBeVisible()
  expect(screen.queryByRole('form')).toBeNull()
  expect(writes(requests)).toHaveLength(0)
  expect(screen.queryByText('database.internal.invalid:5432', { exact: true })).toBeNull()
})

it('posts an owner source declaration with expiry, scoped headers and step-up password', async () => {
  const { requests } = await open()
  await fireEvent.click(screen.getByRole('button', { name: '允许来源访问 database' }))
  const form = screen.getByRole('form', { name: '允许跨智能体来源' })
  await screen.findByRole('option', { name: /来源助手/ })
  expect(within(form).getAllByRole('option').map(o => o.getAttribute('value'))).toEqual(['', SOURCE])
  await fireEvent.update(within(form).getByLabelText('来源智能体'), SOURCE)
  await fireEvent.update(within(form).getByLabelText('有效期至'), until())
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  await fireEvent.submit(form)
  await vi.waitFor(() => expect(writes(requests)).toHaveLength(1))
  const request = writes(requests)[0]!
  expect(path(request)).toBe(`/api/v1/runner/environments/${ENV}/services/${SERVICE}/ingress`)
  expect(await request.clone().json()).toMatchObject({ source_instance_id: SOURCE, password: 'test-password' })
  expect(new Date((await request.clone().json()).expires_at).getTime()).toBeGreaterThan(Date.now())
  expect(request.headers.get('X-Workspace-Id')).toBe(workspace.id)
  expect(request.headers.get('X-CSRF-Token')).toBe('test-csrf')
  expect(screen.queryByText('database.internal.invalid:5432', { exact: true })).toBeNull()
})

it('posts explicit public source CIDRs while a saved endpoint remains unconfirmed', async () => {
  const { requests } = await open()
  await fireEvent.click(screen.getByRole('button', { name: '申请公网入口 database' }))
  const form = screen.getByRole('form', { name: '申请受限公网入口' })
  await fireEvent.update(within(form).getByLabelText('允许的 IPv4 公网来源'), '198.51.100.0/24\n203.0.113.7/32')
  await fireEvent.update(within(form).getByLabelText('有效期至'), until())
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  await fireEvent.submit(form)
  await vi.waitFor(() => expect(writes(requests)).toHaveLength(1))
  expect(path(writes(requests)[0]!)).toBe(`/api/v1/runner/environments/${ENV}/services/${SERVICE}/publish`)
  expect(await writes(requests)[0]!.clone().json()).toMatchObject({ source_cidrs: ['198.51.100.0/24', '203.0.113.7/32'], password: 'test-password' })
  expect(screen.queryByRole('link')).toBeNull()
})

it.each([['撤销服务 database', `services/${SERVICE}`], ['撤销入站规则', `ingress/${RULE}`], ['撤销公网入口', `published-endpoints/${ENDPOINT}`]])('confirms %s before a reauthenticated scoped revoke', async (label, suffix) => {
  const { requests } = await open({ data: network({ runtime_status: 'unsupported' }) })
  await fireEvent.click(screen.getByRole('button', { name: label }))
  expect(writes(requests)).toHaveLength(0)
  const form = screen.getByRole('form', { name: '确认撤销网络规则' })
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  await fireEvent.submit(form)
  await vi.waitFor(() => expect(writes(requests)).toHaveLength(1))
  expect(path(writes(requests)[0]!)).toBe(`/api/v1/runner/environments/${ENV}/${suffix}/revoke`)
  expect(await writes(requests)[0]!.clone().json()).toEqual({ password: 'test-password' })
})

it('clears passwords after unknown writes and never automatically replays them', async () => {
  const { requests } = await open({ handler: r => r.method === 'POST' ? fail(503, 'WRITE_UNKNOWN') : undefined })
  await fireEvent.click(screen.getByRole('button', { name: '申请公网入口 database' }))
  const form = screen.getByRole('form', { name: '申请受限公网入口' })
  await fireEvent.update(within(form).getByLabelText('允许的 IPv4 公网来源'), '198.51.100.0/24')
  await fireEvent.update(within(form).getByLabelText('有效期至'), until())
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  await fireEvent.submit(form)
  expect(await screen.findByRole('alert')).toHaveTextContent('结果未知')
  expect(within(form).getByLabelText('确认登录密码')).toHaveValue('')
  await new Promise(resolve => setTimeout(resolve, 30))
  expect(writes(requests)).toHaveLength(1)
})

it('hides cached usable addresses after a failed network refresh', async () => {
  let unavailable = false
  const active = { ...service(), state: 'active' as const, applied_revision: 2, applied_generation: 1, accepting_new_connections: true, connect_host: 'database.internal.invalid', connect_port: 5432, apply_error: null }
  const { client } = await open({ data: network({ runtime_status: 'active', services: [active] }), handler: r => unavailable && r.method === 'GET' && path(r).endsWith('/network') ? fail(503, 'HTTP_ERROR') : undefined })
  expect(screen.getByText('database.internal.invalid:5432')).toBeVisible()
  unavailable = true
  await client.invalidateQueries({ queryKey: ['runner'] })
  expect(await screen.findByRole('alert')).toHaveTextContent('显示上次读取的规则')
  expect(screen.queryByText('database.internal.invalid:5432')).toBeNull()
  expect(screen.queryByText('内部服务已收到节点生效回执。')).toBeNull()
})

it('clears pending confirmation when the environment changes instead of retargeting it', async () => {
  const { rerender, requests } = await open()
  await fireEvent.click(screen.getByRole('button', { name: '撤销服务 database' }))
  const form = screen.getByRole('form', { name: '确认撤销网络规则' })
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  await rerender({ environmentId: '10000000-0000-4000-8000-000000000002' })
  expect(screen.queryByRole('form', { name: '确认撤销网络规则' })).toBeNull()
  expect(writes(requests)).toHaveLength(0)
})

it('drops network data and open forms after workspace scope changes', async () => {
  const { auth, requests } = await open()
  await fireEvent.click(screen.getByRole('button', { name: '撤销服务 database' }))
  auth.workspaceId = 'other-workspace'
  await vi.waitFor(() => expect(screen.queryByText('database', { exact: true })).toBeNull())
  expect(screen.queryByRole('form', { name: '确认撤销网络规则' })).toBeNull()
  expect(writes(requests)).toHaveLength(0)
})

it('stops presenting an expiring endpoint as connectable before the next scheduled read', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] })
  try {
    const current = { ...base, id: ENDPOINT, service_id: SERVICE, source_cidrs: ['198.51.100.0/24'], state: 'active' as const, applied_revision: 2, applied_generation: 1, accepting_new_connections: true, connect_host: 'public.internal.invalid', connect_port: 443, expires_at: new Date(Date.now() + 2000).toISOString(), apply_error: null }
    await open({ data: network({ published_endpoints: [current] }) })
    expect(screen.getByText('public.internal.invalid:443')).toBeVisible()
    await vi.advanceTimersByTimeAsync(3000)
    expect(screen.queryByText('public.internal.invalid:443')).toBeNull()
    expect(screen.getByText(/已到期，等待刷新/)).toBeVisible()
  } finally { vi.useRealTimers() }
})

it('does not resurrect a password-bearing confirmation after environment access is lost then restored', async () => {
  let denied = false
  const { client, requests } = await open({ handler: r => denied && r.method === 'GET' && path(r).endsWith('/network') ? fail(403, 'FORBIDDEN') : undefined })
  await fireEvent.click(screen.getByRole('button', { name: '撤销服务 database' }))
  await fireEvent.update(within(screen.getByRole('form', { name: '确认撤销网络规则' })).getByLabelText('确认登录密码'), 'test-password')
  denied = true
  await client.invalidateQueries({ queryKey: ['runner'] })
  await vi.waitFor(() => expect(screen.queryByText('database', { exact: true })).toBeNull())
  denied = false
  await client.invalidateQueries({ queryKey: ['runner'] })
  await screen.findByText('database', { exact: true })
  expect(screen.queryByRole('form', { name: '确认撤销网络规则' })).toBeNull()
  expect(writes(requests)).toHaveLength(0)
})

it('edits a service with its captured revision and step-up while retaining failed input', async () => {
  const { requests } = await open({ handler: r => r.method === 'PATCH' ? fail(409, 'VERSION_CONFLICT') : undefined })
  await fireEvent.click(screen.getByRole('button', { name: '编辑服务 database' }))
  const form = screen.getByRole('form', { name: '编辑内部服务' })
  expect(within(form).getByLabelText('服务名称')).toHaveValue('database')
  await fireEvent.update(within(form).getByLabelText('服务名称'), 'database-new')
  await fireEvent.update(within(form).getByLabelText('服务端口'), '5433')
  await fireEvent.update(within(form).getByLabelText('协议'), 'UDP')
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  await fireEvent.submit(form)
  expect(await screen.findByRole('alert')).toHaveTextContent('已被其他人修改')
  const request = requests.find(r => r.method === 'PATCH')!
  expect(path(request)).toBe(`/api/v1/runner/environments/${ENV}/services/${SERVICE}`)
  expect(request.headers.get('X-Workspace-Id')).toBe(workspace.id)
  expect(await request.clone().json()).toEqual({ name: 'database-new', port: 5433, protocol: 'UDP', expected_revision: 2, password: 'test-password' })
  expect(within(form).getByLabelText('服务名称')).toHaveValue('database-new')
  expect(within(form).getByLabelText('确认登录密码')).toHaveValue('')
})

it.each([['编辑入站规则', '编辑跨智能体规则', `ingress/${RULE}`], ['编辑公网入口', '编辑受限公网入口', `published-endpoints/${ENDPOINT}`]])('edits finite authorization leases through %s', async (button, formName, suffix) => {
  const { requests } = await open({ handler: r => r.method === 'PATCH' ? Response.json(network().ingress_rules[0]) : undefined })
  await fireEvent.click(screen.getByRole('button', { name: button }))
  const form = screen.getByRole('form', { name: formName })
  await fireEvent.update(within(form).getByLabelText('有效期至'), until())
  if (suffix.startsWith('published')) await fireEvent.update(within(form).getByLabelText('允许的 IPv4 公网来源'), '203.0.113.7/32')
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  await fireEvent.submit(form)
  await vi.waitFor(() => expect(requests.some(r => r.method === 'PATCH')).toBe(true))
  const request = requests.find(r => r.method === 'PATCH')!
  expect(path(request)).toBe(`/api/v1/runner/environments/${ENV}/${suffix}`)
  expect(await request.clone().json()).toMatchObject({ expected_revision: 2, password: 'test-password' })
  expect(new Date((await request.clone().json()).expires_at).getTime()).toBeGreaterThan(Date.now())
  if (suffix.startsWith('published')) expect(await request.clone().json()).toMatchObject({ source_cidrs: ['203.0.113.7/32'] })
})

it('keeps UDP private and explains TCP-only cross-instance and public gateways', async () => {
  await open({ data: network({ runtime_status: 'active', runtime_ip: '10.42.1.7', services: [{ ...service(), protocol: 'UDP' }] }) })
  expect(screen.getByText(/同一工作空间和同一智能体/)).toBeVisible()
  expect(screen.getByText('10.42.1.7')).toBeVisible()
  expect(screen.queryByRole('button', { name: '允许来源访问 database' })).toBeNull()
  expect(screen.queryByRole('button', { name: '申请公网入口 database' })).toBeNull()
  expect(screen.getByText(/UDP 仅用于同域私网/)).toBeVisible()
})

it.each([false, true])('distinguishes requested revocation from confirmed termination (%s)', async confirmed => {
  await open({ data: network({ services: [{ ...service(), state: 'revoked', revocation_confirmed: confirmed, apply_error: confirmed ? null : 'REVOCATION_PENDING_CONFIRMATION' }] }) })
  expect(screen.getByText(confirmed ? '已撤销，现有连接关闭已确认' : '已撤销，等待现有连接关闭确认', { exact: false })).toBeVisible()
})

it('retains public edit values on refresh errors, hides addresses and clears its password', async () => {
  let down = false
  const { client } = await open({ handler: r => down && r.method === 'GET' && path(r).endsWith('/network') ? fail(503, 'HTTP_ERROR') : undefined })
  await fireEvent.click(screen.getByRole('button', { name: '编辑公网入口' }))
  const form = screen.getByRole('form', { name: '编辑受限公网入口' })
  await fireEvent.update(within(form).getByLabelText('允许的 IPv4 公网来源'), '203.0.113.7/32')
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  down = true
  await client.invalidateQueries({ queryKey: ['runner'] })
  expect(await screen.findByRole('alert')).toHaveTextContent('显示上次读取的规则')
  expect(within(form).getByLabelText('允许的 IPv4 公网来源')).toHaveValue('203.0.113.7/32')
  expect(within(form).getByLabelText('确认登录密码')).toHaveValue('')
  expect(within(form).getByRole('button', { name: '保存修改' })).toBeDisabled()
})

it.each([['UNEXPECTED', '请求失败，请稍后重试'], ['STEP_UP_FAILED', '密码验证失败'], ['RATE_LIMITED', '密码验证次数过多'], ['NETWORK_PROTOCOL_UNSUPPORTED', '仅支持 TCP']])('never renders a password echoed for %s', async (code, message) => {
  await open({ handler: r => r.method === 'POST' ? Response.json({ error: { code, message: 'echo test-password', request_id: 'req', details: {} } }, { status: 500 }) : undefined })
  await fireEvent.click(screen.getByRole('button', { name: '撤销服务 database' }))
  const form = screen.getByRole('form', { name: '确认撤销网络规则' })
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  await fireEvent.submit(form)
  expect(await screen.findByRole('alert')).toHaveTextContent(message)
  expect(screen.queryByText(/test-password/)).toBeNull()
})

it('drops cached source choices after source access is denied and cannot submit them', async () => {
  let denied = false
  const { client, requests } = await open({ handler: r => denied && path(r) === '/api/v1/agents/instances' ? fail(403, 'FORBIDDEN') : undefined })
  await fireEvent.click(screen.getByRole('button', { name: '允许来源访问 database' }))
  const form = screen.getByRole('form', { name: '允许跨智能体来源' })
  await screen.findByRole('option', { name: /来源助手/ })
  await fireEvent.update(within(form).getByLabelText('来源智能体'), SOURCE)
  await fireEvent.update(within(form).getByLabelText('有效期至'), until())
  await fireEvent.update(within(form).getByLabelText('确认登录密码'), 'test-password')
  denied = true
  await client.invalidateQueries({ queryKey: ['agents'] })
  expect(await within(form).findByRole('alert')).toHaveTextContent('来源列表读取失败')
  expect(within(form).queryByRole('option', { name: /来源助手/ })).toBeNull()
  expect(within(form).getByLabelText('确认登录密码')).toHaveValue('')
  expect(within(form).getByRole('button', { name: '保存来源规则' })).toBeDisabled()
  await fireEvent.submit(form)
  expect(writes(requests)).toHaveLength(0)
})
