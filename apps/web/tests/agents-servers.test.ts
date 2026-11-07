import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// T-0147：“服务器”标签（添加、核对指纹、准备、详情、每个智能体的份额与表格）与实例卡片的“部署到服务器”。
const at = '2026-11-07T04:00:00Z'
const GB = 1024 ** 3
const path = (r: Request) => new URL(r.url).pathname
const instance = (over: Partial<Schema['AgentInstanceOut']> = {}): Schema['AgentInstanceOut'] => ({ id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read', max_role: 'member', status: 'pending', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: null, reported_version: null, plugin_version: null, bundle_issued_at: null, im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null }, mail: { accounts: [], read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 }, bundle_outdated: false, server_id: null, server_manual: false, limits: { mem_limit_mb: null, cpus: null }, version: 3, created_at: at, updated_at: at, credentials: [], secret_grants: [], ...over })
const ssh = (over: Partial<Schema['ServerSshOut']> = {}): Schema['ServerSshOut'] => ({ host: 'oc.example.com', address: '203.0.113.10', port: 2222, username: 'deploy', host_key_algorithm: 'ssh-ed25519', host_key_fingerprint: 'SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG', host_key_confirmed: false, host_key_confirmed_at: null, client_key_installed: false, client_key_fingerprint: null, prepared_at: null, ...over })
const server = (over: Partial<Schema['AgentServerOut']> = {}): Schema['AgentServerOut'] => ({ id: 'srv-1', name: '香港 4 核', kind: 'ssh', ssh: ssh(), reported_at: null, stale: true, cores: null, cpu_percent: null, load15: null, mem_total: null, mem_used_percent: null, disk_used_percent: null, instances_total: 0, instances_online: 0, alerts: [], version: 1, created_at: at, updated_at: at, ...over })
const PREPARE = ['connect', 'inspect', 'authorize_key', 'docker', 'reconnect'] as const
const job = (kind: Schema['AgentSshJobOut']['kind'], status: Schema['AgentSshJobOut']['status'], over: Partial<Schema['AgentSshJobOut']> = {}): Schema['AgentSshJobOut'] => ({ id: `job-${kind}`, kind, status, log: '$ sudo -S true\npassword=***', error_code: null, error_message: null, started_at: at, finished_at: status === 'running' ? null : at, updated_at: at,
  steps: PREPARE.map((key, i) => ({ key, status: status === 'succeeded' ? 'done' : i < 2 ? 'done' : i === 2 ? 'running' : 'pending', detail: null })), ...over })
const detail = (s: Schema['AgentServerOut'], over: Partial<Schema['AgentServerDetail']> = {}): Schema['AgentServerDetail'] => ({ server: s, current: null, range: '24h', points: [], agents: [], job: null, warning: null, ...over })
const load = (over: Partial<Schema['AgentLoad']> = {}): Schema['AgentLoad'] => ({ reported_at: at, stale: false, available: true, cpu_cores: 1, cpu_percent: 25, mem_bytes: 4 * GB, mem_limit: null, mem_total: 16 * GB, pids: 30, data_bytes: GB, data_truncated: false, rx_rate: 1024, tx_rate: 512, restarts_1h: 0, ...over })
const row = (id: string, name: string, over: Partial<Schema['ServerAgentRow']> = {}): Schema['ServerAgentRow'] => ({ instance_id: id, name, status: 'online', deployed: true, install_dir: `/home/deploy/myworkspace-openclaw/${id}`, mem_limit_mb: null, cpus: null, load: load(), alerts: [], tokens_1h: 0, running_task: null, ...over })
const current: Schema['HostCurrent'] = { reported_at: at, stale: false, cpu_model: 'Test CPU', cores: 4, cpu_percent: 60, load: [1, 1, 1], mem_total: 16 * GB, mem_available: 4 * GB, mem_used: 12 * GB, mem_limit: null, swap_total: 0, swap_used: 0, data_disk: { total: 100 * GB, free: 50 * GB, used: 50 * GB, used_percent: 50 }, root_disk: { total: 100 * GB, free: 50 * GB, used: 50 * GB, used_percent: 50 }, rx_rate: 2048, tx_rate: 1024, uptime_seconds: 86400 }
const deployment = (over: Partial<Schema['AgentDeploymentState']> = {}): Schema['AgentDeploymentState'] => ({ deployment: null, server: null, job: null, capacity_warning: null, ...over })
const fail = (status: number, code: string, details: Record<string, unknown> = {}, headers: Record<string, string> = {}) => Response.json({ error: { code, message: 'x', request_id: 'req', details } }, { status, headers })

function setup(handler: (r: Request) => Response | Promise<Response | undefined> | undefined, inst: Schema['AgentInstanceOut'] = instance()) {
  return mockApi(async r => await handler(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/instances' ? Response.json({ items: [inst], next_cursor: null, total_estimate: 1 }) :
    path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
    path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) :
    path(r).endsWith('/ops') && r.method === 'GET' ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) : undefined
  ))
}
const json = async (requests: Request[], method: string, p: string) => {
  await vi.waitFor(() => expect(requests.some(r => r.method === method && path(r) === p)).toBe(true))
  return await requests.filter(r => r.method === method && path(r) === p).at(-1)!.clone().json() as unknown
}
async function deployPanel() {
  await openApp('/agents')
  const card = await screen.findByRole('article', { name: '实例：alpha' })
  return within(card).getByRole('region', { name: '部署到服务器' })
}
afterEach(() => vi.restoreAllMocks())

