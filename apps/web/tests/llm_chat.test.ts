// 大模型聚合聊天页面（docs/v2/LLM_CHAT_PLAN.md；src/modules/llm_bridge/chat）：会话列表与筛选、对话与进度、
// 发送 / 停止 / 改标题 / 归档、接着别人的会话发消息前的确认、新建会话、管理器停用、角色；
// 以及多模型同问（T-0232）：分组列表、并排的每一栏、向一组继续发、新建一组。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { fail, me, mockApi, newsModule, openApp, workspace } from './helpers'
import type { Schema } from '../src/services/api'

type Role = Schema['WorkspaceOut']['role']
type Session = Schema['LlmChatSessionOut']
type Message = Schema['LlmChatMessageOut']
type Conversation = Schema['LlmChatConversationOut']
type Events = Schema['LlmChatEventsOut']
type Bridge = Schema['LlmBridgeOut']
type Group = Schema['LlmChatGroupOut']

const at = '2026-11-13T00:00:00Z'
const ONE = '11111111-1111-4111-8111-111111111111', TWO = '22222222-2222-4222-8222-222222222222'
const S1 = `bs_${'1'.repeat(20)}`, S2 = `bs_${'2'.repeat(20)}`, S3 = `bs_${'3'.repeat(20)}`
const T1 = `bt_${'1'.repeat(20)}`, T2 = `bt_${'2'.repeat(20)}`
const ENV = `be_${'a'.repeat(20)}`, ENV2 = `be_${'b'.repeat(20)}`
const chatOf = (id: string) => `/api/v1/llm_bridge/bridges/${id}/chat`
const chat = chatOf(ONE)
const now = () => Date.now() / 1000
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })
const lbModule: Schema['ModuleOut'] = { ...newsModule, key: 'llm_bridge', name: '大模型客户端', tools: [], widgets: [] }

const bridge = (over: Partial<Bridge> = {}): Bridge => ({ id: ONE, name: '主账号', base_url: 'http://llm-bridge:8787', enabled: true, has_token: true, created_at: at, updated_at: at, ...over })
const account = (slot: string, state: string): Schema['LlmBridgeAccountOut'] => ({ slot, label: slot, enabled: true, state, registered: state === 'logged_in', identity: null, method: null, detail: '', first_login_at: null, login_methods: ['existing'], running: 0 })
const channel = (platform: string, mode: string, slot: string, over: Partial<Schema['LlmBridgeChannelOut']> = {}): Schema['LlmBridgeChannelOut'] => ({ platform, mode, slot, enabled: true, available: true, note: '', ...over })
const status = (): Schema['LlmBridgeStatusOut'] => ({
  reachable: true, error: '', version: '0.1.0', platforms: ['chatgpt', 'codex', 'claude', 'workbuddy'], queued: 0, running: 0, load: {},
  settings: { concurrency: 2, min_start_interval_seconds: 2, turn_timeout_seconds: 1800, send_per_hour: 60, max_text_chars: 20000, claude_subscription_ack: false, platforms: ['chatgpt', 'codex', 'claude', 'workbuddy'] },
  accounts: [account('openai', 'logged_in'), account('claude', 'logged_in'), account('workbuddy', 'logged_out')],
  channels: [channel('chatgpt', 'chat', 'openai'), channel('codex', 'dev', 'openai'), channel('claude', 'chat', 'claude'), channel('claude', 'dev', 'claude'), channel('claude', 'cloud', 'claude', { available: false }), channel('workbuddy', 'chat', 'workbuddy')],
})
const session = (over: Partial<Session> = {}): Session => ({ session_id: S1, platform: 'claude', mode: 'chat', model: 'sonnet', title: '周报怎么写', status: 'active', origin: 'bridge', owner: '测试用户', owner_kind: 'me', busy: false, turns: 1, created_at: now() - 600, updated_at: now(), environment: null, ...over })
const asked = (text: string, turn = T1, over: Partial<Message> = {}): Message => ({ role: 'user', turn_id: turn, text, at: now() - 100, by: '测试用户', mine: true, steps: [], ...over })
const answered = (text: string, turn = T1, over: Partial<Message> = {}): Message => ({ role: 'assistant', turn_id: turn, text, at: now() - 90, status: 'completed', steps: [], ...over })
const talk = (over: Partial<Session> = {}, messages: Message[] = [asked('写一个周报模板'), answered('好的，模板如下。')], last = 10): Conversation => ({ session: session(over), messages, last_event_id: last })
const environment = (id: string, name: string): Schema['LlmBridgeEnvironmentOut'] => ({ id, platform: 'codex', mode: 'dev', name, enabled: true, repo_url: 'https://example.com/a.git', branch: 'main', sandbox: 'workspace_write', network: false, description: '' })

