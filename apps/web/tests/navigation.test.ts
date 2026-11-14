// 侧栏自定义分组与零代码页面（T-0161）：侧栏按布局分组排序、零代码页面五种、/p/:id 页面、设置 → 侧栏与页面。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { emptyNavigation, fail, me, mockApi, openApp, workspace } from './helpers'
import { widget } from './dashboard-fixtures'
import type { Schema } from '../src/services/api'

const path = (r: Request) => new URL(r.url).pathname
type Role = Schema['WorkspaceOut']['role']
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })
const NOTE = '70000000-0000-4000-8000-000000000001', BOARD = '70000000-0000-4000-8000-000000000002'
const at = '2026-11-08T00:00:00Z'
const page = (over: Partial<Schema['NavPage']> & Pick<Schema['NavPage'], 'id' | 'title' | 'kind'>): Schema['NavPage'] => ({ section: 'apps', min_role: 'viewer', url: null, path: null, target_id: null, ...over })
const layout: Schema['NavigationOut'] = {
  ...emptyNavigation, version: 3, embed_origins: ['https://grafana.example.com'],
  sections: [{ id: 'fav', title: '常用' }, { id: 'empty', title: '空分组' }],
  section_order: ['custom-fav', 'workspace'],
  placements: { '/tasks': 'custom-fav', '/calendar': 'custom-gone' },
  pages: [
    page({ id: 'grafana', title: 'Grafana', kind: 'link', section: 'custom-fav', url: 'https://grafana.example.com' }),
    page({ id: 'proj', title: '项目 A', kind: 'page', section: 'custom-fav', path: '/projects/p1' }),
    page({ id: 'runbook', title: '值班手册', kind: 'note', section: 'workspace', target_id: NOTE }),
    page({ id: 'board', title: '运营看板', kind: 'dashboard', section: 'tools', target_id: BOARD }),
    page({ id: 'screen', title: '监控大屏', kind: 'embed', section: 'tools', url: 'https://grafana.example.com/d/abc' }),
    page({ id: 'secret', title: '只给 owner', kind: 'link', section: 'custom-fav', url: 'https://owner.example.com', min_role: 'owner' }),
  ],
}
const noteSummary: Schema['NoteSummary'] = { id: NOTE, space_id: 'space-public', space_kind: 'public', space_name: '公共知识库', title: '值班手册', type: 'runbook', folder: '', tags: [], pinned: false, conflict: false, source_kind: 'human', updated_at: at, updated_by: { kind: 'user', id: 'user-a', name: '测试用户' }, version: 1 }
const dashboards = { items: [
  { id: '22222222-2222-4222-8222-222222222222', title: '首页', owner_id: 'user-a', version: 1, is_default: true, created_at: at, updated_at: at },
  { id: BOARD, title: '运营', owner_id: 'user-a', version: 1, is_default: false, created_at: at, updated_at: at },
], next_cursor: null, total_estimate: 2 }

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function setup(role: Role, value: Schema['NavigationOut'] | (() => Response) = layout, handler?: (r: Request) => Response | Promise<Response | undefined> | undefined) {
  return mockApi(async r => {
    const result = await handler?.(r)
    if (result) return result
    if (path(r) === '/api/v1/me' && r.method === 'GET') return as(role)
    if (path(r) === '/api/v1/navigation' && r.method === 'GET') return typeof value === 'function' ? value() : Response.json({ ...value, can_manage: role === 'owner', pages: (value.pages ?? []).filter(p => role === 'owner' || p.min_role !== 'owner') })
    if (path(r) === '/api/v1/dashboards') return Response.json(dashboards)
    if (path(r) === `/api/v1/dashboards/${BOARD}/widgets`) return Response.json({ items: [{ ...widget, dashboard_id: BOARD, config: { text: '运营便签' } }], next_cursor: null, total_estimate: 1 })
    if (path(r) === '/api/v1/knowledge/notes') {
      const q = new URL(r.url).searchParams.get('q')
      return Response.json({ items: !q || '值班手册'.includes(q) ? [noteSummary] : [], next_cursor: null })
    }
    return undefined
  })
}
function sectionOf(nav: HTMLElement, name: string) {
  const button = within(nav).getByRole('button', { name })
  return document.getElementById(button.getAttribute('aria-controls')!)!
}
const groupNames = (nav: HTMLElement) => within(nav).getAllByRole('button').map(b => b.textContent?.trim())

