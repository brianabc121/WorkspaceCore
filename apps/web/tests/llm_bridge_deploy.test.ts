// 页面上一键部署 MCP 管理器（T-0233，docs/v2/LLM_BRIDGE_PLAN.md 第 20 节；src/modules/llm_bridge 的
// DeployManager / DeployJob）：选服务器部署并看进度、部署好之后选中新的管理器、
// 工作台部署的管理器可以重新部署和卸载、这个工作台不能一键部署时只有手动添加、admin 不能部署。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { fail, me, mockApi, newsModule, openApp, workspace } from './helpers'
import { readyServer, server, serverList } from './agents-fixtures'
import type { Schema } from '../src/services/api'

type Role = Schema['WorkspaceOut']['role']
type Bridge = Schema['LlmBridgeOut']
type Job = Schema['LlmBridgeDeployJobOut']
const at = '2026-11-14T00:00:00Z'
const BRIDGE = '11111111-1111-4111-8111-111111111111', NEW = '44444444-4444-4444-8444-444444444444'
const SERVER = '55555555-5555-4555-8555-555555555555', COLD = '66666666-6666-4666-8666-666666666666'
const JOB = '77777777-7777-4777-8777-777777777777'
const api = '/api/v1/llm_bridge'
const path = (r: Request) => new URL(r.url).pathname
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })
const lbModule: Schema['ModuleOut'] = { ...newsModule, key: 'llm_bridge', name: '大模型客户端', tools: [], widgets: [] }
const bridge = (over: Partial<Bridge> = {}): Bridge => ({ id: BRIDGE, name: '1 号', base_url: 'http://llm-bridge:8787', enabled: true, has_token: true, deployment: null, created_at: at, updated_at: at, ...over })
const managed = (over: Partial<Bridge> = {}) => bridge({ id: NEW, name: '2 号', base_url: 'http://10.0.0.5:18787', deployment: { server_id: SERVER, server_name: '机房一号', mode: 'remote', deployed_at: at }, ...over })
const step = (key: string, status: string, detail: string | null = null) => ({ key, status, detail })
const job = (over: Partial<Job> = {}): Job => ({
  id: JOB, kind: 'deploy', status: 'running', server_id: SERVER, server_name: '机房一号', bridge_id: null, name: '2 号', platforms: ['claude', 'workbuddy'],
  mode: null, base_url: null, log: '', error_code: null, error_message: null, started_at: at, finished_at: null,
  steps: [step('connect', 'done', '已登录 deploy@10.0.0.5'), step('inspect', 'running'), step('upload', 'pending'), step('build', 'pending'), step('start', 'pending'), step('verify', 'pending'), step('register', 'pending')],
  ...over,
})
const status: Schema['LlmBridgeStatusOut'] = { reachable: true, error: '', version: '0.1.0', platforms: ['claude', 'workbuddy'], queued: 0, running: 0, settings: null, accounts: [], channels: [] }