interface World {
  bridges: Bridge[]; sessions: Session[]; conversations: Record<string, Conversation>
  /** 读事件时依次给出；可以是还没兑现的 Promise，用来停在“回答到一半” */
  events: (Events | Promise<Events>)[]
  options: Schema['LlmChatOptionsOut']
  groups: Group[]
}
function setup(role: Role, given: Partial<World> = {}, handler?: (r: Request, world: World, url: URL) => Response | undefined | Promise<Response | undefined>) {
  const world: World = { bridges: [bridge()], sessions: [session()], conversations: { [S1]: talk() }, events: [], options: { models: [], models_error: '', environments: [] }, groups: [], ...given }
  const requests = mockApi(async r => {
    const url = new URL(r.url), p = url.pathname
    const result = await handler?.(r, world, url)
    if (result) return result
    if (p === '/api/v1/me' && r.method === 'GET') return as(role)
    if (p === '/api/v1/modules' && r.method === 'GET') return Response.json({ items: [newsModule, lbModule] })
    if (p === '/api/v1/llm_bridge/bridges' && r.method === 'GET') return Response.json({ bridges: world.bridges })
    if (p === '/api/v1/llm_bridge/grants') return Response.json({ grants: [] })
    if (p === '/api/v1/llm_bridge/deployments' && r.method === 'GET') return Response.json({ jobs: [], available: false })
    if (p === '/api/v1/llm_bridge/chat/groups' && r.method === 'GET') return Response.json({ groups: world.groups.filter(g => g.status === (url.searchParams.get('status') ?? 'active')) })
    const group = /^\/api\/v1\/llm_bridge\/chat\/groups\/([^/]+)$/.exec(p)
    if (group && r.method === 'GET') { const found = world.groups.find(g => g.id === group[1]); return found ? Response.json(found) : fail(404, 'NOT_FOUND') }
    if (/^\/api\/v1\/llm_bridge\/bridges\/[^/]+\/status$/.test(p)) return Response.json(status())
    const found = /^\/api\/v1\/llm_bridge\/bridges\/[^/]+\/chat(\/.*)$/.exec(p)
    if (!found || r.method !== 'GET') return undefined
    const rest = found[1]!
    if (rest === '/sessions') return Response.json({ sessions: world.sessions, next_offset: null, sync_failed: '' })
    if (rest === '/options') return Response.json(world.options)
    if (/^\/sessions\/bs_\w+\/events$/.test(rest)) return Response.json((await world.events.shift()) ?? { events: [], busy: false, last_id: Number(url.searchParams.get('after')) })
    const one = /^\/sessions\/(bs_\w+)$/.exec(rest)
    if (one) return world.conversations[one[1]!] ? Response.json(world.conversations[one[1]!]) : fail(404, 'NOT_FOUND')
    return undefined
  })
  return { requests, world }
}
const path = (r: Request) => new URL(r.url).pathname
const query = (r: Request) => new URL(r.url).searchParams
const listRequests = (requests: Request[], id = ONE) => requests.filter(r => path(r) === `${chatOf(id)}/sessions` && r.method === 'GET')
const writes = (requests: Request[]) => requests.filter(r => r.method !== 'GET' && path(r).startsWith('/api/v1/llm_bridge'))
const turn = (id = T2, state = 'queued'): Schema['LlmChatTurnOut'] => ({ turn_id: id, session_id: S1, status: state, ahead_in_session: 0 })
const event = (id: number, kind: string, over: Partial<Schema['LlmChatEventOut']> = {}): Schema['LlmChatEventOut'] => ({ id, turn_id: T2, kind, at: now(), ...over })
const composer = () => screen.findByRole('textbox', { name: '消息内容' })
// 只数消息本身：回答里“过程”的每一步也是列表项
const messages = () => Array.from(screen.getByRole('list', { name: '消息' }).children) as HTMLElement[]

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('大模型聊天', () => {
  it('opens on the chat view, links to the management view and explains what to do without a manager', async () => {
    const { requests } = setup('owner', { bridges: [] })
    const { router } = await openApp('/llm_bridge')
    expect(await screen.findByRole('heading', { level: 1, name: '大模型聊天' })).toBeVisible()
    expect(await screen.findByText('还没有 MCP 管理器。')).toBeVisible()
    expect(screen.getByRole('link', { name: '去添加 MCP 管理器' })).toHaveAttribute('href', '/llm_bridge?view=manage')
    expect(requests.some(r => path(r).includes('/chat/'))).toBe(false)

    const views = screen.getByRole('navigation', { name: '大模型客户端的视图' })
    expect(within(views).getByRole('link', { name: '聊天' })).toHaveAttribute('aria-current', 'page')
    await fireEvent.click(within(views).getByRole('link', { name: '管理器与账号' }))
    expect(await screen.findByRole('heading', { level: 1, name: '大模型客户端管理' })).toBeVisible()
    expect(router.currentRoute.value.query.view).toBe('manage')
    await fireEvent.click(within(views).getByRole('link', { name: '聊天' }))
    expect(await screen.findByRole('heading', { level: 1, name: '大模型聊天' })).toBeVisible()
  })

  it('lists the sessions by day with who made them, and searches, filters and syncs', async () => {
    const midnight = new Date(); midnight.setHours(0, 0, 0, 0)
    const start = midnight.getTime() / 1000
    const { requests } = setup('owner', { sessions: [
      session(),
      session({ session_id: S2, platform: 'codex', mode: 'dev', title: '修登录的测试', owner: '研究员', owner_kind: 'agent', busy: true, updated_at: start - 3600 }),
      session({ session_id: S3, platform: 'chatgpt', title: '', owner: null, owner_kind: 'external', origin: 'external', updated_at: start - 3 * 86400 }),
    ] }, (r, _world, url) => (path(r) === `${chat}/sessions` && url.searchParams.get('sync') ? Response.json({ sessions: [], next_offset: null, sync_failed: 'workbuddy' }) : undefined))
    await openApp('/llm_bridge')
    const side = await screen.findByRole('complementary', { name: '会话列表' })
    const today = await within(side).findByRole('region', { name: '今天' })
    expect(within(today).getByRole('button', { name: /周报怎么写/ })).toHaveTextContent('Claude · 对话 · 我')
    const yesterday = within(side).getByRole('region', { name: '昨天' })
    expect(within(yesterday).getByRole('button', { name: /修登录的测试/ })).toHaveTextContent('Codex · 开发 · 研究员')
    expect(within(yesterday).getByText('回答中')).toBeVisible()
    expect(within(within(side).getByRole('region', { name: '更早' })).getByRole('button', { name: /（没有标题）/ })).toHaveTextContent('ChatGPT · 对话 · 官方程序里已有')
    expect(listRequests(requests)[0]!.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(query(listRequests(requests)[0]!).get('status')).toBe('active')

    // 没登录的平台在筛选里标出来
    const filters = within(side).getByRole('group', { name: '按平台筛选' })
    expect(within(filters).getByRole('button', { name: /WorkBuddy/ })).toHaveTextContent('未登录')
    expect(within(filters).getByRole('button', { name: 'Claude' })).not.toHaveTextContent('未登录')
    await fireEvent.click(within(filters).getByRole('button', { name: 'Claude' }))
    await waitFor(() => expect(listRequests(requests).some(r => query(r).get('platform') === 'claude')).toBe(true))
    expect(within(filters).getByRole('button', { name: 'Claude' })).toHaveAttribute('aria-pressed', 'true')

    await fireEvent.update(within(side).getByRole('searchbox', { name: '按标题搜索会话' }), ' 周报 ')
    await waitFor(() => expect(listRequests(requests).some(r => query(r).get('q') === '周报' && query(r).get('platform') === 'claude')).toBe(true))
    await fireEvent.click(within(side).getByLabelText('看已归档的'))
    await waitFor(() => expect(listRequests(requests).some(r => query(r).get('status') === 'archived')).toBe(true))

    await fireEvent.click(within(side).getByRole('button', { name: '同步官方程序里的会话' }))
    expect(await within(side).findByText('已同步；这些平台的会话读不到：workbuddy')).toBeVisible()
    expect(listRequests(requests).some(r => query(r).get('sync') === 'true')).toBe(true)
    expect(writes(requests)).toEqual([])
  })

  it('shows a conversation as plain text with code blocks, the steps and the session details', async () => {
    const reply = answered('好的。\n```python\nprint("<b>hi</b>")\n```\n<i>完</i>', T1, { steps: [{ kind: 'command', text: 'ls src', at: now() }, { kind: 'files', text: 'src/a.py', at: now() }] })
    setup('owner', { conversations: { [S1]: talk({ model: 'sonnet-5' }, [asked('写一个周报模板'), reply]) } })
    const { container } = await openApp(`/llm_bridge?m=${ONE}&s=${S1}`)
    expect(await screen.findByRole('heading', { level: 2, name: '周报怎么写' })).toBeVisible()
    await screen.findByRole('list', { name: '消息' })
    const [mine, theirs] = messages()
    expect(within(mine!).getByText('我')).toBeVisible()
    expect(within(mine!).getByText('写一个周报模板')).toBeVisible()
    expect(within(theirs!).getByText('Claude')).toBeVisible()
    // 回答里的标签按文字显示，不变成页面元素；三个反引号里的是代码块
    expect(theirs!.querySelector('pre code')).toHaveTextContent('print("<b>hi</b>")')
    expect(within(theirs!).getByText('<i>完</i>')).toBeVisible()
    expect(container.querySelector('.lc-bubble b, .lc-bubble i')).toBeNull()
    expect(within(theirs!).getByText('过程（2 步）')).toBeInTheDocument()
    expect(within(theirs!).getByText('ls src')).toBeInTheDocument()

    const info = screen.getByRole('complementary', { name: '会话信息' })
    expect(within(info).getByText('sonnet-5')).toBeVisible()
    expect(within(info).getByText('主账号')).toBeVisible()
    expect(within(info).getByText('回合（1）')).toBeVisible()
    await fireEvent.click(within(info).getByRole('button', { name: '收起会话信息' }))
    expect(screen.queryByRole('complementary', { name: '会话信息' })).not.toBeInTheDocument()
    // 在列表里选中的那一行标出来
    expect(within(screen.getByRole('complementary', { name: '会话列表' })).getByRole('button', { name: /周报怎么写/ })).toHaveAttribute('aria-current', 'true')
  })

  it('sends with Enter, shows the answer piece by piece and reloads when the turn ends', async () => {
    let finish!: (value: Events) => void
    const bodies: Schema['LlmChatSendIn'][] = []
    const { requests, world } = setup('owner', {}, async (r, w) => {
      if (path(r) !== `${chat}/sessions/${S1}/messages` || r.method !== 'POST') return undefined
      bodies.push(await r.json() as Schema['LlmChatSendIn'])
      w.events = [
        { events: [event(11, 'queued', { by: '测试用户' }), event(12, 'started'), event(13, 'message', { text: '先说结论。' }), event(14, 'command', { command: 'ls docs' })], busy: true, last_id: 14 },
        new Promise<Events>(resolve => { finish = resolve }),
      ]
      return Response.json(turn())
    })
    await openApp(`/llm_bridge?m=${ONE}&s=${S1}`)
    const box = await composer()
    await fireEvent.update(box, '再短一点')
    await fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })
    await fireEvent.keyDown(box, { key: 'Enter', isComposing: true })
    expect(writes(requests)).toEqual([])
    await fireEvent.keyDown(box, { key: 'Enter' })

    // 一段一段出来：已经给出的部分、正在执行的命令；这时可以停止
    expect(await screen.findByText('先说结论。')).toBeVisible()
    expect(bodies).toEqual([{ text: '再短一点', request_id: expect.stringMatching(/^web-[\w-]+$/), takeover: false }])
    expect(box).toHaveValue('')
    expect(messages()).toHaveLength(4)
    expect(within(messages()[2]!).getByText('再短一点')).toBeVisible()
    expect(within(messages()[3]!).getByText('还在继续…')).toBeVisible()
    expect(within(messages()[3]!).getByText('ls docs')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止' })).toBeVisible()
    expect(screen.getByText('上一条还没答完：现在发的不会打断它，会进队列，等前面的做完再发给模型。')).toBeVisible()
    const events = requests.filter(r => path(r).endsWith('/events'))
    expect(query(events[0]!).get('after')).toBe('10')
    expect(Number(query(events[0]!).get('wait_seconds'))).toBeGreaterThan(0)

    // 回合结束：以服务器给的整段会话为准
    world.conversations[S1] = talk({ turns: 2 }, [asked('写一个周报模板'), answered('好的，模板如下。'), asked('再短一点', T2), answered('先说结论。\n\n三句话版本。', T2)], 15)
    finish({ events: [event(15, 'completed')], busy: false, last_id: 15 })
    expect(await screen.findByText(/三句话版本。/)).toBeVisible()
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument())
    expect(messages()).toHaveLength(4)
    expect(query(requests.filter(r => path(r).endsWith('/events')).at(-1)!).get('after')).toBe('14')
  })

  it('asks once before sending into a session someone else made', async () => {
    const bodies: Schema['LlmChatSendIn'][] = []
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { requests } = setup('owner', { conversations: { [S1]: talk({ owner: '研究员', owner_kind: 'agent' }) } }, async r => {
      if (path(r) !== `${chat}/sessions/${S1}/messages`) return undefined
      bodies.push(await r.json() as Schema['LlmChatSendIn'])
      return Response.json(turn(`bt_${String(bodies.length).repeat(20)}`, 'completed'))
    })
    await openApp(`/llm_bridge?m=${ONE}&s=${S1}`)
    const box = await composer()
    expect(screen.getByText(/这是智能体「研究员」建的会话：可以看；接着发消息前会让你确认一次。/)).toBeVisible()
    await fireEvent.update(box, '我来补充一句')
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('智能体「研究员」建的会话'))
    expect(writes(requests)).toEqual([])
    expect(box).toHaveValue('我来补充一句')

    confirm.mockReturnValue(true)
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toMatchObject({ text: '我来补充一句', takeover: true })
    // 同一个会话里确认过一次，之后不再问
    await waitFor(() => expect(box).toHaveValue(''))
    await fireEvent.update(box, '再补一句')
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(confirm).toHaveBeenCalledTimes(2)
  })

  it('asks when the server says the session is not ours and resends the same message', async () => {
    const bodies: Schema['LlmChatSendIn'][] = []
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    setup('admin', {}, async r => {
      if (path(r) !== `${chat}/sessions/${S1}/messages`) return undefined
      bodies.push(await r.json() as Schema['LlmChatSendIn'])
      return bodies.length === 1 ? fail(409, 'TAKEOVER_REQUIRED') : Response.json(turn(T2, 'completed'))
    })
    await openApp(`/llm_bridge?m=${ONE}&s=${S1}`)
    await fireEvent.update(await composer(), '接着问')
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(bodies[0]!.takeover).toBe(false)
    expect(bodies[1]).toEqual({ ...bodies[0]!, takeover: true })
  })

  it('keeps the draft when the outcome is unknown and retries with the same request id', async () => {
    const bodies: Schema['LlmChatSendIn'][] = []
    setup('owner', {}, async r => {
      if (path(r) !== `${chat}/sessions/${S1}/messages`) return undefined
      bodies.push(await r.json() as Schema['LlmChatSendIn'])
      if (bodies.length === 1) throw new Error('connection reset')
      return Response.json(turn(T2, 'completed'))
    })
    await openApp(`/llm_bridge?m=${ONE}&s=${S1}`)
    const box = await composer()
    await fireEvent.update(box, '发一次就好')
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByText(/不确定有没有发出去：再点一次发送不会重复/)).toBeVisible()
    expect(box).toHaveValue('发一次就好')
    await fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[1]!.request_id).toBe(bodies[0]!.request_id)
    await waitFor(() => expect(box).toHaveValue(''))
  })

  it('stops a running turn, renames and archives the session', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const running = [asked('跑一下测试'), answered('', T1, { status: 'running' })]
    const titles: unknown[] = []
    const { requests, world } = setup('owner', { conversations: { [S1]: talk({ busy: true }, running) }, events: [new Promise<Events>(() => {})] }, async (r, w) => {
      const p = path(r)
      if (p === `${chat}/turns/${T1}/cancel` && r.method === 'POST') return Response.json(turn(T1, 'cancelled'))
      if (p === `${chat}/sessions/${S1}` && r.method === 'PATCH') {
        titles.push(await r.json())
        w.conversations[S1] = talk({ title: '跑测试' }, [asked('跑一下测试'), answered('', T1, { status: 'cancelled' })])
        return Response.json(w.conversations[S1]!.session)
      }
      if (p === `${chat}/sessions/${S1}/archive` && r.method === 'POST') {
        w.conversations[S1] = talk({ title: '跑测试', status: 'archived' }, [asked('跑一下测试'), answered('', T1, { status: 'cancelled' })])
        return Response.json(w.conversations[S1]!.session)
      }
      return undefined
    })
    await openApp(`/llm_bridge?m=${ONE}&s=${S1}`)
    expect(await screen.findByText('正在回答…')).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '停止' }))
    await waitFor(() => expect(writes(requests).map(path)).toEqual([`${chat}/turns/${T1}/cancel`]))

    await fireEvent.click(screen.getByRole('button', { name: '改标题' }))
    const rename = screen.getByRole('form', { name: '改会话标题' })
    await fireEvent.update(within(rename).getByRole('textbox', { name: '会话标题' }), '  ')
    await fireEvent.click(within(rename).getByRole('button', { name: '保存' }))
    expect(await screen.findByText('标题不能为空。')).toBeVisible()
    await fireEvent.update(within(rename).getByRole('textbox', { name: '会话标题' }), ' 跑测试 ')
    await fireEvent.click(within(rename).getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('heading', { level: 2, name: '跑测试' })).toBeVisible()
    expect(titles).toEqual([{ title: '跑测试' }])
    expect(await within(screen.getByRole('list', { name: '消息' })).findByText('已停止')).toBeVisible()

    await fireEvent.click(screen.getByRole('button', { name: '归档会话' }))
    expect(await screen.findByText('这个会话已归档，不能再发送。')).toBeVisible()
    expect(screen.queryByRole('textbox', { name: '消息内容' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '归档会话' })).not.toBeInTheDocument()
    expect(world.conversations[S1]!.session.status).toBe('archived')
  })

  it('shows the queue of a busy session and lets queued messages be moved, edited and cancelled', async () => {
    const running = [asked('跑一下测试'), answered('', T1, { status: 'running' })]
    const item = (id: string, text: string, mine = true): Schema['LlmChatQueueItem'] => ({ turn_id: id, status: 'queued', text, by: mine ? '我' : '研究助手', mine, queued_at: 1794650000, started_at: null })
    let queued = [item('bt_' + 'a'.repeat(20), '第二条'), item('bt_' + 'b'.repeat(20), '第三条', false)]
    const bodies: unknown[] = []
    const view = () => ({ session_id: S1, running: { ...item(T1, '跑一下测试'), status: 'running' }, queued })
    const { requests } = setup('owner', { conversations: { [S1]: talk({ busy: true }, running) }, events: [new Promise<Events>(() => {})] }, async r => {
      const p = path(r)
      if (p === `${chat}/sessions/${S1}/queue`) return Response.json(view())
      const one = /\/turns\/(bt_\w+)(\/move|\/cancel)?$/.exec(p)
      if (!one) return undefined
      if (one[2] === '/move') { bodies.push(await r.json()); queued = [queued[1]!, queued[0]!]; return Response.json(view()) }
      if (one[2] === '/cancel') { queued = queued.filter(q => q.turn_id !== one[1]); return Response.json(turn(one[1], 'cancelled')) }
      if (r.method === 'PATCH') { const body = await r.json() as { text: string }; bodies.push(body); queued = queued.map(q => (q.turn_id === one[1] ? { ...q, text: body.text } : q)); return Response.json(view()) }
      return undefined
    })
    await openApp(`/llm_bridge?m=${ONE}&s=${S1}`)
    const queue = await screen.findByRole('region', { name: '消息队列' })
    expect(within(queue).getByRole('button', { name: /队列里有 2 条消息/ })).toBeVisible()
    const rows = () => within(queue).getAllByRole('listitem')
    expect(rows().map(li => li.textContent)).toEqual([expect.stringContaining('第二条'), expect.stringContaining('第三条')])
    expect(rows()[1]).toHaveTextContent('研究助手')
    expect(screen.getByText(/不会打断它，会进队列/)).toBeVisible()

    await fireEvent.click(within(queue).getByRole('button', { name: '把第 2 条调到最前面' }))
    await waitFor(() => expect(rows()[0]).toHaveTextContent('第三条'))
    await fireEvent.click(within(queue).getByRole('button', { name: '修改第 1 条' }))
    const edit = within(queue).getByRole('form', { name: '修改排队中的第 1 条' })
    const box = within(edit).getByRole('textbox', { name: '消息内容' })
    await fireEvent.update(box, '改过的第三条')
    await fireEvent.click(within(edit).getByRole('button', { name: '保存' }))
    await waitFor(() => expect(rows()[0]).toHaveTextContent('改过的第三条'))
    expect(bodies).toEqual([{ to: 'first' }, { text: '改过的第三条' }])
    await fireEvent.click(within(queue).getByRole('button', { name: '取消第 2 条' }))
    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(writes(requests).map(r => `${r.method} ${path(r).split('/chat')[1]}`)).toEqual([
      `POST /turns/${'bt_' + 'b'.repeat(20)}/move`, `PATCH /turns/${'bt_' + 'b'.repeat(20)}`, `POST /turns/${'bt_' + 'a'.repeat(20)}/cancel`,
    ])
  })

  it('shows why a turn failed and what is unknown', async () => {
    setup('owner', { conversations: { [S1]: talk({}, [
      asked('第一问'), answered('写到一半', T1, { status: 'failed', failure: { kind: 'RATE_LIMITED', message: '平台限流了，稍后再试' } }),
      asked('第二问', T2), answered('', T2, { status: 'unknown', failure: { kind: 'TIMEOUT_UNKNOWN', message: '超时，结果未知' } }),
    ]) } })
    await openApp(`/llm_bridge?m=${ONE}&s=${S1}`)
    expect(await screen.findByText('这一回合失败了：平台限流了，稍后再试')).toBeVisible()
    expect(screen.getByText('写到一半')).toBeVisible()
    expect(screen.getByText('这一回合的结果未知：超时，结果未知')).toBeVisible()
  })

  it('creates a session: only logged-in platforms, a development session needs an environment', async () => {
    const bodies: Schema['LlmChatCreateIn'][] = []
    const { requests } = setup('owner', { options: { models: [{ id: 'gpt-5', name: 'GPT-5', description: '', default: true, efforts: ['low', 'high'] }], models_error: '', environments: [environment(ENV, '工作台仓库'), environment(ENV2, '文档仓库')] } }, async (r, w) => {
      if (path(r) !== `${chat}/sessions` || r.method !== 'POST') return undefined
      bodies.push(await r.json() as Schema['LlmChatCreateIn'])
      const made = session({ session_id: S2, platform: 'codex', mode: 'dev', title: '修一下测试', model: 'gpt-5' })
      w.conversations[S2] = { session: made, messages: [asked('修一下测试', T2), answered('', T2, { status: 'queued', ahead_in_session: 0 })], last_event_id: 3 }
      return Response.json({ session: made, turn: { ...turn(), session_id: S2 }, send_error: '' }, { status: 201 })
    })
    const { router } = await openApp('/llm_bridge')
    await fireEvent.click(await screen.findByRole('button', { name: '新建会话' }))
    const form = await screen.findByRole('form', { name: '新建会话' })
    const platforms = await within(form).findByRole('group', { name: '平台' })
    expect(within(platforms).getByRole('radio', { name: /ChatGPT/ })).toBeChecked()
    expect(within(platforms).getByRole('radio', { name: /WorkBuddy/ })).toBeDisabled()
    expect(within(form).getByRole('link', { name: '去登录' })).toHaveAttribute('href', '/llm_bridge?view=manage')

    // Codex 只有“开发”一种用法：要选开发环境
    await fireEvent.click(within(platforms).getByRole('radio', { name: /Codex/ }))
    await waitFor(() => expect(within(within(form).getByRole('group', { name: '用法' })).getByRole('radio', { name: /开发/ })).toBeChecked())
    await waitFor(() => expect(requests.some(r => path(r) === `${chat}/options` && query(r).get('platform') === 'codex' && query(r).get('mode') === 'dev')).toBe(true))
    await within(form).findByRole('option', { name: '工作台仓库（可写工作区）' })
    await fireEvent.update(within(form).getByLabelText('第一条消息'), ' 修一下测试 ')
    await fireEvent.click(within(form).getByRole('button', { name: '新建并发送' }))
    expect(await within(form).findByText('开发会话要选一个开发环境。')).toBeVisible()
    expect(writes(requests)).toEqual([])

    await fireEvent.update(within(form).getByLabelText('开发环境'), ENV)
    await fireEvent.update(within(form).getByLabelText('模型'), 'gpt-5')
    await fireEvent.update(await within(form).findByLabelText('推理强度'), 'high')
    await fireEvent.click(within(form).getByRole('button', { name: '新建并发送' }))
    expect(await screen.findByRole('heading', { level: 2, name: '修一下测试' })).toBeVisible()
    expect(bodies).toEqual([{ platform: 'codex', mode: 'dev', title: '', model: 'gpt-5', effort: 'high', environment: ENV, text: '修一下测试', request_id: expect.stringMatching(/^web-/) }])
    expect(router.currentRoute.value.query).toEqual({ m: ONE, s: S2 })
    expect(await screen.findByText('排队中…')).toBeVisible()
  })

  it('opens the new session with the unsent first message when sending it failed', async () => {
    setup('owner', {}, (r, w) => {
      if (path(r) !== `${chat}/sessions` || r.method !== 'POST') return undefined
      const made = session({ session_id: S2, platform: 'chatgpt', title: '' })
      w.conversations[S2] = { session: made, messages: [], last_event_id: 1 }
      return Response.json({ session: made, turn: null, send_error: '平台还没有登录' }, { status: 201 })
    })
    await openApp(`/llm_bridge?m=${ONE}&new=1`)
    const form = await screen.findByRole('form', { name: '新建会话' })
    await within(form).findByRole('group', { name: '平台' })
    await fireEvent.update(within(form).getByLabelText('第一条消息'), '你好')
    await fireEvent.click(within(form).getByRole('button', { name: '新建并发送' }))
    expect(await screen.findByText('会话建好了，但第一条消息没有发出去：平台还没有登录')).toBeVisible()
    expect(await composer()).toHaveValue('你好')
    expect(screen.getByText('这个会话里还没有消息。')).toBeVisible()
  })

  it('lets a stopped manager be read but not written, and switches managers', async () => {
    const { requests } = setup('owner', { bridges: [bridge({ id: TWO, name: '备用', enabled: false }), bridge()] })
    const { router } = await openApp(`/llm_bridge?m=${TWO}&s=${S1}`)
    expect(await screen.findByText(/这个 MCP 管理器已停用：可以查看，不能发送。/)).toBeVisible()
    expect(screen.queryByRole('textbox', { name: '消息内容' })).not.toBeInTheDocument()
    const side = screen.getByRole('complementary', { name: '会话列表' })
    expect(within(side).getByRole('button', { name: '新建会话' })).toBeDisabled()
    expect(within(side).getByRole('option', { name: '备用（已停用）' })).toBeInTheDocument()
    expect(listRequests(requests, TWO).length).toBeGreaterThan(0)
    expect(listRequests(requests, ONE)).toEqual([])

    await fireEvent.update(within(side).getByLabelText('MCP 管理器'), ONE)
    await waitFor(() => expect(listRequests(requests, ONE).length).toBeGreaterThan(0))
    expect(router.currentRoute.value.query).toEqual({ m: ONE })
    expect(await screen.findByText(/在左边选一个会话接着聊/)).toBeVisible()
    await waitFor(() => expect(within(side).getByRole('button', { name: '新建会话' })).toBeEnabled())
  })

  it('picks the first enabled manager and says when it cannot be reached', async () => {
    const { requests } = setup('owner', { bridges: [bridge({ id: TWO, name: '备用', enabled: false }), bridge()] }, r => (path(r) === `/api/v1/llm_bridge/bridges/${ONE}/status` ? fail(503, 'EXTERNAL_UNAVAILABLE') : undefined))
    await openApp('/llm_bridge')
    expect(await screen.findByText(/MCP 管理器暂时连不上/)).toBeVisible()
    expect(screen.getByText('MCP 管理器「主账号」')).toBeVisible()
    expect(listRequests(requests, ONE).length).toBeGreaterThan(0)
    expect(listRequests(requests, TWO)).toEqual([])
  })

  it('keeps members out of the chat', async () => {
    const { requests } = setup('member')
    await openApp(`/llm_bridge?m=${ONE}&s=${S1}`)
    await waitFor(() => expect(screen.queryByRole('heading', { level: 1, name: '大模型聊天' })).not.toBeInTheDocument())
    expect(requests.some(r => path(r).startsWith('/api/v1/llm_bridge'))).toBe(false)
  })
})

