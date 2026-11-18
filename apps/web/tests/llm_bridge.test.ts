// 大模型客户端管理页面（docs/v2/LLM_BRIDGE_PLAN.md 第 19 节；模块 src/modules/llm_bridge）：MCP 管理器的添加、
// 每个管理器控制的平台和各自的账号、账号状态与登录、
// Claude 订阅登录的风险确认、设置、开发环境、实例权限、会话，以及模块停用时的显示。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { fail, me, mockApi, newsModule, openApp, workspace } from './helpers'
import type { Schema } from '../src/services/api'

const at = '2026-11-13T00:00:00Z'
const BRIDGE = '11111111-1111-4111-8111-111111111111'
const INSTANCE = '22222222-2222-4222-8222-222222222222'
const OTHER = '33333333-3333-4333-8333-333333333333'
const TOKEN = 'bridge-token-0123456789abcdef0123456789'
const base = `/api/v1/llm_bridge/bridges/${BRIDGE}`
const path = (r: Request) => new URL(r.url).pathname
type Role = Schema['WorkspaceOut']['role']
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })
const lbModule: Schema['ModuleOut'] = { ...newsModule, key: 'llm_bridge', name: '大模型客户端', tools: [], widgets: [] }

const bridge: Schema['LlmBridgeOut'] = { id: BRIDGE, name: '主账号', base_url: 'http://10.0.0.5:8787', enabled: true, has_token: true, created_at: at, updated_at: at }
const account = (over: Partial<Schema['LlmBridgeAccountOut']>): Schema['LlmBridgeAccountOut'] => ({ slot: 'openai', label: 'Codex', enabled: true, state: 'logged_out', registered: false, identity: null, method: null, detail: '', first_login_at: null, login_methods: ['device_code', 'api_key', 'existing'], running: 0, ...over })
const settings: Schema['LlmBridgeSettings'] = { concurrency: 2, min_start_interval_seconds: 2, turn_timeout_seconds: 1800, send_per_hour: 60, max_text_chars: 20000, retention_days: 0, digest_enabled: true, digest_follow_retention: true, claude_subscription_ack: false, auto_resume: true, auto_resume_max: 3, resume_text: '继续意外中断的任务', platforms: ['codex', 'claude', 'workbuddy'] }
const status = (over: Partial<Schema['LlmBridgeStatusOut']> = {}): Schema['LlmBridgeStatusOut'] => ({
  reachable: true, error: '', version: '0.1.0', platforms: ['codex', 'claude', 'workbuddy'], queued: 1, running: 2, load: {}, quota: {}, settings,
  accounts: [
    account({ state: 'logged_in', registered: true, identity: 'a***@example.com', method: 'device_code', first_login_at: 1794528000 }),
    account({ slot: 'claude', label: 'Claude', login_methods: ['api_key', 'oauth_token', 'existing'] }),
    account({ slot: 'workbuddy', label: 'CodeBuddy', state: 'mismatch', registered: true, identity: 'w***', detail: '登录的账号变了', login_methods: ['existing'] }),
  ],
  channels: [{ platform: 'codex', mode: 'dev', slot: 'openai', enabled: true, available: true, note: '' }, { platform: 'claude', mode: 'cloud', slot: 'claude', enabled: true, available: false, note: '云端通道还没有实现' }],
  ...over,
})
const grant = (over: Partial<Schema['LlmBridgeGrantOut']> = {}): Schema['LlmBridgeGrantOut'] => ({ instance_id: INSTANCE, instance_name: '甲', enabled: true, can_write: true, can_account: true, can_takeover: false, platforms: ['codex'], bridge_ids: null, environment_ids: [], ...over })

