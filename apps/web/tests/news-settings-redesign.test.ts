// 设置 → 新闻早报改版（T-0195，方案 docs/v2/ai-admin-redesign 计划 B5，效果图 06）：
// 五个标签编辑同一份草稿；owner 改、admin 只读；“最近写入配置 #config_rev”与设置版本分开；
// 未部署 / 被别的工作空间占用 / 连通未知分开显示；有未保存更改时测试推送与现在生成先保存；刷新只读。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { fail, me, mockApi, newsModule, openApp, workspace } from './helpers'
import type { Schema } from '../src/services/api'

const at = '2026-11-07T00:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
type Role = Schema['WorkspaceOut']['role']
type Settings = Schema['NewsSettingsOut']
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })
const channel = (over: Partial<Schema['NewsChannelOut']> = {}): Schema['NewsChannelOut'] => ({ enabled: false, msg_type: 'markdown', chat_id: '', from_addr: '', to_addr: '', smtp_server: '', smtp_port: null, server_url: '', secrets_set: [], ...over })
const settings: Settings = {
  workspace_id: 'workspace-a', enabled: true, timezone: 'Asia/Shanghai', push_times: ['07:30'], briefing_mode: 'trendradar', agent_instance_id: null,
  platforms: ['zhihu', 'weibo'], newsnow_api_url: '', feeds: [{ id: 'hn', name: 'Hacker News', url: 'https://hnrss.org/frontpage', enabled: true, max_age_days: 0 }],
  topics: [{ name: '人工智能', keywords: ['AI', '大模型'], required: [], max_items: 10, description: '', enabled: true }],
  exclusions: ['广告'], filter_method: 'keyword', ai_model: 'deepseek/deepseek-chat', ai_api_base: '', ai_api_key_set: true, ai_analysis: true, ai_translation: false,
  channels: { feishu: channel({ enabled: true, secrets_set: ['webhook_url'] }), telegram: channel() },
  version: 3, config_rev: 5, rendered_at: at, render_error: '', updated_at: at, deployed: true, taken_by_other: false,
  platform_choices: [{ id: 'zhihu', name: '知乎' }, { id: 'weibo', name: '微博' }, { id: 'douyin', name: '抖音' }],
  learned: { computed_at: at, topics: [{ name: '人工智能', score: 4, max_items: 10, adjusted_max_items: 14 }], source_suggestions: [{ source_type: 'hotlist', source_id: 'weibo', source_name: '微博', dismisses: 7, favorites: 0 }] },
}
const status: Schema['NewsStatusOut'] = { deployed: true, enabled: true, online: true, latest_data_date: '2026-11-07', error: null, latest_briefing_at: at, rendered_at: at, render_error: '' }
const instances = [{ id: 'inst-a', name: '新闻助手', status: 'online', description: '' }]
const pending: Schema['NewsBriefingOut'] = { id: 'b1', local_date: '2026-11-07', slot: '07:30', source: 'agent', title: '今日早报', status: 'pending', parts: 0, total_parts: 0, error: '', task_id: null, created_at: at, updated_at: at, content: '' }

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function setup(role: Role, handler?: (r: Request) => Response | undefined, base: Settings = settings, state: Schema['NewsStatusOut'] = status) {
  return mockApi(async r => {
    const result = handler?.(r)
    if (result) return result
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return as(role)
    if (p === '/api/v1/news/settings' && r.method === 'GET') return Response.json(base)
    if (p === '/api/v1/news/status') return Response.json(state)
    if (p === '/api/v1/tasks/assignees') return Response.json({ users: [], instances })
    return undefined
  })
}
const writes = (requests: Request[]) => requests.filter(r => r.method !== 'GET').map(r => `${r.method} ${path(r)}`)
async function openTab(name: string) {
  await fireEvent.click(screen.getByRole('tab', { name }))
  return screen.findByRole('tabpanel', { name })
}
class Stream extends EventTarget {
  static last: Stream | null = null
  readyState = 1
  constructor(public url: string) { super(); Stream.last = this }
  close() { this.readyState = 2 }
}
// 右侧“当前运行状态”里的一项（dt 后面的 dd）
const fact = (card: HTMLElement, name: string) => within(card).getByText(name, { selector: 'dt' }).nextElementSibling as HTMLElement
const newsChanged = () => Stream.last!.dispatchEvent(new MessageEvent('invalidate', { data: JSON.stringify({ workspace_id: 'workspace-a', topics: ['news'], at }) }))

