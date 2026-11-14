// 页面上一键部署 MCP 管理器（T-0234，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md；src/modules/llm_bridge 的
// DeployManager / DeployJob / ManagerCard / ManagerOperation）：两类服务器（智能体服务器、容器服务器）的部署表单、
// 登录密码（sudo 要密码时）、进度、从服务器页面跳过来时选好服务器；工作台部署的管理器的重新部署、重启、卸载
// （先问密码、409 后带密码重发、只删除登记时写明服务器上还留着什么）；不能一键部署时只有手动添加、admin 不能操作。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { fail, me, mockApi, newsModule, openApp, workspace } from './helpers'
import type { Schema } from '../src/services/api'

type Role = Schema['WorkspaceOut']['role']
type Bridge = Schema['LlmBridgeOut']
type Job = Schema['LlmBridgeDeployJobOut']
type Target = Schema['LlmBridgeDeployTargetOut']
type Install = Schema['LlmBridgeInstallOut']
type DeployIn = Schema['LlmBridgeDeployIn']
const at = '2026-11-14T00:00:00Z'
const BRIDGE = '11111111-1111-4111-8111-111111111111', NEW = '44444444-4444-4444-8444-444444444444'
const SERVER = '55555555-5555-4555-8555-555555555555', COLD = '66666666-6666-4666-8666-666666666666', HOST = '88888888-8888-4888-8888-888888888888'
const JOB = '77777777-7777-4777-8777-777777777777', JOB2 = '99999999-9999-4999-8999-999999999999', JOB3 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const PASSWORD = 'login-password-for-sudo'
const ALL = ['chatgpt', 'codex', 'claude', 'workbuddy']
const api = '/api/v1/llm_bridge'
const path = (r: Request) => new URL(r.url).pathname
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })
const lbModule: Schema['ModuleOut'] = { ...newsModule, key: 'llm_bridge', name: '大模型客户端', tools: [], widgets: [] }
const bridge = (over: Partial<Bridge> = {}): Bridge => ({ id: BRIDGE, name: '1 号', base_url: 'http://llm-bridge:8787', enabled: true, has_token: true, deployment: null, created_at: at, updated_at: at, ...over })
const install = (over: Partial<Install> = {}): Install => ({ target_kind: 'agent_server', target_id: SERVER, server_name: '机房一号', mode: 'tunnel', port: 18787, needs_sudo: false, outdated: false, deployed_at: at, ...over })
const managed = (over: Partial<Bridge> = {}) => bridge({ id: NEW, name: '2 号', base_url: 'http://127.0.0.1:18787', deployment: install(), ...over })
const target = (over: Partial<Target> = {}): Target => ({ kind: 'agent_server', id: SERVER, name: '机房一号', host: '10.0.0.5', deployable: true, block_reason: null, block_message: null, may_need_password: false, managers: 0, ...over })
const cold = target({ id: COLD, name: '新机器', host: '10.0.0.6', deployable: false, block_reason: 'not_initialized', block_message: '这台服务器还没有初始化（安装 Docker）' })
// 容器服务器：登录账号不是 root，sudo 可能要密码
const container = target({ kind: 'runner_host', id: HOST, name: '容器一号', host: '10.0.0.9', may_need_password: true })
const step = (key: string, status: string, detail: string | null = null) => ({ key, status, detail })
type Step = ReturnType<typeof step>
// 新部署的八步：刚登录上、正在检查
const STEPS: Step[] = [step('connect', 'done', '已登录 deploy@10.0.0.5'), step('inspect', 'running'), step('upload', 'pending'), step('build', 'pending'), step('start', 'pending'), step('tunnel', 'pending'), step('verify', 'pending'), step('register', 'pending')]
const job = (over: Partial<Job> = {}): Job => ({
  id: JOB, kind: 'deploy', status: 'running', target_kind: 'agent_server', target_id: SERVER, server_name: '机房一号', bridge_id: null, name: '2 号', platforms: ['claude', 'workbuddy'],
  mode: null, log: '', error_code: null, error_message: null, started_at: at, finished_at: null, steps: STEPS, ...over,
})
const done = (steps: Step[]) => steps.map(s => ({ ...s, status: 'done' }))
const status: Schema['LlmBridgeStatusOut'] = { reachable: true, error: '', version: '0.1.0', platforms: ['claude', 'workbuddy'], queued: 0, running: 0, load: {}, settings: null, accounts: [], channels: [] }

