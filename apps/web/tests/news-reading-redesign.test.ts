// 早报阅读页改版（T-0174，二批改造规范第 5 节，计划 Task 3）：真实正文有几节显示几节、来源只取正文里的链接、
// 摘要段落不放假按钮、早报状态分开、安全渲染、刷新只读、模块停用不请求、切工作空间不串早报。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { me, mockApi, newsModule, openApp, workspace } from './helpers'
import type { Schema } from '../src/services/api'
import { briefingSections } from '../src/modules/news/format'

const at = '2026-11-09T00:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
type Role = Schema['WorkspaceOut']['role']
type Today = Schema['NewsTodayOut']
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })
const briefing = (over: Partial<Schema['NewsBriefingOut']> = {}): Schema['NewsBriefingOut'] => ({
  id: 'b1', local_date: '2026-11-09', slot: '07:30', source: 'agent', title: '今日早报', status: 'complete', parts: 1, total_parts: null,
  error: '', task_id: null, created_at: at, updated_at: at, content: '今天只有一段话。', ...over,
})
const item = (over: Partial<Schema['NewsItemOut']> = {}): Schema['NewsItemOut'] => ({ title: 'OpenAI 发布新模型', url: 'https://news.example.com/1', mobile_url: null, source_type: 'hotlist', source_id: 'zhihu', source_name: '知乎', rank: 1, time: '07:00', summary: null, topic: '人工智能', favorite_id: null, dismissed: false, ...over })
const summary = (over: Partial<Schema['NewsBriefingSummary']> = {}): Schema['NewsBriefingSummary'] => ({ id: 'b1', local_date: '2026-11-09', slot: '07:30', source: 'agent', title: '今日早报', status: 'complete', excerpt: '', created_at: at, ...over })
const topics: Today['topics'] = [
  { name: '人工智能', items: [item(), item({ title: 'RSS：大模型部署', url: 'https://rss.example.com/2', source_type: 'rss', source_id: 'hn', source_name: 'Hacker News', rank: null })] },
  { name: '财经', items: [item({ title: 'OpenAI 发布新模型', topic: '财经' })] },
]
const today = (over: Partial<Today> = {}): Today => ({ briefing: briefing(), topics, unavailable: false, message: null, ...over })