describe('news settings redesign: one draft, five tabs', () => {
  it('edits one shared draft across the tabs and saves it once', { timeout: 15000 }, async () => {
    let server = settings
    const requests = setup('owner', r => {
      if (path(r) !== '/api/v1/news/settings') return undefined
      if (r.method === 'PUT') server = { ...settings, version: 4, config_rev: 6 }
      return Response.json(server)
    })
    await openApp('/settings/news')
    const tabs = await screen.findByRole('tablist', { name: '新闻早报设置' })
    expect(within(tabs).getAllByRole('tab').map(t => t.textContent?.trim())).toEqual(['基本与时间', '新闻来源', '兴趣主题', '模型与渠道', '学习偏好'])
    expect(screen.getByRole('tab', { name: '基本与时间' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('link', { name: '前往早报' })).toHaveAttribute('href', '/news')
    await screen.findByLabelText('推送时间 1')
    expect(screen.queryByText('当前表单有未保存更改')).toBeNull()
    expect(screen.queryByRole('button', { name: '保存设置' })).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: '添加推送时间' }))
    await fireEvent.update(screen.getByLabelText('推送时间 2'), '18:30')
    expect(screen.getByText('当前表单有未保存更改')).toBeVisible()

    const sources = await openTab('新闻来源')
    await fireEvent.click(within(sources).getByLabelText('微博'))
    await fireEvent.click(within(sources).getByRole('button', { name: '编辑 RSS 源：Hacker News' }))
    await fireEvent.update(within(within(sources).getByRole('group', { name: 'RSS 源 1' })).getByLabelText('名称'), 'HN 首页')
    const topics = await openTab('兴趣主题')
    await fireEvent.update(within(within(topics).getByRole('group', { name: '兴趣主题 1' })).getByLabelText('必含词（可不填）'), '发布')
    await fireEvent.update(within(topics).getByLabelText('全局排除词（每行一个，或用逗号分隔）'), '广告\n带货')
    const model = await openTab('模型与渠道')
    await fireEvent.update(within(model).getByLabelText('模型'), 'openai/gpt-4o-mini')
    await fireEvent.click(within(model).getByLabelText(/^AI：/))
    expect(within(model).getByLabelText('API Key')).toHaveValue('')
    expect(within(model).getByLabelText('API Key')).toHaveAttribute('placeholder', '已设置（不回显）；不填表示不改')

    // 切回第一个标签：之前的改动都还在，也还没有发出任何写请求
    await openTab('基本与时间')
    expect(screen.getByLabelText('推送时间 2')).toHaveValue('18:30')
    expect(writes(requests)).toEqual([])

    await fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    expect(await screen.findByText('设置已保存：TrendRadar 下一次运行时生效。')).toBeVisible()
    expect(writes(requests)).toEqual(['PUT /api/v1/news/settings'])
    const body = await requests.find(r => r.method === 'PUT')!.clone().json()
    expect(body).toMatchObject({ version: 3, push_times: ['07:30', '18:30'], platforms: ['zhihu'], exclusions: ['广告', '带货'], ai_model: 'openai/gpt-4o-mini', filter_method: 'ai' })
    expect(body).not.toHaveProperty('ai_api_key')
    expect(body).not.toHaveProperty('timezone')
    expect(body.feeds[0]).toMatchObject({ id: 'hn', name: 'HN 首页', url: 'https://hnrss.org/frontpage' })
    expect(body.topics[0].required).toEqual(['发布'])
    expect(screen.queryByText('当前表单有未保存更改')).toBeNull()
    expect(within(screen.getByRole('region', { name: '早报状态摘要' })).getByText('最近写入配置 #6')).toBeVisible()
  })

  it('lets the owner cancel changes back to the saved settings without any request', async () => {
    const requests = setup('owner')
    await openApp('/settings/news')
    await fireEvent.click(await screen.findByRole('button', { name: '添加推送时间' }))
    const model = await openTab('模型与渠道')
    await fireEvent.update(within(model).getByLabelText('API Key'), 'sk-typed-secret')
    await fireEvent.click(screen.getByRole('button', { name: '取消更改' }))
    expect(screen.queryByText('当前表单有未保存更改')).toBeNull()
    expect(within(model).getByLabelText('API Key')).toHaveValue('')
    await openTab('基本与时间')
    expect(screen.queryByLabelText('推送时间 2')).toBeNull()
    expect(writes(requests)).toEqual([])
  })
})