interface World { bridges: Bridge[]; jobs: Record<string, Job>; available: boolean; targets: Target[] }
function setup(role: Role, given: Partial<World> = {}, handler?: (r: Request, world: World) => Response | undefined | Promise<Response | undefined>) {
  const world: World = { bridges: [bridge()], jobs: {}, available: true, targets: [target({ managers: 1 }), cold, container], ...given }
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
    // 能部署到的服务器：写在“单个任务”前面，/deployments/targets 不是一个任务
    if (p === `${api}/deployments/targets` && r.method === 'GET') return Response.json({ targets: world.targets, available: world.available })
    if (p === `${api}/deployments` && r.method === 'GET') return Response.json({ jobs: Object.values(world.jobs).map(j => ({ ...j, log: null })), available: world.available })
    const one = /\/deployments\/([^/]+)$/.exec(p)
    if (one && r.method === 'GET') return world.jobs[one[1]!] ? Response.json(world.jobs[one[1]!]) : fail(404, 'NOT_FOUND')
    return undefined
  })
  return { requests, world }
}
const writes = (requests: Request[]) => requests.filter(r => r.method !== 'GET' && path(r).startsWith(api))
const card = (name: string) => screen.findByRole('article', { name: `MCP 管理器：${name}` })
async function openDeployForm() {
  await fireEvent.click(await screen.findByRole('button', { name: '添加 MCP 管理器' }))
  return screen.findByRole('form', { name: '一键部署 MCP 管理器' })
}
const start = (form: HTMLElement) => fireEvent.click(within(form).getByRole('button', { name: '开始部署' }))
/** 重新部署 / 重启 / 卸载的假接口：记下请求体，按 answer 的结果回一个任务（或一个错误） */
function operations(bodies: [string, string, Record<string, unknown>][], answer: (kind: string, bridgeId: string, body: Record<string, unknown>, world: World) => Job | Response) {
  return async (r: Request, world: World) => {
    const found = /\/bridges\/([^/]+)\/(redeploy|restart|uninstall)$/.exec(path(r))
    if (r.method !== 'POST' || !found) return undefined
    const body = await r.json() as Record<string, unknown>
    bodies.push([found[2]!, found[1]!, body])
    const result = answer(found[2]!, found[1]!, body, world)
    if (result instanceof Response) return result
    world.jobs[result.id] = result
    return Response.json({ ...result, log: null }, { status: 202 })
  }
}
const requestId = expect.stringMatching(/^web-/)

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('一键部署 MCP 管理器', () => {
  it('deploys to an agent server, shows the progress and selects the new manager', async () => {
    const bodies: DeployIn[] = []
    const { requests, world } = setup('owner', {}, async (r, w) => {
      if (path(r) !== `${api}/deployments` || r.method !== 'POST') return undefined
      bodies.push(await r.json() as DeployIn)
      w.jobs[JOB] = job()
      return Response.json(w.jobs[JOB], { status: 202 })
    })
    await openApp('/llm_bridge?view=manage')
    await fireEvent.click(await screen.findByRole('button', { name: '添加 MCP 管理器' }))
    // 这个工作台能一键部署：默认就是一键部署，也可以改成手动添加
    const modes = await screen.findByRole('group', { name: '添加方式' })
    expect(within(modes).getByRole('button', { name: '一键部署到服务器' })).toHaveAttribute('aria-pressed', 'true')
    const form = await screen.findByRole('form', { name: '一键部署 MCP 管理器' })
    // 先选服务器类型，再选服务器：只列这一类
    const pick = await within(form).findByLabelText('部署到哪台服务器')
    expect(within(form).getByRole('radio', { name: '智能体服务器' })).toBeChecked()
    await waitFor(() => expect(pick).toHaveValue(SERVER))
    expect(within(pick).getAllByRole('option').map(o => o.textContent)).toEqual(['选一台服务器', '机房一号 · 已有 1 个 MCP 管理器', '新机器 · 不能部署：这台服务器还没有初始化（安装 Docker）'])
    // 还不能部署的服务器置灰并写明原因
    expect(within(pick).getByRole('option', { name: /^新机器/ })).toBeDisabled()
    expect(within(form).getByRole('link', { name: '管理服务器' })).toHaveAttribute('href', '/agent-servers')
    // 智能体服务器的登录账号能直接用 Docker：不问密码
    expect(within(form).queryByLabelText('登录密码')).not.toBeInTheDocument()

    await start(form)
    expect(await within(form).findByText('名字要 1–40 个字。')).toBeVisible()
    await fireEvent.update(within(form).getByLabelText('名字'), '1 号')
    await start(form)
    expect(await within(form).findByText('已经有同名的 MCP 管理器，换一个名字。')).toBeVisible()
    await fireEvent.update(within(form).getByLabelText('名字'), '2 号')
    const control = within(form).getByRole('group', { name: '控制的平台' })
    for (const name of ['ChatGPT', 'Codex', 'Claude', 'WorkBuddy']) await fireEvent.click(within(control).getByLabelText(name))
    await start(form)
    expect(await within(form).findByText('至少勾选一个要控制的平台。')).toBeVisible()
    expect(writes(requests)).toEqual([])
    for (const name of ['WorkBuddy', 'Claude']) await fireEvent.click(within(control).getByLabelText(name))
    await start(form)

    // 进度：每一步的状态和说明
    const progress = await screen.findByRole('region', { name: '部署「2 号」的进度' })
    expect(bodies).toEqual([{ target_kind: 'agent_server', target_id: SERVER, name: '2 号', platforms: ['claude', 'workbuddy'], request_id: requestId }])
    expect(within(progress).getByText('部署「2 号」：进行中')).toBeVisible()
    expect(progress).toHaveTextContent('智能体服务器「机房一号」')
    expect(within(progress).getByText('登录服务器：已登录 deploy@10.0.0.5')).toBeVisible()
    expect(within(progress).getByText('检查服务器').closest('li')).toHaveAttribute('data-status', 'running')
    expect(within(progress).getByText('安装转发密钥').closest('li')).toHaveAttribute('data-status', 'pending')

    // 部署完成：管理器列表里多了它，并且选中了它；卡片写明部署在哪、用哪种网络方式
    world.bridges = [bridge(), managed()]
    world.jobs[JOB] = job({ status: 'succeeded', bridge_id: NEW, mode: 'tunnel', finished_at: at, steps: done(STEPS) })
    expect(await screen.findByText(/已部署并添加「2 号」。/, {}, { timeout: 5000 })).toBeVisible()
    const added = await card('2 号')
    expect(added).toHaveAttribute('aria-current', 'true')
    expect(within(added).getByText('部署在智能体服务器「机房一号」 · 经 SSH 通道（服务器本机端口 18787）')).toBeVisible()
    expect(within(added).queryByText('http://127.0.0.1:18787')).not.toBeInTheDocument()
    const finished = screen.getByRole('region', { name: '部署「2 号」的进度' })
    expect(within(finished).getByText(/部署完成，已经登记到工作台。/)).toBeVisible()
    expect(finished).toHaveTextContent('经 SSH 通道')
  })

  it('deploys to a container server and asks for the login password when sudo needs one', async () => {
    const bodies: DeployIn[] = []
    const onHost = (over: Partial<Job>) => job({ target_kind: 'runner_host', target_id: HOST, server_name: '容器一号', name: '3 号', platforms: ALL, ...over })
    const refused = (id: string, code: string, message: string) => onHost({ id, status: 'failed', error_code: code, error_message: message, finished_at: at, steps: [step('connect', 'done'), step('inspect', 'failed', message)] })
    const { world } = setup('owner', {}, async (r, w) => {
      if (path(r) !== `${api}/deployments` || r.method !== 'POST') return undefined
      const body = await r.json() as DeployIn
      bodies.push(body)
      const id = bodies.length === 1 ? JOB : JOB2
      w.jobs[id] = body.sudo_password ? onHost({ id }) : refused(id, 'sudo_password_required', '这台服务器的登录账号用 Docker 要 sudo 密码：填写登录密码后重试（只用这一次，不保存）')
      return Response.json(w.jobs[id], { status: 202 })
    })
    await openApp('/llm_bridge?view=manage')
    const form = await openDeployForm()
    await waitFor(() => expect(within(form).getByLabelText('部署到哪台服务器')).toHaveValue(SERVER))
    await fireEvent.click(within(form).getByRole('radio', { name: '容器服务器' }))
    await waitFor(() => expect(within(form).getByLabelText('部署到哪台服务器')).toHaveValue(HOST))
    expect(within(within(form).getByLabelText('部署到哪台服务器')).getAllByRole('option').map(o => o.textContent)).toEqual(['选一台服务器', '容器一号'])
    expect(within(form).getByRole('link', { name: '管理服务器' })).toHaveAttribute('href', '/runner')
    // 登录账号不是 root 的容器服务器：多一个可以不填的“登录密码”
    expect(within(form).getByLabelText('登录密码')).toHaveAttribute('type', 'password')
    expect(within(form).getByText('这台服务器的登录账号用 Docker 要 sudo 密码时填：只用这一次，不保存。免密 sudo 或 root 账号不用填。')).toBeVisible()
    await fireEvent.update(within(form).getByLabelText('名字'), '3 号')
    await start(form)

    // 没填密码，而那台服务器的 sudo 要密码：任务失败，回到表单提示填密码
    const again = await screen.findByRole('form', { name: '一键部署 MCP 管理器' })
    expect(await within(again).findByText(/这台服务器的登录账号用 Docker 要 sudo 密码。在下面填写登录密码后再部署/)).toBeVisible()
    expect(within(again).getByLabelText('名字')).toHaveValue('3 号')
    expect(within(again).getByLabelText('部署到哪台服务器')).toHaveValue(HOST)
    expect(bodies[0]).toEqual({ target_kind: 'runner_host', target_id: HOST, name: '3 号', platforms: ALL, request_id: requestId })
    await start(again)
    expect(await within(again).findByText('填写这台服务器的登录密码。')).toBeVisible()
    expect(bodies).toHaveLength(1)

    // 填了密码再提交：带上密码，换一个幂等号
    await fireEvent.update(within(again).getByLabelText('登录密码'), PASSWORD)
    await start(again)
    expect(await screen.findByRole('region', { name: '部署「3 号」的进度' })).toHaveTextContent('容器服务器「容器一号」')
    expect(bodies[1]).toEqual({ target_kind: 'runner_host', target_id: HOST, name: '3 号', platforms: ALL, request_id: requestId, sudo_password: PASSWORD })
    expect(bodies[1]!.request_id).not.toBe(bodies[0]!.request_id)
    expect(document.body.innerHTML).not.toContain(PASSWORD)

    // 密码不对：任务失败（sudo_denied），再回到表单；密码框是空的
    world.jobs[JOB2] = refused(JOB2, 'sudo_denied', '这个账号不能使用 sudo（或密码不对）')
    expect(await screen.findByText(/登录密码不对，或者这个账号不能使用 sudo。重新填写登录密码后再部署。/, {}, { timeout: 5000 })).toBeVisible()
    const third = screen.getByRole('form', { name: '一键部署 MCP 管理器' })
    expect(within(third).getByLabelText('登录密码')).toHaveValue('')
    expect(within(third).getByLabelText('名字')).toHaveValue('3 号')
  })

  it('opens the deploy form with the server already chosen when it comes from a server page', async () => {
    // 这个工作台只有一台容器服务器，没有智能体服务器
    const { requests } = setup('owner', { targets: [container] })
    const { router } = await openApp(`/llm_bridge?view=manage&deploy=runner_host:${HOST}`)
    const form = await screen.findByRole('form', { name: '一键部署 MCP 管理器' })
    await waitFor(() => expect(within(form).getByLabelText('部署到哪台服务器')).toHaveValue(HOST))
    expect(within(form).getByRole('radio', { name: '容器服务器' })).toBeChecked()
    expect(within(form).getByLabelText('登录密码')).toBeVisible()
    // 参数用过就从地址里去掉：刷新、后退不会再打开一次
    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/llm_bridge?view=manage'))
    expect(requests.find(r => path(r) === `${api}/deployments/targets`)!.headers.get('X-Workspace-Id')).toBe('workspace-a')

    // 另一类一台都没有：写明去哪个页面添加
    await fireEvent.click(within(form).getByRole('radio', { name: '智能体服务器' }))
    expect(await within(form).findByText('还没有智能体服务器。')).toBeVisible()
    expect(within(form).getByRole('link', { name: '去智能体服务器页' })).toHaveAttribute('href', '/agent-servers')
    expect(within(form).queryByRole('button', { name: '开始部署' })).not.toBeInTheDocument()
    expect(writes(requests)).toEqual([])
  })

  it('says why the chosen server cannot take a deployment, and falls back to the kind that has servers', async () => {
    const { requests } = setup('owner', { targets: [target(), cold] })
    const view = await openApp(`/llm_bridge?view=manage&deploy=agent_server:${COLD}`)
    const form = await screen.findByRole('form', { name: '一键部署 MCP 管理器' })
    await waitFor(() => expect(within(form).getByLabelText('部署到哪台服务器')).toHaveValue(COLD))
    expect(within(form).getByRole('note')).toHaveTextContent('这台服务器现在不能部署：这台服务器还没有初始化（安装 Docker）。')
    await fireEvent.update(within(form).getByLabelText('名字'), '2 号')
    await start(form)
    expect(await within(form).findByRole('alert')).toHaveTextContent('这台服务器现在不能部署：这台服务器还没有初始化（安装 Docker）。')
    expect(writes(requests)).toEqual([])
    // 没有容器服务器：写明去容器服务器页添加
    await fireEvent.click(within(form).getByRole('radio', { name: '容器服务器' }))
    expect(await within(form).findByText('还没有容器服务器。')).toBeVisible()
    expect(within(form).getByRole('link', { name: '去容器服务器页' })).toHaveAttribute('href', '/runner')
    view.unmount()

    // 没有指定服务器、智能体服务器一台都没有：默认换到有服务器的那一类
    setup('owner', { targets: [container] })
    await openApp('/llm_bridge?view=manage')
    const other = await openDeployForm()
    await waitFor(() => expect(within(other).getByLabelText('部署到哪台服务器')).toHaveValue(HOST))
    expect(within(other).getByRole('radio', { name: '容器服务器' })).toBeChecked()
  })

  it('shows why a deployment failed, with the redacted output, and lets it be retried', async () => {
    const failed = job({ status: 'failed', error_code: 'verify_failed', error_message: '容器启动了，但工作台连不上它：这台服务器的 sshd 不允许端口转发。', finished_at: at, log: '$ 检查服务器（退出码 0）\nBRIDGE_ADMIN_TOKEN=***\n', steps: [...done(STEPS.slice(0, 6)), step('verify', 'failed', '工作台连不上它'), step('register', 'pending')] })
    let posts = 0
    setup('owner', {}, (r, w) => {
      if (path(r) !== `${api}/deployments` || r.method !== 'POST') return undefined
      posts += 1
      if (posts === 2) return fail(409, 'DEPLOY_BUSY')
      w.jobs[JOB] = failed
      return Response.json(failed, { status: 202 })
    })
    await openApp('/llm_bridge?view=manage')
    const form = await openDeployForm()
    await waitFor(() => expect(within(form).getByLabelText('部署到哪台服务器')).toHaveValue(SERVER))
    await fireEvent.update(within(form).getByLabelText('名字'), '2 号')
    await start(form)
    const progress = await screen.findByRole('region', { name: '部署「2 号」的进度' })
    expect(within(progress).getByRole('alert')).toHaveTextContent('容器启动了，但工作台连不上它：这台服务器的 sshd 不允许端口转发。（verify_failed）')
    expect(within(progress).getByText('工作台连接管理器：工作台连不上它').closest('li')).toHaveAttribute('data-status', 'failed')
    expect(within(progress).getByText(/BRIDGE_ADMIN_TOKEN=\*\*\*/)).toBeInTheDocument()
    // 改一下再部署：回到表单，填过的还在
    await fireEvent.click(screen.getByRole('button', { name: '改一下再部署' }))
    const again = await screen.findByRole('form', { name: '一键部署 MCP 管理器' })
    expect(within(again).getByLabelText('名字')).toHaveValue('2 号')
    await start(again)
    expect(await within(again).findByRole('alert')).toHaveTextContent('请求失败')
  })

  it('offers only the manual form when the workbench cannot deploy, and nothing to an admin', async () => {
    const { requests } = setup('owner', { available: false })
    const view = await openApp(`/llm_bridge?view=manage&deploy=agent_server:${SERVER}`)
    await fireEvent.click(await screen.findByRole('button', { name: '添加 MCP 管理器' }))
    expect(await screen.findByRole('form', { name: '添加 MCP 管理器' })).toBeVisible()
    expect(screen.queryByRole('group', { name: '添加方式' })).not.toBeInTheDocument()
    expect(requests.some(r => path(r) === `${api}/deployments/targets`)).toBe(false)
    view.unmount()

    setup('admin', { bridges: [bridge(), managed()] })
    await openApp(`/llm_bridge?view=manage&deploy=agent_server:${SERVER}`)
    const deployed = await card('2 号')
    expect(within(deployed).getByText('部署在智能体服务器「机房一号」 · 经 SSH 通道（服务器本机端口 18787）')).toBeVisible()
    for (const name of ['重新部署2 号', '重启2 号', '卸载2 号', '改名2 号']) expect(within(deployed).queryByRole('button', { name })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '添加 MCP 管理器' })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: '一键部署 MCP 管理器' })).not.toBeInTheDocument()
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

