// 新闻早报页面（T-0153，docs/v2/NEWS_PLAN.md 第 4、5、7、8 节）：早报页、设置 → 新闻早报、首页的“今日早报”。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { fail, me, mockApi, openApp, workspace } from './helpers'
import { overview, widget } from './dashboard-fixtures'
import type { Schema } from '../src/services/api'
import { buildBody, fieldLabel, splitWords, toForm } from '../src/features/news/settingsForm'
import { briefingHtml, plainLines } from '../src/features/news/format'
import { registry, parseWidget } from '../src/widgets/registry'

const at = '2026-11-07T00:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
type Role = Schema['WorkspaceOut']['role']
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })

const channel = (over: Partial<Schema['NewsChannelOut']> = {}): Schema['NewsChannelOut'] => ({ enabled: false, msg_type: 'markdown', chat_id: '', from_addr: '', to_addr: '', smtp_server: '', smtp_port: null, server_url: '', secrets_set: [], ...over })
const settings: Schema['NewsSettingsOut'] = {
  workspace_id: 'workspace-a', enabled: true, timezone: 'Asia/Shanghai', push_times: ['07:30'], briefing_mode: 'trendradar', agent_instance_id: null,
  platforms: ['zhihu', 'weibo'], newsnow_api_url: '', feeds: [{ id: 'hn', name: 'Hacker News', url: 'https://hnrss.org/frontpage', enabled: true, max_age_days: 0 }],
  topics: [{ name: '人工智能', keywords: ['AI', '大模型'], required: [], max_items: 10, description: '', enabled: true }],
  exclusions: ['广告'], filter_method: 'keyword', ai_model: 'deepseek/deepseek-chat', ai_api_base: '', ai_api_key_set: true, ai_analysis: true, ai_translation: false,
  channels: { feishu: channel({ enabled: true, secrets_set: ['webhook_url'] }), telegram: channel() },
  version: 3, config_rev: 5, rendered_at: at, render_error: '', updated_at: at, deployed: true, taken_by_other: false,
  platform_choices: [{ id: 'zhihu', name: '知乎' }, { id: 'weibo', name: '微博' }, { id: 'douyin', name: '抖音' }],
  learned: { computed_at: at, topics: [{ name: '人工智能', score: 4, max_items: 10, adjusted_max_items: 14 }], source_suggestions: [{ source_type: 'hotlist', source_id: 'douyin', source_name: '抖音', dismisses: 6, favorites: 1 }] },
}
const status: Schema['NewsStatusOut'] = { deployed: true, enabled: true, online: true, latest_data_date: '2026-11-07', error: null, latest_briefing_at: at, rendered_at: at, render_error: '' }
const briefing: Schema['NewsBriefingOut'] = {
  id: 'b1', local_date: '2026-11-07', slot: '07:30', source: 'trendradar', title: '今日早报', status: 'complete', parts: 2, total_parts: 2, error: '', task_id: null, created_at: at, updated_at: at,
  content: '# 要闻\n\n**重点** 内容\n\n<script>window.__newsXss = 1</script>\n\n<img src=x onerror="window.__newsXss = 2">\n\n[恶意](javascript:alert(1)) [正常](https://example.com/a)',
}
const item = (over: Partial<Schema['NewsItemOut']> = {}): Schema['NewsItemOut'] => ({ title: 'OpenAI 发布新模型', url: 'https://news.example.com/1', mobile_url: null, source_type: 'hotlist', source_id: 'zhihu', source_name: '知乎', rank: 1, time: '07:00', summary: null, topic: '人工智能', favorite_id: null, dismissed: false, ...over })
const summary = (over: Partial<Schema['NewsBriefingSummary']> = {}): Schema['NewsBriefingSummary'] => ({ id: 'b1', local_date: '2026-11-07', slot: '07:30', source: 'trendradar', title: '今日早报', status: 'complete', excerpt: '要闻 重点内容', created_at: at, ...over })

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('news settings form helpers', () => {
  it('splits keywords on commas and lines but keeps /regex/ whole', () => {
    expect(splitWords('AI, 大模型，GPT\n/GPT-\\d{1,2}/ ; 机器 学习\nAI')).toEqual(['AI', '大模型', 'GPT', '/GPT-\\d{1,2}/', '机器 学习'])
    expect(splitWords('  \n ,, ')).toEqual([])
  })
  it('omits untouched secrets, sends typed ones and "" for cleared ones, and passes the version', () => {
    const form = toForm(settings)
    expect(buildBody(form, 3)).not.toHaveProperty('ai_api_key')
    expect(buildBody(form, 3).channels?.feishu?.secrets).toEqual({})
    form.ai_api_key = { value: '', clear: true }
    form.channels.feishu.secrets.webhook_url = { value: '  https://open.feishu.cn/hook/new  ', clear: false }
    form.channels.ntfy.secrets.token = { value: '', clear: true }
    form.channels.email.smtp_port = '465'
    const body = buildBody(form, 3)
    expect(body).toMatchObject({ version: 3, ai_api_key: '', agent_instance_id: null, platforms: ['zhihu', 'weibo'], exclusions: ['广告'] })
    expect(body.channels?.feishu?.secrets).toEqual({ webhook_url: 'https://open.feishu.cn/hook/new' })
    expect(body.channels?.ntfy?.secrets).toEqual({ token: '' })
    expect(body.channels?.email?.smtp_port).toBe(465)
    expect(Object.keys(body.channels ?? {})).toHaveLength(9)
    // 时区由部署设置（TRENDRADAR_TZ），网页上只读，不提交
    expect(body).not.toHaveProperty('timezone')
  })
  it('names 422 locations in Chinese', () => {
    expect(fieldLabel(['body', 'topics', 0, 'keywords', 2])).toBe('兴趣主题第 1 个 · 关键词')
    expect(fieldLabel(['body', 'channels', 'telegram', 'secrets', 'bot_token'])).toBe('推送渠道「Telegram」 · 机器人令牌（Bot Token）')
    expect(fieldLabel(['body', 'feeds', 1, 'url'])).toBe('RSS 源第 2 个 · 地址')
    expect(fieldLabel(['body'])).toBe('设置')
  })
})

