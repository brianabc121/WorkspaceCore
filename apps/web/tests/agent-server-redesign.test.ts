import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import { PREPARE_STEPS, at, current, detail, fail, job, path, readyServer, readySsh, row, server, serverList, ssh, summary } from './agents-fixtures'
import type { Schema } from '../src/services/api'

// T-0181（改造方案 Task 4 Step 1–2）：独立的“智能体服务器”页——两个侧栏入口、旧链接跳转、服务端统计、服务器表、
// 只要四项的连接表单（指纹确认后才测试、每步用最新 version、密码只在内存）、初始化面板与“已部署 OpenClaw”。
type Role = Schema['WorkspaceOut']['role']
const SECRET = '  hunter2 secret  '

function setup(handler?: (r: Request) => Response | Promise<Response | undefined> | undefined, role: Role = 'owner') {
  return mockApi(async r => await handler?.(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) :
    path(r).endsWith('/ops') && r.method === 'GET' ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) : undefined))
}
const writes = (requests: Request[]) => requests.filter(r => r.method !== 'GET')
const json = async (requests: Request[], method: string, p: string) => {
  await vi.waitFor(() => expect(requests.some(r => r.method === method && path(r) === p)).toBe(true))
  return await requests.filter(r => r.method === method && path(r) === p).at(-1)!.clone().json() as unknown
}
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); localStorage.clear() })

describe('agent servers page: navigation and old links', () => {
  it('has its own sidebar entry next to OpenClaw 智能体, and the runner page stays separate', async () => {
    setup(r => path(r) === '/api/v1/agents/servers' ? Response.json(serverList([])) : undefined)
    await openApp('/agent-servers')
    expect(await screen.findByRole('heading', { level: 1, name: '智能体服务器' })).toBeVisible()
    const nav = screen.getByRole('navigation', { name: '主导航' })
    const links = within(nav).getAllByRole('link').map(a => a.textContent?.trim())
    expect(within(nav).getByRole('link', { name: '智能体服务器' })).toHaveAttribute('href', '/agent-servers')
    expect(within(nav).getByRole('link', { name: 'OpenClaw 智能体' })).toHaveAttribute('href', '/agents')
    expect(within(nav).getByRole('link', { name: '容器服务器' })).toHaveAttribute('href', '/runner')
    expect(links.indexOf('智能体服务器')).toBe(links.indexOf('OpenClaw 智能体') - 1)
    expect(links).not.toContain('OpenClaw 实例')
  })

  it('is only for the owner', async () => {
    setup(undefined, 'admin')
    const { router } = await openApp('/agent-servers')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
    expect(screen.queryByRole('link', { name: '智能体服务器' })).not.toBeInTheDocument()
  })

  it('replaces the old servers tab with the new page, keeping only a valid server and range', async () => {
    const s = readyServer()
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1') return Response.json(detail(s, { range: new URL(r.url).searchParams.get('range') as '7d' }))
    })
    const { router } = await openApp('/agents?tab=servers&server=srv-1&range=7d')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agent-servers?server=srv-1&range=7d'))
    expect(await screen.findByRole('region', { name: '服务器：香港节点 A' })).toBeVisible()
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/agents/servers/srv-1' && new URL(r.url).searchParams.get('range') === '7d')).toBe(true))
    await router.push('/agents?tab=servers&range=bogus')
    expect(router.currentRoute.value.fullPath).toBe('/agent-servers')
    await router.push('/agents?tab=instances&instance=inst-a')
    expect(router.currentRoute.value.fullPath).toBe('/agents?instance=inst-a')
    expect(writes(requests)).toHaveLength(0)
  })
})