interface World { bridges: Bridge[]; jobs: Record<string, Job>; available: boolean }
function setup(role: Role, given: Partial<World> = {}, handler?: (r: Request, world: World) => Response | undefined | Promise<Response | undefined>) {
  const world: World = { bridges: [bridge()], jobs: {}, available: true, ...given }
  const requests = mockApi(async r => {
    const result = await handler?.(r, world)
    if (result) return result
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return as(role)
    if (p === '/api/v1/modules' && r.method === 'GET') return Response.json({ items: [newsModule, lbModule] })
    if (p === `${api}/bridges` && r.method === 'GET') return Response.json({ bridges: world.bridges })
    if (/\/bridges\/[^/]+\/status$/.test(p)) return Response.json(status)
    if (p === `${api}/grants`) return Response.json({ grants: [] })
    if (/\/bridges\/[^/]+\/environments$/.test(p)) return Response.json({ environments: [] })
    if (p === `${api}/deployments` && r.method === 'GET') return Response.json({ jobs: Object.values(world.jobs).map(j => ({ ...j, log: null })), available: world.available })
    const one = /\/deployments\/([^/]+)$/.exec(p)
    if (one && r.method === 'GET') return world.jobs[one[1]!] ? Response.json(world.jobs[one[1]!]) : fail(404, 'NOT_FOUND')
    if (p === '/api/v1/agents/servers' && r.method === 'GET') {
      return Response.json(serverList([
        readyServer({ id: SERVER, name: '机房一号' }),
        server({ id: COLD, name: '新机器', kind: 'ssh', deployable: false, deploy_block_reason: 'not_initialized' }),
      ]))
    }
    return undefined
  })
  return { requests, world }
}
const writes = (requests: Request[]) => requests.filter(r => r.method !== 'GET' && path(r).startsWith(api))

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('一键部署 MCP 管理器', () => {
  it('deploys to a prepared server, shows the progress and selects the new manager', async () => {
    const bodies: Schema['LlmBridgeDeployIn'][] = []
    const { requests, world } = setup('owner', {}, async (r, w) => {
      if (path(r) !== `${api}/deployments` || r.method !== 'POST') return undefined
      bodies.push(await r.json() as Schema['LlmBridgeDeployIn'])
      w.jobs[JOB] = job()
      return Response.json(w.jobs[JOB], { status: 202 })
    })
    await openApp('/llm_bridge?view=manage')
    await fireEvent.click(await screen.findByRole('button', { name: '添加 MCP 管理器' }))
    // 这个工作台能一键部署：默认就是一键部署，也可以改成手动添加
    const modes = await screen.findByRole('group', { name: '添加方式' })
    expect(within(modes).getByRole('button', { name: '一键部署到服务器' })).toHaveAttribute('aria-pressed', 'true')
    const form = await screen.findByRole('form', { name: '一键部署 MCP 管理器' })
    const pick = await within(form).findByLabelText('部署到哪台服务器')
    await waitFor(() => expect(pick).toHaveValue(SERVER))
    // 还不能部署的服务器写明原因
    expect(within(form).getByRole('option', { name: '新机器（不能部署：还没初始化）' })).toBeInTheDocument()
    expect(within(form).getByRole('link', { name: '管理服务器' })).toHaveAttribute('href', '/agent-servers')

    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
    expect(await within(form).findByText('名字要 1–40 个字。')).toBeVisible()
    await fireEvent.update(within(form).getByLabelText('名字'), '1 号')
    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
    expect(await within(form).findByText('已经有同名的 MCP 管理器，换一个名字。')).toBeVisible()
    await fireEvent.update(pick, COLD)
    await fireEvent.update(within(form).getByLabelText('名字'), '2 号')
    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
    expect(await within(form).findByText('这台服务器现在不能部署：还没初始化。')).toBeVisible()
    expect(writes(requests)).toEqual([])

    await fireEvent.update(pick, SERVER)
    const control = within(form).getByRole('group', { name: '控制的平台' })
    for (const name of ['ChatGPT', 'Codex']) await fireEvent.click(within(control).getByLabelText(name))
    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))

    // 进度：每一步的状态和说明
    const progress = await screen.findByRole('region', { name: '部署「2 号」的进度' })
    expect(bodies).toEqual([{ server_id: SERVER, name: '2 号', platforms: ['claude', 'workbuddy'], request_id: expect.stringMatching(/^web-/) }])
    expect(within(progress).getByText('部署「2 号」：进行中')).toBeVisible()
    expect(within(progress).getByText('登录服务器：已登录 deploy@10.0.0.5')).toBeVisible()
    expect(within(progress).getByText('检查服务器').closest('li')).toHaveAttribute('data-status', 'running')

    // 部署完成：管理器列表里多了它，并且选中了它
    world.bridges = [bridge(), managed()]
    world.jobs[JOB] = job({ status: 'succeeded', bridge_id: NEW, mode: 'remote', base_url: 'http://10.0.0.5:18787', finished_at: at, steps: job().steps.map(s => ({ ...s, status: 'done' })) })
    expect(await screen.findByText(/已部署并添加「2 号」。/, {}, { timeout: 5000 })).toBeVisible()
    const card = await screen.findByRole('article', { name: 'MCP 管理器：2 号' })
    expect(card).toHaveAttribute('aria-current', 'true')
    expect(within(card).getByText('工作台部署在服务器「机房一号」上')).toBeVisible()
    expect(within(progress).getByText(/部署完成，已经登记到工作台。/)).toBeVisible()
  })

  it('shows why a deployment failed, with the redacted output, and lets it be retried', async () => {
    const failed = job({ status: 'failed', error_code: 'public_address', error_message: '这台服务器是用公网地址登记的：一键部署不会把 MCP 管理器的端口开到公网上。', finished_at: at, log: '$ 检查服务器（退出码 0）\nBRIDGE_ADMIN_TOKEN=***\n', steps: [step('connect', 'done'), step('inspect', 'failed', '这台服务器是用公网地址登记的')] })
    let posts = 0
    setup('owner', {}, (r, w) => {
      if (path(r) !== `${api}/deployments` || r.method !== 'POST') return undefined
      posts += 1
      if (posts === 2) return fail(409, 'DEPLOY_BUSY')
      w.jobs[JOB] = failed
      return Response.json(failed, { status: 202 })
    })
    await openApp('/llm_bridge?view=manage')
    await fireEvent.click(await screen.findByRole('button', { name: '添加 MCP 管理器' }))
    const form = await screen.findByRole('form', { name: '一键部署 MCP 管理器' })
    await waitFor(() => expect(within(form).getByLabelText('部署到哪台服务器')).toHaveValue(SERVER))
    await fireEvent.update(within(form).getByLabelText('名字'), '2 号')
    await fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
    const progress = await screen.findByRole('region', { name: '部署「2 号」的进度' })
    expect(within(progress).getByRole('alert')).toHaveTextContent('这台服务器是用公网地址登记的：一键部署不会把 MCP 管理器的端口开到公网上。（public_address）')
    expect(within(progress).getByText(/BRIDGE_ADMIN_TOKEN=\*\*\*/)).toBeInTheDocument()
    // 改一下再部署：回到表单，填过的还在
    await fireEvent.click(screen.getByRole('button', { name: '改一下再部署' }))
    const again = await screen.findByRole('form', { name: '一键部署 MCP 管理器' })
    expect(within(again).getByLabelText('名字')).toHaveValue('2 号')
    await fireEvent.click(within(again).getByRole('button', { name: '开始部署' }))
    expect(await within(again).findByRole('alert')).toHaveTextContent('请求失败')
  })

  it('redeploys and uninstalls a manager the workbench deployed', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const bodies: unknown[] = []
    const { requests, world } = setup('owner', { bridges: [bridge(), managed()] }, async (r, w) => {
      const p = path(r)
      if (r.method !== 'POST' || !p.startsWith(`${api}/bridges/${NEW}/`)) return undefined
      const kind = p.endsWith('/redeploy') ? 'redeploy' : 'uninstall'
      bodies.push([kind, await r.json()])
      const steps = kind === 'redeploy' ? job().steps.slice(0, 6) : [step('connect', 'done'), step('stop', 'done', '容器已删除，数据卷保留'), step('remove', 'done'), step('unregister', 'done')]
      w.jobs[JOB] = job({ kind, status: 'succeeded', bridge_id: kind === 'redeploy' ? NEW : null, finished_at: at, steps: steps.map(s => ({ ...s, status: 'done' })) })
      if (kind === 'uninstall') w.bridges = [bridge()]
      return Response.json(w.jobs[JOB], { status: 202 })
    })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: 'MCP 管理器：2 号' })
    // 工作台部署的：有“重新部署”“卸载”；手动添加的只有“删除”
    expect(within(card).queryByRole('button', { name: '删除2 号' })).not.toBeInTheDocument()
    const manual = screen.getByRole('article', { name: 'MCP 管理器：1 号' })
    expect(within(manual).getByRole('button', { name: '删除1 号' })).toBeVisible()
    expect(within(manual).queryByRole('button', { name: '重新部署1 号' })).not.toBeInTheDocument()

    await fireEvent.click(within(card).getByRole('button', { name: '重新部署2 号' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('登录状态和会话记录都保留'))
    expect(await screen.findByText('重新部署完成：登录状态和会话记录都保留着。')).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '关闭' }))

    // 卸载：先说明会发生什么，可以选连数据一起删
    await fireEvent.click(within(card).getByRole('button', { name: '卸载2 号' }))
    const form = await screen.findByRole('form', { name: '卸载2 号' })
    expect(within(form).getByText(/停掉并删除这个容器，再从工作台去掉它/)).toBeVisible()
    expect(writes(requests)).toHaveLength(1)
    await fireEvent.click(within(form).getByLabelText(/同时删除它的数据/))
    await fireEvent.click(within(form).getByRole('button', { name: '卸载' }))
    expect(await screen.findByText('卸载完成。')).toBeVisible()
    await waitFor(() => expect(screen.queryByRole('article', { name: 'MCP 管理器：2 号' })).not.toBeInTheDocument())
    expect(bodies).toEqual([
      ['redeploy', { request_id: expect.stringMatching(/^web-/) }],
      ['uninstall', { request_id: expect.stringMatching(/^web-/), remove_data: true }],
    ])
    expect(world.bridges).toHaveLength(1)
  })

  it('offers only the manual form when the workbench cannot deploy, and nothing to an admin', async () => {
    const { requests } = setup('owner', { available: false })
    const view = await openApp('/llm_bridge?view=manage')
    await fireEvent.click(await screen.findByRole('button', { name: '添加 MCP 管理器' }))
    expect(await screen.findByRole('form', { name: '添加 MCP 管理器' })).toBeVisible()
    expect(screen.queryByRole('group', { name: '添加方式' })).not.toBeInTheDocument()
    expect(requests.some(r => path(r) === '/api/v1/agents/servers')).toBe(false)
    view.unmount()

    setup('admin', { bridges: [bridge(), managed()] })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: 'MCP 管理器：2 号' })
    expect(within(card).getByText('工作台部署在服务器「机房一号」上')).toBeVisible()
    expect(within(card).queryByRole('button', { name: '卸载2 号' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '添加 MCP 管理器' })).not.toBeInTheDocument()
  })

  it('lets the owner switch to the manual form', async () => {
    setup('owner')
    await openApp('/llm_bridge?view=manage')
    await fireEvent.click(await screen.findByRole('button', { name: '添加 MCP 管理器' }))
    await fireEvent.click(await within(await screen.findByRole('group', { name: '添加方式' })).findByRole('button', { name: '手动添加（已经部署好了）' }))
    const form = await screen.findByRole('form', { name: '添加 MCP 管理器' })
    expect(within(form).getByLabelText('管理令牌')).toBeVisible()
    expect(screen.queryByRole('form', { name: '一键部署 MCP 管理器' })).not.toBeInTheDocument()
  })
})