function setup(role: Role, value: Today, handler?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => {
    const result = await handler?.(r)
    if (result) return result
    if (path(r) === '/api/v1/me' && r.method === 'GET') return as(role)
    if (path(r) === '/api/v1/news/today') return Response.json(value)
    return undefined
  })
}
const reading = (title = '今日早报') => screen.findByRole('article', { name: `早报：${title}` })

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('早报正文：按真实内容排版', () => {
  it('shows an untitled single paragraph as it is: no "3 件事", no numbering, no sources, no feedback buttons', async () => {
    setup('owner', today())
    await openApp('/news')
    const article = await reading()
    expect(article).toHaveTextContent('今天只有一段话。')
    expect(article).not.toHaveTextContent(/3\s*件事/)
    expect(article.querySelectorAll('.news-section')).toHaveLength(1)
    expect(article.querySelector('.news-section-no')).toBeNull()
    expect(article).not.toHaveTextContent('来源：')
    // 正文段落没有对应的资讯条目：没有收藏 / 不感兴趣；独立资讯的来源也不会贴进正文
    expect(within(article).queryByRole('button')).toBeNull()
    expect(article).not.toHaveTextContent('知乎')
    expect(article.querySelector('a[href="https://news.example.com/1"]')).toBeNull()
    // 反馈按钮只在真实资讯条目上
    const related = screen.getByRole('region', { name: '相关资讯' })
    const row = within(within(related).getByRole('region', { name: '主题：人工智能' })).getByRole('listitem', { name: 'OpenAI 发布新模型' })
    expect(within(row).getByRole('button', { name: '收藏' })).toBeVisible()
    expect(within(row).getByRole('link', { name: 'OpenAI 发布新模型' })).toHaveAttribute('href', 'https://news.example.com/1')
  })

  it('numbers exactly the titled sections and takes each section\'s sources from its own links only', async () => {
    setup('member', today({ briefing: briefing({ content: '## 开发工具更新\n\n先看兼容性，见 [InfoQ 报道](https://www.infoq.cn/a/1)。\n\n## 开源模型\n\n部署成本要算清楚。' }) }))
    await openApp('/news')
    const article = await reading()
    const sections = [...article.querySelectorAll<HTMLElement>('.news-section')]
    expect(sections).toHaveLength(2)
    expect(sections.map((s) => s.querySelector('.news-section-no')?.textContent)).toEqual(['01', '02'])
    expect(article).not.toHaveTextContent('03')
    expect(within(sections[0]!).getByRole('heading', { name: '开发工具更新' })).toBeVisible()
    expect(within(sections[0]!).getByText('infoq.cn')).toBeVisible()
    const source = within(sections[0]!).getByRole('link', { name: '阅读来源 →' })
    expect(source).toHaveAttribute('href', 'https://www.infoq.cn/a/1')
    expect(source).toHaveAttribute('target', '_blank')
    expect(source.getAttribute('rel')).toContain('noopener')
    // 第二节正文没有链接：不显示来源，也不借用第一节或相关资讯的来源
    expect(sections[1]).not.toHaveTextContent('来源：')
    expect(within(sections[1]!).queryByRole('link')).toBeNull()
    expect(within(article).queryByRole('button')).toBeNull()
  })

  it('shows a body without links without any source row', async () => {
    setup('viewer', today({ briefing: briefing({ content: '## 一\n\n纯文字一\n\n## 二\n\n纯文字二' }) }))
    await openApp('/news')
    const article = await reading()
    expect(article.querySelectorAll('.news-section')).toHaveLength(2)
    expect(article).not.toHaveTextContent('来源：')
    expect(within(article).queryByRole('link', { name: /阅读来源/ })).toBeNull()
  })

  it('splits on the highest repeated heading and ignores image fallbacks as sources', () => {
    const sections = briefingSections('# 每日热点\n\n导语\n\n## A\n\n![配图](https://img.example/a.png) [原文](https://a.example/x)\n\n## B\n\n[一](https://b.example/1) [二](https://b.example/2)')
    expect(sections.map((s) => s.titled)).toEqual([false, true, true])
    expect(sections[0]!.html).toContain('每日热点')
    expect(sections[1]!.hosts).toEqual(['a.example'])
    expect(sections[1]!.link).toBe('https://a.example/x')
    // 一节里有多个链接：列出域名，但不挑其中一个当“阅读来源”
    expect(sections[2]!.hosts).toEqual(['b.example'])
    expect(sections[2]!.link).toBeNull()
    expect(briefingSections('   ')).toEqual([])
  })

  it('blocks scripts, unsafe links and remote images in the briefing body', async () => {
    const content = '## 要闻\n\n<script>window.__newsXss = 1</script>\n\n<img src=x onerror="window.__newsXss = 2">\n\n[恶意](javascript:alert(1)) ![远程图](https://img.example/track.png) [正常](https://example.com/a)'
    setup('viewer', today({ briefing: briefing({ content }) }))
    await openApp('/news')
    const article = await reading()
    expect(article.querySelector('script, img, iframe, [onerror]')).toBeNull()
    expect(article.querySelector('[src]')).toBeNull()
    expect(article.querySelector('a[href^="javascript"]')).toBeNull()
    expect((window as unknown as { __newsXss?: number }).__newsXss).toBeUndefined()
    // 外部图片只显示成链接（不加载），也不算来源
    expect(within(article).getByRole('link', { name: '图片：远程图' })).toHaveAttribute('href', 'https://img.example/track.png')
    expect(article).not.toHaveTextContent('img.example')
    expect(within(article).getByText('example.com')).toBeVisible()
  })
})