describe('sidebar layout', () => {
  it('orders groups, moves pages and shows every kind of custom page', async () => {
    setup('member')
    await openApp('/')
    const nav = screen.getByRole('navigation', { name: '主导航' })
    await vi.waitFor(() => expect(groupNames(nav)[0]).toBe('常用'))
    // 列出的分组在前；没列出的按默认顺序；没有页面的分组不显示
    expect(groupNames(nav)).toEqual(['常用', '工作空间', '连接与工具', '管理'])
    const fav = sectionOf(nav, '常用')
    expect(within(fav).getAllByRole('link').map(a => a.textContent?.trim())).toEqual(['任务', 'Grafana（在新窗口打开）', '项目 A'])
    const external = within(fav).getByRole('link', { name: /Grafana/ })
    expect(external).toHaveAttribute('href', 'https://grafana.example.com')
    expect(external).toHaveAttribute('target', '_blank')
    expect(external).toHaveAttribute('rel', 'noopener noreferrer')
    expect(within(fav).getByRole('link', { name: '项目 A' })).toHaveAttribute('href', '/projects/p1')
    // 只给 owner 的页面成员看不到；挪到不存在的分组的页面留在原来的分组
    expect(within(nav).queryByRole('link', { name: /只给 owner/ })).not.toBeInTheDocument()
    const home = sectionOf(nav, '工作空间')
    expect(within(home).queryByRole('link', { name: '任务' })).not.toBeInTheDocument()
    expect(within(home).getByRole('link', { name: '日历' })).toHaveAttribute('href', '/calendar')
    expect(within(home).getByRole('link', { name: '值班手册' })).toHaveAttribute('href', `/knowledge/notes/${NOTE}`)
    const tools = sectionOf(nav, '连接与工具')
    expect(within(tools).getByRole('link', { name: '运营看板' })).toHaveAttribute('href', '/p/board')
    expect(within(tools).getByRole('link', { name: '监控大屏' })).toHaveAttribute('href', '/p/screen')
  })

  it('adds the new agent servers page to its default group without losing a saved layout (T-0181)', async () => {
    // 保存布局时还没有“智能体服务器”：它按默认分组补进来；用户挪过的“OpenClaw 智能体”（原 OpenClaw 实例）仍在用户的分组里
    setup('owner', { ...layout, placements: { ...layout.placements, '/agents': 'custom-fav' } })
    await openApp('/')
    const nav = screen.getByRole('navigation', { name: '主导航' })
    await vi.waitFor(() => expect(groupNames(nav)[0]).toBe('常用'))
    const fav = sectionOf(nav, '常用')
    expect(within(fav).getByRole('link', { name: 'OpenClaw 智能体' })).toHaveAttribute('href', '/agents')
    expect(within(fav).getByRole('link', { name: /Grafana/ })).toBeInTheDocument()
    const tools = sectionOf(nav, '连接与工具')
    expect(within(tools).getByRole('link', { name: '智能体服务器' })).toHaveAttribute('href', '/agent-servers')
    expect(within(tools).getByRole('link', { name: '容器服务器' })).toHaveAttribute('href', '/runner')
    expect(within(tools).getByRole('link', { name: '运营看板' })).toHaveAttribute('href', '/p/board')
    expect(within(nav).queryByRole('link', { name: 'OpenClaw 实例' })).not.toBeInTheDocument()
  })

  it('falls back to the default sidebar when the layout cannot be read, and refetches on the navigation event', async () => {
    class Stream extends EventTarget {
      static last: Stream | null = null
      readyState = 1
      constructor(public url: string) { super(); Stream.last = this }
      close() { this.readyState = 2 }
    }
    vi.stubGlobal('EventSource', Stream)
    let broken = true
    setup('owner', () => (broken ? fail(503, 'HTTP_ERROR') : Response.json({ ...layout, can_manage: true })))
    await openApp('/')
    const nav = screen.getByRole('navigation', { name: '主导航' })
    expect(sectionOf(nav, '工作空间').querySelector('a[href="/tasks"]')).toHaveTextContent('任务')
    // 默认侧栏：owner 能看到“应用”分组里的大模型客户端（模块 llm_bridge，minRole admin）
    expect(groupNames(nav)).toEqual(['工作空间', '应用', '连接与工具', '管理'])
    broken = false
    Stream.last!.dispatchEvent(new MessageEvent('invalidate', { data: JSON.stringify({ workspace_id: 'workspace-a', topics: ['navigation'], at }) }))
    await vi.waitFor(() => expect(groupNames(nav)[0]).toBe('常用'))
    expect(within(sectionOf(nav, '常用')).getByRole('link', { name: /只给 owner/ })).toBeInTheDocument()
  })

  it('lists in-app custom pages in search', async () => {
    setup('member')
    await openApp('/')
    await vi.waitFor(() => expect(groupNames(screen.getByRole('navigation', { name: '主导航' }))[0]).toBe('常用'))
    await fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    await fireEvent.update(screen.getByLabelText('搜索页面与组件'), '看板')
    const shortcuts = await screen.findByRole('region', { name: '页面与组件快捷入口' })
    expect(within(shortcuts).getByRole('button', { name: '运营看板' })).toBeVisible()
  })
})