describe('servers tab', () => {
  it('adds a server, confirms its fingerprint and prepares it with a password sent once', async () => {
    let current = server(), polls = 0
    let state = detail(current)
    const requests = setup(async r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json({ items: polls ? [current] : [], unassigned: [{ id: 'inst-a', name: 'alpha', status: 'pending' }] })
      if (path(r) === '/api/v1/agents/servers' && r.method === 'POST') { polls++; return Response.json(state, { status: 201 }) }
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') { polls++; return Response.json(state) }
      if (path(r) === '/api/v1/agents/servers/srv-1/confirm') {
        current = server({ ssh: ssh({ host_key_confirmed: true, host_key_confirmed_at: at }), version: 2 }); state = detail(current)
        return Response.json(state)
      }
      if (path(r) === '/api/v1/agents/servers/srv-1/prepare') {
        const running = detail(current, { job: job('prepare', 'running') })
        // 下一次取详情时任务已经结束
        current = server({ ssh: ssh({ host_key_confirmed: true, host_key_confirmed_at: at, client_key_installed: true, client_key_fingerprint: 'SHA256:client', prepared_at: at }), version: 3 })
        state = detail(current, { job: job('prepare', 'succeeded') })
        return Response.json(running, { status: 202 })
      }
    })
    await openApp('/agents?tab=servers')
    expect(await screen.findByText(/还没有服务器/)).toBeVisible()
    const unassigned = screen.getByRole('region', { name: '还没归属服务器的实例' })
    expect(within(unassigned).getByText('alpha · 等待接入')).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '添加服务器' }))
    const form = screen.getByRole('form', { name: '添加服务器' })
    const probe = within(form).getByRole('button', { name: '读取主机公钥' })
    await fireEvent.update(within(form).getByLabelText('名称 *'), '香港 4 核')
    await fireEvent.update(within(form).getByLabelText('服务器 IP 或域名 *'), ' oc.example.com ')
    await fireEvent.update(within(form).getByLabelText('SSH 端口 *'), '2222')
    await fireEvent.update(within(form).getByLabelText('SSH 账号 *'), 'bad user')
    expect(probe).toBeDisabled()
    await fireEvent.update(within(form).getByLabelText('SSH 账号 *'), 'deploy')
    await fireEvent.click(probe)
    expect(await json(requests, 'POST', '/api/v1/agents/servers')).toEqual({ name: '香港 4 核', host: 'oc.example.com', port: 2222, username: 'deploy' })

    // 添加后进入详情：核对指纹
    const keys = await screen.findByRole('region', { name: 'SSH 与专用密钥' })
    const check = await within(keys).findByRole('group', { name: '核对主机公钥' })
    expect(within(check).getByText('SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG')).toBeVisible()
    expect(within(check).getByText('ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub')).toBeVisible()
    const confirm = within(check).getByRole('button', { name: '确认指纹' })
    expect(confirm).toBeDisabled()
    await fireEvent.click(within(check).getByLabelText(/我已在服务器上核对/))
    await fireEvent.click(confirm)
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-1/confirm')).toEqual({ version: 1, fingerprint: 'SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG' })

    // 准备服务器：一次密码，提交后清空
    const prepare = await within(keys).findByRole('form', { name: '准备服务器' })
    const password = within(prepare).getByLabelText('登录密码 *') as HTMLInputElement
    expect(password).toHaveAttribute('type', 'password')
    await fireEvent.update(password, 'hunter2-secret')
    await fireEvent.click(within(prepare).getByRole('button', { name: '准备服务器' }))
    expect(password.value).toBe('')
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-1/prepare')).toEqual({ version: 2, password: 'hunter2-secret' })
    expect(await within(keys).findByText(/服务器已准备好：之后在这台服务器上部署实例不用再输密码/, {}, { timeout: 4000 })).toBeVisible()
    expect(within(keys).getByText(/已装在服务器上（SHA256:client）/)).toBeVisible()
    expect(within(keys).getByText(/password=\*\*\*/)).toBeInTheDocument()
    const bodies = await Promise.all(requests.filter(r => r.method !== 'GET').map(r => r.clone().text()))
    expect(bodies.filter(b => b.includes('hunter2-secret'))).toHaveLength(1)
    expect(document.body.innerHTML).not.toContain('hunter2-secret')
  })

  it('shows the whole server, each agent’s share, a sortable agents table and one agent’s trend', async () => {
    const s = server({ ssh: ssh({ host_key_confirmed: true, host_key_confirmed_at: at, client_key_installed: true, prepared_at: at }), reported_at: at, stale: false, cores: 4, cpu_percent: 60, mem_total: 16 * GB, mem_used_percent: 75, disk_used_percent: 50, instances_total: 2, instances_online: 2 })
    const agents = [
      row('inst-a', 'alpha', { load: load({ cpu_percent: 10, mem_bytes: 2 * GB }), tokens_1h: 5000, running_task: { id: 't-1', title: '写周报' } }),
      row('inst-b', 'beta', { load: load({ cpu_percent: 30, mem_bytes: 6 * GB, mem_limit: 6.2 * GB }), alerts: ['mem_near_limit'], tokens_1h: 100 }),
    ]
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json({ items: [s], unassigned: [] })
      if (path(r) === '/api/v1/agents/servers/srv-1') return Response.json(detail(s, { current, agents }))
      if (path(r) === '/api/v1/agents/instances/inst-b/host') return Response.json({ instance_id: 'inst-b', reported: true, server: { id: 'srv-1', name: s.name }, current, range: '24h', points: [], alerts: [], agent: agents[1].load, agent_points: [{ at, cpu_avg: 30, cpu_max: 50, cores_avg: 1.2, mem_avg: 6 * GB, mem_max: 6.1 * GB, rx_avg: 1, rx_max: 2, tx_avg: 1, tx_max: 2 }], agent_alerts: ['mem_near_limit'] })
    })
    await openApp('/agents?tab=servers')
    const card = await screen.findByRole('article', { name: '服务器：香港 4 核' })
    expect(within(card).getByText(/2 个智能体，2 个在线 · 4 核/)).toBeVisible()
    expect(within(card).getByText(/CPU/)).toHaveTextContent('CPU 60%')
    await fireEvent.click(within(card).getByRole('button', { name: '查看详情' }))

    const whole = await screen.findByRole('region', { name: '整机负载' })
    // 堆叠条：每个智能体占整机多少；颜色按名字的固定顺序，系统与其他程序单列
    const cpu = within(whole).getByRole('img', { name: /CPU：每个智能体占多少/ })
    expect(cpu).toHaveAccessibleName('CPU：每个智能体占多少：整机 60%。alpha 10%，beta 30%，系统与其他程序 20%')
    const mem = within(whole).getByText('内存：每个智能体占多少').closest('figure')!
    expect(within(mem).getByText('alpha').closest('li')).toHaveTextContent('alpha13%（2.0 GB）')
    expect(within(mem).getByText('系统与其他程序').closest('li')).toHaveTextContent('25%（4.0 GB）')

    const table = within(screen.getByRole('region', { name: '这台服务器上的智能体' })).getByRole('table')
    const names = () => within(table).getAllByRole('row').slice(1).map(r => within(r).getAllByRole('rowheader')[0].textContent?.trim())
    // 默认按 CPU 从高到低
    expect(names()).toEqual(['beta', 'alpha'])
    expect(within(table).getByRole('columnheader', { name: /CPU（占整机）/ })).toHaveAttribute('aria-sort', 'descending')
    await fireEvent.click(within(table).getByRole('button', { name: /1 小时 token/ }))
    expect(names()).toEqual(['alpha', 'beta'])
    await fireEvent.click(within(table).getByRole('button', { name: /1 小时 token/ }))
    expect(names()).toEqual(['beta', 'alpha'])
    expect(within(table).getByText('写周报')).toBeVisible()
    expect(within(table).getByText('内存接近上限')).toBeVisible()
    // 单个智能体的趋势
    const betaRow = within(table).getByText('beta').closest('tr')!
    await fireEvent.click(within(betaRow).getByRole('button', { name: '查看趋势' }))
    const trend = await screen.findByRole('region', { name: 'beta 的趋势' })
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/agents/instances/inst-b/host')).toBe(true))
    expect(await within(trend).findByText('CPU（占整机）')).toBeVisible()
    // 服务器级运维：查看全部容器、清理镜像
    expect(screen.getByRole('region', { name: '服务器运维' })).toBeVisible()
    // 返回列表
    await fireEvent.click(screen.getByRole('button', { name: '← 返回服务器列表' }))
    expect(await screen.findByRole('article', { name: '服务器：香港 4 核' })).toBeVisible()
  })

  it('revokes with a forced fallback and refuses to remove a server that still has agents', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const s = server({ ssh: ssh({ host_key_confirmed: true, host_key_confirmed_at: at, client_key_installed: true, prepared_at: at }), version: 4 })
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json({ items: [s], unassigned: [] })
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(detail(s, { agents: [row('inst-a', 'alpha')] }))
      if (path(r) === '/api/v1/agents/servers/srv-1/revoke') {
        return r.clone().json().then((body: { force: boolean }) => body.force
          ? Response.json(detail(server({ ssh: ssh({ host_key_confirmed: true, prepared_at: at }), version: 5 }), { warning: '没能连上服务器（timeout），只删除了工作台保存的私钥；请在服务器的 ~/.ssh/authorized_keys 里手动删除这一行：ssh-ed25519 AAAA myworkspace' }))
          : fail(503, 'AGENT_SSH_UNREACHABLE', { reason: 'timeout' }))
      }
    })
    await openApp('/agents?tab=servers&server=srv-1')
    const keys = await screen.findByRole('region', { name: 'SSH 与专用密钥' })
    const manage = await within(keys).findByRole('group', { name: '吊销与移除' })
    expect(within(manage).getByText(/上面还有 1 个经工作台部署的实例/)).toBeVisible()
    await fireEvent.click(within(manage).getByRole('button', { name: '吊销专用密钥' }))
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-1/revoke')).toEqual({ version: 4, force: false })
    expect(await within(keys).findByText(/连接超时：检查 IP 与端口/)).toBeVisible()
    await fireEvent.click(within(keys).getByRole('button', { name: /强制吊销/ }))
    await vi.waitFor(() => expect(requests.filter(r => path(r) === '/api/v1/agents/servers/srv-1/revoke')).toHaveLength(2))
    expect(await requests.filter(r => path(r) === '/api/v1/agents/servers/srv-1/revoke')[1].clone().json()).toEqual({ version: 4, force: true })
    expect(await within(keys).findByText(/手动删除这一行：ssh-ed25519 AAAA myworkspace/)).toBeVisible()
  })

  it('removes a revoked server and returns to the list even though its detail is gone', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const s = server({ ssh: ssh({ host_key_confirmed: true, host_key_confirmed_at: at, prepared_at: at }), version: 6 })
    let removed = false
    const requests = setup(async r => {
      // 移除后列表要过一会儿才取回来：回到列表时也不能还显示它
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') {
        if (removed) await new Promise(done => setTimeout(done, 1500))
        return Response.json({ items: removed ? [] : [s], unassigned: [] })
      }
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'DELETE') { removed = true; return new Response(null, { status: 204 }) }
      // 移除后再读详情是 404：页面也要回到服务器列表
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return removed ? fail(404, 'NOT_FOUND') : Response.json(detail(s, { agents: [row('inst-a', 'alpha', { deployed: false, load: null })] }))
    })
    await openApp('/agents?tab=servers&server=srv-1')
    const keys = await screen.findByRole('region', { name: 'SSH 与专用密钥' })
    const prepare = await within(keys).findByRole('form', { name: '准备服务器' })
    await fireEvent.click(within(prepare).getByRole('button', { name: '移除服务器' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'DELETE' && path(r) === '/api/v1/agents/servers/srv-1' && new URL(r.url).searchParams.get('version') === '6')).toBe(true))
    expect(await screen.findByRole('button', { name: '添加服务器' })).toBeVisible()
    expect(screen.queryByRole('article', { name: '服务器：香港 4 核' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'SSH 与专用密钥' })).toBeNull()
  })

  it('explains probe failures and the rate limit when adding a server', async () => {
    let attempt = 0
    setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json({ items: [], unassigned: [] })
      if (path(r) === '/api/v1/agents/servers' && r.method === 'POST') return ++attempt === 1 ? fail(503, 'AGENT_SSH_UNREACHABLE', { reason: 'refused' }) : attempt === 2 ? fail(409, 'AGENT_SERVER_EXISTS') : fail(429, 'RATE_LIMITED', {}, { 'Retry-After': '42' })
    })
    await openApp('/agents?tab=servers')
    await fireEvent.click(await screen.findByRole('button', { name: '添加服务器' }))
    const form = screen.getByRole('form', { name: '添加服务器' })
    await fireEvent.update(within(form).getByLabelText('名称 *'), 'a')
    await fireEvent.update(within(form).getByLabelText('服务器 IP 或域名 *'), '203.0.113.10')
    await fireEvent.update(within(form).getByLabelText('SSH 账号 *'), 'root')
    await fireEvent.click(within(form).getByRole('button', { name: '读取主机公钥' }))
    expect(await within(form).findByText(/连接被拒绝：这个端口上没有 SSH 服务/)).toBeVisible()
    await fireEvent.click(within(form).getByRole('button', { name: '读取主机公钥' }))
    expect(await within(form).findByText(/已经添加过了/)).toBeVisible()
    await fireEvent.click(within(form).getByRole('button', { name: '读取主机公钥' }))
    expect(await within(form).findByText(/请 42 秒后重试/)).toBeVisible()
  })
})

