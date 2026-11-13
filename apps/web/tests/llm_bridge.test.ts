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
const account = (over: Partial<Schema['LlmBridgeAccountOut']>): Schema['LlmBridgeAccountOut'] => ({ slot: 'openai', label: 'OpenAI（ChatGPT / Codex）', enabled: true, state: 'logged_out', registered: false, identity: null, method: null, detail: '', first_login_at: null, login_methods: ['device_code', 'api_key', 'existing'], running: 0, ...over })
const settings: Schema['LlmBridgeSettings'] = { concurrency: 2, turn_timeout_seconds: 1800, send_per_hour: 60, max_text_chars: 20000, claude_subscription_ack: false, platforms: ['chatgpt', 'codex', 'claude', 'workbuddy'] }
const status = (over: Partial<Schema['LlmBridgeStatusOut']> = {}): Schema['LlmBridgeStatusOut'] => ({
  reachable: true, error: '', version: '0.1.0', platforms: ['chatgpt', 'codex', 'claude', 'workbuddy'], queued: 1, running: 2, settings,
  accounts: [
    account({ state: 'logged_in', registered: true, identity: 'a***@example.com', method: 'device_code', first_login_at: 1794528000 }),
    account({ slot: 'claude', label: 'Claude', login_methods: ['api_key', 'oauth_token', 'existing'] }),
    account({ slot: 'workbuddy', label: 'WorkBuddy', state: 'mismatch', registered: true, identity: 'w***', detail: '登录的账号变了', login_methods: ['existing'] }),
  ],
  channels: [{ platform: 'codex', mode: 'dev', slot: 'openai', enabled: true, available: true, note: '' }, { platform: 'claude', mode: 'cloud', slot: 'claude', enabled: true, available: false, note: '云端通道还没有实现' }],
  ...over,
})
const grant = (over: Partial<Schema['LlmBridgeGrantOut']> = {}): Schema['LlmBridgeGrantOut'] => ({ instance_id: INSTANCE, instance_name: '甲', enabled: true, can_write: true, can_account: true, can_takeover: false, platforms: ['codex'], bridge_ids: null, ...over })