describe('news settings redesign: roles and module state', () => {
  it('is read-only for admins on every tab and never writes, tests or generates', { timeout: 15000 }, async () => {
    const requests = setup('admin', undefined, { ...settings, briefing_mode: 'agent', agent_instance_id: 'inst-a' })
    await openApp('/settings/news')
    expect(await screen.findByText(/只有 owner 能修改新闻早报的设置/)).toBeVisible()
    expect(screen.getByLabelText('启用新闻早报')).toBeDisabled()
    expect(screen.getByLabelText('推送时间 1')).toBeDisabled()
    expect(await screen.findByLabelText('执行智能体')).toBeDisabled()
    expect(screen.queryByRole('button', { name: '添加推送时间' })).toBeNull()
    for (const name of ['新闻来源', '兴趣主题', '模型与渠道']) {
      const panel = await openTab(name)
      const controls = panel.querySelectorAll('input, select, textarea')
      expect(controls.length).toBeGreaterThan(0)
      for (const control of controls) expect(control).toBeDisabled()
    }
    expect(screen.queryByRole('button', { name: /保存设置|测试推送|现在生成/ })).toBeNull()
    const channels = screen.getByRole('region', { name: '推送渠道' })
    expect(within(channels).getByText('只有 owner 能发送测试推送或现在生成早报。')).toBeVisible()

    await fireEvent.click(screen.getByRole('button', { name: '刷新状态' }))
    await vi.waitFor(() => expect(requests.filter(r => path(r) === '/api/v1/news/status').length).toBeGreaterThan(1))
    expect(writes(requests)).toEqual([])
  })

  it('does not request the settings when the news module is disabled', async () => {
    const requests = setup('owner', r => (path(r) === '/api/v1/modules' ? Response.json({ items: [{ ...newsModule, enabled: false }] }) : undefined))
    await openApp('/settings/news')
    expect(await screen.findByRole('heading', { level: 1, name: '功能未启用' })).toBeVisible()
    expect(requests.some(r => path(r).startsWith('/api/v1/news/') || path(r) === '/api/v1/tasks/assignees')).toBe(false)
  })

  it('treats MODULE_DISABLED from the settings API as a disabled module, not a failure', async () => {
    let disabled = false
    const requests = setup('owner', r => {
      if (path(r) === '/api/v1/modules') return Response.json({ items: [{ ...newsModule, enabled: !disabled }] })
      if (path(r).startsWith('/api/v1/news/')) { disabled = true; return fail(404, 'MODULE_DISABLED') }
      return undefined
    })
    await openApp('/settings/news')
    expect(await screen.findByRole('heading', { level: 1, name: '功能未启用' })).toBeVisible()
    expect(writes(requests)).toEqual([])
  })
})