describe('agent servers page: list', () => {
  const ipv6 = '2001:db8:85a3:0000:0000:8a2e:0370:7334'
  const items = [
    readyServer({ cores: 8, mem_total: 16 * GB(), instances_total: 3, instances_online: 2 }),
    server({ id: 'srv-2', name: '新加坡节点 B 名字很长很长很长很长很长很长很长很长很长很长很长很长', ssh: ssh({ host: ipv6, address: ipv6, port: 2222, host_key_confirmed: true, password_set: true }), deploy_block_reason: 'not_initialized' }),
    server({ id: 'srv-3', name: '旧服务器', ssh: ssh({ password_set: false, credential_revision: 0, connection_status: 'credentials_required', host_key_confirmed: true, prepared_at: at, legacy_key_cleanup_state: 'pending' }), initialization_status: 'ready', deploy_block_reason: 'credentials_required', instances_total: 1 }),
    server({ id: 'srv-auto', name: '自动识别的服务器（beta）', kind: 'auto', ssh: null, initialization_status: null, deploy_block_reason: 'not_ssh', reported_at: at, stale: false, instances_total: 1, instances_online: 1 }),
  ]
  function GB() { return 1024 ** 3 }

  it('shows the counts from the server summary, not from the visible rows', async () => {
    const requests = setup(r => path(r) === '/api/v1/agents/servers' ? Response.json(serverList(items, { summary: summary({ server_count: 12, ready_count: 7, successful_deployments: null, not_initialized_count: 3, needs_credentials_count: 1, deployment_counts_partial: true }) })) : undefined)
    await openApp('/agent-servers')
    const tiles = await screen.findByRole('region', { name: '服务器统计' })
    await vi.waitFor(() => expect(within(tiles).getByText('服务器').closest('div')).toHaveTextContent('服务器12'))
    expect(within(tiles).getByText('已就绪').closest('div')).toHaveTextContent('已就绪7')
    // 成功部署数未知（有旧记录）：写 —，提示待核验，不当成 0
    expect(within(tiles).getByText('已部署智能体').closest('div')).toHaveTextContent('已部署智能体—待核验')
    expect(within(tiles).getByText('待初始化').closest('div')).toHaveTextContent('待初始化3')
    expect(within(tiles).getByText(/1 台服务器待补充登录密码/)).toBeVisible()

    const table = within(screen.getByRole('region', { name: '服务器列表' })).getByRole('table')
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent?.trim())).toEqual(['服务器', '连接地址', '系统 / 资源', '初始化状态', '连接状态', '智能体', '操作'])
    const first = within(table).getByRole('row', { name: /香港节点 A/ })
    expect(first).toHaveTextContent('203.0.113.10:22')
    expect(first).toHaveTextContent('8 核 / 16 GB')
    expect(first).toHaveTextContent('已就绪')
    expect(first).toHaveTextContent('连接正常')
    expect(first).toHaveTextContent('3 个')
    expect(within(first).getByRole('button', { name: '查看：香港节点 A' })).toBeVisible()
    // 长 IPv6 用 [地址]:端口；不知道的资源写 —
    const second = within(table).getByRole('row', { name: /新加坡节点 B/ })
    expect(second).toHaveTextContent(`[${ipv6}]:2222`)
    expect(within(second).getByRole('cell', { name: /^—$/ })).toBeVisible()
    expect(within(second).getByRole('button', { name: /^初始化：/ })).toBeVisible()
    expect(within(table).getByRole('row', { name: /旧服务器/ })).toHaveTextContent('待补充密码')
    const auto = within(table).getByRole('row', { name: /自动识别的服务器/ })
    expect(auto).toHaveTextContent('不适用')
    expect(auto).toHaveTextContent('上报正常')

    // 搜索与状态筛选只在列表里筛，统计不变
    await fireEvent.update(screen.getByPlaceholderText('服务器名称或 IP'), '2001:DB8')
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    await fireEvent.update(screen.getByPlaceholderText('服务器名称或 IP'), '')
    await fireEvent.update(screen.getByLabelText('按状态筛选服务器'), 'attention')
    expect(within(table).getAllByRole('row').slice(1).map(r => within(r).getByRole('rowheader').textContent)).toEqual(['旧服务器'])
    await fireEvent.update(screen.getByLabelText('按状态筛选服务器'), 'auto')
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    expect(within(tiles).getByText('服务器').closest('div')).toHaveTextContent('服务器12')
    expect(writes(requests)).toHaveLength(0)
  })

  it('opens a server, refreshes and switches between servers without any write request', async () => {
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList(items))
      const id = /^\/api\/v1\/agents\/servers\/([^/]+)$/.exec(path(r))?.[1]
      const s = items.find(i => i.id === id)
      if (s) return Response.json(detail(s))
    })
    const { router } = await openApp('/agent-servers')
    const table = await screen.findByRole('table')
    await fireEvent.click(within(table).getByRole('button', { name: /^初始化：新加坡/ }))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agent-servers?server=srv-2'))
    expect(await screen.findByRole('region', { name: /^服务器：新加坡节点 B/ })).toBeVisible()
    await fireEvent.click(within(table).getByRole('row', { name: /旧服务器/ }))
    expect(await screen.findByRole('region', { name: '服务器：旧服务器' })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await fireEvent.click(within(table).getByRole('row', { name: /自动识别的服务器/ }))
    const auto = await screen.findByRole('region', { name: '服务器：自动识别的服务器（beta）' })
    // 自动识别的服务器：不提供初始化与部署入口
    expect(within(auto).queryByRole('link', { name: /前往部署智能体/ })).not.toBeInTheDocument()
    expect(within(auto).getByText('自动识别的服务器不能自动部署。')).toBeVisible()
    const panel = screen.getByRole('region', { name: '连接与初始化' })
    expect(within(panel).queryByRole('button', { name: /初始化/ })).not.toBeInTheDocument()
    expect(within(panel).getByText(/工作台没有它的登录信息/)).toBeVisible()
    expect(writes(requests)).toHaveLength(0)
  })
})