describe('早报状态与侧栏', () => {
  it.each([
    ['receiving', { status: 'receiving', parts: 1, total_parts: 3, content: '## 第一批\n\n已经收到的片段' }, '正在接收这份早报（已收到第 1/3 批），内容还不完整。', '正在接收：已收到第 1/3 批，内容还不完整。'],
    ['pending', { status: 'pending', content: '' }, '智能体正在写这份早报，写好后这里会更新。', '智能体正在写这份早报，还没有写完。'],
    ['failed', { status: 'failed', content: '', error: '模型超时' }, '这份早报没有生成成功：模型超时', '这份早报没有生成成功。'],
    ['complete', { status: 'complete' }, null, '这份早报已整理完成。'],
  ] as const)('keeps the %s state apart and only calls a complete briefing finished', async (status, over, banner, side) => {
    setup('member', today({ briefing: briefing(over as Partial<Schema['NewsBriefingOut']>) }))
    await openApp('/news')
    const article = await reading()
    if (banner) expect(within(article).getByText(banner)).toBeVisible()
    const overview = screen.getByRole('region', { name: '本期概况' })
    expect(within(overview).getByText(side)).toBeVisible()
    if (status !== 'complete') expect(document.body).not.toHaveTextContent('整理完成')
    if (status === 'receiving') expect(article).toHaveTextContent('已经收到的片段')
  })

  it('counts only the loaded items and sources, without a merged-duplicates figure', async () => {
    setup('member', today())
    await openApp('/news')
    await reading()
    const overview = screen.getByRole('region', { name: '本期概况' })
    expect(within(overview).getAllByText('当前已加载').length).toBeGreaterThan(0)
    // 三条里有两条是同一个链接：按链接去重后 2 条、2 个来源
    expect(within(overview).getByText('2 条')).toBeVisible()
    expect(within(overview).getByText('2 个')).toBeVisible()
    expect(document.body).not.toHaveTextContent(/重复报道|已合并/)
    const related = screen.getByRole('region', { name: '相关资讯' })
    expect(within(related).getByText('当前已加载 2 条')).toBeVisible()
    // 按主题筛选：只看财经
    await fireEvent.click(within(related).getByRole('button', { name: /^财经/ }))
    expect(within(related).queryByRole('region', { name: '主题：人工智能' })).toBeNull()
    expect(within(related).getByRole('region', { name: '主题：财经' })).toBeVisible()
  })

  it('keeps the briefing readable when TrendRadar is down and does not show zeros', async () => {
    setup('member', today({ topics: [], unavailable: true }))
    await openApp('/news')
    expect(await reading()).toHaveTextContent('今天只有一段话。')
    const related = screen.getByRole('region', { name: '相关资讯' })
    expect(within(related).getByText(/TrendRadar 暂时连不上/)).toBeVisible()
    const overview = screen.getByRole('region', { name: '本期概况' })
    expect(within(overview).getAllByText('—')).toHaveLength(2)
    expect(overview).not.toHaveTextContent('0 条')
    expect(within(screen.getByRole('region', { name: '你的关注' })).getByText(/暂时读不到关注主题/)).toBeVisible()
  })

  it.each([
    ['owner', '管理兴趣'],
    ['admin', '查看兴趣设置（只读）'],
    ['member', null],
    ['viewer', null],
  ] as const)('shows the topics of the workspace to %s with the right settings entry and never reads settings', async (role, link) => {
    const requests = setup(role, today())
    await openApp('/news')
    const focus = await screen.findByRole('region', { name: '你的关注' })
    const tags = await within(focus).findByRole('list', { name: '关注的主题' })
    expect(within(tags).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['人工智能2', '财经1'])
    if (link) expect(within(focus).getByRole('link', { name: link })).toHaveAttribute('href', '/settings/news')
    else expect(within(focus).queryByRole('link')).toBeNull()
    if (role !== 'owner') expect(within(focus).queryByRole('link', { name: '管理兴趣' })).toBeNull()
    expect(requests.some((r) => path(r) === '/api/v1/news/settings')).toBe(false)
  })

  it('lists recent briefings from the real archive and opens one in 往期', async () => {
    setup('member', today(), (r) => {
      if (path(r) === '/api/v1/news/briefings') return Response.json({ items: [summary(), summary({ id: 'b2', local_date: '2026-11-08', title: '昨天的早报' }), summary({ id: 'b3', local_date: '2026-11-07', slot: '', title: '更早的早报', status: 'failed' })], next_cursor: null })
      if (path(r) === '/api/v1/news/briefings/b2') return Response.json(briefing({ id: 'b2', local_date: '2026-11-08', title: '昨天的早报', content: '昨天的正文' }))
      return undefined
    })
    const { router } = await openApp('/news')
    const archive = await screen.findByRole('region', { name: '往期早报' })
    const list = await within(archive).findByRole('list', { name: '最近的早报' })
    // 今天这份不重复列在往期里
    expect(within(list).getAllByRole('link').map((a) => a.textContent)).toEqual(['11月8日 07:30昨天的早报', '11月7日更早的早报失败'])
    expect(within(archive).getByRole('link', { name: '查看全部往期' })).toHaveAttribute('href', '/news?tab=archive')
    await fireEvent.click(within(list).getByRole('link', { name: /昨天的早报/ }))
    await vi.waitFor(() => expect(router.currentRoute.value.query).toMatchObject({ tab: 'archive', briefing: 'b2' }))
    expect(await reading('昨天的早报')).toHaveTextContent('昨天的正文')
    expect(screen.getByRole('tab', { name: '往期' })).toHaveAttribute('aria-selected', 'true')
  })
})

