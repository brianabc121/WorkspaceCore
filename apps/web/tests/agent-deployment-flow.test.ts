import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import { at, deploymentState, fail, instance, job, overviewRow, overviewSummary, path, readyServer, server, serverList, ssh } from './agents-fixtures'
import { useAuthStore } from '../src/stores/auth'
import type { Schema } from '../src/services/api'

// T-0182（改造方案 Task 5 Step 1–3）：OpenClaw 智能体页——服务端统计与分页、筛选不串、连接状态与部署任务分开显示；
// 部署抽屉只能选可部署的服务器、预选来自服务器页、默认不限制资源；同一个 request_id 防重复，结果未知先查收据，
// 失败后继续原实例；浏览器里只短暂记下请求号，不存密码。
const KEY = 'myworkspace.agents.deployment-request'
const RID = '5b0c1f6e-3a1d-4c9e-9f2a-7d8e6b5a4c3b'
const srv1 = readyServer({ name: '香港节点 A', cores: 8, mem_total: 16 * 1024 ** 3, instances_total: 3, managed_deployments: 3, allocated_mem_limit_mb: 8192, allocated_cpus: 4 })
const srv2 = readyServer({ id: 'srv-2', name: '新加坡节点 B', ssh: ssh({ host: '198.51.100.20', address: '198.51.100.20', host_key_confirmed: true, host_key_confirmed_at: at, connection_status: 'connected', prepared_at: at }), version: 5 })
const srv3 = server({ id: 'srv-3', name: '测试节点', ssh: ssh({ host_key_confirmed: true }), deploy_block_reason: 'not_initialized' })
const srv4 = server({ id: 'srv-4', name: '旧服务器', ssh: ssh({ password_set: false, connection_status: 'credentials_required', host_key_confirmed: true, prepared_at: at }), initialization_status: 'ready', deploy_block_reason: 'credentials_required' })
const servers = serverList([srv1, srv2, srv3, srv4])
const deployed = (server_id = 'srv-1'): Schema['AgentDeploymentOut'] => ({ server_id, install_dir: `/home/deploy/myworkspace-openclaw/x`, installed_at: at, runtime_installed: true, version: 1 })
const INSTALL = ['connect', 'inspect', 'upload', 'pull', 'start', 'online'] as const
const installJob = (status: Schema['AgentSshJobOut']['status'], over: Partial<Schema['AgentSshJobOut']> = {}) => job('install', status, {
  steps: INSTALL.map((key, i) => ({ key, status: status === 'succeeded' ? 'done' : status === 'running' ? (i < 2 ? 'done' : i === 2 ? 'running' : 'pending') : i < 4 ? 'done' : i === 4 ? 'failed' : 'pending', detail: null })), ...over,
})
const fresh = (over: Partial<Schema['AgentInstanceOut']> = {}) => instance({ id: 'inst-n', name: '项目助手', network_mode: 'public', status: 'pending', ...over })

// 主模型（T-0207）：部署必须选中转站管理里的一条连接和它的一个模型
const OPTION: Schema['PickerConnectionOut'] = { id: 'conn-1', name: '团队多模型中转', connection_type: 'relay', protocol: 'openai_compatible', base_url: 'https://gateway.example.com/v1', model_count: 1, granted: false }
const MODEL: Schema['ConnectionModelOut'] = { id: 'm-1', model_id: 'gpt-4.1', display_name: 'gpt-4.1', source: 'manual', availability: 'listed', last_seen_at: null, version: 1 }
async function pickModel(form: HTMLElement) {
  const connection = await within(form).findByLabelText('模型连接 *') as HTMLSelectElement
  await vi.waitFor(() => expect([...connection.options].some(o => o.value === 'conn-1')).toBe(true))
  await fireEvent.update(connection, 'conn-1')
  const model = await within(form).findByLabelText('主模型 *') as HTMLSelectElement
  await vi.waitFor(() => expect([...model.options].some(o => o.value === 'gpt-4.1')).toBe(true))
  await fireEvent.update(model, 'gpt-4.1')
}