// ---- 多模型同问（T-0232，docs/v2/LLM_CHAT_PLAN.md 第 3.4 节）----
const GROUP = '99999999-9999-4999-8999-999999999999'
const groups = '/api/v1/llm_bridge/chat/groups'
type Member = Schema['LlmChatGroupMemberOut']
const member = (position: number, over: Partial<Member> = {}): Member => ({ position, bridge_id: ONE, bridge_name: '主账号', bridge_enabled: true, platform: 'claude', mode: 'chat', model: 'sonnet', session_id: S1, error: '', ...over })
const groupOf = (over: Partial<Group> = {}): Group => ({
  id: GROUP, title: '解释幂等', status: 'active', owner: '测试用户', owner_kind: 'me', created_at: at, updated_at: at,
  members: [
    member(0),
    member(1, { bridge_id: TWO, bridge_name: '备用', platform: 'chatgpt', model: null, session_id: S2 }),
    member(2, { platform: 'workbuddy', model: null, session_id: null, error: '平台还没有登录' }),
  ],
  ...over,
})
const two = [bridge(), bridge({ id: TWO, name: '备用' })]
const both = (): Record<string, Conversation> => ({
  [S1]: talk({ title: '解释幂等' }, [asked('解释幂等'), answered('做一次和做多次，结果一样。')]),
  [S2]: talk({ session_id: S2, platform: 'chatgpt', title: '解释幂等' }, [asked('解释幂等', T2), answered('重复调用没有额外的副作用。', T2)]),
})