describe('agent servers page: add a server with four fields', () => {
  it('saves, asks to check the fingerprint, then tests with the newest version; the password stays in memory only', async () => {
    let list: Schema['AgentServerOut'][] = []
    const created = server({ name: '203.0.113.40:22', ssh: ssh({ host: '203.0.113.40', address: '203.0.113.40', username: 'admin' }), version: 1 })
    const confirmed = server({ ...created, ssh: ssh({ ...created.ssh!, host_key_confirmed: true, host_key_confirmed_at: at }), deploy_block_reason: 'not_initialized', version: 2 })
    const tested = server({ ...confirmed, ssh: ssh({ ...confirmed.ssh!, connection_status: 'connected', last_connection_at: at }) })
    const requests = setup(async r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json(serverList(list))
      if (path(r) === '/api/v1/agents/servers' && r.method === 'POST') { list = [created]; return Response.json(detail(created), { status: 201 }) }
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(detail(list[0] ?? created))
      if (path(r) === '/api/v1/agents/servers/srv-1/confirm') { list = [confirmed]; return Response.json(detail(confirmed)) }
      if (path(r) === '/api/v1/agents/servers/srv-1/test-connection') { list = [tested]; return Response.json(detail(tested, { warning: '账号可以 sudo；服务器上还没有 Docker，初始化时会安装。' })) }
    })
    const { router, queryClient } = await openApp('/agent-servers')
    await fireEvent.click(await screen.findByRole('button', { name: '添加服务器' }))
    const form = await screen.findByRole('form', { name: '添加服务器' })
    // 只有四项必填：名称可选
    const save = within(form).getByRole('button', { name: '测试并保存' })
    expect(within(form).getByLabelText('服务器名称（可选）')).toHaveValue('')
    expect(within(form).getByLabelText('SSH 端口 *')).toHaveValue(22)
    const password = within(form).getByLabelText('登录密码 *') as HTMLInputElement
    expect(password).toHaveAttribute('type', 'password')
    expect(within(form).queryByLabelText(/密钥|私钥|证书/)).not.toBeInTheDocument()
    // “初始化将完成”是待执行，不是已完成
    const checklist = within(form).getByRole('region', { name: '初始化将完成' })
    expect(within(checklist).getAllByText('待执行')).toHaveLength(4)
    await fireEvent.update(within(form).getByLabelText('IP 地址 *'), ' 203.0.113.40 ')
    await fireEvent.update(within(form).getByLabelText('登录账号 *'), 'admin')
    expect(save).toBeDisabled()
    await fireEvent.update(password, SECRET)
    await fireEvent.click(within(form).getByRole('button', { name: '显示密码' }))
    expect(password).toHaveAttribute('type', 'text')
    await fireEvent.click(save)
    const post = requests.find(r => r.method === 'POST' && path(r) === '/api/v1/agents/servers')!
    expect(post.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    // 名称没填就不发（服务端用 host:port）；密码首尾空格原样发送
    expect(await json(requests, 'POST', '/api/v1/agents/servers')).toEqual({ host: '203.0.113.40', port: 22, username: 'admin', login_password: SECRET })

    // 保存后进入详情（地址栏只有 server），抽屉里核对指纹；没确认之前不能测试
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agent-servers?server=srv-1'))
    const verify = await screen.findByRole('group', { name: '核对主机指纹' })
    expect(within(verify).getByText('SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG')).toBeVisible()
    expect(within(verify).getByText('ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub')).toBeVisible()
    const go = within(verify).getByRole('button', { name: '确认指纹并测试连接' })
    expect(go).toBeDisabled()
    expect(requests.some(r => path(r).endsWith('/test-connection'))).toBe(false)
    await fireEvent.click(within(verify).getByLabelText(/我已在服务器上核对/))
    await fireEvent.click(go)
    // 每一步都用上一步返回的最新 version
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-1/confirm')).toEqual({ version: 1, fingerprint: 'SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG' })
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-1/test-connection')).toEqual({ version: 2 })
    const result = await screen.findByRole('region', { name: '连接测试结果' })
    expect(within(result).getByText('连接正常')).toBeVisible()
    expect(within(result).getByText(/还没有 Docker，初始化时会安装/)).toBeVisible()
    // 测试连接不会初始化
    expect(requests.some(r => path(r).endsWith('/prepare'))).toBe(false)
    expect(writes(requests).map(r => path(r))).toEqual(['/api/v1/agents/servers', '/api/v1/agents/servers/srv-1/confirm', '/api/v1/agents/servers/srv-1/test-connection'])

    // 密码只随添加请求发出一次：不在页面、地址栏、浏览器存储与查询缓存里
    const bodies = await Promise.all(writes(requests).map(r => r.clone().text()))
    expect(bodies.filter(b => b.includes('hunter2'))).toHaveLength(1)
    expect(document.body.innerHTML).not.toContain('hunter2')
    expect(router.currentRoute.value.fullPath).not.toContain('hunter2')
    expect(JSON.stringify({ ...localStorage })).not.toContain('hunter2')
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('hunter2')
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map(q => q.state.data))).not.toContain('hunter2')
    await fireEvent.click(within(result).getByRole('button', { name: '完成' }))
    expect(screen.queryByRole('region', { name: '连接测试结果' })).not.toBeInTheDocument()
  })

  it('clears the password as soon as it is sent, and explains a failed save without echoing it', async () => {
    let attempt = 0
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers' && r.method === 'GET') return Response.json(serverList([]))
      if (path(r) === '/api/v1/agents/servers' && r.method === 'POST') return ++attempt === 1 ? fail(503, 'AGENT_SSH_UNREACHABLE', { reason: 'refused' }) : attempt === 2 ? fail(422, 'VALIDATION_FAILED', { errors: [{ loc: ['body', 'login_password'], msg: 'x', input: 'pw-1' }] }) : fail(429, 'RATE_LIMITED', {}, { 'Retry-After': '42' })
    })
    await openApp('/agent-servers')
    await fireEvent.click(await screen.findByRole('button', { name: '添加服务器' }))
    const form = await screen.findByRole('form', { name: '添加服务器' })
    await fireEvent.update(within(form).getByLabelText('IP 地址 *'), '203.0.113.10')
    await fireEvent.update(within(form).getByLabelText('登录账号 *'), 'root')
    const password = within(form).getByLabelText('登录密码 *') as HTMLInputElement
    for (const [i, expected] of [[1, /连接被拒绝：这个端口上没有 SSH 服务/], [2, /输入不符合要求/], [3, /请 42 秒后重试/]] as const) {
      await fireEvent.update(password, `pw-${i}`)
      await fireEvent.click(within(form).getByRole('button', { name: '测试并保存' }))
      expect(password.value).toBe('')
      expect(await within(form).findByText(expected)).toBeVisible()
    }
    expect(document.body.innerHTML).not.toMatch(/pw-\d/)
    expect(writes(requests)).toHaveLength(3)
  })
})