describe('工作台部署的 MCP 管理器', () => {
  it('redeploys, restarts and uninstalls it from the card', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const bodies: [string, string, Record<string, unknown>][] = []
    const ids: Record<string, string> = { redeploy: JOB, restart: JOB2, uninstall: JOB3 }
    const steps: Record<string, Step[]> = {
      redeploy: done(STEPS.filter(s => !['tunnel', 'register'].includes(s.key))),
      restart: [step('connect', 'done'), step('restart', 'done'), step('verify', 'done', '工作台已连上（经 SSH 通道）')],
      uninstall: [step('connect', 'done'), step('stop', 'done', '容器和数据卷已删除'), step('remove', 'done', '部署目录已删除，转发密钥已从服务器上删除'), step('unregister', 'done', '已从工作台去掉「2 号」')],
    }
    const { requests, world } = setup('owner', { bridges: [bridge(), managed({ deployment: install({ outdated: true }) })] }, operations(bodies, (kind, _id, _body, w) => {
      if (kind === 'uninstall') w.bridges = [bridge()]
      return job({ id: ids[kind]!, kind, status: 'succeeded', bridge_id: kind === 'uninstall' ? null : NEW, mode: 'tunnel', finished_at: at, steps: steps[kind]! })
    }))
    await openApp('/llm_bridge?view=manage')
    const deployed = await card('2 号')
    // 工作台部署的：重新部署、重启、卸载、改名；没有“删除”和“修改”（地址和令牌由部署管理）
    expect(within(deployed).getByRole('note')).toHaveTextContent('工作台带的版本比它新：可以“重新部署”来升级')
    for (const name of ['删除2 号', '修改2 号']) expect(within(deployed).queryByRole('button', { name })).not.toBeInTheDocument()
    const manual = screen.getByRole('article', { name: 'MCP 管理器：1 号' })
    for (const name of ['删除1 号', '修改1 号']) expect(within(manual).getByRole('button', { name })).toBeVisible()
    for (const name of ['重新部署1 号', '重启1 号', '卸载1 号', '改名1 号']) expect(within(manual).queryByRole('button', { name })).not.toBeInTheDocument()
    expect(within(manual).getByText('http://llm-bridge:8787')).toBeVisible()

    // 重新部署：先说明会发生什么，确认后才发请求；这台服务器不要 sudo 密码，不问
    await fireEvent.click(within(deployed).getByRole('button', { name: '重新部署2 号' }))
    const redeploy = await screen.findByRole('form', { name: '重新部署2 号' })
    expect(within(redeploy).getByText(/登录状态和会话记录都保留/)).toBeVisible()
    expect(within(redeploy).queryByLabelText('登录密码')).not.toBeInTheDocument()
    expect(writes(requests)).toEqual([])
    await fireEvent.click(within(redeploy).getByRole('button', { name: '重新部署' }))
    expect(await screen.findByText('重新部署完成：登录状态和会话记录都保留着。')).toBeVisible()
    expect(screen.queryByRole('form', { name: '重新部署2 号' })).not.toBeInTheDocument()

    // 重启
    await fireEvent.click(within(await card('2 号')).getByRole('button', { name: '重启2 号' }))
    const restart = await screen.findByRole('form', { name: '重启2 号' })
    expect(within(restart).getByText(/正在进行的回合会中断/)).toBeVisible()
    await fireEvent.click(within(restart).getByRole('button', { name: '重启' }))
    expect(await screen.findByText('重启完成：工作台已经重新连上它。')).toBeVisible()
    const progress = screen.getByRole('region', { name: '重启「2 号」的进度' })
    expect(within(progress).getByText('重启容器').closest('li')).toHaveAttribute('data-status', 'done')

    // 卸载：先说明会发生什么，可以选连数据卷一起删
    await fireEvent.click(within(await card('2 号')).getByRole('button', { name: '卸载2 号' }))
    const uninstall = await screen.findByRole('form', { name: '卸载2 号' })
    expect(within(uninstall).getByText(/停掉并删除这个容器、删除部署目录和转发密钥，再从工作台去掉它/)).toBeVisible()
    expect(within(uninstall).getByLabelText('只删除登记（服务器连不上时用）')).not.toBeChecked()
    expect(writes(requests)).toHaveLength(2)
    await fireEvent.click(within(uninstall).getByLabelText(/^同时删除数据卷/))
    await fireEvent.click(within(uninstall).getByRole('button', { name: '卸载' }))
    expect(await screen.findByText('卸载完成。')).toBeVisible()
    await waitFor(() => expect(screen.queryByRole('article', { name: 'MCP 管理器：2 号' })).not.toBeInTheDocument())
    expect(bodies).toEqual([
      ['redeploy', NEW, { request_id: requestId }],
      ['restart', NEW, { request_id: requestId }],
      ['uninstall', NEW, { request_id: requestId, remove_data: true, force: false }],
    ])
    expect(world.bridges).toHaveLength(1)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('asks for the login password first when the server needs sudo, and again after a 409 or a wrong password', async () => {
    const bodies: [string, string, Record<string, unknown>][] = []
    const onHost = install({ target_kind: 'runner_host', target_id: HOST, server_name: '容器一号', needs_sudo: true })
    const { requests } = setup('owner', { bridges: [managed({ deployment: onHost }), managed({ id: BRIDGE, name: '3 号' })] }, operations(bodies, (kind, id, body) => {
      if (!body.sudo_password) return fail(409, 'SUDO_PASSWORD_REQUIRED')
      const name = id === NEW ? '2 号' : '3 号'
      if (body.sudo_password !== PASSWORD) return job({ id: JOB, kind, name, status: 'failed', bridge_id: id, error_code: 'sudo_denied', error_message: '这个账号不能使用 sudo（或密码不对）', finished_at: at, steps: [step('connect', 'done'), step('restart', 'failed')] })
      return job({ id: kind === 'restart' ? JOB2 : JOB3, kind, name, status: 'succeeded', bridge_id: id, mode: 'tunnel', finished_at: at, steps: [step('connect', 'done')] })
    }))
    await openApp('/llm_bridge?view=manage')
    const sudo = await card('2 号')
    expect(within(sudo).getByText('部署在容器服务器「容器一号」 · 经 SSH 通道（服务器本机端口 18787）')).toBeVisible()

    // 部署时记下了这台服务器用 Docker 要 sudo 密码：操作前先问一次，不填不发
    await fireEvent.click(within(sudo).getByRole('button', { name: '重启2 号' }))
    const restart = await screen.findByRole('form', { name: '重启2 号' })
    expect(within(restart).getByLabelText('登录密码')).toHaveAttribute('type', 'password')
    await fireEvent.click(within(restart).getByRole('button', { name: '重启' }))
    expect(await within(restart).findByRole('alert')).toHaveTextContent('填写这台服务器的登录密码（只用这一次，不保存）。')
    expect(writes(requests)).toEqual([])
    // 密码不对：任务失败（sudo_denied），确认框重新打开，再填一次
    await fireEvent.update(within(restart).getByLabelText('登录密码'), 'wrong-password')
    await fireEvent.click(within(restart).getByRole('button', { name: '重启' }))
    expect(await screen.findByText('刚才没有成功：登录密码不对，或者这个账号不能使用 sudo。重新填写登录密码后再试。', {}, { timeout: 5000 })).toBeVisible()
    const retry = screen.getByRole('form', { name: '重启2 号' })
    expect(screen.getByRole('region', { name: '重启「2 号」的进度' })).toHaveTextContent('这个账号不能使用 sudo（或密码不对）（sudo_denied）')
    expect(within(retry).getByLabelText('登录密码')).toHaveValue('')
    await fireEvent.update(within(retry).getByLabelText('登录密码'), PASSWORD)
    await fireEvent.click(within(retry).getByRole('button', { name: '重启' }))
    expect(await screen.findByText('重启完成：工作台已经重新连上它。')).toBeVisible()

    // 没记下、服务器却回答要密码（409）：弹出密码框，填了再发（换一个幂等号）
    await fireEvent.click(within(await card('3 号')).getByRole('button', { name: '重新部署3 号' }))
    const redeploy = await screen.findByRole('form', { name: '重新部署3 号' })
    expect(within(redeploy).queryByLabelText('登录密码')).not.toBeInTheDocument()
    await fireEvent.click(within(redeploy).getByRole('button', { name: '重新部署' }))
    expect(await within(redeploy).findByRole('alert')).toHaveTextContent('这台服务器的登录账号用 Docker 要 sudo 密码：在下面填写登录密码，再点“重新部署”')
    await fireEvent.update(within(redeploy).getByLabelText('登录密码'), PASSWORD)
    await fireEvent.click(within(redeploy).getByRole('button', { name: '重新部署' }))
    expect(await screen.findByText('重新部署完成：登录状态和会话记录都保留着。')).toBeVisible()

    expect(bodies).toEqual([
      ['restart', NEW, { request_id: requestId, sudo_password: 'wrong-password' }],
      ['restart', NEW, { request_id: requestId, sudo_password: PASSWORD }],
      ['redeploy', BRIDGE, { request_id: requestId }],
      ['redeploy', BRIDGE, { request_id: requestId, sudo_password: PASSWORD }],
    ])
    expect(new Set(bodies.map(b => b[2].request_id)).size).toBe(4)
    expect(document.body.innerHTML).not.toContain(PASSWORD)
  })

  it('only removes the registration when asked to, and shows what stays on the server', async () => {
    const left = (name: string) => `服务器「${name}」上还留着：容器和数据卷（Compose 项目 mw-mcp-3）；目录 /home/deploy/myworkspace-llm-bridge/3（里面有管理令牌）；deploy 的 authorized_keys 里备注是 mw-mcp-3 的那一行`
    const bodies: [string, string, Record<string, unknown>][] = []
    const orphan = managed({ id: BRIDGE, name: '3 号', deployment: install({ target_id: null, server_name: '旧机房' }) })
    const { requests } = setup('owner', { bridges: [managed({ deployment: install({ needs_sudo: true }) }), orphan] }, operations(bodies, (_kind, id, _body, w) => {
      const gone = w.bridges.find(b => b.id === id)!
      w.bridges = w.bridges.filter(b => b.id !== id)
      const detail = left(gone.deployment!.server_name)
      // 只删除登记：返回的任务已经结束，只有一步，说明里写着服务器上还留着什么
      return job({ id: id === BRIDGE ? JOB : JOB2, kind: 'uninstall', status: 'succeeded', name: gone.name, target_id: gone.deployment!.target_id, server_name: gone.deployment!.server_name, bridge_id: null, mode: 'tunnel', finished_at: at, steps: [step('unregister', 'done', detail)], log: `只删除了工作台里的登记，没有连服务器。\n${detail}\n` })
    }))
    await openApp('/llm_bridge?view=manage')

    // 服务器已经从工作台移除：卡片写明，只提供“卸载（只删除登记）”
    const lost = await card('3 号')
    expect(within(lost).getByRole('note')).toHaveTextContent('服务器已从工作台移除')
    for (const name of ['重新部署3 号', '重启3 号', '卸载3 号', '删除3 号']) expect(within(lost).queryByRole('button', { name })).not.toBeInTheDocument()
    await fireEvent.click(within(lost).getByRole('button', { name: '卸载3 号（只删除登记）' }))
    const form = await screen.findByRole('form', { name: '卸载3 号' })
    expect(within(form).getByLabelText('只删除登记（服务器连不上时用）')).toBeChecked()
    expect(within(form).getByLabelText('只删除登记（服务器连不上时用）')).toBeDisabled()
    expect(within(form).getByLabelText(/^同时删除数据卷/)).toBeDisabled()
    expect(within(form).getByText(/都留在服务器上，删除后会列出来/)).toBeVisible()
    expect(writes(requests)).toEqual([])
    await fireEvent.click(within(form).getByRole('button', { name: '只删除登记' }))
    const progress = await screen.findByRole('region', { name: '卸载「3 号」的进度' })
    expect(within(progress).getByText(`从工作台去掉登记：${left('旧机房')}`)).toBeVisible()
    expect(within(progress).getByText(/只删除了工作台里的登记，没有连服务器：上面写的那些还留在服务器上/)).toBeVisible()
    await waitFor(() => expect(screen.queryByRole('article', { name: 'MCP 管理器：3 号' })).not.toBeInTheDocument())

    // 服务器连不上时：卸载里勾“只删除登记”——不连服务器，所以不问密码、也不能选删除数据卷
    await fireEvent.click(within(await card('2 号')).getByRole('button', { name: '卸载2 号' }))
    const second = await screen.findByRole('form', { name: '卸载2 号' })
    expect(within(second).getByLabelText('登录密码')).toBeVisible()
    await fireEvent.click(within(second).getByLabelText(/^同时删除数据卷/))
    await fireEvent.click(within(second).getByLabelText('只删除登记（服务器连不上时用）'))
    expect(within(second).getByLabelText(/^同时删除数据卷/)).toBeDisabled()
    expect(within(second).queryByLabelText('登录密码')).not.toBeInTheDocument()
    await fireEvent.click(within(second).getByRole('button', { name: '只删除登记' }))
    expect(await screen.findByText(`从工作台去掉登记：${left('机房一号')}`)).toBeVisible()
    await waitFor(() => expect(screen.queryByRole('article', { name: 'MCP 管理器：2 号' })).not.toBeInTheDocument())
    expect(bodies).toEqual([
      ['uninstall', BRIDGE, { request_id: requestId, remove_data: false, force: true }],
      ['uninstall', NEW, { request_id: requestId, remove_data: false, force: true }],
    ])
  })

  it('lets a deployed manager be renamed but not re-addressed, and selects the manager named in the address', async () => {
    const patches: unknown[] = []
    const { world } = setup('owner', { bridges: [bridge(), managed()] }, async (r, w) => {
      if (r.method !== 'PATCH' || path(r) !== `${api}/bridges/${NEW}`) return undefined
      const body = await r.json() as { name: string }
      patches.push(body)
      w.bridges = [bridge(), managed({ name: body.name })]
      return Response.json(w.bridges[1])
    })
    // 从服务器页面的“管理”跳过来：?m=<管理器> 选中它
    await openApp(`/llm_bridge?view=manage&m=${NEW}`)
    const deployed = await card('2 号')
    await waitFor(() => expect(deployed).toHaveAttribute('aria-current', 'true'))
    expect(screen.getByRole('article', { name: 'MCP 管理器：1 号' })).not.toHaveAttribute('aria-current')

    await fireEvent.click(within(deployed).getByRole('button', { name: '改名2 号' }))
    const form = await screen.findByRole('form', { name: '给 MCP 管理器改名' })
    expect(within(form).queryByLabelText('地址')).not.toBeInTheDocument()
    expect(within(form).queryByLabelText('管理令牌')).not.toBeInTheDocument()
    expect(within(form).getByText('这个管理器是工作台部署的：地址和令牌由部署管理，这里只能改名字。')).toBeVisible()
    await fireEvent.update(within(form).getByLabelText('名字'), '二号')
    await fireEvent.click(within(form).getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存「二号」。')).toBeVisible()
    expect(patches).toEqual([{ name: '二号' }])
    expect(world.bridges[1]!.name).toBe('二号')
    // 选中的还是它；点别的卡片能换过去（地址里的 m 只定位一次）
    expect(await card('二号')).toHaveAttribute('aria-current', 'true')
    await fireEvent.click(screen.getByRole('button', { name: '查看 MCP 管理器：1 号' }))
    expect(screen.getByRole('article', { name: 'MCP 管理器：1 号' })).toHaveAttribute('aria-current', 'true')
  })
})