function setup(handler?: (r: Request) => Response | Promise<Response | undefined> | undefined) {
  return mockApi(async r => await handler?.(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
    path(r) === '/api/v1/auth/logout' ? new Response(null, { status: 204 }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/servers' && r.method === 'GET' ? Response.json(servers) :
    path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
    path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) :
    path(r) === '/api/v1/agents/model-connection-options' ? Response.json({ items: [OPTION], next_cursor: null, total: 1 }) :
    path(r) === '/api/v1/model-connections/conn-1/models' ? Response.json({ items: [MODEL], next_cursor: null, total: 1, synced_at: null, sync_state: null, discovered: [] }) :
    path(r).endsWith('/model-binding') && r.method === 'GET' ? Response.json({ binding: null, apply_state: null, legacy: null }) :
    path(r).endsWith('/ops') && r.method === 'GET' ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) : undefined
  ))
}
const posts = (requests: Request[], p: string) => requests.filter(r => r.method === 'POST' && path(r) === p)
const stored = () => sessionStorage.getItem(KEY)
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear() })

describe('OpenClaw agents page: list', () => {
  const a = instance({ id: 'inst-a', name: '开发助手', status: 'online', last_seen_at: new Date(Date.now() - 10_000).toISOString(), limits: { cpus: 2, mem_limit_mb: 4096 } })
  const b = instance({ id: 'inst-b', name: '新闻助手', status: 'offline', last_seen_at: at })
  const c = instance({ id: 'inst-c', name: '知识助手', status: 'disabled' })
  const d = instance({ id: 'inst-d', name: '刚部署的', status: 'pending' })
  const summary = overviewSummary({ total: 120, online: 80, offline: 20, pending: 15, disabled: 5, deploying: 3, attention: 22 })

  it('takes the counts from the server summary, shows connection and deployment separately and pages with the cursor', async () => {
    const requests = setup(r => {
      if (path(r) !== '/api/v1/agents/overview') return undefined
      const cursor = new URL(r.url).searchParams.get('cursor')
      return Response.json(cursor === 'c2'
        ? { items: [overviewRow(c), overviewRow(d, { server: srv2, latest_job: installJob('running') })], next_cursor: null, generated_at: at, summary }
        : { items: [overviewRow(a, { server: srv1, deployment: deployed(), latest_job: installJob('succeeded'), deployment_project: 'myworkspace-openclaw-0a1b2c3d' }), overviewRow(b, { server: srv1, deployment: { ...deployed(), installed_at: null, runtime_installed: null }, latest_job: installJob('failed') })], next_cursor: 'c2', generated_at: at, summary })
    })
    await openApp('/agents')
    expect(await screen.findByRole('heading', { level: 1, name: 'OpenClaw 智能体' })).toBeVisible()
    const tiles = screen.getByRole('region', { name: '智能体统计' })
    await vi.waitFor(() => expect(within(tiles).getByText('智能体总数').closest('div')).toHaveTextContent('智能体总数120'))
    expect(within(tiles).getByText('消息在线').closest('div')).toHaveTextContent('消息在线80')
    expect(within(tiles).getByText('部署中').closest('div')).toHaveTextContent('部署中3')
    expect(within(tiles).getByText('需关注').closest('div')).toHaveTextContent('需关注22')
    expect(tiles).toHaveTextContent('“部署中”与连接状态可能重叠')
    // 没有“运行中 / 已停止”的容器状态卡
    expect(tiles).not.toHaveTextContent(/运行中|已停止/)

    const list = screen.getByRole('region', { name: '智能体列表' })
    const table = await within(list).findByRole('table')
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent?.trim())).toEqual(['智能体', '所属服务器', '连接状态', '部署任务', '资源上限', '最近心跳', '操作'])
    const dev = within(table).getByRole('row', { name: /开发助手/ })
    expect(dev).toHaveTextContent('myworkspace-openclaw-0a1b2c3d')
    expect(dev).toHaveTextContent('消息在线')
    expect(dev).toHaveTextContent('部署成功')
    expect(dev).toHaveTextContent('2 核 · 4 GB')
    expect(dev).toHaveTextContent(/\d+ 秒前/)
    expect(within(dev).getByRole('link', { name: '香港节点 A' })).toHaveAttribute('href', '/agent-servers?server=srv-1')
    const news = within(table).getByRole('row', { name: /新闻助手/ })
    expect(news).toHaveTextContent('离线')
    expect(news).toHaveTextContent('部署失败')
    expect(news).toHaveTextContent('不限制')
    expect(table).not.toHaveTextContent(/运行中|已停止/)
    expect(list).toHaveTextContent('已显示 2 个，共 120 个')

    await fireEvent.click(within(list).getByRole('button', { name: '加载更多智能体' }))
    await vi.waitFor(() => expect(within(table).getAllByRole('row')).toHaveLength(5))
    expect(requests.some(r => path(r) === '/api/v1/agents/overview' && new URL(r.url).searchParams.get('cursor') === 'c2')).toBe(true)
    expect(within(table).getByRole('row', { name: /刚部署的/ })).toHaveTextContent('待接入')
    expect(within(table).getByRole('row', { name: /刚部署的/ })).toHaveTextContent('部署中')
    expect(within(table).getByRole('row', { name: /知识助手/ })).toHaveTextContent('已停用')
    expect(within(table).getByRole('row', { name: /知识助手/ })).toHaveTextContent('手动接入')
    // 统计仍是服务端的全量数字
    expect(within(tiles).getByText('智能体总数').closest('div')).toHaveTextContent('智能体总数120')
    expect(requests.filter(r => r.method !== 'GET')).toHaveLength(0)
  })

  it('never shows a late response for an old filter, and sends search and status to the server', async () => {
    const requests = setup(async r => {
      if (path(r) !== '/api/v1/agents/overview') return undefined
      const params = new URL(r.url).searchParams
      if (params.get('server_id') === 'srv-2') return Response.json({ items: [overviewRow(b, { server: srv2 })], next_cursor: null, generated_at: at, summary: overviewSummary({ total: 1, offline: 1, attention: 1 }) })
      if (params.get('status') || params.get('q')) return Response.json({ items: [overviewRow(b, { server: srv1 })], next_cursor: null, generated_at: at, summary })
      // 不带筛选的第一次请求很慢：它回来时已经换了筛选
      await new Promise(done => setTimeout(done, 400))
      return Response.json({ items: [overviewRow(a, { server: srv1 })], next_cursor: null, generated_at: at, summary })
    })
    await openApp('/agents')
    const list = screen.getByRole('region', { name: '智能体列表' })
    const select = within(list).getByLabelText('按服务器筛选') as HTMLSelectElement
    await vi.waitFor(() => expect([...select.options].map(o => o.textContent)).toEqual(['全部服务器', '香港节点 A', '新加坡节点 B', '测试节点', '旧服务器']))
    await fireEvent.update(select, 'srv-2')
    const table = await within(list).findByRole('table')
    expect(within(table).getByRole('row', { name: /新闻助手/ })).toBeVisible()
    await new Promise(done => setTimeout(done, 500))
    expect(within(list).queryByText('开发助手')).not.toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: '智能体统计' })).getByText('智能体总数').closest('div')).toHaveTextContent('智能体总数1')

    await fireEvent.update(select, '')
    await fireEvent.update(within(list).getByLabelText('按连接状态筛选'), 'offline')
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/agents/overview' && new URL(r.url).searchParams.get('status') === 'offline')).toBe(true))
    // 套用了状态筛选：不写“共 N 个”（summary 不按状态统计）
    await vi.waitFor(() => expect(list).toHaveTextContent('已显示 1 个'))
    expect(list).not.toHaveTextContent('共 120 个')
    await fireEvent.update(within(list).getByPlaceholderText('搜索智能体'), ' 新闻 ')
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/agents/overview' && new URL(r.url).searchParams.get('q') === '新闻')).toBe(true))
  })

  it('opens an agent with all its existing sections, and clears the detail when it cannot be found', async () => {
    setup(r => {
      if (path(r) === '/api/v1/agents/overview') return Response.json({ items: [overviewRow(a, { server: srv1, deployment: deployed(), latest_job: installJob('succeeded'), deployment_project: 'myworkspace-openclaw-0a1b2c3d' })], next_cursor: null, generated_at: at, summary })
      if (path(r) === '/api/v1/agents/instances' && r.method === 'GET') return Response.json({ items: [a], next_cursor: null, total_estimate: 1 })
      if (path(r) === '/api/v1/agents/instances/inst-a/deployment') return Response.json(deploymentState({ server: srv1, deployment: deployed(), job: installJob('succeeded') }))
      if (path(r) === '/api/v1/agents/instances/ghost') return fail(404, 'NOT_FOUND')
    })
    const { router } = await openApp('/agents')
    const table = await screen.findByRole('table')
    await fireEvent.click(within(table).getByRole('button', { name: '查看：开发助手' }))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agents?instance=inst-a'))
    const detail = await screen.findByRole('region', { name: '智能体详情' })
    const facts = within(detail).getByRole('region', { name: '智能体概览' })
    expect(within(facts).getByRole('heading', { name: '开发助手' })).toBeVisible()
    expect(within(facts).getByText('所在服务器').closest('div')).toHaveTextContent('香港节点 A')
    expect(within(facts).getByText('部署方式').closest('div')).toHaveTextContent('平台自动部署（Docker Compose）')
    expect(within(facts).getByText('消息连接').closest('div')).toHaveTextContent('消息在线')
    // 没有真实容器检测：容器状态写“未检测”
    expect(within(facts).getByText('容器状态').closest('div')).toHaveTextContent('未检测')
    const card = within(detail).getByRole('article', { name: '实例：开发助手' })
    for (const name of ['项目管理权限', '附件权限', '终止其他智能体任务的权限', '部署到服务器', '资源上限', '运维', '接入包', '凭证', '高级凭据授权', '主模型', '负载']) {
      expect(within(card).getByRole('region', { name })).toBeInTheDocument()
    }
    expect(within(card).getByRole('button', { name: '停用' })).toBeVisible()
    expect(within(card).getByRole('button', { name: '编辑' })).toBeVisible()

    await router.push('/agents?instance=ghost')
    expect(await screen.findByText('没有找到这个智能体，或你已无权访问。')).toBeVisible()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    await fireEvent.click(within(screen.getByRole('region', { name: '智能体详情' })).getByRole('button', { name: '关闭' }))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agents'))
  })
})