describe('news settings redesign: real state, not guesses', () => {
  const summary = () => screen.findByRole('region', { name: '早报状态摘要' })
  const LABELS = ['TrendRadar 可用', 'TrendRadar 未部署', '早报由另一个工作空间使用', 'TrendRadar 连通状态未知', 'TrendRadar 连不上', '早报未启用，不检查连通']

  it.each([
    { label: 'TrendRadar 未部署', base: { deployed: false }, state: { deployed: false, online: null, latest_data_date: null }, link: '未部署', note: /这个部署没有安装 TrendRadar/ },
    { label: '早报由另一个工作空间使用', base: { enabled: false, taken_by_other: true }, state: { enabled: false, online: null }, link: '不检查（早报未启用）', note: /另一个工作空间已经启用了早报/ },
    { label: 'TrendRadar 连通状态未知', base: {}, state: { online: null }, link: '未知', note: null },
    { label: 'TrendRadar 连不上', base: {}, state: { online: false, error: 'connect timeout' }, link: '连不上', note: null },
    { label: '早报未启用，不检查连通', base: { enabled: false }, state: { enabled: false, online: null }, link: '不检查（早报未启用）', note: null },
  ])('shows "$label" distinctly', async ({ label, base, state, link, note }) => {
    setup('owner', undefined, { ...settings, ...base }, { ...status, ...state })
    await openApp('/settings/news')
    const bar = await summary()
    expect(await within(bar).findByText(label)).toBeVisible()
    for (const other of LABELS.filter(text => text !== label)) expect(within(bar).queryByText(other)).toBeNull()
    const card = screen.getByRole('region', { name: '当前运行状态' })
    expect(fact(card, '连通')).toHaveTextContent(link)
    expect(fact(card, 'TrendRadar')).toHaveTextContent(base.deployed === false ? '未部署' : '已部署')
    if (note) expect(screen.getByText(note)).toBeVisible()
    if (state.error) expect(within(card).getByText('TrendRadar 的说明：connect timeout')).toBeVisible()
  })

  it('shows the config file revision and the settings version separately', async () => {
    setup('owner')
    await openApp('/settings/news')
    const bar = await summary()
    expect(await within(bar).findByText('TrendRadar 可用')).toBeVisible()
    expect(within(bar).getByText('最近写入配置 #5')).toBeVisible()
    const card = screen.getByRole('region', { name: '当前运行状态' })
    expect(fact(card, '配置文件')).toHaveTextContent('已写入 #5')
    expect(fact(card, '设置版本')).toHaveTextContent('第 3 版')
    expect(within(card).getByText(/写入不代表 TrendRadar 已按新配置运行/)).toBeVisible()
    expect(document.body).not.toHaveTextContent(/运行中的配置|v5|#3/)
  })

  it('says when no config file has been written yet', async () => {
    setup('owner', undefined, { ...settings, config_rev: 0, rendered_at: null, version: 0 })
    await openApp('/settings/news')
    const bar = await summary()
    expect(await within(bar).findByText('还没有写入配置文件')).toBeVisible()
    expect(within(bar).queryByText(/最近写入配置 #/)).toBeNull()
  })

  it('does not report a save as fully successful when the config file could not be written', async () => {
    let server = settings
    setup('owner', r => {
      if (path(r) !== '/api/v1/news/settings') return undefined
      if (r.method === 'PUT') server = { ...settings, version: 4, render_error: '配置文件写入失败：PermissionError' }
      return Response.json(server)
    })
    await openApp('/settings/news')
    await fireEvent.click(await screen.findByRole('button', { name: '添加推送时间' }))
    await fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    expect(await screen.findByText(/设置已保存，但配置文件没有写成功/)).toBeVisible()
    expect(screen.queryByText('设置已保存：TrendRadar 下一次运行时生效。')).toBeNull()
    const card = screen.getByRole('region', { name: '当前运行状态' })
    expect(fact(card, '配置文件')).toHaveTextContent('写入失败')
    expect(within(card).getByText('配置文件写入失败：PermissionError')).toBeVisible()
  })

  it('keeps a deleted or disabled agent instance selected instead of picking another one', async () => {
    const requests = setup('owner', r => (path(r) === '/api/v1/news/settings' && r.method === 'PUT' ? Response.json({ ...settings, version: 4 }) : undefined), { ...settings, briefing_mode: 'agent', agent_instance_id: 'inst-gone' })
    await openApp('/settings/news')
    const select = await screen.findByLabelText('执行智能体')
    await vi.waitFor(() => expect(within(select).getByRole('option', { name: /已停用或已删除的实例/ })).toBeTruthy())
    expect(select).toHaveValue('inst-gone')
    expect(screen.getByText(/所选实例已停用或已删除/)).toBeVisible()
    const card = screen.getByRole('region', { name: '当前运行状态' })
    expect(fact(card, '执行实例')).toHaveTextContent('已停用或已删除的实例')
    expect(within(card).queryByText('新闻助手')).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: '添加推送时间' }))
    await fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    await vi.waitFor(() => expect(writes(requests)).toEqual(['PUT /api/v1/news/settings']))
    expect(await requests.find(r => r.method === 'PUT')!.clone().json()).toMatchObject({ briefing_mode: 'agent', agent_instance_id: 'inst-gone' })
  })
})

