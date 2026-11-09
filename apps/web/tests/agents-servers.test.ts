import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import { GB, at, current, deploymentState as deployment, detail, fail, instance, job, load, overviewOf, path, readyServer, row, server, serverList, ssh } from './agents-fixtures'
import type { Schema } from '../src/services/api'

// T-0147 / T-0181：服务器详情（整机与每个智能体的份额、表格、趋势、运维、移除、更换地址）与实例详情里的“部署到服务器”。
// 添加服务器、连接测试、初始化与旧服务器补密码见 agent-server-redesign.test.ts。
const list = (items: Schema['AgentServerOut'][]) => serverList(items)

function setup(handler: (r: Request) => Response | Promise<Response | undefined> | undefined, inst: Schema['AgentInstanceOut'] = instance()) {
  return mockApi(async r => await handler(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/instances' ? Response.json({ items: [inst], next_cursor: null, total_estimate: 1 }) :
    path(r) === '/api/v1/agents/overview' ? Response.json(overviewOf([inst])) :
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
  await openApp('/agents?instance=inst-a')
  const card = await screen.findByRole('article', { name: '实例：alpha' })
  return within(card).getByRole('region', { name: '部署到服务器' })
}
afterEach(() => vi.restoreAllMocks())

describe('server detail', () => {
  it('shows the whole server, each agent’s share, a sortable agents table and one agent’s trend', async () => {
    const s = readyServer({ name: '香港 4 核', reported_at: at, stale: false, cores: 4, cpu_percent: 60, mem_total: 16 * GB, mem_used_percent: 75, disk_used_percent: 50, instances_total: 2, instances_online: 2 })
    const agents = [
      row('inst-a', 'alpha', { load: load({ cpu_percent: 10, mem_bytes: 2 * GB }), tokens_1h: 5000, running_task: { id: 't-1', title: '写周报' } }),
      row('inst-b', 'beta', { load: load({ cpu_percent: 30, mem_bytes: 6 * GB, mem_limit: 6.2 * GB }), alerts: ['mem_near_limit'], tokens_1h: 100 }),
    ]
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json(list([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1') return Response.json(detail(s, { current, agents }))
      if (path(r) === '/api/v1/agents/instances/inst-b/host') return Response.json({ instance_id: 'inst-b', reported: true, server: { id: 'srv-1', name: s.name }, current, range: '24h', points: [], alerts: [], agent: agents[1].load, agent_points: [{ at, cpu_avg: 30, cpu_max: 50, cores_avg: 1.2, mem_avg: 6 * GB, mem_max: 6.1 * GB, rx_avg: 1, rx_max: 2, tx_avg: 1, tx_max: 2 }], agent_alerts: ['mem_near_limit'] })
    })
    const { router } = await openApp('/agent-servers')
    const servers = await within(await screen.findByRole('region', { name: '服务器列表' })).findByRole('table')
    const line = within(servers).getByRole('row', { name: /香港 4 核/ })
    expect(line).toHaveTextContent('2 个2 个消息在线')
    expect(line).toHaveTextContent('4 核 / 16 GB')
    await fireEvent.click(within(line).getByRole('button', { name: '查看：香港 4 核' }))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agent-servers?server=srv-1'))

    const whole = await screen.findByRole('region', { name: '整机负载' })
    // 堆叠条：每个智能体占整机多少；颜色按名字的固定顺序，系统与其他程序单列
    const cpu = within(whole).getByRole('img', { name: /CPU：每个智能体占多少/ })
    expect(cpu).toHaveAccessibleName('CPU：每个智能体占多少：整机 60%。alpha 10%，beta 30%，系统与其他程序 20%')
    const mem = within(whole).getByText('内存：每个智能体占多少').closest('figure')!
    expect(within(mem).getByText('alpha').closest('li')).toHaveTextContent('alpha13%（2.0 GB）')
    expect(within(mem).getByText('系统与其他程序').closest('li')).toHaveTextContent('25%（4.0 GB）')
    // 趋势范围记在地址栏（?range=），刷新、分享后还在
    await fireEvent.click(within(whole).getByRole('button', { name: '最近 7 天' }))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agent-servers?server=srv-1&range=7d'))
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/agents/servers/srv-1' && new URL(r.url).searchParams.get('range') === '7d')).toBe(true))

    const table = within(screen.getByRole('region', { name: '各智能体负载' })).getByRole('table')
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
    // 关闭详情，回到列表
    await fireEvent.click(screen.getByRole('button', { name: '关闭服务器详情' }))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agent-servers'))
    expect(screen.queryByRole('region', { name: '整机负载' })).toBeNull()
  })

  it('removes a server and returns to the list even though its detail is gone', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const s = readyServer({ name: '香港 4 核', version: 6 })
    let removed = false
    const requests = setup(async r => {
      // 移除后列表要过一会儿才取回来：回到列表时也不能还显示它
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') {
        if (removed) await new Promise(done => setTimeout(done, 1500))
        return Response.json(list(removed ? [] : [s]))
      }
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'DELETE') { removed = true; return new Response(null, { status: 204 }) }
      // 移除后再读详情是 404：页面也要回到服务器列表
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return removed ? fail(404, 'NOT_FOUND') : Response.json(detail(s, { agents: [row('inst-a', 'alpha', { deployed: false, deployment_state: 'associated', load: null })] }))
    })
    const { router } = await openApp('/agent-servers?server=srv-1')
    const panel = await screen.findByRole('region', { name: '连接与初始化' })
    await fireEvent.click(within(panel).getByRole('button', { name: '移除服务器' }))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('不会停止或删除服务器上的任何容器'))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'DELETE' && path(r) === '/api/v1/agents/servers/srv-1' && new URL(r.url).searchParams.get('version') === '6')).toBe(true))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agent-servers'))
    expect(screen.queryByRole('region', { name: '连接与初始化' })).toBeNull()
    expect(screen.queryByRole('row', { name: /香港 4 核/ })).toBeNull()
  })

  it('does not offer to move or remove a server that still has platform deployments', async () => {
    const s = readyServer({ managed_deployments: 1, version: 4 })
    setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json(list([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(detail(s, { agents: [row('inst-a', 'alpha')] }))
    })
    await openApp('/agent-servers?server=srv-1')
    const panel = await screen.findByRole('region', { name: '连接与初始化' })
    expect(within(panel).getByRole('button', { name: '移除服务器' })).toBeDisabled()
    expect(within(panel).getByRole('button', { name: '更换地址' })).toBeDisabled()
    expect(within(panel).getByText(/有 1 条平台部署记录/)).toBeVisible()
  })

  it('changes the address with the password and asks to check the new fingerprint', async () => {
    const s = server({ ssh: ssh({ host_key_confirmed: true, host_key_confirmed_at: at }), deploy_block_reason: 'not_initialized', version: 3 })
    const moved = server({ ssh: ssh({ host: '198.51.100.20', address: '198.51.100.20', port: 2200, username: 'ops' }), version: 4 })
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json(list([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(detail(s))
      if (path(r) === '/api/v1/agents/servers/srv-1/address') return Response.json(detail(moved))
    })
    await openApp('/agent-servers?server=srv-1')
    const panel = await screen.findByRole('region', { name: '连接与初始化' })
    await fireEvent.click(within(panel).getByRole('button', { name: '更换地址' }))
    const form = await screen.findByRole('form', { name: '更换地址' })
    expect(within(form).getByLabelText('IP 地址 *')).toHaveValue('203.0.113.10')
    await fireEvent.update(within(form).getByLabelText('IP 地址 *'), '198.51.100.20')
    await fireEvent.update(within(form).getByLabelText('SSH 端口 *'), '2200')
    await fireEvent.update(within(form).getByLabelText('登录账号 *'), 'ops')
    await fireEvent.update(within(form).getByLabelText('登录密码 *'), 'new-pw')
    await fireEvent.click(within(form).getByRole('button', { name: '保存并测试连接' }))
    expect(await json(requests, 'PUT', '/api/v1/agents/servers/srv-1/address')).toEqual({ host: '198.51.100.20', port: 2200, username: 'ops', login_password: 'new-pw', version: 3 })
    // 新地址的指纹要重新核对，之前不会测试
    expect(await screen.findByRole('group', { name: '核对主机指纹' })).toBeVisible()
    expect(requests.some(r => path(r).endsWith('/test-connection'))).toBe(false)
  })
})