describe('OpenClaw agents page: deploy drawer', () => {
  const empty = { items: [], next_cursor: null, generated_at: at, summary: overviewSummary() }

  it('defaults to auto without a server and shows the actual server after acceptance', async () => {
    const state = deploymentState({ server: srv2, deployment: deployed('srv-2'), job: installJob('running') })
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/overview') return Response.json(empty)
      if (path(r) === '/api/v1/agents/deployments' && r.method === 'POST') return Response.json({ request_id: RID, instance: fresh(), deployment: state }, { status: 202 })
      if (path(r) === '/api/v1/agents/instances/inst-n/deployment') return Response.json(state)
      if (path(r) === '/api/v1/agents/instances/inst-n') return Response.json(fresh())
    })
    await openApp('/agents?deploy=1')
    const form = await screen.findByRole('form', { name: '部署 OpenClaw' })
    expect(within(form).getByLabelText('部署位置')).toHaveValue('auto')
    expect(within(form).queryByLabelText('部署服务器 *')).not.toBeInTheDocument()
    await fireEvent.update(within(form).getByLabelText('智能体名称 *'), '项目助手')
    await pickModel(form)
    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
    const progress = await screen.findByRole('region', { name: '新智能体的部署' })
    expect(within(progress).getByRole('link', { name: '新加坡节点 B' })).toHaveAttribute('href', '/agent-servers?server=srv-2')
    const body = await posts(requests, '/api/v1/agents/deployments')[0]!.clone().json()
    expect(body.placement).toEqual({ mode: 'auto' })
    expect(body.server_id).toBeUndefined()
    expect(body.server_version).toBeUndefined()
  })

  it('keeps the specified target and list after capacity rejection until the user switches to auto', async () => {
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/overview') return Response.json(empty)
      if (path(r) === '/api/v1/agents/deployments') return fail(409, 'PLACEMENT_CAPACITY_INSUFFICIENT')
    })
    await openApp('/agents?deploy=1')
    const form = await screen.findByRole('form', { name: '部署 OpenClaw' })
    await fireEvent.update(within(form).getByLabelText('部署位置'), 'specified')
    await within(form).findByRole('option', { name: '香港节点 A' })
    await fireEvent.update(within(form).getByLabelText('部署服务器 *'), 'srv-1')
    await fireEvent.update(within(form).getByLabelText('智能体名称 *'), '项目助手')
    await pickModel(form)
    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
    expect(await within(form).findByRole('alert')).toBeVisible()
    expect(within(form).getByLabelText('部署位置')).toHaveValue('specified')
    expect(within(form).getByLabelText('部署服务器 *')).toHaveValue('srv-1')
    expect(screen.getByRole('region', { name: '智能体列表' })).toBeVisible()
    expect(posts(requests, '/api/v1/agents/deployments')).toHaveLength(1)
    await fireEvent.update(within(form).getByLabelText('部署位置'), 'auto')
    expect(within(form).queryByLabelText('部署服务器 *')).not.toBeInTheDocument()
  })

  it('opens from the servers page with that server, lists only deployable servers, asks for no address or password, and deploys with finite default resources', { timeout: 15000 }, async () => {
    let state = deploymentState({ server: srv2, deployment: { ...deployed('srv-2'), installed_at: null, runtime_installed: null }, job: installJob('running') })
    let overviews = 0
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/overview') { overviews++; return Response.json(empty) }
      if (path(r) === '/api/v1/agents/deployments' && r.method === 'POST') return Response.json({ request_id: RID, instance: fresh(), deployment: state }, { status: 202 })
      if (path(r) === '/api/v1/agents/instances/inst-n/deployment') {
        const now = state
        state = deploymentState({ server: srv2, deployment: deployed('srv-2'), job: installJob('succeeded', { steps: INSTALL.map(key => ({ key, status: 'done', detail: key === 'online' ? '实例已连上工作台' : null })) }) })
        return Response.json(now)
      }
      if (path(r) === '/api/v1/agents/instances/inst-n') return Response.json(fresh())
    })
    const { router } = await openApp('/agents?deploy=1&server_id=srv-2')
    const form = await screen.findByRole('form', { name: '部署 OpenClaw' })
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/agents'))
    const select = within(form).getByLabelText('部署服务器 *') as HTMLSelectElement
    await vi.waitFor(() => expect(select.value).toBe('srv-2'))
    expect([...select.options].filter(o => !o.disabled).map(o => o.textContent)).toEqual(['香港节点 A', '新加坡节点 B'])
    const blocked = within(form).getByText('2 台服务器暂不能部署').closest('details')!
    expect(blocked).toHaveTextContent('测试节点：还没初始化')
    expect(blocked).toHaveTextContent('旧服务器：还没保存登录密码')
    expect(within(blocked).getAllByRole('link', { name: '去处理' })[0]).toHaveAttribute('href', '/agent-servers?server=srv-3')
    // 没有 IP、账号或密码输入；版本是锁定的推荐版本；资源默认不限制
    expect(within(form).queryByLabelText(/IP|账号|密码/)).not.toBeInTheDocument()
    expect(within(form).getByText('平台推荐版本（已锁定）')).toBeVisible()
    expect(within(form).getByLabelText('CPU 上限（核）')).toHaveValue('1')
    expect(within(form).getByLabelText('内存上限（MiB）')).toHaveValue('2048')
    expect(within(form).getByRole('region', { name: '自动部署流程' })).toHaveTextContent('检查服务器环境拉取镜像创建独立容器与数据目录接入工作台等待心跳并确认上线')
    const start = within(form).getByRole('button', { name: '开始部署' })
    expect(start).toBeDisabled()
    await fireEvent.update(within(form).getByLabelText('智能体名称 *'), ' 项目助手 ')
    // 没选主模型不能部署；新建时列出全部可用连接，并说明选择即授权
    expect(start).toBeDisabled()
    expect(within(form).getByText(/选择即把这条连接授权给新智能体/)).toBeVisible()
    await pickModel(form)
    await fireEvent.click(start)
    await fireEvent.click(start)
    await vi.waitFor(() => expect(posts(requests, '/api/v1/agents/deployments')).toHaveLength(1))
    const post = posts(requests, '/api/v1/agents/deployments')[0]!
    const body = await post.clone().json() as Schema['AgentDeploymentCreateIn']
    expect(body).toEqual({ request_id: expect.stringMatching(/^[0-9a-f-]{36}$/), placement: { mode: 'specified', server_id: 'srv-2' }, server_version: 5, name: '项目助手', description: '', cpus: 1, mem_limit_mb: 2048, model: { connection_id: 'conn-1', model_id: 'gpt-4.1' } })
    expect(post.headers.get('Idempotency-Key')).toBe(body.request_id)

    // 接纳后显示真实步骤，直到部署完成；结束后刷新总览
    const progress = await screen.findByRole('region', { name: '新智能体的部署' })
    expect(within(progress).getByRole('link', { name: '新加坡节点 B' })).toHaveAttribute('href', '/agent-servers?server=srv-2')
    expect(await within(progress).findByText('部署完成，实例已连上工作台。', {}, { timeout: 5000 })).toBeVisible()
    expect(within(progress).getAllByRole('listitem').map(li => li.getAttribute('data-status'))).toEqual(['done', 'done', 'done', 'done', 'done', 'done'])
    const before = overviews
    await vi.waitFor(() => expect(overviews).toBeGreaterThan(before - 1))
    expect(stored()).toBeNull()
    // 只发过一次新建；没有带密码的请求
    expect(posts(requests, '/api/v1/agents/deployments')).toHaveLength(1)
    const bodies = await Promise.all(requests.filter(r => r.method !== 'GET').map(r => r.clone().text()))
    expect(bodies.some(b => /password/.test(b))).toBe(false)
  })

  it('does not quietly pick another server when the linked one cannot be used, and passes manual limits exactly', async () => {
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/overview') return Response.json(empty)
      if (path(r) === '/api/v1/agents/deployments') return fail(409, 'AGENT_INGRESS_NOT_CONFIGURED')
    })
    const first = await openApp('/agents?deploy=1&server_id=srv-3')
    let form = await screen.findByRole('form', { name: '部署 OpenClaw' })
    expect(await within(form).findByText(/「测试节点」暂不能部署：还没初始化/)).toBeVisible()
    expect((within(form).getByLabelText('部署服务器 *') as HTMLSelectElement).value).toBe('')
    expect(within(form).getByRole('link', { name: '去服务器页处理' })).toHaveAttribute('href', '/agent-servers?server=srv-3')
    first.unmount()

    await openApp('/agents?deploy=1&server_id=nope')
    form = await screen.findByRole('form', { name: '部署 OpenClaw' })
    expect(await within(form).findByText(/地址里指定的服务器不存在/)).toBeVisible()
    const select = within(form).getByLabelText('部署服务器 *') as HTMLSelectElement
    expect(select.value).toBe('')
    await fireEvent.update(within(form).getByLabelText('智能体名称 *'), '项目助手')
    await fireEvent.update(select, 'srv-1')
    expect(within(form).getByLabelText('所选服务器')).toHaveTextContent('已分配内存上限 8 GB / 16 GB（不是实际占用）')
    await fireEvent.update(within(form).getByLabelText('CPU 上限（核）'), '0.05')
    expect(within(form).getByText(/CPU 上限是 0.1 到 256 之间的数/)).toBeVisible()
    expect(within(form).getByRole('button', { name: '开始部署' })).toBeDisabled()
    await fireEvent.update(within(form).getByLabelText('CPU 上限（核）'), '2')
    await fireEvent.update(within(form).getByLabelText('内存上限（MiB）'), '4096')
    await pickModel(form)
    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
    await vi.waitFor(() => expect(posts(requests, '/api/v1/agents/deployments')).toHaveLength(1))
    const firstBody = await posts(requests, '/api/v1/agents/deployments')[0]!.clone().json() as Schema['AgentDeploymentCreateIn']
    expect(firstBody).toMatchObject({ placement: { mode: 'specified', server_id: 'srv-1' }, cpus: 2, mem_limit_mb: 4096 })
    // 被明确拒绝（没有建实例）：说明原因、留在表单，不显示“已上线”
    expect(await within(form).findByText(/外网接入入口还没有配置/)).toBeVisible()
    expect(screen.queryByRole('region', { name: '新智能体的部署' })).not.toBeInTheDocument()
    expect(stored()).toBeNull()
    // 改了再提交是一个新的请求
    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
    await vi.waitFor(() => expect(posts(requests, '/api/v1/agents/deployments')).toHaveLength(2))
    const secondBody = await posts(requests, '/api/v1/agents/deployments')[1]!.clone().json() as Schema['AgentDeploymentCreateIn']
    expect(secondBody.request_id).not.toBe(firstBody.request_id)
  })
})

