// 应用中心（T-0211，方案 docs/v2/app-center/ AC-3 ~ AC-5）：模块列表与详情、四类状态、owner / admin 权限、
// 版本冲突与结果未知、重试与检测、自定义页面整份带版本保存（409 保留草稿）、操作记录、接入指南。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { emptyNavigation, fail, me, mockApi, newsModule, openApp, workspace } from './helpers'
import type { Schema } from '../src/services/api'
import { modules } from '../src/modules/registry'
import { buildAppCenterViewModel, buildModuleView, filterModules } from '../src/features/app-center/viewModel'
import { applyPageChange } from '../src/features/navigation/layout'

const path = (r: Request) => new URL(r.url).pathname
type Role = Schema['WorkspaceOut']['role']
type Management = Schema['ModuleManagementOut']
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }, { ...workspace, id: 'workspace-b', name: '团队空间', role }] })
const NEWS_TOOLS = ['news_briefing', 'news_by_date', 'news_latest', 'news_related', 'news_rss', 'news_search', 'news_trending']

function news(over: { module?: Partial<Schema['ModuleOut']>; health?: Partial<Schema['ModuleHealthOut']>; sync?: Partial<Schema['ModuleSyncOut']>; attention?: boolean; role?: Role } = {}): Management {
  const owner = (over.role ?? 'owner') === 'owner'
  return {
    module: { ...newsModule, tools: NEWS_TOOLS, state_version: 2, can_manage: owner, ...over.module },
    host_api: 1,
    capabilities: {
      tools: NEWS_TOOLS.map(name => ({ name, risk: 'read' as const })),
      widgets: ['news_briefing'],
      crons: [{ id: 'news_tick', kind: 'scheduled', schedule_summary: '每分钟' }],
      event_topics: ['news'],
      depends_on: [],
      dependents: [],
      services: [{ id: 'trendradar', name: 'TrendRadar（新闻早报）' }],
      has_sync_hook: true,
    },
    health: { state: 'unknown', checked_at: null, stale: false, checks: [{ id: 'trendradar', name: 'TrendRadar（新闻早报）', kind: 'integration', provider: 'trendradar', status: 'unknown', reason: 'not_checked', checked_at: null }], ...over.health },
    sync: { state: 'succeeded', state_version: 2, attempt_id: '90000000-0000-4000-8000-000000000001', target_enabled: true, trigger: 'toggle', reason: null, started_at: '2026-11-10T00:00:00Z', finished_at: '2026-11-10T00:00:01Z', ...over.sync },
    attention: over.attention ?? false,
    actions: { can_toggle: owner, can_retry_sync: owner && ['failed', 'unknown', 'unrecorded'].includes(over.sync?.state ?? 'succeeded'), can_check_health: (over.module?.enabled ?? true) },
  }
}
const list = (items: Management[]): Schema['ModuleManagementList'] => ({
  items,
  summary: { registered: items.length, enabled: items.filter(i => i.module.enabled).length, attention: items.filter(i => i.attention).length },
  generated_at: '2026-11-10T00:00:00Z',
})
const navPage = (over: Partial<Schema['NavPage']> & Pick<Schema['NavPage'], 'id' | 'title' | 'kind'>): Schema['NavPage'] => ({ section: 'apps', min_role: 'viewer', url: null, path: null, target_id: null, ...over })
const layout: Schema['NavigationOut'] = {
  ...emptyNavigation, version: 4,
  sections: [{ id: 'fav', title: '常用' }],
  section_order: ['custom-fav', 'workspace'],
  placements: { '/tasks': 'custom-fav' },
  pages: [
    navPage({ id: 'ops', title: '运维看板', kind: 'embed', section: 'tools', url: 'https://grafana.example.com/d/1' }),
    navPage({ id: 'docs', title: '团队文档', kind: 'note', section: 'custom-fav', target_id: '70000000-0000-4000-8000-000000000001' }),
    navPage({ id: 'tools', title: '常用工具', kind: 'link', url: 'https://tools.example.com' }),
    navPage({ id: 'owner-only', title: '只给 owner', kind: 'link', url: 'https://owner.example.com', min_role: 'owner' }),
  ],
  embed_origins: ['https://grafana.example.com'],
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function setup(role: Role, items: () => Management[], handler?: (r: Request) => Response | Promise<Response | undefined> | undefined, nav: () => Schema['NavigationOut'] = () => layout) {
  return mockApi(async r => {
    const result = await handler?.(r)
    if (result) return result
    if (path(r) === '/api/v1/me' && r.method === 'GET') return as(role)
    if (path(r) === '/api/v1/modules' && r.method === 'GET') return Response.json({ items: items().map(i => i.module) })
    if (path(r) === '/api/v1/modules/management') return Response.json(list(items()))
    if (/^\/api\/v1\/modules\/[^/]+\/activity$/.test(path(r)) || path(r) === '/api/v1/modules/activity') return Response.json({ items: [], next_cursor: null, total_estimate: null })
    if (path(r) === '/api/v1/navigation' && r.method === 'GET') {
      const value = nav()
      return Response.json({ ...value, can_manage: role === 'owner', pages: (value.pages ?? []).filter(p => role === 'owner' || p.min_role !== 'owner') })
    }
    return undefined
  })
}

describe('view model', () => {
  it('takes the entries from the module definition and never guesses paths', () => {
    const vm = buildAppCenterViewModel([news(), news({ module: { key: 'ghost', name: '幽灵' } })], modules)
    const [first, ghost] = vm.modules
    expect(first!.primary).toEqual({ path: '/news', title: '早报' })
    expect(first!.settings).toEqual({ path: '/settings/news', title: '新闻早报' })
    expect(ghost!.frontendMissing).toBe(true)
    expect(ghost!.primary).toBeNull()
    expect(ghost!.settings).toBeNull()
    // 声明的路径不是本模块的路由：不显示
    const fake = { key: 'news', management: { primaryPath: '/elsewhere' }, routes: [] }
    expect(buildModuleView(news(), fake).primary).toBeNull()
    expect(buildModuleView(news(), { key: 'news' }).settings).toBeNull()
  })

  it('counts declarations, keeps the four states apart and filters without touching the totals', () => {
    const view = buildModuleView(news({ module: { ready: false, missing: ['TRENDRADAR_MCP_URL'] }, sync: { state: 'failed', reason: 'config_render_failed' }, attention: true }), modules[0])
    expect(view.counts).toEqual({ pages: 2, widgets: 1, tools: 7, readOnlyTools: true, crons: 1 })
    expect([view.enabled.text, view.deploy.text, view.health.text, view.sync.text]).toEqual(['已启用', '待完成', '未检测', '同步失败'])
    expect(view.notice).toBe('开关已保存，外部配置同步失败：外部配置文件没有写成。')
    const off = buildModuleView(news({ module: { enabled: false } }), modules[0])
    expect(filterModules([view, off], '', 'enabled')).toEqual([view])
    expect(filterModules([view, off], '', 'attention')).toEqual([view])
    expect(filterModules([view, off], 'news_rss', 'all')).toHaveLength(2)
    expect(filterModules([view, off], '没有这个', 'all')).toHaveLength(0)
  })

  it('changes only one page and keeps the rest of the layout as read', () => {
    const body = applyPageChange(layout, { kind: 'upsert', page: { ...layout.pages![1]!, title: '团队文档 2' } })
    expect(body).toEqual({ version: 4, sections: layout.sections, placements: layout.placements, section_order: layout.section_order, pages: layout.pages!.map(p => (p.id === 'docs' ? { ...p, title: '团队文档 2' } : p)) })
    const removed = applyPageChange(layout, { kind: 'remove', id: 'tools' })
    expect(removed.pages!.map(p => p.id)).toEqual(['ops', 'docs', 'owner-only'])
  })
})

describe('应用中心', () => {
  it('shows the summary, the module card and its declared capabilities without probing services', async () => {
    const requests = setup('owner', () => [news({ module: { ready: false, missing: ['TRENDRADAR_MCP_URL'] }, health: { state: 'not_configured', checks: [] }, attention: true })])
    await openApp('/settings/modules')
    expect(await screen.findByRole('heading', { level: 1, name: '应用中心' })).toBeVisible()
    const tiles = screen.getByRole('region', { name: '应用统计' })
    await vi.waitFor(() => expect(within(tiles).getByText('已接入模块').closest('div')).toHaveTextContent('1'))
    expect(within(tiles).getByText('需处理').closest('div')).toHaveTextContent('1')
    // owner 能看到全部 4 个入口（含“只给 owner”）
    await vi.waitFor(() => expect(within(tiles).getByText('自定义页面').closest('div')).toHaveTextContent('4'))
    const card = await screen.findByRole('article', { name: '模块：新闻早报' })
    expect(within(card).getByRole('switch', { name: '启用新闻早报' })).toHaveAttribute('aria-checked', 'true')
    expect(within(card).getByText('待部署')).toBeVisible()
    for (const text of ['2 个页面', '1 个首页组件', '7 个只读工具', '1 个定时任务']) expect(within(card).getByText(text)).toBeVisible()
    expect(within(card).getByText('已启用此应用，完成部署配置后即可使用完整功能。')).toBeVisible()
    expect(within(card).getByRole('link', { name: '打开应用' })).toHaveAttribute('href', '/news')
    expect(within(card).getByRole('link', { name: '应用设置' })).toHaveAttribute('href', '/settings/news')
    expect(requests.some(r => r.method === 'POST')).toBe(false)
    // 页面入口预览：当前账号能看到的前 3 项
    const preview = screen.getByRole('region', { name: '页面入口' })
    expect(within(preview).getAllByRole('listitem').map(li => li.getAttribute('aria-label'))).toEqual(['入口：运维看板', '入口：团队文档', '入口：常用工具'])
  })

  it('opens the details from the address, keeps the four states apart and retries a failed sync', async () => {
    let current = news({ sync: { state: 'failed', reason: 'config_render_failed' }, attention: true })
    const requests = setup('owner', () => [current], async r => {
      if (path(r) === '/api/v1/modules/news/reconcile') {
        current = news({ sync: { state: 'succeeded', trigger: 'retry' } })
        return Response.json(current)
      }
      return undefined
    })
    await openApp('/settings/modules?module=news')
    const detail = await screen.findByRole('region', { name: '应用详情：新闻早报' })
    expect(within(detail).getByText('开关已保存，外部配置同步失败')).toBeVisible()
    expect(within(detail).getByText('工作空间开关').parentElement).toHaveTextContent('已启用')
    expect(within(detail).getByText('部署配置').parentElement).toHaveTextContent('已完成')
    expect(within(detail).getByText('服务连通性').parentElement).toHaveTextContent('未检测')
    expect(within(detail).getByText('外部配置同步').parentElement).toHaveTextContent('同步失败')
    await fireEvent.click(within(detail).getByRole('button', { name: '重试同步' }))
    expect(await screen.findByText('「新闻早报」的外部配置已重新同步。')).toBeVisible()
    const retry = requests.find(r => path(r) === '/api/v1/modules/news/reconcile')!
    expect(await retry.clone().json()).toEqual({ expected_state_version: 2 })
    expect(requests.some(r => r.method === 'PATCH')).toBe(false)
  })

  it('lets the owner disable after confirming, with the version it read', async () => {
    let current = news()
    const requests = setup('owner', () => [current], async r => {
      if (r.method === 'PATCH' && path(r) === '/api/v1/modules/news') {
        current = news({ module: { enabled: false, state_version: 3 }, sync: { state_version: 3, target_enabled: false } })
        return Response.json(current.module)
      }
      return undefined
    })
    await openApp('/settings/modules')
    await fireEvent.click(await screen.findByRole('switch', { name: '启用新闻早报' }))
    const dialog = await screen.findByRole('dialog', { name: '停用应用' })
    expect(dialog).toHaveTextContent('数据与配置保留，再启用就恢复。')
    expect(requests.some(r => r.method === 'PATCH')).toBe(false)
    await fireEvent.click(within(dialog).getByRole('button', { name: '停用应用' }))
    expect(await screen.findByText('「新闻早报」已停用。')).toBeVisible()
    const patch = requests.find(r => r.method === 'PATCH')!
    expect(await patch.clone().json()).toEqual({ enabled: false, expected_state_version: 2 })
    await vi.waitFor(() => expect(screen.getByRole('switch', { name: '启用新闻早报' })).toHaveAttribute('aria-checked', 'false'))
    expect(screen.getByRole('navigation', { name: '主导航' }).querySelector('a[href="/news"]')).toBeNull()
  })

  it('rereads the real state after a version conflict or an unknown result and does not send again', async () => {
    let calls = 0
    const requests = setup('owner', () => [news({ module: { enabled: false } })], r => {
      if (r.method === 'PATCH') {
        calls++
        if (calls === 1) return Response.json({ error: { code: 'VERSION_CONFLICT', message: '模块状态已被其他操作修改，请刷新后重试', request_id: 'req', details: { current_state_version: 5 } } }, { status: 409 })
        throw new TypeError('network down')
      }
      return undefined
    })
    await openApp('/settings/modules')
    const before = () => requests.filter(r => path(r) === '/api/v1/modules/management').length
    const start = before()
    await fireEvent.click(await screen.findByRole('switch', { name: '启用新闻早报' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('模块状态已被其他操作修改，已刷新为最新状态')
    await vi.waitFor(() => expect(before()).toBeGreaterThan(start))
    const middle = before()
    await fireEvent.click(screen.getByRole('switch', { name: '启用新闻早报' }))
    expect(await screen.findByText(/结果未知/)).toBeVisible()
    await vi.waitFor(() => expect(before()).toBeGreaterThan(middle))
    expect(requests.filter(r => r.method === 'PATCH')).toHaveLength(2)
  })

  it('is read-only for admins (who can still check the connection) and closed to members', async () => {
    const requests = setup('admin', () => [news({ role: 'admin' })], r => (path(r) === '/api/v1/modules/news/health-check' ? Response.json({ state: 'down', checked_at: '2026-11-10T00:00:00Z', stale: false, checks: [] }) : undefined))
    const first = await openApp('/settings/modules?module=news')
    const card = await screen.findByRole('article', { name: '模块：新闻早报' })
    expect(within(card).queryByRole('switch')).not.toBeInTheDocument()
    expect(within(card).getByText('已启用')).toBeVisible()
    expect(screen.queryByRole('button', { name: '添加入口' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /编辑入口/ })).not.toBeInTheDocument()
    const detail = await screen.findByRole('region', { name: '应用详情：新闻早报' })
    expect(within(detail).queryByRole('button', { name: '停用应用' })).not.toBeInTheDocument()
    expect(within(detail).getByText('你可以查看；只有 owner 能启用、停用或重试同步。')).toBeVisible()
    await fireEvent.click(within(detail).getByRole('button', { name: '检测连接' }))
    expect(await screen.findByText('「新闻早报」检测完成：连接失败。')).toBeVisible()
    expect(requests.some(r => r.method === 'PATCH' || path(r).endsWith('/reconcile'))).toBe(false)
    first.unmount()

    setup('member', () => [news({ role: 'member' })])
    const { router } = await openApp('/settings/modules')
    expect(router.currentRoute.value.path).toBe('/forbidden')
  })

  it('searches the full list while the summary stays the same, and tells failures apart', async () => {
    setup('owner', () => [news(), news({ module: { key: 'finance', name: '记账', description: '记录收支。', enabled: false, tools: [] } })])
    await openApp('/settings/modules')
    expect(await screen.findByRole('article', { name: '模块：记账' })).toBeVisible()
    expect(screen.getByRole('article', { name: '模块：记账' })).toHaveTextContent('前后端版本不一致')
    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索应用名称或功能' }), '收支')
    await vi.waitFor(() => expect(screen.queryByRole('article', { name: '模块：新闻早报' })).not.toBeInTheDocument())
    expect(within(screen.getByRole('region', { name: '应用统计' })).getByText('已接入模块').closest('div')).toHaveTextContent('2')
    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索应用名称或功能' }), '没有这个')
    expect(await screen.findByText('没有符合条件的模块。')).toBeVisible()
  })

  it('shows an error state when the module list cannot be read', async () => {
    setup('owner', () => [], r => (path(r) === '/api/v1/modules/management' ? fail(503, 'HTTP_ERROR') : undefined))
    await openApp('/settings/modules')
    expect(await screen.findByText(/模块读取失败/)).toBeVisible()
    expect(screen.getByRole('button', { name: '重试' })).toBeVisible()
  })

  it('loads only the selected module activity in the details and all modules in the activity tab', async () => {
    const requests = setup('owner', () => [news()])
    await openApp('/settings/modules?module=news')
    const detail = await screen.findByRole('region', { name: '应用详情：新闻早报' })
    await fireEvent.click(within(detail).getByRole('tab', { name: '操作记录' }))
    expect(await within(detail).findByText('还没有操作记录。')).toBeVisible()
    expect(requests.some(r => path(r) === '/api/v1/modules/news/activity')).toBe(true)
    expect(requests.some(r => path(r) === '/api/v1/modules/activity')).toBe(false)
    await fireEvent.click(within(screen.getByRole('tablist', { name: '应用中心' })).getByRole('tab', { name: '操作记录' }))
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/modules/activity')).toBe(true))
  })

  it('explains the three ways to connect without offering install, upgrade or data wiping', async () => {
    setup('owner', () => [news({ module: { ready: false, missing: ['TRENDRADAR_MCP_URL'] } })])
    await openApp('/settings/modules?tab=guide')
    const guide = await screen.findByRole('region', { name: '应用接入指南' })
    for (const title of ['功能模块', '零代码入口', '外部应用 · 待支持', '部署外部服务']) expect(within(guide).getByRole('heading', { name: new RegExp(title) })).toBeVisible()
    expect(await within(guide).findByText('TRENDRADAR_MCP_URL')).toBeVisible()
    expect(screen.queryByRole('button', { name: /安装|升级|清除|卸载|上传/ })).not.toBeInTheDocument()
  })
})

describe('自定义页面入口', () => {
  it('saves one edited entry with the whole layout and the version it read', async () => {
    let current = layout
    const requests = setup('owner', () => [news()], async r => {
      if (path(r) === '/api/v1/navigation' && r.method === 'PUT') {
        const body = await r.clone().json() as Schema['NavigationIn']
        current = { ...layout, ...body, version: 5, can_manage: true } as Schema['NavigationOut']
        return Response.json(current)
      }
      return undefined
    }, () => current)
    await openApp('/settings/modules?tab=pages')
    const panel = await screen.findByRole('region', { name: '自定义页面' })
    expect(within(panel).getAllByRole('listitem')).toHaveLength(4)
    await fireEvent.click(within(panel).getByRole('button', { name: '编辑入口：常用工具' }))
    const dialog = await screen.findByRole('dialog', { name: '编辑入口' })
    await fireEvent.update(within(dialog).getByLabelText('名称'), '常用工具箱')
    await fireEvent.click(await within(dialog).findByRole('button', { name: '保存入口' }))
    expect(await screen.findByText('入口已保存。')).toBeVisible()
    const put = requests.find(r => r.method === 'PUT')!
    const body = await put.clone().json() as Schema['NavigationIn']
    expect(body).toEqual(applyPageChange(layout, { kind: 'upsert', page: { ...layout.pages![2]!, title: '常用工具箱' } }))
    expect(body.pages!.map(p => p.id)).toEqual(['ops', 'docs', 'tools', 'owner-only'])
  })

  it('keeps the draft on 409 and merges into the latest layout only after confirming', async () => {
    let saves = 0
    const latest: Schema['NavigationOut'] = { ...layout, version: 9, pages: [...layout.pages!, navPage({ id: 'new-elsewhere', title: '别人加的', kind: 'link', url: 'https://x.example.com' })] }
    let current = layout
    const requests = setup('owner', () => [news()], async r => {
      if (path(r) === '/api/v1/navigation' && r.method === 'PUT') {
        saves++
        if (saves === 1) { current = latest; return Response.json({ error: { code: 'VERSION_CONFLICT', message: '侧栏布局已被修改', request_id: 'req', details: {} } }, { status: 409 }) }
        return Response.json({ ...latest, version: 10 })
      }
      return undefined
    }, () => current)
    await openApp('/settings/modules?tab=pages')
    const panel = await screen.findByRole('region', { name: '自定义页面' })
    await fireEvent.click(within(panel).getByRole('button', { name: '添加入口' }))
    const form = await screen.findByRole('dialog', { name: '添加入口' })
    await fireEvent.update(within(form).getByLabelText('名称'), '新入口')
    await fireEvent.update(within(form).getByLabelText('链接地址'), 'https://new.example.com')
    await fireEvent.click(await within(form).findByRole('button', { name: '保存入口' }))
    const conflict = await screen.findByRole('dialog', { name: '侧栏布局已被别人修改' })
    expect(conflict).toHaveTextContent('你的修改还在，没有提交')
    expect(saves).toBe(1)
    await fireEvent.click(within(conflict).getByRole('button', { name: '合并后保存' }))
    expect(await screen.findByText('入口已保存。')).toBeVisible()
    const second = await requests.filter(r => r.method === 'PUT')[1]!.clone().json() as Schema['NavigationIn']
    expect(second.version).toBe(9)
    expect(second.pages!.map(p => p.title)).toEqual(['运维看板', '团队文档', '常用工具', '只给 owner', '别人加的', '新入口'])
  })

  it('removes an entry from the navigation only', async () => {
    const requests = setup('owner', () => [news()], async r => (path(r) === '/api/v1/navigation' && r.method === 'PUT' ? Response.json({ ...layout, version: 5, pages: layout.pages!.filter(p => p.id !== 'docs') }) : undefined))
    await openApp('/settings/modules?tab=pages')
    const panel = await screen.findByRole('region', { name: '自定义页面' })
    await fireEvent.click(within(panel).getByRole('button', { name: '移除入口：团队文档' }))
    const dialog = await screen.findByRole('dialog', { name: '移除入口' })
    expect(dialog).toHaveTextContent('不会删除它指向的笔记、仪表盘或外部网站')
    await fireEvent.click(within(dialog).getByRole('button', { name: '移除入口' }))
    expect(await screen.findByText('入口已从侧栏移除（指向的内容没有删除）。')).toBeVisible()
    const body = await requests.find(r => r.method === 'PUT')!.clone().json() as Schema['NavigationIn']
    expect(body.pages!.map(p => p.id)).toEqual(['ops', 'tools', 'owner-only'])
    expect(requests.some(r => r.method === 'DELETE')).toBe(false)
  })
})