function setup(role: Role, handler?: (r: Request) => Response | undefined | Promise<Response | undefined>, bridges = [bridge]) {
  return mockApi(async r => {
    const result = await handler?.(r)
    if (result) return result
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return as(role)
    if (p === '/api/v1/modules' && r.method === 'GET') return Response.json({ items: [newsModule, lbModule] })
    if (p === '/api/v1/llm_bridge/bridges' && r.method === 'GET') return Response.json({ bridges })
    if (p === `${base}/status`) return Response.json(status())
    if (p === `${base}/connectivity`) return Response.json({ supported: true, slots: {} })
    if (p === '/api/v1/llm_bridge/grants') return Response.json({ grants: [grant()] })
    // 一键部署（T-0234）在这个文件里按“不可用”处理：添加管理器走手动填写；一键部署见 llm_bridge_deploy.test.ts
    if (p === '/api/v1/llm_bridge/local' && r.method === 'GET') return Response.json({ available: false, bridge_id: null, taken: false })
    if (p === '/api/v1/llm_bridge/deployments' && r.method === 'GET') return Response.json({ jobs: [], available: false })
    if (p === `${base}/environments` && r.method === 'GET') return Response.json({ environments: [] })
    if (p === `${base}/sessions`) return Response.json({ sessions: [], sync_failed: '' })
    return undefined
  })
}
const writes = (requests: Request[]) => requests.filter(r => r.method !== 'GET' && path(r).startsWith('/api/v1/llm_bridge'))
const openTab = async (name: string) => { await fireEvent.click(await screen.findByRole('tab', { name })); return screen.findByRole('tabpanel', { name }) }

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('大模型客户端管理', () => {
  it('shows the registered bridges, the account states and the channels', async () => {
    const requests = setup('owner')
    await openApp('/llm_bridge?view=manage')
    expect(await screen.findByRole('heading', { level: 2, name: '管理器与账号' })).toBeVisible()
    const list = await screen.findByRole('region', { name: 'MCP 管理器' })
    expect(await within(list).findByRole('button', { name: '查看 MCP 管理器：主账号' })).toBeVisible()
    expect(within(list).getByText('http://10.0.0.5:8787')).toBeVisible()

    const openai = await screen.findByRole('article', { name: '账号：Codex' })
    expect(within(openai).getByText('已登录')).toBeVisible()
    expect(within(openai).getByText('a***@example.com')).toBeVisible()
    expect(within(openai).getByText('设备码登录')).toBeVisible()
    expect(within(screen.getByRole('article', { name: '账号：Claude' })).getByText('未登录')).toBeVisible()
    const workbuddy = screen.getByRole('article', { name: '账号：CodeBuddy' })
    expect(within(workbuddy).getByText('账号不一致')).toBeVisible()
    expect(within(workbuddy).getByText(/只能点“更换账号”/)).toBeVisible()
    expect(within(workbuddy).queryByRole('button', { name: '登录CodeBuddy' })).not.toBeInTheDocument()
    const overview = screen.getByRole('group', { name: '运行概览' })
    expect(overview).toHaveTextContent('排队中 1')
    expect(overview).toHaveTextContent('进行中 2')
    // 通道按平台一行：支持的用法（不可用的标出来）、整体可用性、说明
    const channels = screen.getByRole('table', { name: '通道' })
    const claudeRow = within(channels).getByRole('row', { name: /Claude/ })
    expect(claudeRow).toHaveTextContent('云端不可用')
    expect(within(claudeRow).getByText('不可用', { selector: '.lb-avail' })).toBeVisible()
    expect(within(channels).getByText('云端通道还没有实现')).toBeVisible()
    expect(within(within(channels).getByRole('row', { name: /Codex/ })).getByText('可用', { selector: '.lb-avail' })).toBeVisible()
    expect(requests.find(r => path(r) === `${base}/status`)!.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })

  it('explains what a bridge is when none is registered and registers one without keeping the token', async () => {
    let bridges: Schema['LlmBridgeOut'][] = []
    const requests = setup('owner', r => {
      if (path(r) !== '/api/v1/llm_bridge/bridges') return undefined
      if (r.method === 'POST') { bridges = [bridge]; return Response.json(bridge, { status: 201 }) }
      return Response.json({ bridges })
    })
    await openApp('/llm_bridge?view=manage')
    expect(await screen.findByText('还没有 MCP 管理器。')).toBeVisible()
    expect(screen.getByText(/可以添加多个，各自一套账号。$/, { selector: '.lb-empty p' })).toBeVisible()

    await fireEvent.click(screen.getByRole('button', { name: '添加 MCP 管理器' }))
    const form = screen.getByRole('form', { name: '添加 MCP 管理器' })
    await fireEvent.update(within(form).getByLabelText('名字'), '主账号')
    await fireEvent.update(within(form).getByLabelText('地址'), 'http://10.0.0.5:8787')
    await fireEvent.update(within(form).getByLabelText('管理令牌'), 'too-short')
    await fireEvent.click(within(form).getByRole('button', { name: '添加' }))
    expect(await screen.findByText(/令牌至少 32 个字符/)).toBeVisible()
    expect(writes(requests)).toEqual([])

    await fireEvent.update(within(form).getByLabelText('管理令牌'), TOKEN)
    // 控制的平台默认全选；一个都不勾不能添加
    const choose = within(form).getByRole('group', { name: '控制的平台' })
    for (const name of ['Codex', 'Claude', 'CodeBuddy']) expect(within(choose).getByLabelText(name)).toBeChecked()
    for (const name of ['Codex', 'Claude', 'CodeBuddy']) await fireEvent.click(within(choose).getByLabelText(name))
    await fireEvent.click(within(form).getByRole('button', { name: '添加' }))
    expect(await screen.findByText('至少勾选一个要控制的平台。')).toBeVisible()
    expect(writes(requests)).toEqual([])
    // 人类的例子：1 号管理器控制 Claude / CodeBuddy（勾选的先后不影响提交的顺序）
    await fireEvent.click(within(choose).getByLabelText('CodeBuddy'))
    await fireEvent.click(within(choose).getByLabelText('Claude'))
    await fireEvent.click(within(form).getByRole('button', { name: '添加' }))
    expect(await screen.findByText(/^已添加「主账号」。/)).toBeVisible()
    expect(await writes(requests)[0]!.clone().json()).toEqual({ name: '主账号', base_url: 'http://10.0.0.5:8787', token: TOKEN, platforms: ['claude', 'workbuddy'] })
    expect(screen.queryByRole('form', { name: '添加 MCP 管理器' })).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(TOKEN)
    expect(await screen.findByRole('article', { name: '账号：Claude' })).toBeVisible()
  })

  it('gives an admin a read-only view without any write control', async () => {
    const requests = setup('admin', r => (path(r) === `${base}/environments` ? Response.json({ environments: [{ id: 'be_0123456789abcdef0123', platform: 'codex', mode: 'dev', name: '工作台仓库', enabled: true, repo_url: 'https://github.com/o/r', branch: 'main', sandbox: 'workspace_write', network: false, description: '' }] }) : undefined))
    await openApp('/llm_bridge?view=manage')
    expect(await screen.findByText('只有 owner 能修改大模型客户端；你看到的是只读视图。')).toBeVisible()
    await screen.findByRole('article', { name: '账号：Claude' })
    for (const name of [/^添加 MCP 管理器$/, /^修改/, /^删除/, /^停用/, /^登录/, /^退出登录/, /^更换账号/, /管理操作$/]) expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    // 卡片上的启用开关只表示状态
    expect(screen.getByRole('switch', { name: '启用主账号' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '编辑控制的平台' })).not.toBeInTheDocument()
    // 重新检查只是读状态，admin 可以点
    expect(screen.getByRole('button', { name: '重新检查Claude' })).toBeEnabled()

    const grants = await screen.findByRole('group', { name: '实例权限：甲' })
    expect(grants).toBeDisabled()
    const env = await openTab('开发环境')
    expect(await within(env).findByText('工作台仓库')).toBeVisible()
    expect(within(env).getByText('可写工作区')).toBeVisible()
    expect(within(env).queryByRole('button')).not.toBeInTheDocument()
    const set = await openTab('运行设置')
    expect(within(set).getByLabelText('每个平台同时进行的会话数')).toBeDisabled()
    expect(within(set).queryByRole('button', { name: '保存设置' })).not.toBeInTheDocument()
    expect(writes(requests)).toEqual([])
  })

  it('shows the Codex device code (ChatGPT account) in the login progress, polls the state and refreshes the status when it is done', { timeout: 20000 }, async () => {
    // 旧版本的 MCP 管理器：不报告每一步、没有 started_at；“开始登录”直接给网址和验证码（T-0279 起也用逐步进度的样式）
    let polls = 0, loggedIn = false
    const requests = setup('owner', r => {
      if (path(r) === `${base}/status`) return Response.json(status({ accounts: [account({ state: loggedIn ? 'logged_in' : 'logged_out', identity: loggedIn ? 'n***@example.com' : null })] }))
      if (path(r) !== `${base}/accounts/openai/login`) return undefined
      if (r.method === 'POST') return Response.json({ state: 'pending', verification_url: 'https://auth.example.com/device', user_code: 'ABCD-1234', detail: '' })
      polls += 1
      if (polls >= 3) loggedIn = true
      return Response.json({ state: loggedIn ? 'done' : 'pending', detail: '' })
    })
    const view = await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：Codex' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录Codex' }))
    const form = within(card).getByRole('form', { name: '登录Codex' })
    await fireEvent.click(within(form).getByLabelText('设备码登录'))
    await fireEvent.click(within(form).getByRole('button', { name: '获取验证码' }))

    const progress = await screen.findByRole('region', { name: '登录「Codex」的进度' })
    expect(progress).toHaveTextContent('MCP 管理器「主账号」 · 设备码登录')
    const open = await within(progress).findByRole('group', { name: '打开网址并输入验证码' })
    expect(within(open).getByRole('link', { name: 'https://auth.example.com/device' })).toHaveAttribute('rel', 'noopener noreferrer')
    expect(within(open).getByText('ABCD-1234')).toBeVisible()
    const steps = within(progress).getAllByRole('listitem')
    expect(steps.map(li => li.getAttribute('data-status'))).toEqual(['done', 'done', 'done', 'running', 'pending'])
    expect(steps[1]).toHaveTextContent('启动 Codex 的本机程序（codex app-server）')
    expect(steps[2]).toHaveTextContent('向 OpenAI 申请设备码')
    expect(steps[3]).toHaveTextContent('等你在自己的浏览器里打开网址、输入验证码并登录')
    expect(within(progress).getByText('这个 MCP 管理器的版本不报告每一步，重新部署（升级）后就能看到。')).toBeVisible()
    expect(await writes(requests)[0]!.clone().json()).toEqual({ method: 'device_code' })

    expect(await screen.findByText('「Codex」已登录。', { selector: '[role="status"]:not(strong)' }, { timeout: 10000 })).toBeVisible()
    expect(progress).toHaveAttribute('data-status', 'succeeded')
    expect(within(progress).queryByRole('group', { name: '打开网址并输入验证码' })).not.toBeInTheDocument()
    expect(await screen.findByText('n***@example.com')).toBeVisible()

    // 登录完成后不再轮询；离开页面时也不会留下定时器
    const done = polls
    view.unmount()
    await new Promise(resolve => setTimeout(resolve, 3200))
    expect(polls).toBe(done)
  })

  it('shows each step of the Codex device login the MCP manager reports, with its output, then the code and the result', { timeout: 30000 }, async () => {
    const step = (key: string, status: string, detail = '') => ({ key, status, detail, started_at: null, finished_at: null })
    let progress: Record<string, unknown> = { state: 'done', started_at: '2026-11-17T02:00:00+00:00', detail: '', steps: [step('start', 'done')], log: [] }
    let answer: (r: Response) => void = () => {}
    const requests = setup('owner', r => {
      if (path(r) !== `${base}/accounts/openai/login`) return undefined
      if (r.method === 'POST') return new Promise<Response>(resolve => { answer = resolve })
      return Response.json(progress)
    })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：Codex' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录Codex' }))
    const form = within(card).getByRole('form', { name: '登录Codex' })
    await fireEvent.click(within(form).getByLabelText('设备码登录'))
    await fireEvent.click(within(form).getByRole('button', { name: '获取验证码' }))

    const panel = await screen.findByRole('region', { name: '登录「Codex」的进度' })
    expect(panel).toHaveAttribute('data-status', 'running')
    // 管理器接手、正在向 OpenAI 申请设备码：这一步进行中，下面是这边做了什么
    progress = { state: 'pending', started_at: '2026-11-17T03:00:00.120+00:00', detail: '', verification_url: null, user_code: null, steps: [step('start', 'done', '已在运行'), step('request_code', 'running'), step('wait_user', 'pending'), step('finish', 'pending')], log: ['03:00:00 Codex 的本机程序（codex app-server）已在运行', '03:00:00 向 OpenAI 申请设备码（account/login/start，最多等 60 秒；要访问 auth.openai.com）'] }
    await waitFor(() => expect(within(panel).getAllByRole('listitem').map(li => li.getAttribute('data-status'))).toEqual(['done', 'done', 'running', 'pending', 'pending']), { timeout: 5000 })
    expect(within(panel).getByText('已在运行')).toBeVisible()
    expect(within(panel).getByLabelText('最新输出')).toHaveTextContent('向 OpenAI 申请设备码（account/login/start')
    expect(panel).toHaveTextContent('正在执行第 3 / 5 步')
    expect(panel).toHaveTextContent('验证码只显示在上面，不记在这里')

    // 拿到设备码：等你去输入
    const got = { state: 'pending', started_at: '2026-11-17T03:00:00.120+00:00', detail: '', verification_url: 'https://auth.openai.com/codex/device', user_code: 'WXYZ-9876', steps: [step('start', 'done', '已在运行'), step('request_code', 'done', '用时 4.3 秒'), step('wait_user', 'running', 'Codex 会自己向 OpenAI 查登录结果，完成后自动进入下一步'), step('finish', 'pending')], log: [] }
    answer(Response.json(got))
    progress = got
    const open = await within(panel).findByRole('group', { name: '打开网址并输入验证码' })
    expect(within(open).getByRole('link', { name: 'https://auth.openai.com/codex/device' })).toBeVisible()
    expect(within(open).getByText('WXYZ-9876')).toBeVisible()
    expect(within(panel).getByText('用时 4.3 秒')).toBeVisible()
    // 在浏览器里登录完
    progress = { ...got, state: 'done', finished_at: '2026-11-17T03:01:00+00:00', steps: [step('start', 'done'), step('request_code', 'done'), step('wait_user', 'done', '已在浏览器里完成登录'), step('finish', 'done', 'OpenAI 确认登录成功')] }
    await waitFor(() => expect(panel).toHaveAttribute('data-status', 'succeeded'), { timeout: 5000 })
    // 成功后每一步收起来，说明照样在
    expect(panel).toHaveTextContent('OpenAI 确认登录成功')
    expect(within(panel).getByText('「Codex」已登录。')).toBeVisible()
    expect(writes(requests).filter(r => path(r).endsWith('/accounts/openai/login'))).toHaveLength(1)
  })

  it('puts the reason under the failed device-code step and offers a new code', { timeout: 30000 }, async () => {
    const step = (key: string, status: string, detail = '') => ({ key, status, detail, started_at: null, finished_at: null })
    const reason = '向 OpenAI 申请设备码没有成功：error sending request for url。常见原因：这个 MCP 管理器所在的服务器访问不了 OpenAI'
    let started = false
    const requests = setup('owner', r => {
      if (path(r) !== `${base}/accounts/openai/login`) return undefined
      if (r.method === 'POST') { started = true; return Response.json({ error: { code: 'UNAVAILABLE', message: reason, request_id: 'r', details: {} } }, { status: 503 }) }
      return Response.json(!started ? { state: 'none', detail: '' } : { state: 'failed', started_at: '2026-11-17T03:00:00+00:00', finished_at: '2026-11-17T03:00:09+00:00', detail: reason, steps: [step('start', 'done', '已在运行'), step('request_code', 'failed', reason), step('wait_user', 'pending'), step('finish', 'pending')], log: ['03:00:09 申请设备码出错（8.7 秒）：error sending request for url', `03:00:09 失败：${reason}`] })
    })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：Codex' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录Codex' }))
    const form = within(card).getByRole('form', { name: '登录Codex' })
    await fireEvent.click(within(form).getByLabelText('设备码登录'))
    await fireEvent.click(within(form).getByRole('button', { name: '获取验证码' }))

    const panel = await screen.findByRole('region', { name: '登录「Codex」的进度' })
    await waitFor(() => expect(panel).toHaveAttribute('data-status', 'failed'))
    const items = within(panel).getAllByRole('listitem')
    expect(items.map(li => li.getAttribute('data-status'))).toEqual(['done', 'done', 'failed', 'pending', 'pending'])
    expect(within(items[2]!).getByRole('alert')).toHaveTextContent(`失败原因${reason}`)
    await fireEvent.click(within(items[2]!).getByRole('button', { name: '查看命令输出' }))
    expect(panel.querySelector('details')!.textContent).toContain('申请设备码出错（8.7 秒）')
    await fireEvent.click(within(panel).getByRole('button', { name: '重新获取验证码' }))
    await waitFor(() => expect(writes(requests).filter(r => path(r).endsWith('/accounts/openai/login'))).toHaveLength(2))
  })

  it('logs CodeBuddy in through a web page: a link to open, no code, no container login', { timeout: 20000 }, async () => {
    // 旧版本的 MCP 管理器：不报告每一步、没有 started_at；“开始登录”直接给网址
    let polls = 0, loggedIn = false
    const wb = (over: Partial<Schema['LlmBridgeAccountOut']> = {}) => account({ slot: 'workbuddy', label: 'CodeBuddy', login_methods: ['web_cn', 'web_intl', 'api_key'], ...over })
    const url = 'https://copilot.tencent.com/login?state=abc'
    const requests = setup('owner', r => {
      if (path(r) === `${base}/status`) return Response.json(status({ accounts: [wb(loggedIn ? { state: 'logged_in', identity: '测***', method: 'web_cn' } : {})] }))
      if (path(r) !== `${base}/accounts/workbuddy/login`) return undefined
      if (r.method === 'POST') return Response.json({ state: 'pending', verification_url: url, user_code: null, detail: '' })
      polls += 1
      if (polls >= 3) loggedIn = true
      return Response.json({ state: loggedIn ? 'done' : polls === 1 ? 'none' : 'pending', detail: '' })
    })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：CodeBuddy' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录CodeBuddy' }))
    const form = within(card).getByRole('form', { name: '登录CodeBuddy' })
    // 默认就是网页登录；没有“容器里已有的登录”，也不用填任何东西
    expect(within(form).getByLabelText('网页登录（国内站）')).toBeChecked()
    expect(within(form).getByLabelText('网页登录（国际站）')).toBeInTheDocument()
    expect(within(form).queryByLabelText('容器里已有的登录')).not.toBeInTheDocument()
    expect(within(form).queryByLabelText('API 密钥', { selector: 'input[type="password"]' })).not.toBeInTheDocument()
    await fireEvent.click(within(form).getByRole('button', { name: '获取登录网址' }))

    // 逐步进度（T-0276），和智能体服务器的初始化一个样式；旧管理器按页面知道的情况显示
    const progress = await screen.findByRole('region', { name: '登录「CodeBuddy」的进度' })
    const open = await within(progress).findByRole('group', { name: '打开登录网址' })
    const link = within(open).getByRole('link', { name: url })
    expect(link).toHaveAttribute('href', url)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(progress).not.toHaveTextContent('验证码')
    const steps = within(progress).getAllByRole('listitem')
    expect(steps.map(li => li.getAttribute('data-status'))).toEqual(['done', 'done', 'done', 'running', 'pending'])
    expect(steps[0]).toHaveTextContent('把请求交给 MCP 管理器「主账号」')
    expect(steps[3]).toHaveTextContent('等你在自己的浏览器里打开网址并登录')
    expect(within(progress).getByText('这个 MCP 管理器的版本不报告每一步，重新部署（升级）后就能看到。')).toBeVisible()
    expect(await writes(requests)[0]!.clone().json()).toEqual({ method: 'web_cn' })

    expect(await screen.findByText('「CodeBuddy」已登录。', { selector: '[role="status"]:not(strong)' }, { timeout: 10000 })).toBeVisible()
    expect(progress).toHaveAttribute('data-status', 'succeeded')
    await fireEvent.click(within(progress).getByRole('button', { name: '关闭' }))
    expect(screen.queryByRole('region', { name: '登录「CodeBuddy」的进度' })).not.toBeInTheDocument()
    expect(await within(card).findByText('网页登录（国内站）')).toBeVisible()
  })

  it('shows each step the MCP manager reports while CodeBuddy starts its login service, then the link and the result', { timeout: 30000 }, async () => {
    const wb = account({ slot: 'workbuddy', label: 'CodeBuddy', login_methods: ['web_cn', 'web_intl', 'api_key'] })
    const url = 'https://www.codebuddy.ai/login?state=xyz'
    const step = (key: string, status: string, detail = '') => ({ key, status, detail, started_at: null, finished_at: null })
    // 上一次失败的登录（开始前查到的）：不能当成这一次的结果
    let progress: Record<string, unknown> = { state: 'failed', started_at: '2026-11-17T02:00:00+00:00', detail: '上一次的失败', steps: [step('start', 'failed', '上一次的失败')], log: [] }
    let answer: (r: Response) => void = () => {}
    const requests = setup('owner', r => {
      if (path(r) === `${base}/status`) return Response.json(status({ accounts: [wb] }))
      if (path(r) !== `${base}/accounts/workbuddy/login`) return undefined
      if (r.method === 'POST') return new Promise<Response>(resolve => { answer = resolve })
      return Response.json(progress)
    })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：CodeBuddy' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录CodeBuddy' }))
    const form = within(card).getByRole('form', { name: '登录CodeBuddy' })
    await fireEvent.click(within(form).getByLabelText('网页登录（国际站）'))
    await fireEvent.click(within(form).getByRole('button', { name: '获取登录网址' }))

    const panel = await screen.findByRole('region', { name: '登录「CodeBuddy」的进度' })
    expect(panel).toHaveAttribute('data-status', 'running')
    expect(panel).toHaveTextContent('MCP 管理器「主账号」 · 网页登录（国际站）')
    expect(panel).not.toHaveTextContent('上一次的失败')
    // 管理器接手、正在启动登录服务：这一步进行中，下面是最新的运行输出
    progress = { state: 'pending', started_at: '2026-11-17T03:00:00+00:00', detail: '', verification_url: null, steps: [step('start', 'running'), step('request_url', 'pending'), step('wait_user', 'pending'), step('finish', 'pending')], log: ['03:00:01 启动 codebuddy --serve（只监听 127.0.0.1:41234）', '03:00:02 Gateway listening on http://127.0.0.1:41234 password=***'] }
    await waitFor(() => expect(within(panel).getAllByRole('listitem').map(li => li.getAttribute('data-status'))).toEqual(['done', 'running', 'pending', 'pending', 'pending']), { timeout: 5000 })
    const items = within(panel).getAllByRole('listitem')
    expect(items[1]).toHaveTextContent('启动 CodeBuddy 的登录服务')
    expect(items[2]).toHaveTextContent('向 CodeBuddy（国际站）申请登录网址')
    expect(within(panel).getByLabelText('最新输出')).toHaveTextContent('password=***')
    expect(panel).toHaveTextContent('正在执行第 2 / 5 步')

    // 拿到网址：前两步完成，等你登录
    const got = { state: 'pending', started_at: '2026-11-17T03:00:00+00:00', detail: '', verification_url: url, steps: [step('start', 'done', '用时 3.2 秒'), step('request_url', 'done', '用时 1.1 秒'), step('wait_user', 'running', '在你自己的浏览器里打开网址并登录（最多等 10 分钟）'), step('finish', 'pending')], log: [] }
    answer(Response.json(got))
    progress = got
    const link = await within(panel).findByRole('link', { name: url })
    expect(link).toHaveAttribute('href', url)
    expect(within(panel).getByText('用时 3.2 秒')).toBeVisible()
    // 人在网页上登录完
    progress = { ...got, state: 'done', finished_at: '2026-11-17T03:01:00+00:00', steps: [step('start', 'done'), step('request_url', 'done'), step('wait_user', 'done', '已在浏览器里登录'), step('finish', 'done', '账号：测试账号')] }
    await waitFor(() => expect(panel).toHaveAttribute('data-status', 'succeeded'), { timeout: 5000 })
    expect(within(panel).getByText('「CodeBuddy」已登录。')).toBeVisible()
    expect(writes(requests).filter(r => path(r).endsWith('/accounts/workbuddy/login'))).toHaveLength(1)
  })

  it('puts the reason under the failed step with the manager output, and offers to try again', { timeout: 30000 }, async () => {
    const wb = account({ slot: 'workbuddy', label: 'CodeBuddy', login_methods: ['web_cn', 'web_intl', 'api_key'] })
    const step = (key: string, status: string, detail = '') => ({ key, status, detail, started_at: null, finished_at: null })
    const reason = '向 CodeBuddy 申请登录网址没有成功：account.login.failed。常见原因：这个 MCP 管理器所在的服务器访问不了这个站点'
    let started = false
    const requests = setup('owner', r => {
      if (path(r) === `${base}/status`) return Response.json(status({ accounts: [wb] }))
      if (path(r) !== `${base}/accounts/workbuddy/login`) return undefined
      // 管理器报“平台出错”：工作台按 503 UNAVAILABLE 原文转给页面
      if (r.method === 'POST') { started = true; return Response.json({ error: { code: 'UNAVAILABLE', message: reason, request_id: 'r', details: {} } }, { status: 503 }) }
      return Response.json(!started ? { state: 'none', detail: '' } : { state: 'failed', started_at: '2026-11-17T03:00:00+00:00', finished_at: '2026-11-17T03:00:05+00:00', detail: reason, steps: [step('start', 'done', '用时 2.0 秒'), step('request_url', 'failed', reason), step('wait_user', 'pending'), step('finish', 'pending')], log: ['03:00:03 向 CodeBuddy 申请登录网址（登录方式 internal）', `03:00:05 失败：${reason}`] })
    })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：CodeBuddy' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录CodeBuddy' }))
    await fireEvent.click(within(within(card).getByRole('form', { name: '登录CodeBuddy' })).getByRole('button', { name: '获取登录网址' }))

    const panel = await screen.findByRole('region', { name: '登录「CodeBuddy」的进度' })
    await waitFor(() => expect(panel).toHaveAttribute('data-status', 'failed'))
    const items = within(panel).getAllByRole('listitem')
    expect(items.map(li => li.getAttribute('data-status'))).toEqual(['done', 'done', 'failed', 'pending', 'pending'])
    expect(within(items[2]!).getByRole('alert')).toHaveTextContent(`失败原因${reason}`)
    expect(within(items[3]!).getByText('未执行')).toBeVisible()
    expect(panel).toHaveTextContent('完成 2 / 5 步，在第 3 步失败')
    // 运行信息可以展开看
    await fireEvent.click(within(items[2]!).getByRole('button', { name: '查看命令输出' }))
    expect(panel.querySelector('details')!.textContent).toContain('向 CodeBuddy 申请登录网址（登录方式 internal）')
    // 重新获取：再发起一次
    await fireEvent.click(within(panel).getByRole('button', { name: '重新获取登录网址' }))
    await waitFor(() => expect(writes(requests).filter(r => path(r).endsWith('/accounts/workbuddy/login'))).toHaveLength(2))
  })

  // 开始网页登录要先在管理器里启动 CodeBuddy 的登录服务，常常超过普通请求的 15 秒：请求没有完整答复时不重新发起，改查登录进度（T-0275）
  for (const [name, broken] of [
    ['the connection drops', () => { throw new TypeError('Failed to fetch') }],
    ['a CDN in front of the site times out (Cloudflare 524)', () => new Response('<html>524</html>', { status: 524, headers: { 'content-type': 'text/html' } })],
  ] as const) {
    it(`recovers the CodeBuddy login link by polling when ${name}`, { timeout: 30000 }, async () => {
      let polls = 0
      const url = 'https://www.codebuddy.ai/login?state=xyz'
      const wb = account({ slot: 'workbuddy', label: 'CodeBuddy', login_methods: ['web_cn', 'web_intl', 'api_key'] })
      const requests = setup('owner', r => {
        if (path(r) === `${base}/status`) return Response.json(status({ accounts: [wb] }))
        if (path(r) !== `${base}/accounts/workbuddy/login`) return undefined
        if (r.method === 'POST') return broken()
        polls += 1
        // 开始前、刚断开时管理器还在启动登录服务（没有在登录）；之后申请到了网址
        return Response.json(polls <= 2 ? { state: 'none', detail: '' } : { state: 'pending', verification_url: url, user_code: null, detail: '' })
      })
      await openApp('/llm_bridge?view=manage')
      const card = await screen.findByRole('article', { name: '账号：CodeBuddy' })
      await fireEvent.click(within(card).getByRole('button', { name: '登录CodeBuddy' }))
      const form = within(card).getByRole('form', { name: '登录CodeBuddy' })
      await fireEvent.click(within(form).getByLabelText('网页登录（国际站）'))
      await fireEvent.click(within(form).getByRole('button', { name: '获取登录网址' }))

      const panel = await screen.findByRole('region', { name: '登录「CodeBuddy」的进度' })
      expect(await within(panel).findByText('“开始登录”的请求没等到答复，正在向管理器查进度')).toBeVisible()
      expect(screen.queryByText(/连接中断或超时/)).not.toBeInTheDocument()
      const link = await within(panel).findByRole('link', { name: url }, { timeout: 10000 })
      expect(link).toHaveAttribute('href', url)
      expect(panel).toHaveAttribute('data-status', 'running')
      // 只发起了一次：没有重启管理器里的登录服务
      expect(writes(requests).filter(r => path(r).endsWith('/accounts/workbuddy/login'))).toHaveLength(1)
      await fireEvent.click(within(panel).getByRole('button', { name: '不等了' }))
      expect(screen.queryByRole('region', { name: '登录「CodeBuddy」的进度' })).not.toBeInTheDocument()
    })
  }

  it('says why when no CodeBuddy login link turns up in time', { timeout: 30000 }, async () => {
    const wb = account({ slot: 'workbuddy', label: 'CodeBuddy', login_methods: ['web_cn', 'web_intl', 'api_key'] })
    let answer: (r: Response) => void = () => {}
    const requests = setup('owner', r => {
      if (path(r) === `${base}/status`) return Response.json(status({ accounts: [wb] }))
      if (path(r) !== `${base}/accounts/workbuddy/login`) return undefined
      if (r.method === 'POST') return new Promise<Response>(resolve => { answer = resolve })
      return Response.json({ state: 'none', detail: '' })
    })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：CodeBuddy' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录CodeBuddy' }))
    await fireEvent.click(within(within(card).getByRole('form', { name: '登录CodeBuddy' })).getByRole('button', { name: '获取登录网址' }))
    const panel = await screen.findByRole('region', { name: '登录「CodeBuddy」的进度' })
    // 请求还在等：第一步“交给 MCP 管理器”进行中
    expect(within(panel).getAllByRole('listitem')[0]).toHaveAttribute('data-status', 'running')
    // 工作台等管理器也超时了（503）：改查进度；一直没有网址，到时间说明原因，不当成已登录
    await waitFor(() => expect(writes(requests)).toHaveLength(1))
    answer(fail(503, 'EXTERNAL_UNAVAILABLE'))
    expect(await within(panel).findByText('“开始登录”的请求没等到答复，正在向管理器查进度')).toBeVisible()
    const now = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(now + 10 * 60_000)
    await waitFor(() => expect(panel).toHaveAttribute('data-status', 'failed'), { timeout: 10000 })
    expect(panel).toHaveTextContent('没有拿到「CodeBuddy」的登录网址')
    expect(within(panel).getAllByRole('listitem')[0]).toHaveAttribute('data-status', 'failed')
    expect(screen.queryByText('「CodeBuddy」已登录。')).not.toBeInTheDocument()
  })

  it('stops polling when the page is left while a device login is pending', { timeout: 20000 }, async () => {
    let polls = 0
    setup('owner', r => {
      if (path(r) !== `${base}/accounts/openai/login`) return undefined
      if (r.method === 'POST') return Response.json({ state: 'pending', verification_url: 'https://auth.example.com/device', user_code: 'ABCD-1234', detail: '' })
      polls += 1
      return Response.json({ state: 'pending', detail: '' })
    })
    const view = await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：Codex' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录Codex' }))
    await fireEvent.click(within(card).getByLabelText('设备码登录'))
    await fireEvent.click(within(card).getByRole('button', { name: '获取验证码' }))
    await screen.findByRole('region', { name: '登录「Codex」的进度' })
    await waitFor(() => expect(polls).toBeGreaterThanOrEqual(2), { timeout: 5000 })
    const seen = polls
    view.unmount()
    await new Promise(resolve => setTimeout(resolve, 3200))
    expect(polls).toBe(seen)
  })

  it('logs Claude in through web authorization: a link, each step, then the code pasted back', { timeout: 30000 }, async () => {
    // T-0287：管理器替人运行官方程序的 auth login；拿到网址后等人把授权页显示的授权码粘贴回来
    const cl = account({ slot: 'claude', label: 'Claude', login_methods: ['web', 'api_key', 'oauth_token', 'existing'] })
    const url = 'https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=s1'
    const step = (key: string, status: string, detail = '') => ({ key, status, detail, started_at: null, finished_at: null })
    const started = '2026-11-17T03:00:00.000+00:00'
    const waiting = { state: 'pending', started_at: started, detail: '', verification_url: url, needs_code: true, steps: [step('start', 'done', '官方程序已启动'), step('request_url', 'done', '已取得登录网址'), step('wait_user', 'running', '在浏览器里登录并授权'), step('submit_code', 'pending'), step('finish', 'pending')], log: ['03:00:01 启动 Claude 官方程序：auth login --claudeai'] }
    let progress: Record<string, unknown> = { state: 'none' }
    let loggedIn = false
    const requests = setup('owner', async r => {
      if (path(r) === `${base}/status`) return Response.json(status({ settings: { ...settings, claude_subscription_ack: true }, accounts: [cl.state === 'logged_in' || loggedIn ? { ...cl, state: 'logged_in', identity: 's***@example.com', method: 'web' } : cl] }))
      if (path(r) === `${base}/accounts/claude/login/code`) {
        progress = { ...waiting, needs_code: false, steps: [step('start', 'done'), step('request_url', 'done'), step('wait_user', 'done', '已收到授权码'), step('submit_code', 'running', '官方程序用授权码换取登录'), step('finish', 'pending')] }
        return Response.json(progress)
      }
      if (path(r) !== `${base}/accounts/claude/login`) return undefined
      if (r.method === 'POST') { progress = waiting; return Response.json(waiting) }
      return Response.json(progress)
    })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：Claude' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录Claude' }))
    const form = within(card).getByRole('form', { name: '登录Claude' })
    // 默认就是网页授权登录，不用填任何东西
    expect(within(form).getByLabelText('网页授权登录（推荐）')).toBeChecked()
    expect(within(form).queryByRole('textbox')).not.toBeInTheDocument()
    await fireEvent.click(within(form).getByRole('button', { name: '获取登录网址' }))

    const panel = await screen.findByRole('region', { name: '登录「Claude」的进度' })
    expect(panel).toHaveTextContent('MCP 管理器「主账号」 · 网页授权登录')
    const open = await within(panel).findByRole('group', { name: '登录 Claude 并交回授权码' })
    expect(within(open).getByRole('link', { name: url })).toHaveAttribute('href', url)
    expect(open).toHaveTextContent('授权后页面会显示一段授权码（中间有一个 #）')
    const items = within(panel).getAllByRole('listitem').filter(li => li.hasAttribute('data-status'))
    expect(items.map(li => li.getAttribute('data-status'))).toEqual(['done', 'done', 'done', 'running', 'pending', 'pending'])
    expect(items[3]).toHaveTextContent('等你在自己的浏览器里登录并授权，再把授权码粘贴到下面')
    expect(items[4]).toHaveTextContent('官方程序用授权码完成登录')
    expect(items[5]).toHaveTextContent('确认登录结果（官方程序报告的账号）')

    // 不完整的授权码：页面先拦下，不发给管理器
    const box = within(open).getByLabelText('授权码')
    await fireEvent.update(box, 'abc')
    await fireEvent.click(within(open).getByRole('button', { name: '提交授权码' }))
    expect(await within(open).findByText(/授权码不完整/)).toBeVisible()
    expect(writes(requests).filter(r => path(r).endsWith('/login/code'))).toHaveLength(0)

    await fireEvent.update(box, '  abc#def  ')
    await fireEvent.click(within(open).getByRole('button', { name: '提交授权码' }))
    expect(await within(panel).findByText('已把授权码交给官方程序，正在完成登录…')).toBeVisible()
    const sent = writes(requests).filter(r => path(r).endsWith('/login/code'))
    expect(await sent[0]!.clone().json()).toEqual({ code: 'abc#def' })
    expect(within(panel).queryByLabelText('授权码')).not.toBeInTheDocument()

    // 官方程序完成登录
    loggedIn = true
    progress = { ...waiting, state: 'done', needs_code: false, finished_at: '2026-11-17T03:01:00.000+00:00', steps: [step('start', 'done'), step('request_url', 'done'), step('wait_user', 'done'), step('submit_code', 'done', '官方程序报告登录成功'), step('finish', 'done', '已登录：s***@example.com')] }
    await waitFor(() => expect(panel).toHaveAttribute('data-status', 'succeeded'), { timeout: 5000 })
    expect(within(panel).getByText('「Claude」已登录。')).toBeVisible()
    expect(document.body.innerHTML).not.toContain('abc#def')
    expect(await within(card).findByText('s***@example.com')).toBeVisible()
  })

  it('blocks Claude subscription logins until the risk is acknowledged, then sends the token once and clears it', async () => {
    let ack = false, rejectOnce = true
    const requests = setup('owner', async r => {
      if (path(r) === `${base}/status`) return Response.json(status({ settings: { ...settings, claude_subscription_ack: ack } }))
      if (path(r) === `${base}/settings` && r.method === 'PUT') { ack = true; return Response.json({ ...settings, claude_subscription_ack: true }) }
      if (path(r) === `${base}/accounts/claude/login` && r.method === 'POST') {
        // 后端也会拦：第一次返回 403（例如确认刚被别人撤掉）
        if (rejectOnce) { rejectOnce = false; return fail(403, 'FORBIDDEN') }
        return Response.json({ state: 'done', detail: '' })
      }
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：Claude' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录Claude' }))
    const form = within(card).getByRole('form', { name: '登录Claude' })
    // 默认是 API 密钥：不需要确认
    expect(within(form).getByLabelText('API 密钥', { selector: 'input[type="radio"]' })).toBeChecked()
    expect(within(form).queryByRole('group', { name: 'Claude 订阅登录的风险确认' })).not.toBeInTheDocument()

    await fireEvent.click(within(form).getByLabelText('订阅令牌', { selector: 'input[type="radio"]' }))
    // 订阅令牌怎么拿：分步写清楚（T-0287）
    const guide = within(form).getByRole('list', { name: '订阅令牌怎么拿' })
    expect(guide).toHaveTextContent('claude setup-token')
    expect(within(guide).getByRole('link', { name: 'Claude 官方说明' })).toHaveAttribute('href', 'https://code.claude.com/docs/en/cli-reference')
    expect(within(guide).getAllByRole('listitem')).toHaveLength(4)
    const risk = within(form).getByRole('group', { name: 'Claude 订阅登录的风险确认' })
    expect(within(risk).getByText(/Anthropic 要求开发者用 API 密钥接入；把订阅账号的登录用于自动化处在灰区，账号可能被限制。详见方案第 6\.3 节/)).toBeVisible()
    expect(within(form).getByRole('button', { name: '登录' })).toBeDisabled()
    expect(within(form).getByLabelText('订阅令牌', { selector: 'input[type="password"]' })).toBeDisabled()
    expect(writes(requests)).toEqual([])

    await fireEvent.click(within(risk).getByRole('checkbox'))
    await waitFor(() => expect(within(form).queryByRole('group', { name: 'Claude 订阅登录的风险确认' })).not.toBeInTheDocument())
    expect(await writes(requests)[0]!.clone().json()).toEqual({ claude_subscription_ack: true })

    const secret = within(form).getByLabelText('订阅令牌', { selector: 'input[type="password"]' })
    await fireEvent.update(secret, 'sk-ant-oat01-secret-value')
    await fireEvent.click(within(form).getByRole('button', { name: '登录' }))
    expect(await screen.findByText(/Claude 用订阅登录前，要先确认了解合规风险/)).toBeVisible()
    await fireEvent.click(within(form).getByRole('button', { name: '登录' }))
    expect(await screen.findByText('「Claude」已登录。')).toBeVisible()
    const logins = writes(requests).filter(r => path(r).endsWith('/accounts/claude/login'))
    expect(await logins[1]!.clone().json()).toEqual({ method: 'oauth_token', oauth_token: 'sk-ant-oat01-secret-value' })
    expect(within(card).queryByRole('form')).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain('sk-ant-oat01-secret-value')
  })

  it('archives the sessions only after confirming an account change', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true)
    const requests = setup('owner', r => (path(r) === `${base}/accounts/workbuddy/reset` ? Response.json({ archived_sessions: 3 }) : undefined))
    await openApp('/llm_bridge?view=manage')
    const card = await screen.findByRole('article', { name: '账号：CodeBuddy' })
    await fireEvent.click(within(card).getByRole('button', { name: '更换账号CodeBuddy' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('这个 MCP 管理器上这个平台的全部会话都会被归档'))
    expect(writes(requests)).toEqual([])
    await fireEvent.click(within(card).getByRole('button', { name: '更换账号CodeBuddy' }))
    expect(await screen.findByText('已归档 3 个会话；现在可以登录新的账号。')).toBeVisible()
  })

  it('lets the owner pick which development environments an instance may use; none means no dev sessions', async () => {
    let current = grant()
    const ENV = 'be_0123456789abcdef0123'
    const requests = setup('owner', async r => {
      if (path(r) === '/api/v1/llm_bridge/grants') return Response.json({ grants: [current] })
      if (path(r) === `/api/v1/llm_bridge/grants/${INSTANCE}` && r.method === 'PUT') { current = { ...current, ...(await r.clone().json()) }; return Response.json(current) }
      if (path(r) === `${base}/environments` && r.method === 'GET') return Response.json({ environments: [{ id: ENV, platform: 'codex', mode: 'dev', name: '工作台仓库', enabled: true, repo_url: '', branch: '', sandbox: 'exec', network: false, description: '' }] })
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const row = await screen.findByRole('group', { name: '实例权限：甲' })
    const envs = within(row).getByRole('group', { name: '甲可用的开发环境' })
    expect(within(envs).getByText('可用的开发环境（现在：一个都不能用）')).toBeVisible()
    const box = await within(envs).findByLabelText(/工作台仓库（Codex，/)
    expect(box).not.toBeChecked()
    await fireEvent.click(box)
    await screen.findByText('已保存「甲」的权限。')
    await waitFor(() => expect(within(row).getByLabelText(/工作台仓库（Codex，/)).toBeChecked())
    const bodies = await Promise.all(writes(requests).map(r => r.clone().json()))
    expect(bodies).toEqual([{ enabled: true, can_write: true, can_account: true, can_takeover: false, platforms: ['codex'], bridge_ids: null, environment_ids: [ENV] }])
  })

  it('shows an instance limited to chatting with Codex, and lets the owner widen it to all of Codex', async () => {
    // 管理员选的“Codex（只能对话）”（T-0280）：不能用开发模式、读不到别人的开发会话
    let current = grant({ platforms: ['codex:chat'] })
    const requests = setup('owner', async r => {
      if (path(r) === '/api/v1/llm_bridge/grants') return Response.json({ grants: [current] })
      if (path(r) === `/api/v1/llm_bridge/grants/${INSTANCE}` && r.method === 'PUT') { current = { ...current, ...(await r.clone().json()) }; return Response.json(current) }
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const row = await screen.findByRole('group', { name: '实例权限：甲' })
    const platforms = within(row).getByRole('group', { name: '甲允许的平台' })
    expect(within(platforms).getByLabelText('Codex（只能对话）')).toBeChecked()
    expect(within(platforms).getByLabelText('Codex')).not.toBeChecked()
    expect(within(platforms).queryByLabelText('ChatGPT')).not.toBeInTheDocument()
    expect(within(platforms).getByText(/勾“Codex”就对话、开发都能用/)).toBeVisible()
    // 原来只勾了 ChatGPT 的实例升级后是整个 Codex（T-0282），不再提“升级后是这一项”
    expect(within(platforms).queryByText(/升级后是这一项/)).not.toBeInTheDocument()
    // 勾上整个 Codex：只能对话那一项一并去掉（二选一）
    await fireEvent.click(within(platforms).getByLabelText('Codex'))
    await screen.findByText('已保存「甲」的权限。')
    const bodies = await Promise.all(writes(requests).map(r => r.clone().json()))
    expect(bodies.map(b => b.platforms)).toEqual([['codex']])
    await waitFor(() => expect(within(row).getByLabelText('Codex（只能对话）')).not.toBeChecked())
  })

  it('lists the sessions of every manager, filters them, searches the content and sums up usage', async () => {
    const indexed = (over: Partial<Schema['LlmBridgeIndexSessionOut']> = {}): Schema['LlmBridgeIndexSessionOut'] => ({
      bridge_id: BRIDGE, bridge_name: '主账号', session_id: `bs_${'a'.repeat(20)}`, platform: 'claude', mode: 'dev', model: 'sonnet', title: '季度复盘', status: 'active', origin: 'bridge',
      owner_kind: 'agent', owner_id: INSTANCE, owner: '甲', source: { kind: 'agent', tasks: [{ task_id: 't-1', title: '写复盘' }] }, busy: false, turns_total: 3, turns_failed: 1, turns_interrupted: 0, tokens: 12345,
      created_at: at, updated_at: at, last_turn_at: at, ...over,
    })
    const queries: URLSearchParams[] = []
    const requests = setup('owner', r => {
      const url = new URL(r.url), p = url.pathname
      if (p === '/api/v1/llm_bridge/sessions/owners') return Response.json({ owners: [{ owner_kind: 'agent', owner_id: INSTANCE, owner: '甲', sessions: 2 }, { owner_kind: 'user', owner_id: 'user:u1', owner: '测试用户', sessions: 1 }] })
      if (p === '/api/v1/llm_bridge/sessions/search') return Response.json({ hits: [{ session: indexed(), bridge_id: BRIDGE, bridge_name: '主账号', session_id: `bs_${'a'.repeat(20)}`, turn_id: null, role: 'assistant', snippet: '…本季度的三个要点是…', at: 1794531600 }], failed: ['备用'] })
      if (p === '/api/v1/llm_bridge/usage') return Response.json({ days: 30, failed: [], rows: [
        { day: '2026-11-15', bridge_id: BRIDGE, bridge_name: '主账号', platform: 'claude', owner_kind: 'agent', owner_id: INSTANCE, owner: '甲', turns: 4, failed: 1, interrupted: 0, tokens: 9000 },
        { day: '2026-11-14', bridge_id: BRIDGE, bridge_name: '主账号', platform: 'codex', owner_kind: 'agent', owner_id: INSTANCE, owner: '甲', turns: 2, failed: 0, interrupted: 0, tokens: 1000 },
        { day: '2026-11-15', bridge_id: BRIDGE, bridge_name: '主账号', platform: 'claude', owner_kind: 'user', owner_id: 'user:u1', owner: '测试用户', turns: 1, failed: 0, interrupted: 0, tokens: 500 },
      ] })
      if (p === '/api/v1/llm_bridge/sessions') {
        queries.push(url.searchParams)
        const only = url.searchParams.get('owner_id')
        return Response.json({ sessions: only === 'user:u1' ? [indexed({ title: '我自己问的', owner_kind: 'user', owner_id: 'user:u1', owner: '测试用户', source: null, turns_failed: 0, mode: 'chat' })] : [indexed()], next_offset: null, sync_failed: url.searchParams.get('sync') ? ['备用'] : [] })
      }
      return undefined
    })
    await openApp('/llm_bridge?view=records')
    expect(await screen.findByRole('heading', { level: 2, name: '会话记录' })).toBeVisible()
    // 列表：谁建的、平台和用法、回合和失败、token；点标题进聊天页；出处里的任务能点到任务
    const table = await screen.findByRole('table', { name: '会话记录' })
    const row = within(table).getAllByRole('row')[1]!
    expect(within(row).getByRole('link', { name: '打开会话：季度复盘' })).toHaveAttribute('href', `/llm_bridge?m=${BRIDGE}&s=bs_${'a'.repeat(20)}`)
    expect(within(row).getByRole('link', { name: '写复盘' })).toHaveAttribute('href', '/tasks?task=t-1')
    expect(row).toHaveTextContent('甲智能体')
    expect(row).toHaveTextContent('Claude · 开发')
    expect(row).toHaveTextContent('1 个失败')
    expect(row).toHaveTextContent('12,345')

    // 筛选：选具体的人、只看有问题的 → 条件带到接口上
    const filters = screen.getByRole('form', { name: '筛选会话' })
    await fireEvent.update(within(filters).getByLabelText('谁建的'), 'user:u1')
    expect(await within(await screen.findByRole('table', { name: '会话记录' })).findByText('我自己问的')).toBeVisible()
    await fireEvent.click(within(filters).getByLabelText('只看有失败或中断的'))
    await waitFor(() => expect(queries.at(-1)!.get('problem')).toBe('true'))
    expect(queries.at(-1)!.get('owner_id')).toBe('user:u1')
    await fireEvent.click(within(filters).getByRole('button', { name: '清除筛选' }))
    await waitFor(() => expect(queries.at(-1)!.has('owner_id')).toBe(false))
    // 现在同步：没同步上的说出来
    await fireEvent.click(screen.getByRole('button', { name: '现在同步' }))
    expect(await screen.findByText('这些 MCP 管理器没有同步上：备用（列表里是它们上一次同步到的）')).toBeVisible()

    // 全文搜索
    const search = screen.getByRole('search', { name: '搜索对话内容' })
    await fireEvent.click(within(search).getByRole('button', { name: '搜索' }))
    expect(await screen.findByText('至少写两个字。')).toBeVisible()
    await fireEvent.update(within(search).getByLabelText('要找的文字'), '三个要点')
    await fireEvent.click(within(search).getByRole('button', { name: '搜索' }))
    const hits = await screen.findByRole('list', { name: '“三个要点”的搜索结果' })
    expect(within(hits).getByText('…本季度的三个要点是…')).toBeVisible()
    expect(within(hits).getByRole('link', { name: '季度复盘' })).toHaveAttribute('href', `/llm_bridge?m=${BRIDGE}&s=bs_${'a'.repeat(20)}`)
    expect(screen.getByText('这些 MCP 管理器没有搜到（连不上或版本太旧）：备用')).toBeVisible()
    expect(new URL(requests.find(r => path(r) === '/api/v1/llm_bridge/sessions/search')!.url).searchParams.get('q')).toBe('三个要点')

    // 用量：按人汇总，token 多的在前
    const usage = await screen.findByRole('table', { name: '用量' })
    const lines = within(usage).getAllByRole('row').slice(1)
    expect(lines[0]).toHaveTextContent('甲智能体')
    expect(within(lines[0]!).getByRole('cell', { name: '6' })).toBeVisible()
    expect(lines[0]).toHaveTextContent('10,000')
    expect(lines[1]).toHaveTextContent('测试用户人')
    expect(screen.getByText('一共 7 个回合，10,500 个 token。')).toBeVisible()
  })

  it('sends the whole grant on every toggle; disabling clears the flags but keeps the chosen platforms', async () => {
    let current = grant()
    const requests = setup('owner', async r => {
      if (path(r) === '/api/v1/llm_bridge/grants') return Response.json({ grants: [current] })
      if (path(r) === `/api/v1/llm_bridge/grants/${INSTANCE}` && r.method === 'PUT') { current = { ...current, ...(await r.clone().json()) }; return Response.json(current) }
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const row = await screen.findByRole('group', { name: '实例权限：甲' })
    expect(within(row).getByLabelText('新建会话和发送')).toBeChecked()
    expect(within(row).getByLabelText('Codex')).toBeChecked()

    await fireEvent.click(within(row).getByLabelText('接手别人的会话'))
    await screen.findByText('已保存「甲」的权限。')
    await waitFor(() => expect(row).toBeEnabled())
    await fireEvent.click(within(row).getByLabelText('Claude'))
    await waitFor(() => expect(within(row).getByLabelText('Claude')).toBeChecked())
    await waitFor(() => expect(row).toBeEnabled())
    await fireEvent.click(within(row).getByLabelText('启用'))
    expect(await within(row).findByText('未启用')).toBeVisible()
    await waitFor(() => expect(row).toBeEnabled())

    const bodies = await Promise.all(writes(requests).map(r => r.clone().json()))
    expect(bodies).toEqual([
      { enabled: true, can_write: true, can_account: true, can_takeover: true, platforms: ['codex'], bridge_ids: null, environment_ids: [] },
      { enabled: true, can_write: true, can_account: true, can_takeover: true, platforms: ['codex', 'claude'], bridge_ids: null, environment_ids: [] },
      // 关掉时三项权限收回，已选的平台保留（再打开时不用重选）
      { enabled: false, can_write: false, can_account: false, can_takeover: false, platforms: ['codex', 'claude'], bridge_ids: null, environment_ids: [] },
    ])
    for (const name of ['新建会话和发送', '查看账号下全部会话', '接手别人的会话']) { expect(within(row).getByLabelText(name)).not.toBeChecked(); expect(within(row).getByLabelText(name)).toBeDisabled() }
  })

  it('shows every manager with the platforms it controls and each manager its own accounts', async () => {
    // 人类的例子：1 号控制 Claude / CodeBuddy，2 号控制 Codex / Claude / CodeBuddy；同一个平台在两个管理器上是不同的账号
    const one = { ...bridge, name: '1 号' }, two = { ...bridge, id: OTHER, name: '2 号', base_url: 'http://llm-bridge-2:8787' }
    const claude = (identity: string) => account({ slot: 'claude', label: 'Claude', state: 'logged_in', registered: true, identity, method: 'api_key', login_methods: ['api_key'] })
    const idle = account({ slot: 'workbuddy', label: 'CodeBuddy', login_methods: ['api_key'] })
    setup('owner', r => {
      if (path(r) === `${base}/status`) return Response.json(status({ platforms: ['claude', 'workbuddy'], accounts: [account({ enabled: false }), claude('a***@example.com'), idle], channels: [{ platform: 'codex', mode: 'dev', slot: 'openai', enabled: false, available: false, note: '' }, { platform: 'claude', mode: 'chat', slot: 'claude', enabled: true, available: true, note: '' }] }))
      if (path(r) === `/api/v1/llm_bridge/bridges/${OTHER}/status`) return Response.json(status({ platforms: ['codex', 'claude', 'workbuddy'], accounts: [account({}), claude('b***@example.com'), idle] }))
      return undefined
    }, [one, two])
    await openApp('/llm_bridge?view=manage')
    const first = await screen.findByRole('article', { name: 'MCP 管理器：1 号' })
    const second = screen.getByRole('article', { name: 'MCP 管理器：2 号' })
    expect(first).toHaveAttribute('aria-current', 'true')
    const names = (card: HTMLElement, label: string) => within(card).findByRole('list', { name: label }).then(list => within(list).getAllByRole('listitem').map(li => li.textContent))
    expect(await names(first, '1 号控制的平台')).toEqual(['Claude已登录', 'CodeBuddy未登录'])
    expect(await names(second, '2 号控制的平台')).toEqual(['Codex未登录', 'Claude已登录', 'CodeBuddy未登录'])

    // 1 号的详情：只有它控制的平台的账号和通道；没勾选的 Codex 没登录过，不显示
    const panel = await screen.findByRole('tabpanel', { name: '平台与账号' })
    expect(await within(panel).findByText('a***@example.com')).toBeVisible()
    expect(within(panel).queryByRole('article', { name: '账号：Codex' })).not.toBeInTheDocument()
    const control = within(panel).getByRole('group', { name: '控制的平台' })
    expect(within(control).getByLabelText('Claude')).toBeChecked()
    expect(within(control).getByLabelText('Codex')).not.toBeChecked()
    expect(within(within(panel).getByRole('table', { name: '通道' })).queryByText('Codex')).not.toBeInTheDocument()

    // 换到 2 号：另一套账号
    await fireEvent.click(within(second).getByRole('button', { name: '查看 MCP 管理器：2 号' }))
    expect(await within(await screen.findByRole('tabpanel', { name: '平台与账号' })).findByText('b***@example.com')).toBeVisible()
    expect(screen.queryByText('a***@example.com')).not.toBeInTheDocument()
    expect(second).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: '登录Codex' })).toBeVisible()
  })

  it('finds managers by name, opens the tab named in the address and keeps the switch and the actions on each card', async () => {
    const one = { ...bridge, name: '1 号' }, two = { ...bridge, id: OTHER, name: '2 号', base_url: 'http://llm-bridge-2:8787' }
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup('owner', r => {
      if (path(r) === `/api/v1/llm_bridge/bridges/${OTHER}/status`) return Response.json(status())
      if (path(r) === base && r.method === 'PATCH') return Response.json({ ...one, enabled: false })
      return undefined
    }, [one, two])
    // 从“会话纪要”的“纪要设置”过来：?tab=settings 直接打开运行设置
    await openApp('/llm_bridge?view=manage&tab=settings')
    expect(await screen.findByRole('tab', { name: '运行设置' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Owner')).toBeVisible()
    // 左边（效果图 T-0286）：标题旁是“添加”，下面按名字找，最底下是说明
    const list = screen.getByRole('region', { name: 'MCP 管理器' })
    expect(within(list).getByRole('heading', { name: 'MCP 管理器 · 2' })).toBeVisible()
    expect(within(list).getByRole('button', { name: '添加 MCP 管理器' })).toBeVisible()
    expect(within(list).getByText('每个管理器，每个平台可登录一个账号')).toBeVisible()
    await fireEvent.update(within(list).getByRole('searchbox', { name: '搜索管理器' }), '2')
    await waitFor(() => expect(within(list).queryByRole('article', { name: 'MCP 管理器：1 号' })).not.toBeInTheDocument())
    expect(within(list).getByRole('article', { name: 'MCP 管理器：2 号' })).toBeVisible()
    await fireEvent.update(within(list).getByRole('searchbox', { name: '搜索管理器' }), '')

    // 右边详情：连接状态、在哪儿；操作不在这里，在卡片上
    const detail = screen.getByRole('region', { name: 'MCP 管理器：1 号' })
    expect(await within(detail).findByText('已连接')).toBeVisible()
    expect(within(detail).getByText('http://10.0.0.5:8787')).toBeVisible()
    expect(within(detail).queryByRole('button', { name: /管理操作/ })).not.toBeInTheDocument()

    // 卡片的“…”：手动添加的是修改、删除；按 Esc 收起
    const first = await within(list).findByRole('article', { name: 'MCP 管理器：1 号' })
    expect(first).toHaveAttribute('aria-current', 'true')
    await fireEvent.click(within(first).getByRole('button', { name: '1 号的管理操作' }))
    const menu = within(first).getByRole('group', { name: '管理操作：1 号' })
    for (const name of ['修改1 号', '删除1 号']) expect(within(menu).getByRole('button', { name })).toBeVisible()
    await fireEvent.keyDown(document, { key: 'Escape' })
    expect(within(first).queryByRole('group', { name: '管理操作：1 号' })).not.toBeInTheDocument()

    // 启用开关：停用前先确认，取消就不发请求
    const toggle = within(first).getByRole('switch', { name: '启用1 号' })
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    confirm.mockReturnValueOnce(false)
    await fireEvent.click(toggle)
    expect(writes(requests)).toEqual([])
    await fireEvent.click(toggle)
    await waitFor(() => expect(writes(requests).map(r => r.method)).toEqual(['PATCH']))
    expect(await writes(requests)[0]!.clone().json()).toEqual({ enabled: false })
    expect(await screen.findByText('已停用「1 号」。')).toBeVisible()
  })

  it('changes the controlled platforms after confirming, never to none, and only offers logout for an unchecked platform', async () => {
    let platforms = ['codex', 'claude']
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const requests = setup('owner', async r => {
      if (path(r) === `${base}/status`) return Response.json(status({ platforms, accounts: [account({ enabled: platforms.includes('codex'), state: 'logged_in', registered: true, identity: 'a***@example.com', method: 'device_code' }), account({ slot: 'claude', label: 'Claude', enabled: platforms.includes('claude'), login_methods: ['api_key'] }), account({ slot: 'workbuddy', label: 'CodeBuddy', enabled: platforms.includes('workbuddy'), login_methods: ['api_key'] })] }))
      if (path(r) === `${base}/settings` && r.method === 'PUT') { platforms = (await r.clone().json()).platforms; return Response.json({ ...settings, platforms }) }
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const control = () => screen.findByRole('group', { name: '控制的平台' })
    expect(within(await control()).getByLabelText('Codex')).toBeChecked()
    // 没勾选、也没登录过的平台不占地方
    expect(screen.queryByRole('article', { name: '账号：CodeBuddy' })).not.toBeInTheDocument()
    // 平时只读：点“编辑”后才能勾选，点“完成”收起
    expect(within(await control()).getByLabelText('Codex')).toBeDisabled()
    await fireEvent.click(screen.getByRole('button', { name: '编辑控制的平台' }))
    expect(within(await control()).getByLabelText('Codex')).toBeEnabled()
    expect(screen.getByRole('button', { name: '完成编辑控制的平台' })).toHaveAttribute('aria-pressed', 'true')

    // 取消勾选要确认；不确认就什么都不发，勾选框回到原样
    await fireEvent.click(within(await control()).getByLabelText('Codex'))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('不再让「主账号」控制 Codex？'))
    expect(writes(requests)).toEqual([])
    await waitFor(async () => expect(within(await control()).getByLabelText('Codex')).toBeChecked())

    confirm.mockReturnValue(true)
    await fireEvent.click(within(await control()).getByLabelText('Codex'))
    expect(await screen.findByText('「主账号」不再控制 Codex。')).toBeVisible()
    expect(await writes(requests)[0]!.clone().json()).toEqual({ platforms: ['claude'] })
    // 还登录着的平台留一张卡片：说明原因，只能退出登录，不能再登录
    const openai = await screen.findByRole('article', { name: '账号：Codex' })
    expect(await within(openai).findByText(/这个管理器没有勾选这个平台/)).toBeVisible()
    expect(within(openai).getByRole('button', { name: '退出登录Codex' })).toBeVisible()
    expect(within(openai).queryByRole('button', { name: '登录Codex' })).not.toBeInTheDocument()

    // 最后一个不能取消
    await fireEvent.click(within(await control()).getByLabelText('Claude'))
    expect(await screen.findByText(/^至少要控制一个平台。/)).toBeVisible()
    expect(writes(requests)).toHaveLength(1)

    // 勾选新的平台不用确认
    confirm.mockClear()
    await fireEvent.click(within(await control()).getByLabelText('CodeBuddy'))
    expect(await screen.findByText(/^已让「主账号」控制 CodeBuddy。接下来在下面登录它的账号。$/)).toBeVisible()
    expect(confirm).not.toHaveBeenCalled()
    expect(await writes(requests)[1]!.clone().json()).toEqual({ platforms: ['claude', 'workbuddy'] })
    expect(await screen.findByRole('article', { name: '账号：CodeBuddy' })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '完成编辑控制的平台' }))
    expect(within(await control()).getByLabelText('Claude')).toBeDisabled()
  })

  it('limits an instance to chosen managers: all by default, an explicit list, and a warning when none is left', async () => {
    const two = { ...bridge, id: OTHER, name: '2 号' }
    let current = grant({ platforms: [] })
    const requests = setup('owner', async r => {
      if (path(r) === '/api/v1/llm_bridge/grants') return Response.json({ grants: [current] })
      if (path(r) === `/api/v1/llm_bridge/grants/${INSTANCE}` && r.method === 'PUT') { current = { ...current, ...(await r.clone().json()) }; return Response.json(current) }
      if (path(r) === `/api/v1/llm_bridge/bridges/${OTHER}/status`) return Response.json(status())
      return undefined
    }, [bridge, two])
    await openApp('/llm_bridge?view=manage')
    const row = await screen.findByRole('group', { name: '实例权限：甲' })
    const managers = within(row).getByRole('group', { name: '甲允许的 MCP 管理器' })
    const all = within(managers).getByLabelText('全部（包括以后添加的）')
    expect(all).toBeChecked()
    for (const name of ['主账号', '2 号']) { expect(within(managers).getByLabelText(name)).toBeChecked(); expect(within(managers).getByLabelText(name)).toBeDisabled() }

    // 取消“全部”：变成现在这几个的明确列表，之后可以逐个取消
    await fireEvent.click(all)
    await waitFor(() => expect(within(managers).getByLabelText('主账号')).toBeEnabled())
    await fireEvent.click(within(managers).getByLabelText('主账号'))
    await waitFor(() => expect(within(managers).getByLabelText('主账号')).not.toBeChecked())
    await waitFor(() => expect(row).toBeEnabled())
    await fireEvent.click(within(managers).getByLabelText('2 号'))
    expect(await within(row).findByText(/这个实例现在一个 MCP 管理器都不能用/)).toBeVisible()
    await waitFor(() => expect(row).toBeEnabled())
    await fireEvent.click(within(managers).getByLabelText('全部（包括以后添加的）'))
    await waitFor(() => expect(within(row).queryByText(/一个 MCP 管理器都不能用/)).not.toBeInTheDocument())

    const bodies = await Promise.all(writes(requests).map(r => r.clone().json()))
    expect(bodies.map(b => b.bridge_ids)).toEqual([[BRIDGE, OTHER], [OTHER], [], null])
  })

  it('turns session digests off in the settings, sending only what changed', async () => {
    const requests = setup('owner', async r => (path(r) === `${base}/settings` ? Response.json({ ...settings, ...(await r.clone().json()) }) : undefined))
    await openApp('/llm_bridge?view=manage')
    const set = await openTab('运行设置')
    // 会话纪要（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 5.2）：默认开；只在改了的时候提交（旧版本的管理器不认识这两项）
    const digest = await within(set).findByRole('group', { name: '会话纪要' })
    expect(within(digest).getByLabelText(/把会话的重点自动整理成纪要/)).toBeChecked()
    expect(within(digest).getByLabelText(/纪要一起放进回收站/)).toBeChecked()
    await fireEvent.click(within(digest).getByLabelText(/把会话的重点自动整理成纪要/))
    await fireEvent.click(within(set).getByRole('button', { name: '保存设置' }))
    await waitFor(() => expect(writes(requests)).toHaveLength(1))
    const body = await writes(requests)[0]!.clone().json()
    expect(body).toMatchObject({ digest_enabled: false })
    expect(body).not.toHaveProperty('digest_follow_retention')
  })

  it('sets where each platform compacts automatically, sending only what changed', async () => {
    const withAuto = { ...settings, autocompact_claude_percent: 0, autocompact_workbuddy_percent: 0, autocompact_codex_tokens: 0 }
    const requests = setup('owner', async r => {
      if (path(r) === `${base}/settings`) return Response.json({ ...withAuto, ...(await r.clone().json()) })
      if (path(r) === `${base}/status`) return Response.json({ ...status(), settings: withAuto })
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const set = await openTab('运行设置')
    const group = await within(set).findByRole('group', { name: '自动压缩的位置' })
    const claude = within(group).getByLabelText('Claude：上下文用到百分之多少时自动压缩')
    expect(claude).toHaveValue(0)
    await fireEvent.update(claude, '60')
    await fireEvent.click(within(set).getByRole('button', { name: '保存设置' }))
    await waitFor(() => expect(writes(requests)).toHaveLength(1))
    const body = await writes(requests)[0]!.clone().json()
    expect(body).toMatchObject({ autocompact_claude_percent: 60 })
    expect(body).not.toHaveProperty('autocompact_codex_tokens')
  })

  it('turns the compaction hooks on (off by default) and hides the switch for an older manager', async () => {
    const withHooks = { ...settings, hooks_enabled: false }
    const requests = setup('owner', async r => {
      if (path(r) === `${base}/settings`) return Response.json({ ...withHooks, ...(await r.clone().json()) })
      if (path(r) === `${base}/status`) return Response.json({ ...status(), settings: withHooks })
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const set = await openTab('运行设置')
    const group = await within(set).findByRole('group', { name: '压缩时的钩子' })
    const box = within(group).getByLabelText(/压缩上下文时，提醒它保留要紧的内容/)
    expect(box).not.toBeChecked()
    expect(within(group).getByText(/默认关/)).toBeVisible()
    // 没改就不提交这一项
    await fireEvent.click(within(set).getByRole('button', { name: '保存设置' }))
    await waitFor(() => expect(writes(requests)).toHaveLength(1))
    expect(await writes(requests)[0]!.clone().json()).not.toHaveProperty('hooks_enabled')
    const save = await within(set).findByRole('button', { name: '保存设置' })
    await fireEvent.click(within(set).getByLabelText(/压缩上下文时，提醒它保留要紧的内容/))
    await fireEvent.click(save)
    await waitFor(() => expect(writes(requests)).toHaveLength(2))
    expect(await writes(requests)[1]!.clone().json()).toMatchObject({ hooks_enabled: true })
  })

  it('shows no hooks switch for a manager that does not know it', async () => {
    setup('owner', async r => (path(r) === `${base}/status` ? Response.json({ ...status(), settings }) : undefined))
    await openApp('/llm_bridge?view=manage')
    const set = await openTab('运行设置')
    await within(set).findByRole('button', { name: '保存设置' })
    expect(within(set).queryByRole('group', { name: '压缩时的钩子' })).toBeNull()
  })

  it('saves settings within the backend limits and environments in dev mode with an exec warning', async () => {
    const requests = setup('owner', async r => {
      if (path(r) === `${base}/settings`) return Response.json({ ...settings, ...(await r.clone().json()) })
      if (path(r) === `${base}/environments` && r.method === 'POST') return Response.json({ id: 'be_0123456789abcdef0123', ...(await r.clone().json()) }, { status: 201 })
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const set = await openTab('运行设置')
    const concurrency = await within(set).findByLabelText('每个平台同时进行的会话数')
    expect(concurrency).toHaveValue(2)
    expect(concurrency).toHaveAttribute('max', '16')
    await fireEvent.update(concurrency, '17')
    await fireEvent.click(within(set).getByRole('button', { name: '保存设置' }))
    expect(await within(set).findByText('「每个平台同时进行的会话数」要填 1–16 之间的整数。')).toBeVisible()
    expect(writes(requests)).toEqual([])
    await fireEvent.update(concurrency, '4')
    await fireEvent.click(within(set).getByRole('button', { name: '保存设置' }))
    expect(await within(set).findByText('设置已保存。')).toBeVisible()
    expect(await writes(requests)[0]!.clone().json()).toEqual({ concurrency: 4, min_start_interval_seconds: 2, turn_timeout_seconds: 1800, send_per_hour: 60, max_text_chars: 20000, retention_days: 0, auto_resume_max: 3, auto_resume: true, resume_text: '继续意外中断的任务' })

    // 额度用完后自动继续（T-0237）：开关、次数、发的那句话；那句话不能空
    const resume = within(set).getByRole('group', { name: '额度用完后自动继续' })
    const sentence = within(resume).getByLabelText('自动继续时发的那句话')
    expect(sentence).toHaveValue('继续意外中断的任务')
    await fireEvent.update(sentence, '  ')
    await fireEvent.click(within(set).getByRole('button', { name: '保存设置' }))
    expect(await within(set).findByText('「自动继续时发的那句话」要填 1–200 个字。')).toBeVisible()
    await fireEvent.update(sentence, '接着做')
    await fireEvent.click(within(resume).getByRole('checkbox'))
    await fireEvent.update(within(set).getByLabelText('同一次中断最多自动继续几次'), '5')
    await fireEvent.click(within(set).getByRole('button', { name: '保存设置' }))
    await waitFor(() => expect(writes(requests)).toHaveLength(2))
    expect(await writes(requests)[1]!.clone().json()).toMatchObject({ auto_resume: false, auto_resume_max: 5, resume_text: '接着做' })

    const env = await openTab('开发环境')
    await fireEvent.click(await within(env).findByRole('button', { name: '新增环境' }))
    const form = within(env).getByRole('form', { name: '新增开发环境' })
    expect(within(form).queryByRole('option', { name: 'ChatGPT' })).not.toBeInTheDocument()
    await fireEvent.update(within(form).getByLabelText('平台'), 'claude')
    await fireEvent.update(within(form).getByLabelText('名字'), '工作台仓库')
    await fireEvent.update(within(form).getByLabelText('权限'), 'exec')
    expect(within(form).getByRole('note')).toHaveTextContent('命令以容器里的普通用户身份运行，没有操作系统级沙箱，能读到这个 MCP 管理器数据卷里的其他内容。')
    await fireEvent.update(within(form).getByLabelText('代码仓库地址（可不填）'), 'http://example.com/repo')
    await fireEvent.click(within(form).getByRole('button', { name: '保存' }))
    expect(await within(env).findByText('代码仓库地址只支持 https:// 开头的地址。')).toBeVisible()
    await fireEvent.update(within(form).getByLabelText('代码仓库地址（可不填）'), 'https://github.com/o/r')
    await fireEvent.click(within(form).getByLabelText('允许联网'))
    await fireEvent.click(within(form).getByRole('button', { name: '保存' }))
    expect(await within(env).findByText('已新增环境「工作台仓库」。')).toBeVisible()
    expect(await writes(requests)[2]!.clone().json()).toEqual({ platform: 'claude', mode: 'dev', name: '工作台仓库', repo_url: 'https://github.com/o/r', branch: null, sandbox: 'exec', network: true, description: '', enabled: true })
  })

  it('shows the quota of each platform, when it resets and that it is paused, and asks the platform on request', async () => {
    const resets = 1794531600, seen = 1794528000
    const quota: Schema['LlmBridgeStatusOut']['quota'] = {
      codex: { can_refresh: true, reports_quota: true, paused_until: null, pause_estimated: false, waiting_resume: 0, tokens_5h: 12345, windows: [
        { kind: 'five_hour', used_percent: 42, resets_at: resets, reset_passed: false, status: 'ok', plan: 'plus', observed_at: seen },
        { kind: 'weekly', used_percent: null, resets_at: resets - 9000, reset_passed: true, status: 'ok', plan: null, observed_at: seen },
      ] },
      claude: { can_refresh: false, reports_quota: true, paused_until: resets, pause_estimated: true, waiting_resume: 2, tokens_5h: 0, windows: [
        { kind: 'five_hour', used_percent: 100, resets_at: null, reset_passed: false, status: 'exceeded', plan: null, observed_at: seen },
      ] },
      workbuddy: { can_refresh: false, reports_quota: false, paused_until: null, pause_estimated: false, waiting_resume: 0, tokens_5h: 0, windows: [] },
    }
    const requests = setup('owner', r => {
      if (path(r) === `${base}/status`) return Response.json(status({ quota }))
      if (path(r) === `${base}/quota`) return Response.json({ platforms: quota })
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const codex = await screen.findByRole('article', { name: '额度：Codex' })
    expect(within(codex).getByText('5 小时额度')).toBeVisible()
    expect(within(codex).getByText('已用 42%，剩余 58%')).toBeVisible()
    expect(within(codex).getByRole('progressbar', { name: '5 小时额度已用' })).toHaveValue(42)
    expect(within(codex).getByText('plus')).toBeVisible()
    expect(within(codex).getByText('已过重置时间，等下一次回合确认')).toBeVisible()
    expect(within(codex).getByText(/最近 5 小时经这个管理器用掉 12,345 个 token/)).toBeVisible()
    const claude = screen.getByRole('article', { name: '额度：Claude' })
    expect(within(claude).getByText('已暂停')).toBeVisible()
    expect(within(claude).getByRole('note')).toHaveTextContent(/额度用完，这个平台暂停到 .+（平台没有给出重置时间，这是估计的）；到时间后自动恢复。\s*有 2 个被打断的会话在等自动继续。/)
    expect(within(claude).getByText('已用 100%，剩余 0%')).toBeVisible()
    expect(within(claude).getByText('平台没有给出')).toBeVisible()
    expect(within(screen.getByRole('article', { name: '额度：CodeBuddy' })).getByText(/官方程序不提供剩余积分和重置时间/)).toBeVisible()

    await fireEvent.click(screen.getByRole('button', { name: '向平台查一次' }))
    await waitFor(() => expect(requests.some(r => path(r) === `${base}/quota` && new URL(r.url).searchParams.get('refresh') === 'true')).toBe(true))
    expect(writes(requests)).toEqual([])
  })

  it('shows whether the manager\u2019s server can use each platform, with latency, and rechecks on request', async () => {
    const target = (host: string, label: string, state: string, latency_ms: number | null, error: string | null = null): Schema['LlmBridgeConnectivityTargetOut'] => ({ host, label, ok: state === 'ok', state, latency_ms, http_status: state === 'unreachable' ? null : state === 'ok' ? 401 : 403, error })
    const first: Schema['LlmBridgeConnectivityOut'] = { supported: true, slots: {
      openai: { ok: false, need: 'all', checked_at: 1794528000, targets: [
        target('chatgpt.com', 'ChatGPT（订阅登录、对话）', 'ok', 35),
        target('auth.openai.com', 'OpenAI 登录（设备码）', 'blocked', 12, '平台拒绝了这台服务器的访问（HTTP 403，拦截页）'),
        target('api.openai.com', 'OpenAI API（API 密钥）', 'unreachable', null, '超时（8 秒内没有应答）'),
      ] },
      claude: { ok: true, need: 'all', checked_at: 1794528000, targets: [target('api.anthropic.com', 'Anthropic API', 'ok', 1800), target('claude.ai', 'Claude（订阅登录）', 'ok', 90)] },
    } }
    const fixed: Schema['LlmBridgeConnectivityOut'] = { supported: true, slots: { ...first.slots, openai: { ok: true, need: 'all', checked_at: 1794528060, targets: first.slots.openai!.targets.map(t => target(t.host, t.label, 'ok', 40)) } } }
    const requests = setup('owner', r => {
      if (path(r) !== `${base}/connectivity`) return undefined
      return Response.json(new URL(r.url).searchParams.get('refresh') === 'true' ? fixed : first)
    })
    await openApp('/llm_bridge?view=manage')
    const openai = await screen.findByRole('group', { name: '平台连通性：Codex' })
    expect(await within(openai).findByText('这台服务器用不了')).toBeVisible()
    const rows = within(openai).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('chatgpt.com')
    expect(rows[0]).toHaveTextContent('可用')
    expect(rows[0]).toHaveTextContent('35 ms')
    // 连得上但被平台拒绝：延迟照样显示，并写明原因
    expect(rows[1]).toHaveTextContent('被平台拒绝')
    expect(rows[1]).toHaveTextContent('12 ms')
    expect(rows[1]).toHaveTextContent('平台拒绝了这台服务器的访问（HTTP 403，拦截页）')
    expect(rows[2]).toHaveTextContent('连不上')
    expect(rows[2]).toHaveTextContent('超时（8 秒内没有应答）')
    expect(within(openai).getByRole('alert')).toHaveTextContent(/用不了Codex：登录和对话都会失败。\s*网络是通的，但平台拒绝了这台服务器/)
    // 可用但延迟高：提醒一句
    const claude = screen.getByRole('group', { name: '平台连通性：Claude' })
    expect(within(claude).getAllByText('可用').length).toBeGreaterThan(0)
    expect(within(claude).getByText('1800 ms')).toBeVisible()
    expect(within(claude).getByText('延迟比较高：能用，但登录和回答会慢一些。')).toBeVisible()
    expect(within(claude).queryByRole('alert')).not.toBeInTheDocument()
    // 旧版本的管理器没有分步用时：说明这个数是总耗时，不是网络延迟（T-0278）
    expect(within(claude).getByText(/这个 MCP 管理器是旧版本：毫秒数是完整访问一次的总耗时/)).toBeVisible()
    expect(within(claude).queryByLabelText(/^每一步的用时/)).not.toBeInTheDocument()

    await fireEvent.click(screen.getByRole('button', { name: '重新检测连通性' }))
    await waitFor(() => expect(within(openai).queryByRole('alert')).not.toBeInTheDocument())
    expect(within(openai).getAllByText('40 ms')).toHaveLength(3)
    expect(requests.some(r => path(r) === `${base}/connectivity` && new URL(r.url).searchParams.get('refresh') === 'true')).toBe(true)
    expect(writes(requests)).toEqual([])
  })

  it('shows the connect time as latency, times each step and points at slow name resolution', async () => {
    type Target = Schema['LlmBridgeConnectivityTargetOut']
    const timings = (dns_ms: number | null, connect_ms: number | null, tls_ms: number | null, response_ms: number | null) => ({ dns_ms, connect_ms, tls_ms, response_ms })
    const ok = (host: string, label: string, t: Target['timings'], extra: Partial<Target> = {}): Target => ({ host, label, ok: true, state: 'ok', latency_ms: t!.connect_ms ?? null, http_status: 401, error: null, timings: t, stage: null, address: '162.159.140.245', via_proxy: false, ...extra })
    const data: Schema['LlmBridgeConnectivityOut'] = { supported: true, slots: {
      openai: { ok: false, need: 'all', checked_at: 1794528000, targets: [
        ok('chatgpt.com', 'ChatGPT（订阅登录、对话）', timings(4012, 2, 9, 380)),
        ok('auth.openai.com', 'OpenAI 登录（设备码）', timings(4102, 2, 11, 3500)),
        { host: 'api.openai.com', label: 'OpenAI API（API 密钥）', ok: false, state: 'unreachable', latency_ms: null, http_status: null, error: '域名解析超时：没有解析出平台的地址（共等了 12 秒）', timings: timings(null, null, null, null), stage: 'dns', address: null, via_proxy: false },
      ] },
      claude: { ok: true, need: 'all', checked_at: 1794528000, targets: [
        ok('api.anthropic.com', 'Anthropic API', timings(null, 120, 30, 200), { address: null, via_proxy: true }),
        ok('claude.ai', 'Claude（订阅登录）', timings(null, 110, 28, 150), { address: null, via_proxy: true }),
      ] },
    } }
    // 账号还没登录：检测没连上就是用不了（登录了的情况见下一个用例）
    const loggedOut = status({ accounts: [account({}), account({ slot: 'claude', label: 'Claude' })] })
    setup('owner', r => (path(r) === `${base}/connectivity` ? Response.json(data) : path(r) === `${base}/status` ? Response.json(loggedOut) : undefined))
    await openApp('/llm_bridge?view=manage')
    const openai = await screen.findByRole('group', { name: '平台连通性：Codex' })
    const rows = await within(openai).findAllByRole('listitem')
    // 延迟是建立连接的时间（和 ping 可比），每一步分开列出，慢的那一步标出来
    expect(within(rows[0]!).getByText('2 ms')).toBeVisible()
    const steps = within(openai).getByLabelText('每一步的用时：chatgpt.com')
    expect(steps).toHaveTextContent('解析 4012 ms')
    expect(steps).toHaveTextContent('连接 2 ms')
    expect(steps).toHaveTextContent('加密握手 9 ms')
    expect(steps).toHaveTextContent('平台应答 380 ms')
    expect(steps).toHaveTextContent('IPv4 162.159.140.245')
    expect(within(steps).getByText('解析 4012 ms')).toHaveClass('is-slow')
    expect(within(steps).getByText('连接 2 ms')).not.toHaveClass('is-slow')
    // 卡在哪一步写清楚
    expect(within(openai).getByLabelText('每一步的用时：api.openai.com')).toHaveTextContent('解析没有完成')
    expect(rows[2]).toHaveTextContent('域名解析超时：没有解析出平台的地址（共等了 12 秒）')
    // 用不了的原因是解析慢，不让人去换服务器
    const alert = within(openai).getByRole('alert')
    expect(alert).toHaveTextContent('卡在域名解析上（见下面），不一定是这台服务器访问不了平台。')
    expect(alert).not.toHaveTextContent('部署到能访问这个平台的服务器上')
    expect(within(openai).getByText(/域名解析慢（最慢 4\.1 秒）：MCP 管理器的容器每查一次域名都要等好几秒/)).toHaveTextContent('建立连接最多只用了 2 ms，网络本身不慢。')
    expect(within(openai).getByText('平台应答慢（最慢 3.5 秒）：网络本身没问题，是平台那边回得慢。')).toBeVisible()
    expect(within(openai).getByText(/延迟是建立连接用的时间（一次网络往返），可以和服务器上 ping 的结果对照。/)).toBeVisible()
    expect(within(openai).queryByText(/这个 MCP 管理器是旧版本/)).not.toBeInTheDocument()
    expect(screen.getAllByText('连不上 · 域名解析慢').length).toBeGreaterThan(0)
    // 经 HTTPS 代理：域名由代理解析，没有解析这一步
    const claude = screen.getByRole('group', { name: '平台连通性：Claude' })
    const viaProxy = within(claude).getByLabelText('每一步的用时：api.anthropic.com')
    expect(viaProxy).toHaveTextContent('经 HTTPS 代理（域名由代理解析）')
    expect(viaProxy).not.toHaveTextContent('解析 ')
    expect(within(claude).queryByText(/域名解析慢/)).not.toBeInTheDocument()
    expect(screen.getAllByText('可用 · 110 ms').length).toBeGreaterThan(0)
  })

  it('lists the addresses it tried, explains an IPv6 that does not answer and trusts a working login', async () => {
    // T-0284：管理器像官方程序一样，一个地址 0.25 秒没连上就试下一个；这里列出每个地址，
    // IPv6 不通、IPv4 通时说明官方程序照常能用；账号已经登录时不再断言“登录和对话都会失败”
    type Target = Schema['LlmBridgeConnectivityTargetOut']
    const timings = (dns_ms: number | null, connect_ms: number | null, tls_ms: number | null, response_ms: number | null) => ({ dns_ms, connect_ms, tls_ms, response_ms })
    const failed = (host: string, label: string, dns: number, attempts: Target['attempts']): Target => ({ host, label, ok: false, state: 'unreachable', latency_ms: null, http_status: null, error: '建立连接超时：平台的服务器没有回应（共等了 12 秒）', timings: timings(dns, null, null, null), stage: 'connect', address: null, via_proxy: false, attempts })
    const data: Schema['LlmBridgeConnectivityOut'] = { supported: true, slots: {
      openai: { ok: false, need: 'all', checked_at: 1794528000, targets: [
        { host: 'chatgpt.com', label: 'ChatGPT（订阅登录、对话）', ok: true, state: 'ok', latency_ms: 2, http_status: 403, error: null, timings: timings(4060, 255, 9, 300), stage: null, address: '104.18.32.47', via_proxy: false,
          attempts: [{ address: '2606:4700::6812:202f', family: 6, ms: 253, error: '没有回应' }, { address: '104.18.32.47', family: 4, ms: 2, error: null }] },
        failed('api.openai.com', 'OpenAI API（API 密钥）', 8068, [{ address: '2606:4700::6810:7b60', family: 6, ms: 3932, error: '没有回应' }]),
      ] },
      claude: { ok: false, need: 'all', checked_at: 1794528000, targets: [
        failed('api.anthropic.com', 'Anthropic API', 8072, [{ address: '160.79.104.10', family: 4, ms: 3928, error: '没有回应' }]),
      ] },
    } }
    setup('owner', r => (path(r) === `${base}/connectivity` ? Response.json(data) : undefined))
    await openApp('/llm_bridge?view=manage')
    const openai = await screen.findByRole('group', { name: '平台连通性：Codex' })
    const tried = await within(openai).findByLabelText('试过的地址：chatgpt.com')
    expect(within(tried).getByText('IPv6 2606:4700::6812:202f 0.3 秒没有回应')).toHaveClass('is-slow')
    expect(within(tried).getByText('IPv4 104.18.32.47 连上 2 ms')).not.toHaveClass('is-slow')
    // 延迟只算连上的那个地址；IP 已经在“试过的地址”里，不再单独写
    expect(within(openai).getAllByRole('listitem')[0]).toHaveTextContent('2 ms')
    expect(within(openai).getByLabelText('每一步的用时：chatgpt.com')).not.toHaveTextContent('104.18.32.47')
    expect(within(openai).getByLabelText('试过的地址：api.openai.com')).toHaveTextContent('IPv6 2606:4700::6810:7b60 3.9 秒没有回应')
    expect(within(openai).getByText(/这台服务器连平台的 IPv6 地址没有回应，IPv4 是通的/)).toBeVisible()
    // Codex 已经登录：以对话为准，不说“登录和对话都会失败”
    expect(within(openai).getByText(/检测没连上Codex，但这个账号已经登录。对话照常能用的话以对话为准/)).toBeVisible()
    expect(within(openai).queryByRole('alert')).not.toBeInTheDocument()
    expect(within(openai).getByText(/域名解析慢/)).toHaveTextContent('建立连接最多只用了 2 ms，网络本身不慢。')
    // Claude 没登录、一个地址都没连上：照常说用不了；没有连上过的地址，不写“建立连接最多只用了 0 ms”
    const claude = screen.getByRole('group', { name: '平台连通性：Claude' })
    expect(within(claude).getByRole('alert')).toHaveTextContent('这个 MCP 管理器所在的服务器现在用不了Claude')
    expect(within(claude).getByText(/域名解析慢/)).not.toHaveTextContent('建立连接最多只用了')
    expect(within(claude).queryByText(/IPv6/)).not.toBeInTheDocument()
  })

  it('lists sessions, syncs on request and shows the turns of a session', async () => {
    const session = { session_id: 'bs_0123456789abcdef0123', platform: 'codex', mode: 'dev', model: 'gpt-5', title: '修复登录页', status: 'active', origin: 'bridge', owner: '甲', created_at: 1794528000, updated_at: 1794528600, turns: 1 }
    const requests = setup('owner', r => {
      if (path(r) === `${base}/sessions`) return Response.json({ sessions: [session], sync_failed: new URL(r.url).searchParams.get('sync') === 'true' ? 'Claude 没有登录' : '' })
      if (path(r) === `${base}/sessions/${session.session_id}/turns`) return Response.json({ turns: [{ turn_id: 'bt_1', status: 'failed', text: '把按钮改成蓝色', result: null, failure: { code: 'TIMEOUT', message: '回合超时' }, queued_at: 1794528000 }, { turn_id: 'bt_0', status: 'completed', text: '看看登录页', result: '<b>已看完</b>', queued_at: 1794527000 }] })
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    const panel = await openTab('会话')
    expect(within(panel).getByText(/看不到 chatgpt\.com、claude\.ai 网页上的对话，也看不到其他机器上的会话。/)).toBeVisible()
    const table = await within(panel).findByRole('table', { name: '会话' })
    expect(within(table).getByText('Codex · 开发')).toBeVisible()
    expect(within(table).getByText('经 MCP 管理器新建')).toBeVisible()

    await fireEvent.click(within(panel).getByRole('button', { name: '同步官方程序里的会话' }))
    expect(await within(panel).findByText('同步没有完全成功：Claude 没有登录')).toBeVisible()
    expect(requests.some(r => path(r) === `${base}/sessions` && new URL(r.url).searchParams.get('sync') === 'true')).toBe(true)

    await fireEvent.click(within(table).getByRole('button', { name: '查看会话：修复登录页' }))
    const turns = await within(panel).findByRole('region', { name: '回合：修复登录页' })
    expect(await within(turns).findByText('把按钮改成蓝色')).toBeVisible()
    expect(within(turns).getByText('回合超时')).toBeVisible()
    // 结果按纯文字显示，不当成 HTML
    expect(within(turns).getByText('<b>已看完</b>')).toBeVisible()
    expect(writes(requests)).toEqual([])
  })

  it('shows an unreachable bridge and a rejected token as readable messages', async () => {
    let mode: 'unreachable' | 'auth' = 'unreachable'
    setup('owner', r => {
      if (path(r) === `${base}/status`) return mode === 'unreachable' ? Response.json({ reachable: false, error: '连接被拒绝', version: '', accounts: [], channels: [], queued: 0, running: 0, load: {}, settings: null }) : fail(503, 'BRIDGE_AUTH_FAILED')
      if (path(r) === `${base}/environments`) return fail(503, 'EXTERNAL_UNAVAILABLE')
      return undefined
    })
    await openApp('/llm_bridge?view=manage')
    expect(await screen.findByText('连不上这个 MCP 管理器：连接被拒绝')).toBeVisible()
    // 列表里的卡片也如实显示，不是一直转圈
    const card = screen.getByRole('article', { name: 'MCP 管理器：主账号' })
    expect(await within(card).findByRole('alert')).toHaveTextContent('连不上：连接被拒绝')
    mode = 'auth'
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await within(screen.getByRole('tabpanel', { name: '平台与账号' })).findByText(/MCP 管理器不接受这枚令牌/)).toBeVisible()
    expect(await within(card).findByRole('alert')).toHaveTextContent(/MCP 管理器不接受这枚令牌/)
    const env = await openTab('开发环境')
    expect(await within(env).findByText(/MCP 管理器暂时连不上/)).toBeVisible()
    expect(screen.getByRole('heading', { level: 2, name: '管理器与账号' })).toBeVisible()
  })

  it('treats MODULE_DISABLED as 功能已停用, not as a failure', async () => {
    const requests = setup('owner', r => (path(r).startsWith('/api/v1/llm_bridge/') ? fail(404, 'MODULE_DISABLED') : undefined))
    await openApp('/llm_bridge?view=manage')
    expect(await screen.findByText('功能已停用：大模型客户端在当前工作空间没有启用。')).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '添加 MCP 管理器' })).not.toBeInTheDocument()
    expect(writes(requests)).toEqual([])
  })

  it('keeps members out of the page', async () => {
    const requests = setup('member')
    await openApp('/llm_bridge?view=manage')
    await waitFor(() => expect(screen.queryByRole('heading', { level: 2, name: '管理器与账号' })).not.toBeInTheDocument())
    expect(requests.some(r => path(r).startsWith('/api/v1/llm_bridge'))).toBe(false)
  })
})