function setup(role: Role, handler?: (r: Request) => Response | undefined | Promise<Response | undefined>, bridges = [bridge]) {
  return mockApi(async r => {
    const result = await handler?.(r)
    if (result) return result
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return as(role)
    if (p === '/api/v1/modules' && r.method === 'GET') return Response.json({ items: [newsModule, lbModule] })
    if (p === '/api/v1/llm_bridge/bridges' && r.method === 'GET') return Response.json({ bridges })
    if (p === `${base}/status`) return Response.json(status())
    if (p === '/api/v1/llm_bridge/grants') return Response.json({ grants: [grant()] })
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
    await openApp('/llm_bridge')
    expect(await screen.findByRole('heading', { level: 1, name: '大模型客户端管理' })).toBeVisible()
    const list = await screen.findByRole('region', { name: 'MCP 管理器' })
    expect(await within(list).findByRole('button', { name: '查看 MCP 管理器：主账号' })).toBeVisible()
    expect(within(list).getByText('http://10.0.0.5:8787')).toBeVisible()

    const openai = await screen.findByRole('article', { name: '账号：OpenAI（ChatGPT / Codex）' })
    expect(within(openai).getByText('已登录')).toBeVisible()
    expect(within(openai).getByText('a***@example.com')).toBeVisible()
    expect(within(openai).getByText('设备码登录')).toBeVisible()
    expect(within(screen.getByRole('article', { name: '账号：Claude' })).getByText('未登录')).toBeVisible()
    const workbuddy = screen.getByRole('article', { name: '账号：WorkBuddy' })
    expect(within(workbuddy).getByText('账号不一致')).toBeVisible()
    expect(within(workbuddy).getByText(/只能点“更换账号”/)).toBeVisible()
    expect(within(workbuddy).queryByRole('button', { name: '登录WorkBuddy' })).not.toBeInTheDocument()
    expect(screen.getByText('排队中 1')).toBeVisible()
    expect(screen.getByText('进行中 2')).toBeVisible()
    expect(within(screen.getByRole('table', { name: '通道' })).getByText('云端通道还没有实现')).toBeVisible()
    expect(requests.find(r => path(r) === `${base}/status`)!.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })

  it('explains what a bridge is when none is registered and registers one without keeping the token', async () => {
    let bridges: Schema['LlmBridgeOut'][] = []
    const requests = setup('owner', r => {
      if (path(r) !== '/api/v1/llm_bridge/bridges') return undefined
      if (r.method === 'POST') { bridges = [bridge]; return Response.json(bridge, { status: 201 }) }
      return Response.json({ bridges })
    })
    await openApp('/llm_bridge')
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
    for (const name of ['ChatGPT', 'Codex', 'Claude', 'WorkBuddy']) expect(within(choose).getByLabelText(name)).toBeChecked()
    for (const name of ['ChatGPT', 'Codex', 'Claude', 'WorkBuddy']) await fireEvent.click(within(choose).getByLabelText(name))
    await fireEvent.click(within(form).getByRole('button', { name: '添加' }))
    expect(await screen.findByText('至少勾选一个要控制的平台。')).toBeVisible()
    expect(writes(requests)).toEqual([])
    // 人类的例子：1 号管理器控制 Claude / WorkBuddy（勾选的先后不影响提交的顺序）
    await fireEvent.click(within(choose).getByLabelText('WorkBuddy'))
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
    await openApp('/llm_bridge')
    expect(await screen.findByText('只有 owner 能修改大模型客户端；你看到的是只读视图。')).toBeVisible()
    await screen.findByRole('article', { name: '账号：Claude' })
    for (const name of [/^添加 MCP 管理器$/, /^修改/, /^删除/, /^停用/, /^登录/, /^退出登录/, /^更换账号/]) expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    // 重新检查只是读状态，admin 可以点
    expect(screen.getByRole('button', { name: '重新检查Claude' })).toBeEnabled()

    const grants = await screen.findByRole('group', { name: '实例权限：甲' })
    expect(grants).toBeDisabled()
    const env = await openTab('开发环境')
    expect(await within(env).findByText('工作台仓库')).toBeVisible()
    expect(within(env).getByText('可写工作区')).toBeVisible()
    expect(within(env).queryByRole('button')).not.toBeInTheDocument()
    const set = await openTab('设置')
    expect(within(set).getByLabelText('同时进行的回合数')).toBeDisabled()
    expect(within(set).queryByRole('button', { name: '保存设置' })).not.toBeInTheDocument()
    expect(writes(requests)).toEqual([])
  })

  it('shows the device code, polls the login state and refreshes the status when it is done', { timeout: 20000 }, async () => {
    let polls = 0, loggedIn = false
    const requests = setup('owner', r => {
      if (path(r) === `${base}/status`) return Response.json(status({ accounts: [account({ state: loggedIn ? 'logged_in' : 'logged_out', identity: loggedIn ? 'n***@example.com' : null })] }))
      if (path(r) !== `${base}/accounts/openai/login`) return undefined
      if (r.method === 'POST') return Response.json({ state: 'pending', verification_url: 'https://auth.example.com/device', user_code: 'ABCD-1234', detail: '' })
      polls += 1
      if (polls >= 2) loggedIn = true
      return Response.json({ state: loggedIn ? 'done' : 'pending', detail: '' })
    })
    const view = await openApp('/llm_bridge')
    const card = await screen.findByRole('article', { name: '账号：OpenAI（ChatGPT / Codex）' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录OpenAI（ChatGPT / Codex）' }))
    const form = within(card).getByRole('form', { name: '登录OpenAI（ChatGPT / Codex）' })
    await fireEvent.click(within(form).getByLabelText('设备码登录'))
    await fireEvent.click(within(form).getByRole('button', { name: '获取验证码' }))

    const device = await screen.findByRole('region', { name: '设备码登录' })
    expect(within(device).getByText('https://auth.example.com/device')).toBeVisible()
    expect(within(device).getByText('ABCD-1234')).toBeVisible()
    expect(await writes(requests)[0]!.clone().json()).toEqual({ method: 'device_code' })

    expect(await screen.findByText('「OpenAI（ChatGPT / Codex）」已登录。', {}, { timeout: 10000 })).toBeVisible()
    expect(polls).toBe(2)
    expect(screen.queryByRole('region', { name: '设备码登录' })).not.toBeInTheDocument()
    expect(await screen.findByText('n***@example.com')).toBeVisible()

    // 登录完成后不再轮询；离开页面时也不会留下定时器
    view.unmount()
    await new Promise(resolve => setTimeout(resolve, 3200))
    expect(polls).toBe(2)
  })

  it('stops polling when the page is left while a device login is pending', { timeout: 20000 }, async () => {
    let polls = 0
    setup('owner', r => {
      if (path(r) !== `${base}/accounts/openai/login`) return undefined
      if (r.method === 'POST') return Response.json({ state: 'pending', verification_url: 'https://auth.example.com/device', user_code: 'ABCD-1234', detail: '' })
      polls += 1
      return Response.json({ state: 'pending', detail: '' })
    })
    const view = await openApp('/llm_bridge')
    const card = await screen.findByRole('article', { name: '账号：OpenAI（ChatGPT / Codex）' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录OpenAI（ChatGPT / Codex）' }))
    await fireEvent.click(within(card).getByLabelText('设备码登录'))
    await fireEvent.click(within(card).getByRole('button', { name: '获取验证码' }))
    await screen.findByRole('region', { name: '设备码登录' })
    await waitFor(() => expect(polls).toBe(1), { timeout: 5000 })
    view.unmount()
    await new Promise(resolve => setTimeout(resolve, 3200))
    expect(polls).toBe(1)
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
    await openApp('/llm_bridge')
    const card = await screen.findByRole('article', { name: '账号：Claude' })
    await fireEvent.click(within(card).getByRole('button', { name: '登录Claude' }))
    const form = within(card).getByRole('form', { name: '登录Claude' })
    // 默认是 API 密钥：不需要确认
    expect(within(form).getByLabelText('API 密钥', { selector: 'input[type="radio"]' })).toBeChecked()
    expect(within(form).queryByRole('group', { name: 'Claude 订阅登录的风险确认' })).not.toBeInTheDocument()

    await fireEvent.click(within(form).getByLabelText('订阅令牌', { selector: 'input[type="radio"]' }))
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
    await openApp('/llm_bridge')
    const card = await screen.findByRole('article', { name: '账号：WorkBuddy' })
    await fireEvent.click(within(card).getByRole('button', { name: '更换账号WorkBuddy' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('这个 MCP 管理器上这个平台的全部会话都会被归档'))
    expect(writes(requests)).toEqual([])
    await fireEvent.click(within(card).getByRole('button', { name: '更换账号WorkBuddy' }))
    expect(await screen.findByText('已归档 3 个会话；现在可以登录新的账号。')).toBeVisible()
  })

  it('sends the whole grant on every toggle; disabling clears the flags but keeps the chosen platforms', async () => {
    let current = grant()
    const requests = setup('owner', async r => {
      if (path(r) === '/api/v1/llm_bridge/grants') return Response.json({ grants: [current] })
      if (path(r) === `/api/v1/llm_bridge/grants/${INSTANCE}` && r.method === 'PUT') { current = { ...current, ...(await r.clone().json()) }; return Response.json(current) }
      return undefined
    })
    await openApp('/llm_bridge')
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
      { enabled: true, can_write: true, can_account: true, can_takeover: true, platforms: ['codex'], bridge_ids: null },
      { enabled: true, can_write: true, can_account: true, can_takeover: true, platforms: ['codex', 'claude'], bridge_ids: null },
      // 关掉时三项权限收回，已选的平台保留（再打开时不用重选）
      { enabled: false, can_write: false, can_account: false, can_takeover: false, platforms: ['codex', 'claude'], bridge_ids: null },
    ])
    for (const name of ['新建会话和发送', '查看账号下全部会话', '接手别人的会话']) { expect(within(row).getByLabelText(name)).not.toBeChecked(); expect(within(row).getByLabelText(name)).toBeDisabled() }
  })

  it('shows every manager with the platforms it controls and each manager its own accounts', async () => {
    // 人类的例子：1 号控制 Claude / WorkBuddy，2 号控制 Codex / Claude / WorkBuddy；同一个平台在两个管理器上是不同的账号
    const one = { ...bridge, name: '1 号' }, two = { ...bridge, id: OTHER, name: '2 号', base_url: 'http://llm-bridge-2:8787' }
    const claude = (identity: string) => account({ slot: 'claude', label: 'Claude', state: 'logged_in', registered: true, identity, method: 'api_key', login_methods: ['api_key'] })
    const idle = account({ slot: 'workbuddy', label: 'WorkBuddy', login_methods: ['api_key'] })
    setup('owner', r => {
      if (path(r) === `${base}/status`) return Response.json(status({ platforms: ['claude', 'workbuddy'], accounts: [account({ enabled: false }), claude('a***@example.com'), idle], channels: [{ platform: 'codex', mode: 'dev', slot: 'openai', enabled: false, available: false, note: '' }, { platform: 'claude', mode: 'chat', slot: 'claude', enabled: true, available: true, note: '' }] }))
      if (path(r) === `/api/v1/llm_bridge/bridges/${OTHER}/status`) return Response.json(status({ platforms: ['codex', 'claude', 'workbuddy'], accounts: [account({}), claude('b***@example.com'), idle] }))
      return undefined
    }, [one, two])
    await openApp('/llm_bridge')
    const first = await screen.findByRole('article', { name: 'MCP 管理器：1 号' })
    const second = screen.getByRole('article', { name: 'MCP 管理器：2 号' })
    expect(first).toHaveAttribute('aria-current', 'true')
    const names = (card: HTMLElement, label: string) => within(card).findByRole('list', { name: label }).then(list => within(list).getAllByRole('listitem').map(li => li.textContent))
    expect(await names(first, '1 号控制的平台')).toEqual(['Claude已登录', 'WorkBuddy未登录'])
    expect(await names(second, '2 号控制的平台')).toEqual(['Codex未登录', 'Claude已登录', 'WorkBuddy未登录'])

    // 1 号的详情：只有它控制的平台的账号和通道；没勾选的 ChatGPT / Codex 没登录过，不显示
    const panel = await screen.findByRole('tabpanel', { name: '平台与账号' })
    expect(await within(panel).findByText('a***@example.com')).toBeVisible()
    expect(within(panel).queryByRole('article', { name: '账号：OpenAI（ChatGPT / Codex）' })).not.toBeInTheDocument()
    const control = within(panel).getByRole('group', { name: '控制的平台' })
    expect(within(control).getByLabelText('Claude')).toBeChecked()
    expect(within(control).getByLabelText('Codex')).not.toBeChecked()
    expect(within(within(panel).getByRole('table', { name: '通道' })).queryByText('Codex')).not.toBeInTheDocument()

    // 换到 2 号：另一套账号
    await fireEvent.click(within(second).getByRole('button', { name: '查看 MCP 管理器：2 号' }))
    expect(await within(await screen.findByRole('tabpanel', { name: '平台与账号' })).findByText('b***@example.com')).toBeVisible()
    expect(screen.queryByText('a***@example.com')).not.toBeInTheDocument()
    expect(second).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: '登录OpenAI（ChatGPT / Codex）' })).toBeVisible()
  })

  it('changes the controlled platforms after confirming, never to none, and only offers logout for an unchecked platform', async () => {
    let platforms = ['codex', 'claude']
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const requests = setup('owner', async r => {
      if (path(r) === `${base}/status`) return Response.json(status({ platforms, accounts: [account({ enabled: platforms.includes('codex'), state: 'logged_in', registered: true, identity: 'a***@example.com', method: 'device_code' }), account({ slot: 'claude', label: 'Claude', enabled: platforms.includes('claude'), login_methods: ['api_key'] }), account({ slot: 'workbuddy', label: 'WorkBuddy', enabled: platforms.includes('workbuddy'), login_methods: ['api_key'] })] }))
      if (path(r) === `${base}/settings` && r.method === 'PUT') { platforms = (await r.clone().json()).platforms; return Response.json({ ...settings, platforms }) }
      return undefined
    })
    await openApp('/llm_bridge')
    const control = () => screen.findByRole('group', { name: '控制的平台' })
    expect(within(await control()).getByLabelText('Codex')).toBeChecked()
    // 没勾选、也没登录过的平台不占地方
    expect(screen.queryByRole('article', { name: '账号：WorkBuddy' })).not.toBeInTheDocument()

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
    const openai = await screen.findByRole('article', { name: '账号：OpenAI（ChatGPT / Codex）' })
    expect(await within(openai).findByText(/这个管理器没有勾选这个平台/)).toBeVisible()
    expect(within(openai).getByRole('button', { name: '退出登录OpenAI（ChatGPT / Codex）' })).toBeVisible()
    expect(within(openai).queryByRole('button', { name: '登录OpenAI（ChatGPT / Codex）' })).not.toBeInTheDocument()

    // 最后一个不能取消
    await fireEvent.click(within(await control()).getByLabelText('Claude'))
    expect(await screen.findByText(/^至少要控制一个平台。/)).toBeVisible()
    expect(writes(requests)).toHaveLength(1)

    // 勾选新的平台不用确认
    confirm.mockClear()
    await fireEvent.click(within(await control()).getByLabelText('WorkBuddy'))
    expect(await screen.findByText(/^已让「主账号」控制 WorkBuddy。接下来在下面登录它的账号。$/)).toBeVisible()
    expect(confirm).not.toHaveBeenCalled()
    expect(await writes(requests)[1]!.clone().json()).toEqual({ platforms: ['claude', 'workbuddy'] })
    expect(await screen.findByRole('article', { name: '账号：WorkBuddy' })).toBeVisible()
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
    await openApp('/llm_bridge')
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

  it('saves settings within the backend limits and environments in dev mode with an exec warning', async () => {
    const requests = setup('owner', async r => {
      if (path(r) === `${base}/settings`) return Response.json({ ...settings, ...(await r.clone().json()) })
      if (path(r) === `${base}/environments` && r.method === 'POST') return Response.json({ id: 'be_0123456789abcdef0123', ...(await r.clone().json()) }, { status: 201 })
      return undefined
    })
    await openApp('/llm_bridge')
    const set = await openTab('设置')
    const concurrency = await within(set).findByLabelText('同时进行的回合数')
    expect(concurrency).toHaveValue(2)
    expect(concurrency).toHaveAttribute('max', '16')
    await fireEvent.update(concurrency, '17')
    await fireEvent.click(within(set).getByRole('button', { name: '保存设置' }))
    expect(await within(set).findByText('「同时进行的回合数」要填 1–16 之间的整数。')).toBeVisible()
    expect(writes(requests)).toEqual([])
    await fireEvent.update(concurrency, '4')
    await fireEvent.click(within(set).getByRole('button', { name: '保存设置' }))
    expect(await within(set).findByText('设置已保存。')).toBeVisible()
    expect(await writes(requests)[0]!.clone().json()).toEqual({ concurrency: 4, turn_timeout_seconds: 1800, send_per_hour: 60, max_text_chars: 20000 })

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
    expect(await writes(requests)[1]!.clone().json()).toEqual({ platform: 'claude', mode: 'dev', name: '工作台仓库', repo_url: 'https://github.com/o/r', branch: null, sandbox: 'exec', network: true, description: '', enabled: true })
  })

  it('lists sessions, syncs on request and shows the turns of a session', async () => {
    const session = { session_id: 'bs_0123456789abcdef0123', platform: 'codex', mode: 'dev', model: 'gpt-5', title: '修复登录页', status: 'active', origin: 'bridge', owner: '甲', created_at: 1794528000, updated_at: 1794528600, turns: 1 }
    const requests = setup('owner', r => {
      if (path(r) === `${base}/sessions`) return Response.json({ sessions: [session], sync_failed: new URL(r.url).searchParams.get('sync') === 'true' ? 'Claude 没有登录' : '' })
      if (path(r) === `${base}/sessions/${session.session_id}/turns`) return Response.json({ turns: [{ turn_id: 'bt_1', status: 'failed', text: '把按钮改成蓝色', result: null, failure: { code: 'TIMEOUT', message: '回合超时' }, queued_at: 1794528000 }, { turn_id: 'bt_0', status: 'completed', text: '看看登录页', result: '<b>已看完</b>', queued_at: 1794527000 }] })
      return undefined
    })
    await openApp('/llm_bridge')
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
      if (path(r) === `${base}/status`) return mode === 'unreachable' ? Response.json({ reachable: false, error: '连接被拒绝', version: '', accounts: [], channels: [], queued: 0, running: 0, settings: null }) : fail(503, 'BRIDGE_AUTH_FAILED')
      if (path(r) === `${base}/environments`) return fail(503, 'EXTERNAL_UNAVAILABLE')
      return undefined
    })
    await openApp('/llm_bridge')
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
    expect(screen.getByRole('heading', { level: 1, name: '大模型客户端管理' })).toBeVisible()
  })

  it('treats MODULE_DISABLED as 功能已停用, not as a failure', async () => {
    const requests = setup('owner', r => (path(r).startsWith('/api/v1/llm_bridge/') ? fail(404, 'MODULE_DISABLED') : undefined))
    await openApp('/llm_bridge')
    expect(await screen.findByText('功能已停用：大模型客户端在当前工作空间没有启用。')).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '添加 MCP 管理器' })).not.toBeInTheDocument()
    expect(writes(requests)).toEqual([])
  })

  it('keeps members out of the page', async () => {
    const requests = setup('member')
    await openApp('/llm_bridge')
    await waitFor(() => expect(screen.queryByRole('heading', { level: 1, name: '大模型客户端管理' })).not.toBeInTheDocument())
    expect(requests.some(r => path(r).startsWith('/api/v1/llm_bridge'))).toBe(false)
  })
})