describe('多模型同问', () => {
  it('lists the groups, shows each model side by side and sends to all of them', async () => {
    const bodies: Schema['LlmChatGroupSendIn'][] = []
    const { requests } = setup('owner', { bridges: two, groups: [groupOf()], conversations: both() }, async r => {
      if (path(r) !== `${groups}/${GROUP}/messages` || r.method !== 'POST') return undefined
      bodies.push(await r.json() as Schema['LlmChatGroupSendIn'])
      return Response.json({ group: groupOf(), results: [
        { position: 0, turn: { turn_id: `bt_${'3'.repeat(20)}`, session_id: S1, status: 'queued', ahead_in_session: 0 }, error: '' },
        { position: 1, turn: null, error: '这个小时的发送次数用完了' },
        { position: 2, turn: null, error: '这个模型的会话没有建成' },
      ] })
    })
    const { router } = await openApp('/llm_bridge')
    const side = await screen.findByRole('complementary', { name: '会话列表' })
    const asked = await within(side).findByRole('region', { name: '同问' })
    const entry = within(asked).getByRole('button', { name: /解释幂等/ })
    expect(entry).toHaveTextContent('同问 3 个模型 · 我')
    await fireEvent.click(entry)
    expect(await screen.findByRole('heading', { level: 2, name: '解释幂等' })).toBeVisible()
    expect(router.currentRoute.value.query).toEqual({ m: ONE, g: GROUP })
    expect(entry).toHaveAttribute('aria-current', 'true')

    // 每个模型一栏：各自读自己那个管理器上的会话；没建成的那一栏给出原因
    const claude = await screen.findByRole('region', { name: 'Claude（主账号）' })
    expect(await within(claude).findByText('做一次和做多次，结果一样。')).toBeVisible()
    expect(within(claude).getByText('sonnet · 主账号')).toBeVisible()
    expect(within(claude).getByRole('link', { name: '在这个会话里继续' })).toHaveAttribute('href', `/llm_bridge?m=${ONE}&s=${S1}`)
    const gpt = screen.getByRole('region', { name: 'ChatGPT（备用）' })
    expect(await within(gpt).findByText('重复调用没有额外的副作用。')).toBeVisible()
    expect(requests.some(r => path(r) === `${chatOf(TWO)}/sessions/${S2}`)).toBe(true)
    const buddy = screen.getByRole('region', { name: 'WorkBuddy（主账号）' })
    expect(within(buddy).getByText('这个模型的会话没有建成：平台还没有登录')).toBeVisible()
    expect(within(buddy).queryByRole('link', { name: '在这个会话里继续' })).not.toBeInTheDocument()

    // 继续问：只发给有会话的那两个；某一个没发出去只影响它自己那一栏
    const form = screen.getByRole('form', { name: '向这一组继续发' })
    expect(within(form).getByText('有 1 个不能继续发（会话没建成，或者它的 MCP 管理器已停用、已删除）。')).toBeVisible()
    await fireEvent.update(within(form).getByRole('textbox', { name: '问题内容' }), '举一个例子')
    await fireEvent.click(within(form).getByRole('button', { name: '发给 2 个模型' }))
    expect(await within(claude).findByText('举一个例子')).toBeVisible()
    expect(within(claude).getByText('排队中…')).toBeVisible()
    expect(within(gpt).getByText('这一个没有发出去：这个小时的发送次数用完了')).toBeVisible()
    expect(within(gpt).queryByText('举一个例子')).not.toBeInTheDocument()
    expect(bodies).toEqual([{ text: '举一个例子', request_id: expect.stringMatching(/^web-/), takeover: false }])
    expect(within(form).getByRole('textbox', { name: '问题内容' })).toHaveValue('')
  })

  it('asks before continuing a group someone else made, and archives it', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const bodies: Schema['LlmChatGroupSendIn'][] = []
    const { requests, world } = setup('admin', { bridges: two, groups: [groupOf({ owner: '小王', owner_kind: 'user' })], conversations: both() }, async (r, w) => {
      if (path(r) === `${groups}/${GROUP}/messages` && r.method === 'POST') {
        bodies.push(await r.json() as Schema['LlmChatGroupSendIn'])
        return Response.json({ group: w.groups[0], results: [] })
      }
      if (path(r) === `${groups}/${GROUP}/archive` && r.method === 'POST') {
        w.groups = [groupOf({ owner: '小王', owner_kind: 'user', status: 'archived' })]
        return Response.json(w.groups[0])
      }
      return undefined
    })
    await openApp(`/llm_bridge?g=${GROUP}`)
    const form = await screen.findByRole('form', { name: '向这一组继续发' })
    expect(within(form).getByText(/这一组是「小王」建的：可以看；接着发问题前会让你确认一次。/)).toBeVisible()
    const box = within(form).getByRole('textbox', { name: '问题内容' })
    await fireEvent.update(box, '我也问一句')
    await fireEvent.keyDown(box, { key: 'Enter' })
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('「小王」建的'))
    expect(writes(requests)).toEqual([])
    confirm.mockReturnValue(true)
    await fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(bodies).toEqual([{ text: '我也问一句', request_id: expect.stringMatching(/^web-/), takeover: true }]))

    await fireEvent.click(screen.getByRole('button', { name: '归档这一组' }))
    expect(await screen.findByText(/这一组同问已归档，不能再一起发；每个模型的会话还在，可以单独继续。/)).toBeVisible()
    expect(screen.queryByRole('textbox', { name: '问题内容' })).not.toBeInTheDocument()
    expect(world.groups[0]!.status).toBe('archived')
    // 归档后不在“同问”里了；每一栏还能看
    await waitFor(() => expect(within(screen.getByRole('complementary', { name: '会话列表' })).queryByRole('region', { name: '同问' })).not.toBeInTheDocument())
    expect(screen.getByRole('region', { name: 'Claude（主账号）' })).toBeVisible()
  })

  it('starts a group with the logged-in platforms and lets models be added across managers', async () => {
    const bodies: Schema['LlmChatGroupCreateIn'][] = []
    const { requests } = setup('owner', { bridges: two, conversations: both() }, async (r, w) => {
      if (path(r) !== groups || r.method !== 'POST') return undefined
      bodies.push(await r.json() as Schema['LlmChatGroupCreateIn'])
      w.groups = [groupOf({ title: '解释幂等', members: [member(0), member(1, { bridge_id: TWO, bridge_name: '备用', session_id: S2 })] })]
      return Response.json({ group: w.groups[0], results: [] }, { status: 201 })
    })
    const { router } = await openApp('/llm_bridge')
    await fireEvent.click(await within(await screen.findByRole('complementary', { name: '会话列表' })).findByRole('button', { name: '多模型同问' }))
    const form = await screen.findByRole('form', { name: '多模型同问' })
    // 当前管理器上已登录、能对话的平台先加进来（Codex 只有“开发”，WorkBuddy 没登录）
    const chosen = await within(form).findByRole('list', { name: '发给（2 个）' })
    expect(within(chosen).getAllByRole('listitem').map(li => li.textContent)).toEqual(['ChatGPT · 默认模型 · 主账号', 'Claude · 默认模型 · 主账号'])

    await fireEvent.click(within(chosen).getByRole('button', { name: '移除 ChatGPT · 默认模型 · 主账号' }))
    await fireEvent.update(within(form).getByLabelText('问题'), ' 解释幂等 ')
    await fireEvent.click(within(form).getByRole('button', { name: '发给 1 个模型' }))
    expect(await within(form).findByText(/至少选 2 个模型/)).toBeVisible()
    expect(writes(requests)).toEqual([])

    // 从另一个管理器加一个；同一个不能加两次
    const picker = within(form).getByRole('group', { name: '添加一个模型' })
    await fireEvent.update(within(picker).getByLabelText('MCP 管理器'), TWO)
    await waitFor(() => expect(requests.some(r => path(r) === `/api/v1/llm_bridge/bridges/${TWO}/status`)).toBe(true))
    await within(picker).findByRole('option', { name: 'Claude' })
    await fireEvent.update(within(picker).getByLabelText('平台'), 'claude')
    await fireEvent.click(within(picker).getByRole('button', { name: '添加' }))
    expect(await within(form).findByRole('list', { name: '发给（2 个）' })).toHaveTextContent('Claude · 默认模型 · 备用')
    await fireEvent.click(within(picker).getByRole('button', { name: '添加' }))
    expect(await within(picker).findByText('这个模型已经在里面了。')).toBeVisible()

    await fireEvent.click(within(form).getByRole('button', { name: '发给 2 个模型' }))
    expect(await screen.findByRole('heading', { level: 2, name: '解释幂等' })).toBeVisible()
    expect(bodies).toEqual([{ title: '', text: '解释幂等', request_id: expect.stringMatching(/^web-/), targets: [
      { bridge_id: ONE, platform: 'claude', mode: 'chat' }, { bridge_id: TWO, platform: 'claude', mode: 'chat' },
    ] }])
    expect(router.currentRoute.value.query).toEqual({ m: ONE, g: GROUP })
    expect(await screen.findByRole('region', { name: 'Claude（备用）' })).toBeVisible()
  })
})