describe('agent servers page: connection and initialization', () => {
  it('cannot test before the fingerprint is confirmed; initialization shows the real failing step', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let s = server({ version: 4 })
    let state = detail(s)
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(state)
      if (path(r) === '/api/v1/agents/servers/srv-1/confirm') { s = server({ ssh: ssh({ host_key_confirmed: true, host_key_confirmed_at: at }), deploy_block_reason: 'not_initialized', version: 5 }); state = detail(s); return Response.json(state) }
      if (path(r) === '/api/v1/agents/servers/srv-1/prepare') {
        s = server({ ...s, initialization_status: 'failed', deploy_block_reason: 'initialization_failed', version: 6 })
        state = detail(s, { job: job('prepare', 'failed', { error_code: 'AGENT_SSH_SUDO_FAILED', error_message: '这个账号不能用 sudo：请换 root 或能 sudo 的账号', steps: [{ key: 'connect', status: 'done', detail: null }, { key: 'inspect', status: 'failed', detail: 'sudo 被拒绝' }, { key: 'docker', status: 'pending', detail: null }] }) })
        return Response.json(detail(s, { job: job('prepare', 'running') }), { status: 202 })
      }
    })
    await openApp('/agent-servers?server=srv-1')
    const panel = await screen.findByRole('region', { name: '连接与初始化' })
    expect(within(panel).getByRole('button', { name: '测试连接' })).toBeDisabled()
    expect(within(panel).getByRole('button', { name: '初始化服务器' })).toBeDisabled()
    expect(within(panel).getByRole('region', { name: '初始化将完成' })).toHaveTextContent('待执行')
    const check = within(panel).getByRole('group', { name: '核对主机公钥' })
    expect(within(check).getByRole('button', { name: '确认指纹' })).toBeDisabled()
    await fireEvent.click(within(check).getByLabelText(/我已在服务器上核对/))
    await fireEvent.click(within(check).getByRole('button', { name: '确认指纹' }))
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-1/confirm')).toEqual({ version: 4, fingerprint: 'SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG' })
    await vi.waitFor(() => expect(within(panel).getByRole('button', { name: '测试连接' })).toBeEnabled())
    // 确认指纹不会自动测试或初始化
    expect(writes(requests)).toHaveLength(1)
    await fireEvent.click(within(panel).getByRole('button', { name: '初始化服务器' }))
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-1/prepare')).toEqual({ version: 5 })
    const progress = await within(panel).findByRole('region', { name: '初始化服务器进度' }, { timeout: 4000 })
    expect(await within(progress).findByText(/这个账号不能用 sudo/, {}, { timeout: 4000 })).toBeVisible()
    const steps = within(progress).getAllByRole('listitem')
    expect(steps.map(li => li.getAttribute('data-status'))).toEqual(['done', 'failed', 'pending'])
    // 步骤名与说明分两行；失败原因挂在失败的那一步下面；结束后没轮到的步骤是“未执行”（T-0269）
    expect(within(steps[0]!).getByText('用保存的密码登录服务器')).toBeVisible()
    expect(within(steps[1]!).getByText('检测系统与权限')).toBeVisible()
    expect(within(steps[1]!).getByText('sudo 被拒绝')).toBeVisible()
    const reason = within(steps[1]!).getByRole('alert')
    expect(reason).toHaveTextContent('失败原因这个账号不能用 sudo：请换 root 或能 sudo 的账号AGENT_SSH_SUDO_FAILED')
    expect(within(steps[1]!).getByText('失败')).toBeVisible()
    expect(within(steps[2]!).getByText('未执行')).toBeVisible()
    expect(progress).toHaveTextContent('完成 1 / 3 步，在第 2 步失败')
    expect(progress).toHaveTextContent('后面 1 步未执行')
    // 命令输出默认折叠，“查看命令输出”展开它
    const log = progress.querySelector('details')!
    expect(log.open).toBe(false)
    expect(within(progress).getByText('2 行 · 密码、令牌、私钥已替换成 ***')).toBeInTheDocument()
    await fireEvent.click(within(reason).getByRole('button', { name: '查看命令输出' }))
    await vi.waitFor(() => expect(log.open).toBe(true))
    expect(within(log).getByText('$ sudo -S true')).toHaveClass('is-cmd')
    // 失败后主按钮变成“重新初始化”
    expect(within(panel).getByRole('button', { name: '重新初始化' })).toBeEnabled()
    expect(within(panel).getAllByText('初始化失败').length).toBeGreaterThan(0)
    expect(within(panel).queryByText(/服务器环境初始化完成/)).not.toBeInTheDocument()
  })

  it('asks an old server for its password, keeps its initialization, tests right away and retires the old key on request', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const old = server({ id: 'srv-3', name: '旧服务器', ssh: readySsh({ password_set: false, credential_revision: 0, connection_status: 'credentials_required', legacy_key_cleanup_state: 'pending', client_key_installed: true }), initialization_status: 'ready', deploy_block_reason: 'credentials_required', managed_deployments: 1, successful_deployments: null, deployment_counts_partial: true, version: 7 })
    const saved = server({ ...old, ssh: readySsh({ password_set: true, credential_revision: 1, connection_status: 'unknown', legacy_key_cleanup_state: 'pending' }), deploy_block_reason: null, deployable: true, version: 8 })
    const tested = server({ ...saved, ssh: readySsh({ password_set: true, credential_revision: 1, legacy_key_cleanup_state: 'pending' }) })
    const cleaned = server({ ...tested, ssh: readySsh({ password_set: true, credential_revision: 1, legacy_key_cleanup_state: 'manual_required' }), version: 9 })
    let s = old
    const agents = [row('inst-a', 'alpha', { deployment_state: 'deployed', mem_limit_mb: 4096, cpus: 2, latest_job: job('install', 'succeeded') })]
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-3' && r.method === 'GET') return Response.json(detail(s, { agents }))
      if (path(r) === '/api/v1/agents/servers/srv-3/credentials') { s = saved; return Response.json(detail(s, { agents })) }
      if (path(r) === '/api/v1/agents/servers/srv-3/test-connection') { s = tested; return Response.json(detail(s, { agents })) }
      if (path(r) === '/api/v1/agents/servers/srv-3/legacy-key-cleanup') { s = cleaned; return Response.json(detail(s, { agents })) }
    })
    await openApp('/agent-servers?server=srv-3')
    const panel = await screen.findByRole('region', { name: '连接与初始化' })
    const ask = within(panel).getByRole('group', { name: '补充服务器登录密码' })
    expect(ask).toHaveTextContent('上面已有的智能体照常运行')
    expect(within(panel).getByRole('button', { name: '测试连接' })).toBeDisabled()
    expect(within(panel).getByRole('button', { name: '清理旧公钥' })).toBeDisabled()
    // 旧公钥还没退役：不能移除服务器（后端也会 409 AGENT_SERVER_LEGACY_KEY_PENDING），并说明原因
    expect(within(panel).getByRole('button', { name: '移除服务器' })).toBeDisabled()
    expect(within(panel).getByText(/先完成上面的“旧公钥退役”，再移除服务器/)).toBeVisible()
    // 旧的“专用密钥 / 吊销”入口没有了
    expect(screen.queryByRole('button', { name: /吊销/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/专用密钥已装/)).not.toBeInTheDocument()
    await fireEvent.click(within(ask).getByRole('button', { name: '补充服务器登录密码' }))
    const form = await screen.findByRole('form', { name: '补充服务器登录密码' })
    expect(within(form).queryByLabelText('IP 地址 *')).not.toBeInTheDocument()
    await fireEvent.update(within(form).getByLabelText('登录密码 *'), 'old-server-pw')
    await fireEvent.click(within(form).getByRole('button', { name: '保存并测试连接' }))
    expect(await json(requests, 'PUT', '/api/v1/agents/servers/srv-3/credentials')).toEqual({ login_password: 'old-server-pw', version: 7 })
    // 指纹早已确认：保存后直接用新的 version 测试；不重新初始化
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-3/test-connection')).toEqual({ version: 8 })
    expect(await screen.findByRole('region', { name: '连接测试结果' })).toHaveTextContent('连接正常')
    expect(requests.some(r => path(r).endsWith('/prepare'))).toBe(false)
    await vi.waitFor(() => expect(within(panel).getByText('已就绪')).toBeVisible())
    await fireEvent.click(screen.getByRole('button', { name: '完成' }))

    const retire = await within(panel).findByRole('group', { name: '旧公钥退役' })
    await vi.waitFor(() => expect(within(retire).getByRole('button', { name: '清理旧公钥' })).toBeEnabled())
    await fireEvent.click(within(retire).getByRole('button', { name: '清理旧公钥' }))
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-3/legacy-key-cleanup')).toEqual({ version: 8 })
    // 远端没删掉：显示要手动处理，不报告清理完成
    expect(await within(panel).findByText('旧专用公钥需要手动删除')).toBeVisible()
    expect(document.body.innerHTML).not.toContain('old-server-pw')
  })

  it('shows real CPU, memory and disk or why there is none, and lists the deployed OpenClaw with links to each agent', async () => {
    const s = readyServer({ managed_deployments: 2, successful_deployments: 1, allocated_cpus: null, allocated_mem_limit_mb: null, has_unlimited_limits: true, instances_total: 3 })
    const agents = [
      row('inst-a', '开发助手', { deployment_state: 'deployed', cpus: 2, mem_limit_mb: 4096, latest_job: job('install', 'succeeded') }),
      row('inst-b', '新闻助手', { deployment_state: 'failed', status: 'pending', latest_job: job('install', 'failed') }),
      row('inst-c', '手动接入的', { deployment_state: 'associated', deployed: false, deployment_project: null, install_dir: null, status: 'offline' }),
    ]
    let reported = false
    setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1') return Response.json(detail(s, { agents, current: reported ? current : null }))
    })
    const first = await openApp('/agent-servers?server=srv-1')
    const head = await screen.findByRole('region', { name: '服务器：香港节点 A' })
    // 页头一行：连接、服务器环境、CPU、内存、硬盘、部署智能体（T-0269，后加硬盘）；初始化了但还没有采样也没有上报：写“等待采样”，不画 0
    const meters = within(head).getByRole('list', { name: '服务器概览' })
    expect(within(meters).getAllByRole('listitem').map(li => li.querySelector('.asv-stat-label')?.textContent)).toEqual(['连接', '服务器环境', 'CPU', '内存', '硬盘', '部署智能体'])
    expect(within(meters).getAllByText('等待采样')).toHaveLength(3)
    expect(meters).not.toHaveTextContent('0%')
    expect(meters).toHaveTextContent('连接连接正常')
    expect(meters).toHaveTextContent('服务器环境已就绪')
    expect(within(head).getByRole('link', { name: /前往部署智能体/ })).toHaveAttribute('href', '/agents?deploy=1&server_id=srv-1')
    const deployed = screen.getByRole('region', { name: '已部署 OpenClaw' })
    expect(within(deployed).getByRole('heading', { name: '已部署 OpenClaw · 3' })).toBeVisible()
    expect(deployed).toHaveTextContent('平台部署记录 2 条 · 部署成功 1 · 已分配上限 含不限制 / 含不限制（不是实际占用）')
    const table = within(deployed).getByRole('table')
    const dev = within(table).getByRole('row', { name: /开发助手/ })
    expect(dev).toHaveTextContent('已部署')
    expect(dev).toHaveTextContent('myworkspace-openclaw-inst-a')
    expect(dev).toHaveTextContent('2 核 · 4 GB')
    expect(dev).toHaveTextContent('部署 · 成功')
    expect(within(dev).getByRole('link', { name: '查看智能体：开发助手' })).toHaveAttribute('href', '/agents?instance=inst-a')
    expect(within(table).getByRole('row', { name: /新闻助手/ })).toHaveTextContent('部署失败')
    const manual = within(table).getByRole('row', { name: /手动接入的/ })
    expect(manual).toHaveTextContent('归属（非平台部署）')
    expect(manual).toHaveTextContent('不限制')
    // 没有真实容器检测：不写“运行中 / 已停止”
    expect(deployed).not.toHaveTextContent(/运行中|已停止/)
    first.unmount()

    reported = true
    await openApp('/agent-servers?server=srv-1')
    const live = await screen.findByRole('list', { name: '服务器概览' })
    // 没有平台采样时退回智能体上报
    await vi.waitFor(() => expect(live).toHaveTextContent('CPU60%'))
    expect(live).toHaveTextContent('内存12.0 GB / 16.0 GB')
    expect(live).toHaveTextContent('硬盘50.0 GB / 100 GB')
  })
})