describe('custom pages (/p/:id)', () => {
  it('shows an embed page in a sandboxed iframe with a link to open it in a new window', async () => {
    setup('viewer')
    await openApp('/p/screen')
    expect(await screen.findByRole('heading', { level: 1, name: '监控大屏' })).toBeVisible()
    const frame = document.querySelector('iframe')!
    expect(frame).toHaveAttribute('src', 'https://grafana.example.com/d/abc')
    expect(frame).toHaveAttribute('title', '监控大屏')
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups')
    expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer')
    const open = screen.getByRole('link', { name: '在新窗口打开' })
    expect(open).toHaveAttribute('href', 'https://grafana.example.com/d/abc')
    expect(open).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    await vi.waitFor(() => expect(document.title).toBe('监控大屏 · My Workspace'))
  })

  it('shows the widgets of a dashboard page read-only', async () => {
    setup('member')
    await openApp('/p/board')
    expect(await screen.findByRole('heading', { level: 1, name: '运营看板' })).toBeVisible()
    expect(await screen.findByText('运营便签')).toBeVisible()
    expect(screen.getByText(/只读显示仪表盘「运营」/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '编辑布局' })).not.toBeInTheDocument()
  })

  it('explains unknown pages and pages above the role', async () => {
    setup('member')
    const first = await openApp('/p/nothing')
    expect(await screen.findByRole('heading', { level: 1, name: '页面不存在或你没有权限' })).toBeVisible()
    first.unmount()
    setup('member', { ...layout, pages: [page({ id: 'boss', title: '老板看板', kind: 'dashboard', target_id: BOARD, min_role: 'admin' })] })
    await openApp('/p/boss')
    expect(await screen.findByRole('heading', { level: 1, name: '页面不存在或你没有权限' })).toBeVisible()
  })
})