describe('OpenClaw agents page: recovery', () => {
  const empty = { items: [], next_cursor: null, generated_at: at, summary: overviewSummary() }
  const accepted = (status: Schema['AgentSshJobOut']['status']): Schema['AgentDeploymentRequestOut'] => ({ request_id: RID, state: 'accepted', instance: fresh(), deployment: deploymentState({ server: srv1, deployment: { ...deployed(), installed_at: null, runtime_installed: null }, job: installJob(status) }), error_code: null })

  it('asks for the receipt with the same request id when the response is lost, instead of creating again', async () => {
    let sent = ''
    const requests = setup(async r => {
      if (path(r) === '/api/v1/agents/overview') return Response.json(empty)
      if (path(r) === '/api/v1/agents/deployments' && r.method === 'POST') { sent = (await r.clone().json() as { request_id: string }).request_id; throw new TypeError('network down') }
      if (path(r).startsWith('/api/v1/agents/deployments/requests/')) return Response.json({ ...accepted('running'), request_id: sent })
      if (path(r) === '/api/v1/agents/instances/inst-n/deployment') return Response.json(accepted('running').deployment)
      if (path(r) === '/api/v1/agents/instances/inst-n') return Response.json(fresh())
    })
    await openApp('/agents?deploy=1&server_id=srv-1')
    const form = await screen.findByRole('form', { name: '部署 OpenClaw' })
    await vi.waitFor(() => expect((within(form).getByLabelText('部署服务器 *') as HTMLSelectElement).value).toBe('srv-1'))
    await fireEvent.update(within(form).getByLabelText('智能体名称 *'), '项目助手')
    await pickModel(form)
    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
    const progress = await screen.findByRole('region', { name: '新智能体的部署' })
    expect(within(progress).getByText('项目助手')).toBeVisible()
    expect(requests.some(r => r.method === 'GET' && path(r) === `/api/v1/agents/deployments/requests/${sent}`)).toBe(true)
    expect(posts(requests, '/api/v1/agents/deployments')).toHaveLength(1)
    // 浏览器里只记着请求号、实例号与用户 / 工作空间
    expect(JSON.parse(stored()!)).toEqual({ request_id: sent, instance_id: 'inst-n', user_id: 'user-a', workspace_id: 'workspace-a' })
  })

  it('finds the request again after a page refresh without sending a new one, and continues a failed deployment on the same instance', { timeout: 15000 }, async () => {
    sessionStorage.setItem(KEY, JSON.stringify({ request_id: RID, user_id: 'user-a', workspace_id: 'workspace-a' }))
    let receipts = 0
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/overview') return Response.json(empty)
      if (path(r) === `/api/v1/agents/deployments/requests/${RID}`) return Response.json(++receipts === 1 ? { request_id: RID, state: 'processing', instance: null, deployment: null, error_code: null } : accepted('failed'))
      if (path(r) === '/api/v1/agents/instances/inst-n/deployment') return Response.json(accepted('failed').deployment)
      if (path(r) === '/api/v1/agents/instances/inst-n') return Response.json(fresh({ version: 4 }))
      if (path(r) === '/api/v1/agents/instances/inst-n/deploy') return Response.json(deploymentState({ ...accepted('running').deployment!, job: installJob('running') }), { status: 202 })
    })
    await openApp('/agents')
    expect(await screen.findByRole('region', { name: '正在确认部署请求' })).toBeVisible()
    const progress = await screen.findByRole('region', { name: '新智能体的部署' }, { timeout: 5000 })
    expect(await within(progress).findByText(/实例已经保留/)).toBeVisible()
    expect(receipts).toBeGreaterThanOrEqual(2)
    const resume = within(progress).getByRole('button', { name: '继续部署原实例' })
    await vi.waitFor(() => expect(resume).toBeEnabled())
    await fireEvent.click(resume)
    await vi.waitFor(() => expect(posts(requests, '/api/v1/agents/instances/inst-n/deploy')).toHaveLength(1))
    expect(await posts(requests, '/api/v1/agents/instances/inst-n/deploy')[0]!.clone().json()).toEqual({ server_id: 'srv-1', version: 4 })
    expect(posts(requests, '/api/v1/agents/deployments')).toHaveLength(0)
  })

  it('says the receipt is gone and points to the existing agents instead of creating again', async () => {
    sessionStorage.setItem(KEY, JSON.stringify({ request_id: RID, user_id: 'user-a', workspace_id: 'workspace-a' }))
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/overview') return Response.json(empty)
      if (path(r) === `/api/v1/agents/deployments/requests/${RID}`) return fail(404, 'NOT_FOUND')
    })
    await openApp('/agents')
    const missing = await screen.findByRole('region', { name: '部署请求结果未知' })
    expect(within(missing).getByText(/找不到上一次部署请求的记录/)).toBeVisible()
    await fireEvent.click(within(missing).getByRole('button', { name: '查看现有智能体' }))
    expect(screen.queryByRole('region', { name: '部署请求结果未知' })).not.toBeInTheDocument()
    expect(stored()).toBeNull()
    expect(requests.filter(r => r.method !== 'GET')).toHaveLength(0)
  })

  it('ignores and clears a record left by another user, and clears its own on logout', async () => {
    sessionStorage.setItem(KEY, JSON.stringify({ request_id: RID, user_id: 'user-b', workspace_id: 'workspace-a' }))
    const requests = setup(r => path(r) === '/api/v1/agents/overview' ? Response.json(empty) : undefined)
    const { pinia } = await openApp('/agents')
    expect(await screen.findByRole('heading', { level: 1, name: 'OpenClaw 智能体' })).toBeVisible()
    expect(screen.queryByRole('region', { name: '正在确认部署请求' })).not.toBeInTheDocument()
    expect(stored()).toBeNull()
    expect(requests.some(r => path(r).startsWith('/api/v1/agents/deployments'))).toBe(false)

    sessionStorage.setItem(KEY, JSON.stringify({ request_id: RID, user_id: 'user-a', workspace_id: 'workspace-a' }))
    await useAuthStore(pinia).logout()
    await vi.waitFor(() => expect(stored()).toBeNull())
  })
})