// T-0269：按效果图重排的“连接与初始化”——登录前提清单、进行中的任务、成功条、没有失败步骤时的失败原因
describe('agent servers page: initialization layout', () => {
  const STEPS6 = ['connect', 'inspect', 'docker', 'reconnect', 'workspace', 'verify'] as const

  it('locks every action while a job runs, shows which step and its newest output, and keeps the steps as “等待”', async () => {
    const s = server({ ssh: readySsh({ prepared_at: null }), initialization_status: 'initializing', deploy_block_reason: 'initializing' })
    const running = job('prepare', 'running', { log: '$ 安装 Docker 与 Compose\nGet:1 https://download.example.invalid stable InRelease\n\nSetting up containerd.io ...\n' })
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1') return Response.json(detail(s, { job: running }))
    })
    await openApp('/agent-servers?server=srv-1')
    const panel = await screen.findByRole('region', { name: '连接与初始化' })
    expect(within(panel).getByRole('button', { name: '初始化中…' })).toBeDisabled()
    expect(panel).toHaveTextContent('这台服务器正在执行任务，结束后再操作。')
    const checks = within(panel).getByRole('region', { name: '登录前提' })
    expect(checks).toHaveTextContent('4 / 4 通过')
    expect(checks).toHaveTextContent('登录地址deploy@203.0.113.10:22')
    expect(checks).toHaveTextContent('登录密码已加密保存第 1 版')
    expect(checks).toHaveTextContent('主机指纹已确认')
    expect(checks).toHaveTextContent('连接测试连接正常')
    for (const name of ['更换地址', '更新密码', '测试连接']) expect(within(checks).getByRole('button', { name })).toBeDisabled()
    const progress = within(panel).getByRole('region', { name: '初始化服务器进度' })
    expect(progress).toHaveAttribute('data-status', 'running')
    expect(progress).toHaveTextContent('正在执行第 3 / 4 步')
    expect(progress).toHaveTextContent('完成 2 步')
    const steps = within(progress).getAllByRole('listitem')
    expect(within(steps[2]!).getByText('进行中')).toBeVisible()
    const live = within(steps[2]!).getByLabelText('最新输出')
    expect(live).toHaveTextContent('Setting up containerd.io ...')
    expect(within(live).getByText('$ 安装 Docker 与 Compose')).toHaveClass('is-cmd')
    expect(within(steps[3]!).getByText('等待')).toBeVisible()
    expect(within(panel).getByText('可以离开这个页面')).toBeVisible()
    expect(within(panel).getByRole('button', { name: '移除服务器' })).toBeDisabled()
    expect(within(panel).getByText('这台服务器正在执行任务，结束后才能移除。')).toBeVisible()
    expect(writes(requests)).toHaveLength(0)
  })

  it('points to the fingerprint check from the checklist and shows the plan before the first run', async () => {
    const s = server()
    setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1') return Response.json(detail(s))
    })
    await openApp('/agent-servers?server=srv-1')
    const panel = await screen.findByRole('region', { name: '连接与初始化' })
    expect(panel).toHaveTextContent('先核对主机指纹，再测试连接与初始化。')
    const checks = within(panel).getByRole('region', { name: '登录前提' })
    expect(checks).toHaveTextContent('2 / 4 通过')
    expect(checks).toHaveTextContent('主机指纹待核对')
    expect(checks).toHaveTextContent('连接测试未检测核对指纹后才能测试')
    expect(within(checks).getByRole('button', { name: '测试连接' })).toBeDisabled()
    await fireEvent.click(within(checks).getByRole('button', { name: '去核对' }))
    const verify = within(panel).getByRole('group', { name: '核对主机公钥' })
    await vi.waitFor(() => expect(document.activeElement).toBe(within(verify).getByLabelText(/我已在服务器上核对/)))
    const plan = within(panel).getByRole('region', { name: '初始化将完成' })
    expect(within(plan).getAllByRole('listitem')).toHaveLength(4)
    expect(within(plan).getAllByText('待执行')).toHaveLength(4)
    expect(within(panel).getByRole('button', { name: '初始化服务器' })).toBeDisabled()
    expect(within(panel).getByText('工作台不再保存它的地址、主机公钥与登录密码；不会停止或删除服务器上的任何容器。')).toBeVisible()
  })

  it('folds a finished initialization into one success strip, opens the steps on request, and shows a short notice', async () => {
    const s = readyServer()
    const done = job('prepare', 'succeeded', { steps: STEPS6.map(key => ({ key, status: 'done', detail: null })) })
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(detail(s, { job: done }))
      if (path(r) === '/api/v1/agents/servers/srv-1/test-connection') return Response.json(detail(s, { job: done }))
    })
    await openApp('/agent-servers?server=srv-1')
    const panel = await screen.findByRole('region', { name: '连接与初始化' })
    expect(panel).toHaveTextContent('环境已就绪，可以在这台服务器上部署智能体了。')
    const progress = within(panel).getByRole('region', { name: '初始化服务器进度' })
    expect(within(progress).getByRole('status')).toHaveTextContent('服务器初始化完成：可以在这台服务器上部署智能体了。')
    for (const chip of ['Docker 与 Compose', '部署目录', '平台连通']) expect(within(progress).getByText(chip)).toBeVisible()
    expect(within(progress).queryAllByRole('listitem')).toHaveLength(0)
    const toggle = within(progress).getByRole('button', { name: '查看每一步（6 / 6 步完成）' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await fireEvent.click(toggle)
    expect(within(progress).getAllByRole('listitem')).toHaveLength(6)
    expect(within(progress).getByRole('button', { name: '收起每一步' })).toHaveAttribute('aria-expanded', 'true')
    expect(within(panel).getByRole('button', { name: '重新初始化' })).toBeEnabled()
    await fireEvent.click(within(within(panel).getByRole('region', { name: '登录前提' })).getByRole('button', { name: '测试连接' }))
    expect(await json(requests, 'POST', '/api/v1/agents/servers/srv-1/test-connection')).toEqual({ version: 1 })
    const toast = await within(panel).findByText('连接正常：已用保存的密码登录服务器。')
    expect(toast).toHaveAttribute('role', 'status')
    expect(toast).toHaveClass('asv-toast')
  })

  it('shows the reason on its own when the job failed before any step, and marks the error lines in the output', async () => {
    const s = server({ ssh: readySsh({ prepared_at: null }), initialization_status: 'failed', deploy_block_reason: 'initialization_failed' })
    const failed = job('prepare', 'failed', {
      error_code: 'unsupported_os', error_message: '自动安装 Docker 只支持 Ubuntu 22.04 / 24.04、Debian 12（amd64、arm64）',
      log: 'os_id=debian\n$ 核对物理服务器身份（退出码 0）\n！自动安装 Docker 只支持 Ubuntu 22.04 / 24.04、Debian 12（amd64、arm64）',
      steps: PREPARE_STEPS.map(key => ({ key, status: key === 'connect' ? 'done' : 'pending', detail: null })),
    })
    setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1') return Response.json(detail(s, { job: failed }))
    })
    await openApp('/agent-servers?server=srv-1')
    const progress = await screen.findByRole('region', { name: '初始化服务器进度' })
    expect(progress).toHaveTextContent('完成 1 / 4 步后失败')
    expect(progress).toHaveTextContent('后面 3 步未执行')
    const reason = within(progress).getByRole('alert')
    expect(reason).toHaveTextContent('失败原因自动安装 Docker 只支持')
    expect(reason).toHaveTextContent('unsupported_os')
    expect(within(progress).getAllByText('未执行')).toHaveLength(3)
    const log = progress.querySelector('details')!
    expect(within(log).getByText(/^！自动安装 Docker/)).toHaveClass('is-err')
    expect(within(log).getByText('os_id=debian')).not.toHaveClass('is-err')
    expect(within(log).getByText('3 行 · 密码、令牌、私钥已替换成 ***')).toBeInTheDocument()
  })
})