describe('news settings redesign: dirty drafts and side effects', () => {
  it('keeps unsaved changes when the settings change elsewhere and on a version conflict', { timeout: 15000 }, async () => {
    vi.stubGlobal('EventSource', Stream)
    let server = settings
    const requests = setup('owner', r => {
      if (path(r) !== '/api/v1/news/settings') return undefined
      return r.method === 'PUT' ? fail(409, 'VERSION_CONFLICT') : Response.json(server)
    })
    await openApp('/settings/news')
    expect(await screen.findByLabelText('推送时间 1')).toHaveValue('07:30')

    // 没有改动：别处的修改直接显示
    server = { ...settings, version: 4, push_times: ['08:00'] }
    newsChanged()
    await vi.waitFor(() => expect(screen.getByLabelText('推送时间 1')).toHaveValue('08:00'))

    // 有未保存的改动：别处再改也不覆盖
    await fireEvent.update(screen.getByLabelText('推送时间 1'), '09:30')
    server = { ...settings, version: 5, push_times: ['10:00'], exclusions: ['广告', '带货'] }
    newsChanged()
    expect(await screen.findByText(/设置刚被别处修改了/)).toBeVisible()
    expect(screen.getByLabelText('推送时间 1')).toHaveValue('09:30')
    expect(screen.getByText('当前表单有未保存更改')).toBeVisible()

    await fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    expect(await screen.findByText(/设置已被别人修改/)).toBeVisible()
    expect((await requests.find(r => r.method === 'PUT')!.clone().json()).version).toBe(4)
    expect(screen.getByLabelText('推送时间 1')).toHaveValue('09:30')

    await fireEvent.click(screen.getByRole('button', { name: '重新加载' }))
    await vi.waitFor(() => expect(screen.getByLabelText('推送时间 1')).toHaveValue('10:00'))
    expect(screen.queryByText('当前表单有未保存更改')).toBeNull()
  })

  it('refreshes the status with reads only and keeps the draft', async () => {
    const requests = setup('owner')
    await openApp('/settings/news')
    await fireEvent.update(await screen.findByLabelText('推送时间 1'), '06:00')
    const before = requests.length
    await fireEvent.click(screen.getByRole('button', { name: '刷新状态' }))
    await vi.waitFor(() => expect(requests.slice(before).map(path)).toEqual(expect.arrayContaining(['/api/v1/news/status', '/api/v1/news/settings'])))
    expect(requests.slice(before).every(r => r.method === 'GET')).toBe(true)
    expect(screen.getByLabelText('推送时间 1')).toHaveValue('06:00')
    expect(writes(requests)).toEqual([])
  })

  it('saves pending changes before a test push or generate, and keeps each channel result', { timeout: 15000 }, async () => {
    let server: Settings = { ...settings, briefing_mode: 'agent', agent_instance_id: 'inst-a' }
    let putReply = (): Response => { server = { ...server, version: server.version + 1, config_rev: server.config_rev + 1 }; return Response.json(server) }
    const requests = setup('owner', r => {
      const p = path(r)
      if (p === '/api/v1/news/settings') return r.method === 'PUT' ? putReply() : Response.json(server)
      if (p === '/api/v1/news/settings/test-push') return Response.json({ results: [{ channel: 'feishu', ok: true, detail: '' }, { channel: 'generic_webhook', ok: false, detail: 'HTTP 500' }] })
      if (p === '/api/v1/news/briefings/generate') return Response.json(pending, { status: 201 })
      return undefined
    })
    await openApp('/settings/news')
    const channels = await screen.findByRole('region', { name: '推送渠道' })
    expect(await within(channels).findByText(/Webhook 地址已设置，不回显/)).toBeVisible()
    expect(within(channels).getByText(/“已配置”不代表一定送达/)).toBeVisible()

    // 没有改动：直接用已保存的设置发
    await fireEvent.click(within(channels).getByRole('button', { name: '发送测试推送' }))
    const results = await within(channels).findByRole('list', { name: '测试推送结果' })
    expect(within(results).getByText('飞书：成功')).toBeVisible()
    expect(within(results).getByText('通用 Webhook：失败（HTTP 500）')).toBeVisible()
    expect(within(channels).getByText(/1 个渠道发送成功、1 个失败/)).toBeVisible()
    expect(document.body).not.toHaveTextContent(/全部送达|已送达/)
    expect(writes(requests)).toEqual(['POST /api/v1/news/settings/test-push'])

    // 有未保存的改动：先保存，再发
    await fireEvent.click(screen.getByRole('button', { name: '添加推送时间' }))
    await fireEvent.click(within(channels).getByRole('button', { name: '保存并发送测试推送' }))
    await vi.waitFor(() => expect(writes(requests)).toEqual(['POST /api/v1/news/settings/test-push', 'PUT /api/v1/news/settings', 'POST /api/v1/news/settings/test-push']))
    expect(await within(channels).findByRole('list', { name: '测试推送结果' })).toBeVisible()
    expect(screen.queryByText('当前表单有未保存更改')).toBeNull()

    // 保存失败（版本冲突）：不生成
    putReply = () => fail(409, 'VERSION_CONFLICT')
    await fireEvent.click(screen.getByRole('button', { name: '添加推送时间' }))
    await fireEvent.click(within(channels).getByRole('button', { name: '保存并现在生成' }))
    expect(await within(channels).findByText(/没有执行：设置还没有保存成功/)).toBeVisible()
    expect(requests.some(r => path(r) === '/api/v1/news/briefings/generate')).toBe(false)
    expect(screen.getByText('当前表单有未保存更改')).toBeVisible()

    // 保存了但配置文件没写成功：TrendRadar 还在用旧配置，不发测试推送
    putReply = () => { server = { ...server, version: server.version + 1, render_error: '配置文件写入失败：OSError' }; return Response.json(server) }
    await fireEvent.click(within(channels).getByRole('button', { name: '保存并发送测试推送' }))
    expect(await within(channels).findByText(/没有执行：设置已保存，但配置文件没有写成功/)).toBeVisible()
    expect(requests.filter(r => path(r) === '/api/v1/news/settings/test-push')).toHaveLength(2)

    // 没有改动时现在生成：只发生成请求
    await fireEvent.click(within(channels).getByRole('button', { name: '现在生成一份' }))
    expect(await within(channels).findByText(/已让智能体开始写早报/)).toBeVisible()
    expect(writes(requests).at(-1)).toBe('POST /api/v1/news/briefings/generate')
  })

  it('only offers 现在生成 for the agent mode', async () => {
    setup('owner')
    await openApp('/settings/news')
    const channels = await screen.findByRole('region', { name: '推送渠道' })
    expect(await within(channels).findByRole('button', { name: '现在生成一份' })).toBeDisabled()
    expect(within(channels).getByText(/只有“智能体写早报”方式能现在生成/)).toBeVisible()
  })

  it('lists learned preferences and source suggestions without applying them', async () => {
    const requests = setup('owner')
    await openApp('/settings/news')
    await screen.findByLabelText('推送时间 1')
    const panel = await openTab('学习偏好')
    const learned = within(panel).getByRole('region', { name: '学到的偏好' })
    expect(within(learned).getByText(/按最近 60 天的“收藏”和“不感兴趣”计算/)).toBeVisible()
    expect(within(learned).getByText('+4')).toBeVisible()
    expect(within(learned).getByText('14（上调）')).toBeVisible()
    expect(within(learned).getByText(/「微博」（热榜）被标了 7 次不感兴趣、0 次收藏/)).toBeVisible()
    expect(within(learned).getByText(/不会自动关/)).toBeVisible()
    expect(screen.queryByText('当前表单有未保存更改')).toBeNull()

    await fireEvent.click(within(learned).getByRole('button', { name: '去“新闻来源”查看' }))
    const sources = await screen.findByRole('tabpanel', { name: '新闻来源' })
    expect(within(sources).getByLabelText('微博')).toBeChecked()
    expect(screen.queryByText('当前表单有未保存更改')).toBeNull()
    expect(writes(requests)).toEqual([])
  })
})