describe('早报页：刷新、模块与工作空间', () => {
  it('refresh only re-reads: no generate, test push, settings or feedback writes', async () => {
    const requests = setup('owner', today())
    await openApp('/news')
    await reading()
    expect(document.querySelector('main.is-wide')).not.toBeNull()
    const before = requests.filter((r) => path(r) === '/api/v1/news/today').length
    const header = screen.getByRole('heading', { level: 1, name: '早报' }).closest('header')!
    await fireEvent.click(await within(header as HTMLElement).findByRole('button', { name: '刷新' }))
    await vi.waitFor(() => expect(requests.filter((r) => path(r) === '/api/v1/news/today').length).toBe(before + 1))
    await vi.waitFor(() => expect(within(header as HTMLElement).getByRole('button', { name: '刷新' })).toBeEnabled())
    const news = requests.filter((r) => path(r).startsWith('/api/v1/news/'))
    expect(news.every((r) => r.method === 'GET')).toBe(true)
    expect(news.some((r) => /generate|test-push|settings|feedback/.test(path(r)))).toBe(false)
    // 早报设置入口仍在原位置（设置页），这里只是链接过去
    expect(within(header as HTMLElement).getByRole('link', { name: '早报设置' })).toHaveAttribute('href', '/settings/news')
  })

  it('does not request anything from a disabled news module', async () => {
    const requests = mockApi((r) => (path(r) === '/api/v1/modules' ? Response.json({ items: [{ ...newsModule, enabled: false }] }) : undefined))
    await openApp('/news')
    expect(await screen.findByRole('heading', { level: 1, name: '功能未启用' })).toBeVisible()
    expect(screen.queryByRole('article')).toBeNull()
    expect(requests.some((r) => path(r).startsWith('/api/v1/news/'))).toBe(false)
    expect(screen.getByRole('navigation', { name: '主导航' }).querySelector('a[href="/news"]')).toBeNull()
  })

  it('never shows the previous workspace\'s briefing after switching', async () => {
    let finish: ((r: Response) => void) | undefined
    mockApi((r) => {
      if (path(r) === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B 空间', role: 'viewer' }] })
      if (path(r) === '/api/v1/news/today') {
        if (r.headers.get('X-Workspace-Id') === 'workspace-a') return Response.json(today({ briefing: briefing({ title: 'A 空间的早报' }) }))
        return new Promise<Response>((resolve) => { finish = resolve }) as unknown as Response
      }
      return undefined
    })
    await openApp('/news')
    await reading('A 空间的早报')
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    await vi.waitFor(() => expect(finish).toBeDefined())
    expect(screen.queryByRole('article', { name: '早报：A 空间的早报' })).toBeNull()
    expect(document.body).not.toHaveTextContent('A 空间的早报')
    finish!(Response.json(today({ briefing: briefing({ id: 'b9', title: 'B 空间的早报' }), topics: [] })))
    expect(await reading('B 空间的早报')).toBeVisible()
    expect(document.body).not.toHaveTextContent('A 空间的早报')
  })
})