describe('设置 → 侧栏与页面', () => {
  it('lets the owner add a group, move a page, add a link and save the whole layout', async () => {
    let saved: Schema['NavigationOut'] = { ...emptyNavigation }
    const requests = setup('owner', () => Response.json(saved), async r => {
      if (path(r) !== '/api/v1/navigation' || r.method !== 'PUT') return undefined
      const body = await r.clone().json() as Schema['NavigationIn']
      saved = { ...emptyNavigation, ...body, version: body.version + 1 }
      return Response.json(saved)
    })
    await openApp('/settings/navigation')
    expect(await screen.findByRole('heading', { level: 1, name: '侧栏与页面' })).toBeVisible()
    const groups = screen.getByRole('region', { name: '分组' })
    await fireEvent.update(within(groups).getByLabelText('新分组名称'), ' 常用 ')
    await fireEvent.click(within(groups).getByRole('button', { name: '添加分组' }))
    const fav = within(groups).getByRole('listitem', { name: '分组：常用' })
    for (let i = 0; i < 3; i++) await fireEvent.click(within(fav).getByRole('button', { name: '上移' }))
    expect(within(groups).getAllByRole('listitem').map(li => li.getAttribute('aria-label'))).toEqual(['分组：常用', '分组：工作空间', '分组：应用', '分组：连接与工具', '分组：管理'])

    const places = screen.getByRole('region', { name: '页面位置' })
    // 新的独立页面也能挪到别的分组（T-0181）
    expect(within(places).getByLabelText('智能体服务器所在分组')).toBeInTheDocument()
    expect(within(places).getByLabelText('OpenClaw 智能体所在分组')).toBeInTheDocument()
    const select = within(places).getByLabelText('任务所在分组') as HTMLSelectElement
    const key = (within(select).getByRole('option', { name: '常用' }) as HTMLOptionElement).value
    expect(key).toMatch(/^custom-[a-z0-9][a-z0-9-]{0,39}$/)
    await fireEvent.update(select, key)

    await fireEvent.click(screen.getByRole('button', { name: '添加页面' }))
    const dialog = screen.getByRole('dialog', { name: '添加页面' })
    await fireEvent.update(within(dialog).getByLabelText('名称'), 'Grafana')
    await fireEvent.update(within(dialog).getByLabelText('链接地址'), 'javascript:alert(1)')
    await fireEvent.click(within(dialog).getByRole('button', { name: '加入页面' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('外部链接要以 http:// 或 https:// 开头')
    await fireEvent.update(within(dialog).getByLabelText('链接地址'), 'https://grafana.example.com')
    await fireEvent.update(within(dialog).getByLabelText('分组'), key)
    await fireEvent.click(within(dialog).getByRole('button', { name: '加入页面' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: '页面：Grafana' })).toHaveTextContent('外部链接')
    expect(screen.getByText('有未保存的修改')).toBeVisible()

    await fireEvent.click(screen.getByRole('button', { name: '保存侧栏布局' }))
    expect(await screen.findByText('侧栏布局已保存。')).toBeVisible()
    const put = requests.find(r => r.method === 'PUT')!
    expect(put.headers.get('X-Workspace-Id')).toBe('workspace-a')
    const body = await put.clone().json() as Schema['NavigationIn']
    const id = key.slice('custom-'.length)
    expect(body).toEqual({
      version: 0, sections: [{ id, title: '常用' }], placements: { '/tasks': key }, section_order: [key, 'workspace', 'apps', 'tools', 'admin'],
      pages: [{ id: body.pages![0]!.id, title: 'Grafana', section: key, kind: 'link', min_role: 'viewer', url: 'https://grafana.example.com', path: null, target_id: null }],
    })
    expect(body.pages![0]!.id).toMatch(/^[a-z0-9][a-z0-9-]{0,39}$/)
    // 保存后侧栏立刻按新布局显示
    const nav = screen.getByRole('navigation', { name: '主导航' })
    await vi.waitFor(() => expect(groupNames(nav)[0]).toBe('常用'))
    expect(within(sectionOf(nav, '常用')).getAllByRole('link').map(a => a.textContent?.trim())).toEqual(['任务', 'Grafana（在新窗口打开）'])
    expect(screen.queryByText('有未保存的修改')).not.toBeInTheDocument()
  })

  it('reloads after a version conflict and shows field errors from the server', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let current: Schema['NavigationOut'] = { ...layout, version: 3 }
    let reply: () => Response = () => fail(409, 'VERSION_CONFLICT')
    const requests = setup('owner', () => Response.json({ ...current, can_manage: true }), r => (path(r) === '/api/v1/navigation' && r.method === 'PUT' ? reply() : undefined))
    await openApp('/settings/navigation')
    const pages = await screen.findByRole('region', { name: '自定义页面' })
    await fireEvent.click(within(within(pages).getByRole('listitem', { name: '页面：值班手册' })).getByRole('button', { name: '删除' }))
    expect(within(pages).queryByRole('listitem', { name: '页面：值班手册' })).not.toBeInTheDocument()
    current = { ...layout, version: 4, sections: [...layout.sections!, { id: 'other', title: '别人的分组' }] }
    await fireEvent.click(screen.getByRole('button', { name: '保存侧栏布局' }))
    expect(await screen.findByText(/侧栏布局已被别人修改，已重新加载/)).toBeVisible()
    expect(screen.getByRole('listitem', { name: '分组：别人的分组' })).toBeVisible()
    expect(within(pages).getByRole('listitem', { name: '页面：值班手册' })).toBeVisible()

    reply = () => Response.json({ error: { code: 'VALIDATION_FAILED', message: '请求参数校验失败', request_id: 'req', details: { errors: [{ loc: ['body', 'pages', 4, 'url'], msg: '这个来源不允许嵌入：请在部署设置 EMBED_ORIGINS 里加上它（见部署指南）', type: 'value_error' }] } } }, { status: 422 })
    await fireEvent.update(within(screen.getByRole('listitem', { name: '分组：别人的分组' })).getByLabelText('分组名称'), '别人的')
    await fireEvent.click(screen.getByRole('button', { name: '保存侧栏布局' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('页面「监控大屏」 · 地址：这个来源不允许嵌入')
    const puts = requests.filter(r => r.method === 'PUT')
    expect((await puts[1]!.clone().json() as Schema['NavigationIn']).version).toBe(4)
  })

  it('moves the pages of a deleted group to 应用', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup('owner', layout, r => (r.method === 'PUT' ? Response.json({ ...layout, version: 4 }) : undefined))
    await openApp('/settings/navigation')
    const groups = await screen.findByRole('region', { name: '分组' })
    await fireEvent.click(within(within(groups).getByRole('listitem', { name: '分组：常用' })).getByRole('button', { name: '删除' }))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('里面的 3 个自定义页面会移到“应用”，挪进来的 1 个页面回到原来的分组'))
    await fireEvent.click(screen.getByRole('button', { name: '保存侧栏布局' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'PUT')).toBe(true))
    const body = await requests.find(r => r.method === 'PUT')!.clone().json() as Schema['NavigationIn']
    expect(body.sections).toEqual([{ id: 'empty', title: '空分组' }])
    // 挪到常用的“任务”回到默认分组；指向不存在分组的挪动不再提交
    expect(body.placements).toEqual({})
    expect(body.pages!.filter(p => ['grafana', 'proj', 'secret'].includes(p.id)).map(p => p.section)).toEqual(['apps', 'apps', 'apps'])
    expect(body.section_order).not.toContain('custom-fav')
  })

  it('picks a note and a dashboard, and explains embed origins', async () => {
    const requests = setup('owner', { ...emptyNavigation })
    await openApp('/settings/navigation')
    await fireEvent.click(await screen.findByRole('button', { name: '添加页面' }))
    let dialog = screen.getByRole('dialog', { name: '添加页面' })
    await fireEvent.update(within(dialog).getByLabelText('名称'), '值班')
    await fireEvent.update(within(dialog).getByLabelText('类型'), 'note')
    const results = await within(dialog).findByRole('list', { name: '笔记搜索结果' })
    await fireEvent.update(within(dialog).getByLabelText('关键词'), '手册')
    await fireEvent.click(within(dialog).getByRole('button', { name: '搜索' }))
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/knowledge/notes' && new URL(r.url).searchParams.get('q') === '手册')).toBe(true))
    await fireEvent.click(within(await within(dialog).findByRole('list', { name: '笔记搜索结果' })).getByRole('button', { name: /值班手册/ }))
    expect(results).toBeDefined()
    expect(within(dialog).getByText('值班手册', { selector: 'strong' })).toBeVisible()
    await fireEvent.click(within(dialog).getByRole('button', { name: '加入页面' }))

    await fireEvent.click(screen.getByRole('button', { name: '添加页面' }))
    dialog = screen.getByRole('dialog', { name: '添加页面' })
    await fireEvent.update(within(dialog).getByLabelText('名称'), '运营看板')
    await fireEvent.update(within(dialog).getByLabelText('类型'), 'dashboard')
    const board = await within(dialog).findByLabelText('仪表盘')
    expect(within(board).getAllByRole('option').map(o => o.textContent)).toEqual(['请选择', '首页（首页）', '运营'])
    await fireEvent.update(board, BOARD)
    await fireEvent.click(within(dialog).getByRole('button', { name: '加入页面' }))

    await fireEvent.click(screen.getByRole('button', { name: '添加页面' }))
    dialog = screen.getByRole('dialog', { name: '添加页面' })
    await fireEvent.update(within(dialog).getByLabelText('类型'), 'embed')
    expect(within(dialog).getByRole('note')).toHaveTextContent('EMBED_ORIGINS')
    await fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))

    await fireEvent.click(screen.getByRole('button', { name: '保存侧栏布局' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'PUT')).toBe(true))
    const body = await requests.find(r => r.method === 'PUT')!.clone().json() as Schema['NavigationIn']
    expect(body.pages!.map(p => [p.title, p.kind, p.target_id, p.url, p.path])).toEqual([['值班', 'note', NOTE, null, null], ['运营看板', 'dashboard', BOARD, null, null]])
  })

  it('only accepts embed addresses from the allowed origins', async () => {
    setup('owner', { ...emptyNavigation, embed_origins: ['https://grafana.example.com'] })
    await openApp('/settings/navigation')
    await fireEvent.click(await screen.findByRole('button', { name: '添加页面' }))
    const dialog = screen.getByRole('dialog', { name: '添加页面' })
    await fireEvent.update(within(dialog).getByLabelText('名称'), '大屏')
    await fireEvent.update(within(dialog).getByLabelText('类型'), 'embed')
    expect(within(within(dialog).getByRole('list', { name: '允许嵌入的来源' })).getByText('https://grafana.example.com')).toBeVisible()
    await fireEvent.update(within(dialog).getByLabelText('嵌入地址'), 'https://evil.example.com/x')
    await fireEvent.click(within(dialog).getByRole('button', { name: '加入页面' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('嵌入页的地址必须来自允许嵌入的来源。')
    await fireEvent.update(within(dialog).getByLabelText('嵌入地址'), 'https://grafana.example.com/d/abc')
    await fireEvent.click(within(dialog).getByRole('button', { name: '加入页面' }))
    expect(screen.getByRole('listitem', { name: '页面：大屏' })).toHaveTextContent('嵌入页')
  })

  it('is read-only for admins and forbidden for members', async () => {
    setup('admin')
    const first = await openApp('/settings/navigation')
    expect(await screen.findByText('你可以查看；只有 owner 能修改侧栏。')).toBeVisible()
    expect(screen.queryByRole('button', { name: '保存侧栏布局' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '添加页面' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('任务所在分组')).toBeDisabled()
    expect(screen.getByRole('listitem', { name: '页面：Grafana' })).toBeVisible()
    expect(screen.queryByRole('listitem', { name: '页面：只给 owner' })).not.toBeInTheDocument()
    first.unmount()

    setup('member')
    const { router } = await openApp('/settings/navigation')
    expect(router.currentRoute.value.path).toBe('/forbidden')
    expect(screen.getByRole('navigation', { name: '主导航' }).querySelector('a[href="/settings/navigation"]')).toBeNull()
  })
})