describe('deploy to a server', () => {
  const ready = readyServer({ name: '香港 4 核', instances_total: 1 })
  const fresh = server({ id: 'srv-2', name: '新加坡 2 核', ssh: ssh({ host: 'sg.example.com', host_key_confirmed: true, host_key_confirmed_at: at }), deploy_block_reason: 'not_initialized' })
  const pending = server({ id: 'srv-3', name: '还没核对' })

  it('deploys to an initialized server with its saved password (never asks for one) and follows the job', async () => {
    let state = deployment(), calls = 0
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json(list([ready, fresh, pending]))
      if (path(r) === '/api/v1/agents/instances/inst-a/deployment') { calls++; return Response.json(state) }
      if (path(r) === '/api/v1/agents/instances/inst-a/deploy') {
        const running = deployment({ server: ready, job: job('install', 'running', { steps: [{ key: 'upload', status: 'running', detail: null }, { key: 'online', status: 'pending', detail: null }] }), capacity_warning: '这台服务器剩余内存不多（可用 1.2 GB）：再部署一个实例可能会变慢。' })
        state = deployment({ server: ready, deployment: { server_id: 'srv-1', install_dir: '/home/deploy/myworkspace-openclaw/0123456789ab', installed_at: at, runtime_installed: true, version: 2 }, job: job('install', 'succeeded', { steps: [{ key: 'upload', status: 'done', detail: null }, { key: 'online', status: 'done', detail: '实例已连上工作台' }] }) })
        return Response.json(running, { status: 202 })
      }
    })
    const panel = await deployPanel()
    const form = await within(panel).findByRole('form', { name: '部署' })
    const select = within(form).getByLabelText('部署到') as HTMLSelectElement
    // 只列出服务端判断可以部署的服务器（已初始化、确认过指纹、有密码）；不在这里要密码或初始化
    expect([...select.options].map(o => o.textContent)).toEqual(['香港 4 核（已就绪，1 个实例）'])
    expect(select.value).toBe('srv-1')
    expect(within(panel).queryByLabelText(/密码/)).not.toBeInTheDocument()
    await fireEvent.click(within(form).getByRole('button', { name: '部署到这台服务器' }))
    expect(await json(requests, 'POST', '/api/v1/agents/instances/inst-a/deploy')).toEqual({ server_id: 'srv-1', version: 3 })
    expect(await within(panel).findByText(/剩余内存不多/)).toBeVisible()
    expect(await within(panel).findByText('部署完成，实例已连上工作台。', {}, { timeout: 4000 })).toBeVisible()
    expect(calls).toBeGreaterThanOrEqual(2)
    expect(within(panel).getByText('/home/deploy/myworkspace-openclaw/0123456789ab')).toBeVisible()
    expect(within(panel).getByRole('link', { name: '香港 4 核' })).toHaveAttribute('href', '/agent-servers?server=srv-1')
  })

  it('points to the servers page when no server can be deployed to', async () => {
    setup(r => path(r) === '/api/v1/agents/servers' && r.method === 'GET' ? Response.json(list([fresh, pending])) : undefined)
    const panel = await deployPanel()
    expect(await within(panel).findByText(/还没有可以部署的服务器/)).toBeVisible()
    expect(within(panel).getByRole('link', { name: '智能体服务器' })).toHaveAttribute('href', '/agent-servers')
    expect(within(panel).queryByLabelText(/密码/)).not.toBeInTheDocument()
  })

  it('continues a failed deployment on the same server and undeploys with removal of its own directory only', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const failed = deployment({ server: ready, deployment: { server_id: 'srv-1', install_dir: '/home/deploy/myworkspace-openclaw/0123456789ab', installed_at: null, runtime_installed: null, version: 2 }, job: job('install', 'failed', { error_code: 'AGENT_SSH_ONLINE_TIMEOUT', error_message: '等了 10 分钟实例没有连上工作台', steps: [{ key: 'start', status: 'done', detail: null }, { key: 'online', status: 'failed', detail: null }] }) })
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json(list([ready]))
      if (path(r) === '/api/v1/agents/instances/inst-a/deployment') return Response.json(failed)
      if (path(r) === '/api/v1/agents/instances/inst-a/deploy') return Response.json(deployment({ ...failed, job: job('install', 'running') }), { status: 202 })
      if (path(r) === '/api/v1/agents/instances/inst-a/undeploy') return Response.json(deployment({ ...failed, job: job('uninstall', 'running', { steps: [{ key: 'stop', status: 'running', detail: null }] }) }), { status: 202 })
    }, instance({ server_id: 'srv-1', server_manual: true, version: 5 }))
    const panel = await deployPanel()
    const form = await within(panel).findByRole('form', { name: '部署' })
    expect(within(form).queryByLabelText('部署到')).not.toBeInTheDocument()
    expect(within(panel).getByText(/等了 10 分钟实例没有连上工作台/)).toBeVisible()
    await fireEvent.click(within(form).getByRole('button', { name: '继续部署' }))
    expect(await json(requests, 'POST', '/api/v1/agents/instances/inst-a/deploy')).toEqual({ server_id: 'srv-1', version: 5 })
    const group = await within(panel).findByRole('group', { name: '卸载' })
    await fireEvent.click(within(group).getByLabelText(/同时删除这个实例的目录/))
    await fireEvent.click(within(group).getByRole('button', { name: '从服务器上卸载' }))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('同一台服务器上的其他实例不受影响'))
    expect(await json(requests, 'POST', '/api/v1/agents/instances/inst-a/undeploy')).toEqual({ version: 5, remove_files: true })
  })

  it('lets a manually deployed intranet instance be assigned to a server, and back to automatic', async () => {
    const auto = server({ id: 'srv-auto', name: '自动识别的服务器（beta）', kind: 'auto', ssh: null, initialization_status: null, deploy_block_reason: 'not_ssh' })
    let inst = instance({ network_mode: 'intranet' })
    const requests = setup(async r => {
      if (path(r) === '/api/v1/agents/instances' && r.method === 'GET') return Response.json({ items: [inst], next_cursor: null, total_estimate: 1 })
      if (path(r) === '/api/v1/agents/overview') return Response.json(overviewOf([inst]))
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json(list([ready, auto]))
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
    await openApp('/agents?instance=inst-a')
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