describe('deploy to a server', () => {
  const ready = server({ ssh: ssh({ host_key_confirmed: true, host_key_confirmed_at: at, client_key_installed: true, prepared_at: at }), instances_total: 1 })
  const fresh = server({ id: 'srv-2', name: '新加坡 2 核', ssh: ssh({ host: 'sg.example.com', host_key_confirmed: true, host_key_confirmed_at: at }) })
  const pending = server({ id: 'srv-3', name: '还没核对' })

  it('deploys to a prepared server without a password and follows the job; an unprepared one asks once', async () => {
    let state = deployment(), calls = 0
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json({ items: [ready, fresh, pending], unassigned: [] })
      if (path(r) === '/api/v1/agents/instances/inst-a/deployment') { calls++; return Response.json(state) }
      if (path(r) === '/api/v1/agents/instances/inst-a/deploy') {
        const running = deployment({ server: ready, job: job('install', 'running', { steps: [{ key: 'upload', status: 'running', detail: null }, { key: 'online', status: 'pending', detail: null }] }), capacity_warning: '这台服务器剩余内存不多（可用 1.2 GB）：再部署一个实例可能会变慢。' })
        state = deployment({ server: ready, deployment: { server_id: 'srv-1', install_dir: '/home/deploy/myworkspace-openclaw/0123456789ab', installed_at: at, version: 2 }, job: job('install', 'succeeded', { steps: [{ key: 'upload', status: 'done', detail: null }, { key: 'online', status: 'done', detail: '实例已连上工作台' }] }) })
        return Response.json(running, { status: 202 })
      }
    })
    const panel = await deployPanel()
    const form = await within(panel).findByRole('form', { name: '部署' })
    const select = within(form).getByLabelText('部署到') as HTMLSelectElement
    // 只列出确认过指纹的 SSH 服务器，默认选已准备的
    expect([...select.options].map(o => o.textContent)).toEqual(['香港 4 核（已准备，1 个实例）', '新加坡 2 核（还没准备，要输一次密码）'])
    expect(select.value).toBe('srv-1')
    expect(within(form).queryByLabelText('登录密码 *')).not.toBeInTheDocument()
    // 选没准备的服务器要输密码；换回来密码清空
    await fireEvent.update(select, 'srv-2')
    const password = within(form).getByLabelText('登录密码 *') as HTMLInputElement
    expect(within(form).getByRole('button', { name: '部署到这台服务器' })).toBeDisabled()
    await fireEvent.update(password, 'password-for-sg')
    await fireEvent.update(select, 'srv-1')
    expect(within(form).queryByLabelText('登录密码 *')).not.toBeInTheDocument()
    await fireEvent.update(select, 'srv-2')
    expect((within(form).getByLabelText('登录密码 *') as HTMLInputElement).value).toBe('')
    await fireEvent.update(select, 'srv-1')
    await fireEvent.click(within(form).getByRole('button', { name: '部署到这台服务器' }))
    expect(await json(requests, 'POST', '/api/v1/agents/instances/inst-a/deploy')).toEqual({ server_id: 'srv-1', version: 3 })
    expect(await within(panel).findByText(/剩余内存不多/)).toBeVisible()
    expect(await within(panel).findByText('部署完成，实例已连上工作台。', {}, { timeout: 4000 })).toBeVisible()
    expect(calls).toBeGreaterThanOrEqual(2)
    expect(within(panel).getByText('/home/deploy/myworkspace-openclaw/0123456789ab')).toBeVisible()
    expect(within(panel).getByRole('link', { name: '香港 4 核' })).toHaveAttribute('href', '/agents?tab=servers&server=srv-1')
    const bodies = await Promise.all(requests.filter(r => r.method !== 'GET').map(r => r.clone().text()))
    expect(bodies.some(b => b.includes('password-for-sg'))).toBe(false)
  })

  it('undeploys with removal of its own directory only', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const deployed = deployment({ server: ready, deployment: { server_id: 'srv-1', install_dir: '/home/deploy/myworkspace-openclaw/0123456789ab', installed_at: at, version: 2 } })
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json({ items: [ready], unassigned: [] })
      if (path(r) === '/api/v1/agents/instances/inst-a/deployment') return Response.json(deployed)
      if (path(r) === '/api/v1/agents/instances/inst-a/undeploy') return Response.json(deployment({ ...deployed, job: job('uninstall', 'running', { steps: [{ key: 'stop', status: 'running', detail: null }] }) }), { status: 202 })
    }, instance({ server_id: 'srv-1', server_manual: true, version: 5 }))
    const panel = await deployPanel()
    const form = await within(panel).findByRole('form', { name: '部署' })
    expect(within(form).queryByLabelText('部署到')).not.toBeInTheDocument()
    expect(within(form).getByRole('button', { name: '重新部署' })).toBeEnabled()
    const group = within(panel).getByRole('group', { name: '卸载' })
    await fireEvent.click(within(group).getByLabelText(/同时删除这个实例的目录/))
    await fireEvent.click(within(group).getByRole('button', { name: '从服务器上卸载' }))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('同一台服务器上的其他实例不受影响'))
    expect(await json(requests, 'POST', '/api/v1/agents/instances/inst-a/undeploy')).toEqual({ version: 5, remove_files: true })
  })

  it('lets a manually deployed intranet instance be assigned to a server, and back to automatic', async () => {
    const auto = server({ id: 'srv-auto', name: '自动识别的服务器（beta）', kind: 'auto', ssh: null })
    let inst = instance({ network_mode: 'intranet' })
    const requests = setup(async r => {
      if (path(r) === '/api/v1/agents/instances' && r.method === 'GET') return Response.json({ items: [inst], next_cursor: null, total_estimate: 1 })
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json({ items: [ready, auto], unassigned: [] })
      if (path(r) === '/api/v1/agents/instances/inst-a/deployment') return Response.json(deployment({ server: inst.server_id ? auto : null }))
      if (path(r) === '/api/v1/agents/instances/inst-a/server') {
        const body = await r.clone().json() as { server_id: string | null }
        inst = instance({ network_mode: 'intranet', server_id: body.server_id, server_manual: body.server_id !== null, version: inst.version + 1 })
        return Response.json(deployment({ server: body.server_id ? auto : null }))
      }
    })
    const panel = await deployPanel()
    expect(await within(panel).findByText(/只支持外网模式的实例/)).toBeVisible()
    expect(within(panel).queryByRole('form', { name: '部署' })).not.toBeInTheDocument()
    const form = within(panel).getByRole('form', { name: '手动指定所在服务器' })
    const save = within(form).getByRole('button', { name: '保存所在服务器' })
    expect(save).toBeDisabled()
    await fireEvent.update(within(form).getByLabelText('手动接入的实例在哪台服务器'), 'srv-auto')
    await fireEvent.click(save)
    expect(await json(requests, 'PUT', '/api/v1/agents/instances/inst-a/server')).toEqual({ server_id: 'srv-auto', version: 3 })
    expect(await within(panel).findByText(/· 手动指定/)).toBeVisible()
    // 实例刷新后以手动指定为准
    const select = within(form).getByLabelText('手动接入的实例在哪台服务器') as HTMLSelectElement
    await vi.waitFor(() => expect(select.value).toBe('srv-auto'))
    await fireEvent.update(select, 'auto')
    await vi.waitFor(() => expect(save).toBeEnabled())
    await fireEvent.click(save)
    await vi.waitFor(() => expect(requests.filter(r => path(r) === '/api/v1/agents/instances/inst-a/server')).toHaveLength(2))
    expect(await requests.filter(r => path(r) === '/api/v1/agents/instances/inst-a/server')[1].clone().json()).toEqual({ server_id: null, version: 4 })
  })

  it('saves resource limits and validates them', async () => {
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/instances/inst-a/limits') return Response.json({ mem_limit_mb: 2048, cpus: 1.5 })
    })
    await openApp('/agents')
    const card = await screen.findByRole('article', { name: '实例：alpha' })
    const limits = within(card).getByRole('region', { name: '资源上限' })
    expect(within(limits).getByText('资源上限：不限制')).toBeVisible()
    const save = within(limits).getByRole('button', { name: '保存上限' })
    expect(save).toBeDisabled()
    await fireEvent.update(within(limits).getByLabelText('内存上限（MiB，256 起）'), '100')
    expect(within(limits).getByText(/内存上限是 256 到 1048576 之间的整数/)).toBeVisible()
    expect(save).toBeDisabled()
    await fireEvent.update(within(limits).getByLabelText('内存上限（MiB，256 起）'), '2048')
    await fireEvent.update(within(limits).getByLabelText('CPU 上限（核，可以是小数）'), '1.5')
    await fireEvent.click(save)
    expect(await json(requests, 'PUT', '/api/v1/agents/instances/inst-a/limits')).toEqual({ mem_limit_mb: 2048, cpus: 1.5, version: 3 })
    expect(await within(limits).findByText(/重新部署这个实例后生效/)).toBeVisible()
  })
})