describe('news settings page', () => {
  function setup(role: Role, handler?: (r: Request) => Response | undefined, base = settings) {
    return mockApi(async r => {
      const result = handler?.(r)
      if (result) return result
      const p = path(r)
      if (p === '/api/v1/me' && r.method === 'GET') return as(role)
      if (p === '/api/v1/news/settings' && r.method === 'GET') return Response.json(base)
      if (p === '/api/v1/news/status') return Response.json(status)
      if (p === '/api/v1/tasks/assignees') return Response.json({ users: [], instances: [{ id: 'inst-a', name: '甲', status: 'online', description: '' }] })
      return undefined
    })
  }

  it('saves with the version, sends only typed or cleared secrets and never shows them back', async () => {
    let saved = settings
    const requests = setup('owner', r => {
      if (path(r) !== '/api/v1/news/settings') return undefined
      if (r.method === 'PUT') saved = { ...settings, version: saved.version + 1, ai_api_key_set: false, channels: { ...settings.channels, telegram: channel({ enabled: true, chat_id: '42', secrets_set: ['bot_token'] }) } }
      return Response.json(saved)
    })
    await openApp('/settings/news')
    expect(await screen.findByRole('heading', { level: 1, name: '新闻早报' })).toBeVisible()
    const state = await screen.findByRole('region', { name: '状态' })
    expect(await within(state).findByText('在线')).toBeVisible()
    expect(screen.getByText('Asia/Shanghai')).toBeVisible()

    const telegram = screen.getByRole('group', { name: '推送渠道：Telegram' })
    await fireEvent.click(within(telegram).getByLabelText('启用Telegram'))
    await fireEvent.update(within(telegram).getByLabelText('会话 ID（Chat ID）'), '42')
    await fireEvent.update(within(telegram).getByLabelText('机器人令牌（Bot Token）'), 'tg-secret-token')
    const feishu = screen.getByRole('group', { name: '推送渠道：飞书' })
    expect(within(feishu).getByText('已设置')).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '清除API Key' }))
    expect(screen.getByText('保存后清除')).toBeVisible()
    const topic = screen.getByRole('group', { name: '兴趣主题 1' })
    expect(within(topic).getByLabelText('关键词')).toHaveValue('AI, 大模型')
    await fireEvent.update(within(topic).getByLabelText('关键词'), 'AI, 大模型, /GPT-\\d{1,2}/')
    await fireEvent.click(screen.getByRole('button', { name: '添加推送时间' }))
    await fireEvent.update(screen.getByLabelText('推送时间 2'), '18:00')
    await fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    expect(await screen.findByText('设置已保存：TrendRadar 下一次运行时生效。')).toBeVisible()

    const put = requests.find(r => r.method === 'PUT')!
    expect(put.headers.get('X-Workspace-Id')).toBe('workspace-a')
    const body = await put.clone().json()
    expect(body).toMatchObject({ version: 3, ai_api_key: '', push_times: ['07:30', '18:00'], briefing_mode: 'trendradar', agent_instance_id: null })
    expect(body.topics[0].keywords).toEqual(['AI', '大模型', '/GPT-\\d{1,2}/'])
    expect(body.channels.feishu).toMatchObject({ enabled: true, secrets: {} })
    expect(body.channels.telegram).toMatchObject({ enabled: true, chat_id: '42', secrets: { bot_token: 'tg-secret-token' } })
    expect(body.channels.email).toMatchObject({ enabled: false, smtp_port: null, secrets: {} })
    expect(within(telegram).getByLabelText('机器人令牌（Bot Token）')).toHaveValue('')
    expect(document.body).not.toHaveTextContent('tg-secret-token')

    // 不动密钥再保存一次：密钥一个都不带，版本用上次保存返回的
    await fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    await vi.waitFor(() => expect(requests.filter(r => r.method === 'PUT')).toHaveLength(2))
    const second = await requests.filter(r => r.method === 'PUT')[1]!.clone().json()
    expect(second.version).toBe(4)
    expect(second).not.toHaveProperty('ai_api_key')
    expect(second.channels.telegram.secrets).toEqual({})
  })

  it('checks the form before saving and explains conflicts and field errors', async () => {
    let reply: Response = fail(409, 'VERSION_CONFLICT')
    const requests = setup('owner', r => (path(r) === '/api/v1/news/settings' && r.method === 'PUT' ? reply : undefined))
    await openApp('/settings/news')
    const telegram = await screen.findByRole('group', { name: '推送渠道：Telegram' })
    await fireEvent.click(within(telegram).getByLabelText('启用Telegram'))
    await fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    expect(await screen.findByText('推送渠道「Telegram」已启用：请填写机器人令牌（Bot Token）。')).toBeVisible()
    expect(requests.some(r => r.method === 'PUT')).toBe(false)
    await fireEvent.click(within(telegram).getByLabelText('启用Telegram'))

    await fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    expect(await screen.findByText(/设置已被别人修改/)).toBeVisible()
    expect(screen.getByRole('button', { name: '重新加载' })).toBeVisible()

    reply = Response.json({ error: { code: 'VALIDATION_FAILED', message: '请求参数校验失败', request_id: 'req', details: { errors: [
      { loc: ['body', 'channels', 'feishu', 'secrets', 'webhook_url'], msg: '不能指向内网、回环或链路本地地址', type: 'value_error' },
      { loc: ['body', 'topics', 0], msg: 'Value error, 至少写一个关键词', type: 'value_error' },
    ] } } }, { status: 422 })
    await fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
    expect(await screen.findByText('推送渠道「飞书」 · Webhook 地址：不能指向内网、回环或链路本地地址')).toBeVisible()
    expect(screen.getByText('兴趣主题第 1 个：至少写一个关键词')).toBeVisible()
  })

  it('shows banners, pushes a test and asks the agent for a briefing in mode B', async () => {
    let generate: Response = fail(409, 'BRIEFING_PENDING')
    const requests = setup('owner', r => {
      if (path(r) === '/api/v1/news/settings/test-push') return Response.json({ results: [{ channel: 'feishu', ok: true, detail: '' }, { channel: 'generic_webhook', ok: false, detail: 'HTTP 500' }] })
      if (path(r) === '/api/v1/news/briefings/generate') return generate
      return undefined
    }, { ...settings, briefing_mode: 'agent', agent_instance_id: 'inst-a', deployed: false, taken_by_other: true, render_error: '配置卷不可写' })
    await openApp('/settings/news')
    expect(await screen.findByText(/这个部署没有安装 TrendRadar/)).toBeVisible()
    expect(screen.getByText(/另一个工作空间已经启用了早报/)).toBeVisible()
    expect(screen.getByText('配置文件写入失败：配置卷不可写')).toBeVisible()
    expect(await screen.findByLabelText('写早报的智能体实例')).toHaveValue('inst-a')

    await fireEvent.click(screen.getByRole('button', { name: '发一条测试推送' }))
    const results = await screen.findByRole('list', { name: '测试推送结果' })
    expect(within(results).getByText('飞书：成功')).toBeVisible()
    expect(within(results).getByText('通用 Webhook：失败（HTTP 500）')).toBeVisible()

    await fireEvent.click(screen.getByRole('button', { name: '现在生成一份' }))
    expect(await screen.findByText('已经有一份早报正在写，等它完成或失败后再试。')).toBeVisible()
    generate = Response.json({ ...briefing, status: 'pending', content: '' }, { status: 201 })
    await fireEvent.click(screen.getByRole('button', { name: '现在生成一份' }))
    expect(await screen.findByText(/已让智能体开始写早报/)).toBeVisible()
    expect(requests.filter(r => path(r) === '/api/v1/news/briefings/generate')).toHaveLength(2)

    const learned = screen.getByRole('region', { name: '学到的偏好' })
    expect(within(learned).getByText('+4')).toBeVisible()
    expect(within(learned).getByText('14（上调）')).toBeVisible()
    expect(within(learned).getByText(/「抖音」（热榜）被标了 6 次不感兴趣、1 次收藏/)).toBeVisible()
  })

  it('is read-only for admins and hidden from members', async () => {
    setup('admin')
    const first = await openApp('/settings/news')
    expect(await screen.findByText(/只有 owner 能修改新闻早报的设置/)).toBeVisible()
    expect(screen.getByLabelText('启用新闻早报')).toBeDisabled()
    expect(screen.queryByRole('button', { name: '保存设置' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发一条测试推送' })).not.toBeInTheDocument()

    first.unmount()
    mockApi(r => (path(r) === '/api/v1/me' && r.method === 'GET' ? as('member') : undefined))
    const { router } = await openApp('/')
    const nav = screen.getByRole('navigation', { name: '主导航' })
    expect(nav.querySelector('a[href="/settings/news"]')).toBeNull()
    expect(nav.querySelector('a[href="/news"]')).toHaveTextContent('早报')
    await router.push('/settings/news')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
  })
})

describe('news page', () => {
  function setup(role: Role, handler?: (r: Request) => Response | undefined) {
    let favorite: string | null = null
    const requests = mockApi(async r => {
      const result = handler?.(r)
      if (result) return result
      const p = path(r)
      if (p === '/api/v1/me' && r.method === 'GET') return as(role)
      if (p === '/api/v1/news/today') return Response.json({ briefing, topics: [{ name: '人工智能', items: [item({ favorite_id: favorite })] }, { name: '财经', items: [] }], unavailable: false, message: null })
      if (p === '/api/v1/news/feedback' && r.method === 'POST') {
        const body = await r.clone().json() as Schema['NewsFeedbackIn']
        if (body.kind === 'favorite') favorite = 'fav-1'
        return Response.json({ id: body.kind === 'favorite' ? 'fav-1' : 'dis-1', ...body, created_at: at }, { status: 201 })
      }
      if (p === '/api/v1/news/feedback/fav-1' && r.method === 'DELETE') { favorite = null; return new Response(null, { status: 204 }) }
      return undefined
    })
    return requests
  }

  it('renders the briefing markdown safely and the topic items as external links', async () => {
    setup('viewer')
    await openApp('/news')
    expect(await screen.findByRole('heading', { level: 1, name: '早报' })).toBeVisible()
    const article = await screen.findByRole('article', { name: '早报：今日早报' })
    expect(within(article).getByText('2026/11/07 07:30')).toBeVisible()
    expect(within(article).getByText('已送达')).toBeVisible()
    expect(article.querySelector('strong')).toHaveTextContent('重点')
    expect(article.querySelector('script')).toBeNull()
    expect(article.querySelector('img')).toBeNull()
    expect(article.querySelector('[onerror]')).toBeNull()
    expect((window as unknown as { __newsXss?: number }).__newsXss).toBeUndefined()
    expect(article).toHaveTextContent('<script>window.__newsXss = 1</script>')
    expect(within(article).queryByRole('link', { name: '恶意' })).toBeNull()
    const safe = within(article).getByRole('link', { name: '正常' })
    expect(safe).toHaveAttribute('href', 'https://example.com/a')
    expect(safe).toHaveAttribute('target', '_blank')
    expect(safe.getAttribute('rel')).toContain('noopener')

    const topic = screen.getByRole('region', { name: '主题：人工智能' })
    const link = within(topic).getByRole('link', { name: 'OpenAI 发布新模型' })
    expect(link).toHaveAttribute('href', 'https://news.example.com/1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(within(topic).getByText('知乎')).toBeVisible()
    expect(within(topic).getByText('第 1 名')).toBeVisible()
    // 访客看得到，但没有收藏 / 不感兴趣
    expect(within(topic).queryByRole('button', { name: '收藏' })).not.toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: '主题：财经' })).getByText('今天这个主题还没有条目。')).toBeVisible()
  })

  it('toggles a favorite and lets the owner add an exclusion word when dismissing', async () => {
    const requests = setup('owner')
    await openApp('/news')
    const row = await screen.findByRole('listitem', { name: 'OpenAI 发布新模型' })
    await fireEvent.click(within(row).getByRole('button', { name: '收藏' }))
    const pressed = await within(screen.getByRole('listitem', { name: 'OpenAI 发布新模型' })).findByRole('button', { name: '已收藏' })
    expect(pressed).toHaveAttribute('aria-pressed', 'true')
    const post = requests.find(r => r.method === 'POST')!
    expect(post.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(await post.clone().json()).toEqual({ kind: 'favorite', url: 'https://news.example.com/1', title: 'OpenAI 发布新模型', source_type: 'hotlist', source_id: 'zhihu', source_name: '知乎', topic: '人工智能' })

    await vi.waitFor(() => expect(pressed).toBeEnabled())
    await fireEvent.click(pressed)
    const plain = await within(screen.getByRole('listitem', { name: 'OpenAI 发布新模型' })).findByRole('button', { name: '收藏' })
    expect(requests.some(r => r.method === 'DELETE' && path(r) === '/api/v1/news/feedback/fav-1')).toBe(true)
    await vi.waitFor(() => expect(plain).toBeEnabled())

    await fireEvent.click(within(screen.getByRole('listitem', { name: 'OpenAI 发布新模型' })).getByRole('button', { name: '不感兴趣' }))
    const form = screen.getByRole('form', { name: '标为不感兴趣' })
    await fireEvent.update(within(form).getByLabelText('同时把一个词加入全局排除（可不填）'), ' 发布 ')
    await fireEvent.click(within(form).getByRole('button', { name: '标为不感兴趣' }))
    await vi.waitFor(() => expect(requests.filter(r => r.method === 'POST')).toHaveLength(2))
    expect(await requests.filter(r => r.method === 'POST')[1]!.clone().json()).toMatchObject({ kind: 'dismiss', url: 'https://news.example.com/1', exclude_word: '发布' })
  })

  it('dismisses at once for members (no exclusion word) and shows errors', async () => {
    const requests = setup('member', r => (r.method === 'POST' && path(r) === '/api/v1/news/feedback' && requests.filter(x => x.method === 'POST').length > 1 ? fail(403, 'FORBIDDEN') : undefined))
    await openApp('/news')
    const row = await screen.findByRole('listitem', { name: 'OpenAI 发布新模型' })
    await fireEvent.click(within(row).getByRole('button', { name: '不感兴趣' }))
    await vi.waitFor(() => expect(requests.filter(r => r.method === 'POST')).toHaveLength(1))
    expect(screen.queryByRole('form', { name: '标为不感兴趣' })).not.toBeInTheDocument()
    const body = await requests.find(r => r.method === 'POST')!.clone().json()
    expect(body).toMatchObject({ kind: 'dismiss' })
    expect(body).not.toHaveProperty('exclude_word')
    const star = within(screen.getByRole('listitem', { name: 'OpenAI 发布新模型' })).getByRole('button', { name: '收藏' })
    await vi.waitFor(() => expect(star).toBeEnabled())
    await fireEvent.click(star)
    expect(await screen.findByText('你没有执行此操作的权限。')).toBeVisible()
  })

  it('opens a briefing from a notification link and pages through the archive', async () => {
    const requests = setup('member', r => {
      if (path(r) === '/api/v1/news/briefings/b2') return Response.json({ ...briefing, id: 'b2', title: '昨天的早报', local_date: '2026-11-06', status: 'failed', error: '智能体 90 分钟没有写完', content: '' })
      if (path(r) === '/api/v1/news/briefings') {
        const cursor = new URL(r.url).searchParams.get('cursor')
        return Response.json(cursor ? { items: [summary({ id: 'b3', title: '更早的早报', local_date: '2026-11-05' })], next_cursor: null } : { items: [summary(), summary({ id: 'b2', title: '昨天的早报', local_date: '2026-11-06', status: 'failed' })], next_cursor: 'c2' })
      }
      return undefined
    })
    const { router } = await openApp('/news?briefing=b2')
    const article = await screen.findByRole('article', { name: '早报：昨天的早报' })
    expect(within(article).getByText('这份早报没有生成成功：智能体 90 分钟没有写完')).toBeVisible()
    expect(screen.getByRole('tab', { name: '往期' })).toHaveAttribute('aria-selected', 'true')

    await fireEvent.click(screen.getByRole('button', { name: '← 返回往期列表' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.briefing).toBeUndefined())
    const list = await screen.findByRole('list', { name: '往期早报' })
    expect(within(list).getAllByRole('button')).toHaveLength(2)
    await fireEvent.click(screen.getByRole('button', { name: '加载更多' }))
    expect(await within(list).findByText('更早的早报')).toBeVisible()
    expect(new URL(requests.filter(r => path(r) === '/api/v1/news/briefings').at(-1)!.url).searchParams.get('cursor')).toBe('c2')
    await fireEvent.click(within(list).getByRole('button', { name: /今日早报/ }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.briefing).toBe('b1'))
  })

  it('filters the hot list by platform, reads RSS by days and searches', async () => {
    const requests = setup('viewer', r => {
      if (path(r) === '/api/v1/news/hot') {
        const platform = new URL(r.url).searchParams.get('platform')
        return Response.json(platform ? { items: [item({ title: '微博热搜一', source_id: 'weibo', source_name: '微博', topic: null })], unavailable: false, message: null } : { items: [], unavailable: true, message: 'TrendRadar 暂时连不上' })
      }
      if (path(r) === '/api/v1/news/rss') return Response.json({ items: [item({ title: 'RSS 条目', source_type: 'rss', source_id: 'hn', source_name: 'Hacker News', rank: null })], unavailable: false, message: null })
      if (path(r) === '/api/v1/news/search') return Response.json({ items: [], unavailable: false, message: '还没有数据（TrendRadar 每 30 分钟抓取一次）' })
      return undefined
    })
    const { router } = await openApp('/news?tab=hot')
    expect(await screen.findByText('TrendRadar 暂时连不上，稍后再看。')).toBeVisible()
    await fireEvent.update(screen.getByLabelText('平台'), 'weibo')
    expect(await screen.findByRole('link', { name: '微博热搜一' })).toBeVisible()
    expect(new URL(requests.filter(r => path(r) === '/api/v1/news/hot').at(-1)!.url).searchParams.get('platform')).toBe('weibo')

    await fireEvent.click(screen.getByRole('tab', { name: 'RSS' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.tab).toBe('rss'))
    await fireEvent.update(await screen.findByLabelText('时间范围'), '3')
    expect(await screen.findByRole('link', { name: 'RSS 条目' })).toBeVisible()
    expect(new URL(requests.filter(r => path(r) === '/api/v1/news/rss').at(-1)!.url).searchParams.get('days')).toBe('3')

    await fireEvent.click(screen.getByRole('tab', { name: '搜索' }))
    const form = await screen.findByRole('search', { name: '搜索新闻' })
    await fireEvent.update(within(form).getByLabelText('关键词'), ' 大模型 ')
    await fireEvent.click(within(form).getByLabelText('包含 RSS'))
    await fireEvent.click(within(form).getByRole('button', { name: '搜索' }))
    expect(await screen.findByText('还没有数据（TrendRadar 每 30 分钟抓取一次）')).toBeVisible()
    const query = new URL(requests.find(r => path(r) === '/api/v1/news/search')!.url).searchParams
    expect(Object.fromEntries(query)).toEqual({ q: '大模型', days: '7', include_rss: 'false', limit: '50' })
  })

  it('lists favorites and removes one', async () => {
    let gone = false
    const requests = setup('member', r => {
      if (path(r) === '/api/v1/news/feedback' && r.method === 'GET') return Response.json({ items: gone ? [] : [{ id: 'fav-9', kind: 'favorite', url: 'https://news.example.com/9', title: '收藏的新闻', source_type: 'rss', source_id: 'hn', source_name: 'Hacker News', topic: '', created_at: at }], next_cursor: null })
      if (r.method === 'DELETE' && path(r) === '/api/v1/news/feedback/fav-9') { gone = true; return new Response(null, { status: 204 }) }
      return undefined
    })
    await openApp('/news?tab=favorites')
    const row = await screen.findByRole('listitem', { name: '收藏的新闻' })
    expect(within(row).getByRole('link', { name: '收藏的新闻' })).toHaveAttribute('rel', 'noopener noreferrer')
    expect(new URL(requests.find(r => path(r) === '/api/v1/news/feedback')!.url).searchParams.get('kind')).toBe('favorite')
    await fireEvent.click(within(row).getByRole('button', { name: '取消收藏' }))
    expect(await screen.findByText(/还没有收藏/)).toBeVisible()
  })

  it('refetches news when the workspace event stream says news changed', async () => {
    class Stream extends EventTarget {
      static last: Stream | null = null
      readyState = 1
      constructor(public url: string) { super(); Stream.last = this }
      close() { this.readyState = 2 }
    }
    vi.stubGlobal('EventSource', Stream)
    const requests = setup('viewer')
    await openApp('/news')
    await screen.findByRole('article', { name: '早报：今日早报' })
    const before = requests.filter(r => path(r) === '/api/v1/news/today').length
    Stream.last!.dispatchEvent(new MessageEvent('invalidate', { data: JSON.stringify({ workspace_id: 'workspace-a', topics: ['news'], at }) }))
    await vi.waitFor(() => expect(requests.filter(r => path(r) === '/api/v1/news/today').length).toBe(before + 1))
  })
})

describe('今日早报 widget', () => {
  it('registers news_briefing with its default and limits', () => {
    expect(registry.news_briefing.title).toBe('今日早报')
    expect(registry.news_briefing.configSchema.parse({})).toEqual({ lines: 6 })
    expect(parseWidget({ ...widget, widget_type: 'news_briefing', config: { lines: 1 } })).toBeNull()
    expect(parseWidget({ ...widget, widget_type: 'news_briefing', config: { lines: 21 } })).toBeNull()
  })

  it('shows the time, title and the first lines as plain text and links to the news page', async () => {
    const card: Schema['WidgetOut'] = { ...widget, id: 'w-news', widget_type: 'news_briefing', config: { lines: 3 } }
    mockApi(r => {
      const p = path(r)
      if (p === '/api/v1/dashboard/overview') return Response.json({ ...overview, widgets: [card] })
      if (p === '/api/v1/news/briefings') return Response.json({ items: [summary()], next_cursor: null })
      if (p === '/api/v1/news/briefings/b1') return Response.json({ ...briefing, content: '# 今日要闻\n\n- **第一条**：<b>大模型</b>发布\n- [第二条](https://example.com/2)\n\n---\n\n第三段\n第四段' })
      return undefined
    })
    const { router } = await openApp('/')
    const article = await screen.findByRole('article', { name: '今日早报' })
    const lines = await within(article).findByRole('list', { name: '早报开头' })
    await vi.waitFor(() => expect(within(lines).getAllByRole('listitem').map(li => li.textContent)).toEqual(['今日要闻', '第一条：大模型发布', '第二条']))
    expect(article.querySelector('b')).toBeNull()
    expect(within(article).getByText('2026/11/07 07:30')).toBeVisible()
    await fireEvent.click(within(article).getByRole('link', { name: '今日早报' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/news'))
  })

  it('explains when there is no briefing yet', async () => {
    const card: Schema['WidgetOut'] = { ...widget, id: 'w-news', widget_type: 'news_briefing', config: {} }
    mockApi(r => (path(r) === '/api/v1/dashboard/overview' ? Response.json({ ...overview, widgets: [card] }) : path(r) === '/api/v1/news/briefings' ? Response.json({ items: [], next_cursor: null }) : undefined))
    await openApp('/')
    const article = await screen.findByRole('article', { name: '今日早报' })
    expect(await within(article).findByText(/还没有早报/)).toBeVisible()
  })
})

describe('news markdown helpers', () => {
  it('renders without raw HTML and strips markdown to plain lines', () => {
    const html = briefingHtml('<iframe src="https://evil.example"></iframe>\n\n[[内部]] ![图](https://img.example/a.png)')
    expect(html).not.toContain('<iframe')
    expect(html).not.toContain('<img')
    expect(html).toContain('[[内部]]')
    expect(plainLines('```\ncode\n```\n> 引用 *强调*\n| a | b |\n|---|---|\n| 1 | 2 |', 5)).toEqual(['code', '引用 强调', 'a · b', '1 · 2'])
  })
})
