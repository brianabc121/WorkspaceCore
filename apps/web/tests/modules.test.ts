// 插拔式模块（T-0159、T-0160，docs/modules/README.md）：注册表一致性、侧栏与路由按启用状态显示、应用中心、停用后的小组件。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import type { RouteRecordRaw } from 'vue-router'
import { fail, me, mockApi, newsModule, openApp, workspace } from './helpers'
import { overview, widget } from './dashboard-fixtures'
import type { Schema } from '../src/services/api'
import { moduleEvents, moduleNav, moduleOfPath, moduleRoutes, moduleWidgets, modules } from '../src/modules/registry'
import { navigation } from '../src/app/navigation'
import { parseWidget, registry } from '../src/widgets/registry'
import { useModulesStore } from '../src/stores/modules'
import WidgetEditor from '../src/features/dashboard/WidgetEditor.vue'
import registrySource from '../src/modules/registry.ts?raw'

const path = (r: Request) => new URL(r.url).pathname
type Role = Schema['WorkspaceOut']['role']
const as = (role: Role) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })
const off: Schema['ModuleOut'] = { ...newsModule, enabled: false }

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function routeComponents(routes: readonly RouteRecordRaw[]): unknown[] {
  return routes.flatMap((route) => ['component' in route ? route.component : undefined, ...routeComponents(route.children ?? [])].filter(Boolean))
}

describe('module registry', () => {
  it('has unique, well-formed keys and the marker line make module-new inserts before', () => {
    const keys = modules.map((module) => module.key)
    expect(new Set(keys).size).toBe(keys.length)
    for (const key of keys) expect(key).toMatch(/^[a-z][a-z0-9_]{1,31}$/)
    expect(registrySource).toContain('\n  // module-new: 新模块加在这一行上面（make module-new 会自动插入）\n]')
    for (const line of registrySource.split('\n').filter((item) => item.startsWith('import '))) expect(line).toMatch(/ from '[^']+'$/)
  })

  it('gives every nav entry a route of the same module and loads every route component lazily', async () => {
    for (const module of modules) {
      const paths = (module.routes ?? []).map((route) => route.path)
      for (const entry of module.nav ?? []) expect(paths, `${module.key} ${entry.path}`).toContain(entry.path)
      const components = routeComponents(module.routes ?? [])
      expect(components.length).toBeGreaterThan(0)
      for (const component of components) {
        // 按需加载的组件是 () => import(...)：调用后才得到模块；直接导入的 SFC 是对象
        expect(typeof component).toBe('function')
        const loaded = await (component as () => Promise<{ default: unknown }>)()
        expect(loaded.default).toBeTruthy()
      }
    }
    expect(moduleRoutes.every((route) => typeof route.meta?.module === 'string')).toBe(true)
  })

  it('registers module widgets in the widget registry and the import/export union', async () => {
    const types = moduleWidgets.map((item) => item.type)
    expect(new Set(types).size).toBe(types.length)
    for (const item of moduleWidgets) {
      const definition = registry[item.type]
      expect(definition, item.type).toBeTruthy()
      expect(definition.module).toBe(item.module)
      expect(typeof item.component).toBe('function')
      expect(parseWidget({ ...widget, widget_type: item.type, config: {} })?.config).toEqual(item.configSchema.parse({}))
    }
    // T-0164：首页效果图里叫“AI 新闻速览”
    expect(registry.news_briefing).toMatchObject({ title: 'AI 新闻速览', defaultSize: { w: 4, h: 6 }, module: 'news' })
  })

  it('merges module nav entries into the existing sections and maps paths and events back to modules', () => {
    const paths = navigation.map((item) => item.path)
    expect(paths.slice(paths.indexOf('/calendar'), paths.indexOf('/calendar') + 2)).toEqual(['/calendar', '/news'])
    expect(paths.slice(paths.indexOf('/settings/mail'), paths.indexOf('/settings/mail') + 2)).toEqual(['/settings/mail', '/settings/news'])
    for (const entry of moduleNav) expect(navigation.find((item) => item.path === entry.path)).toMatchObject({ module: entry.module, section: entry.section })
    expect(navigation.find((item) => item.path === '/settings/news')?.minRole).toBe('admin')
    expect(moduleOfPath('/news')).toBe('news')
    expect(moduleOfPath('/news/anything')).toBe('news')
    expect(moduleOfPath('/settings/news')).toBe('news')
    expect(moduleOfPath('/newsletter')).toBeNull()
    expect(moduleOfPath('/settings')).toBeNull()
    expect(moduleOfPath('/tasks')).toBeNull()
    expect(moduleEvents.news).toEqual(['news'])
  })
})

describe('module enablement in the shell', () => {
  it('hides the entries of a disabled module and shows them again after a modules event', async () => {
    class Stream extends EventTarget {
      static last: Stream | null = null
      readyState = 1
      constructor(public url: string) { super(); Stream.last = this }
      close() { this.readyState = 2 }
    }
    vi.stubGlobal('EventSource', Stream)
    let current = off
    const requests = mockApi(r => (path(r) === '/api/v1/modules' ? Response.json({ items: [current] }) : undefined))
    await openApp('/')
    const nav = screen.getByRole('navigation', { name: '主导航' })
    await vi.waitFor(() => expect(nav.querySelector('a[href="/news"]')).toBeNull())
    await fireEvent.click(screen.getByRole('button', { name: '管理' }))
    expect(nav.querySelector('a[href="/settings/news"]')).toBeNull()
    expect(within(nav).getByRole('link', { name: '应用中心' })).toHaveAttribute('href', '/settings/modules')

    current = newsModule
    const before = requests.filter(r => path(r) === '/api/v1/modules').length
    Stream.last!.dispatchEvent(new MessageEvent('invalidate', { data: JSON.stringify({ workspace_id: 'workspace-a', topics: ['modules', 'overview'], at: '2026-11-08T00:00:00Z' }) }))
    await vi.waitFor(() => expect(nav.querySelector('a[href="/news"]')).toHaveTextContent('早报'))
    expect(requests.filter(r => path(r) === '/api/v1/modules').length).toBeGreaterThan(before)
    expect(requests.find(r => path(r) === '/api/v1/modules')!.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })

  it('shows 功能未启用 in place of a disabled module page without loading its data', async () => {
    const requests = mockApi(r => (path(r) === '/api/v1/modules' ? Response.json({ items: [off] }) : undefined))
    const { router } = await openApp('/news')
    expect(await screen.findByRole('heading', { level: 1, name: '功能未启用' })).toBeVisible()
    expect(screen.getByText('「新闻早报」在当前工作空间没有启用。')).toBeVisible()
    expect(screen.getByRole('link', { name: '前往应用中心' })).toHaveAttribute('href', '/settings/modules')
    expect(router.currentRoute.value.path).toBe('/news')
    expect(requests.some(r => path(r).startsWith('/api/v1/news/'))).toBe(false)
  })

  it('tells members to ask the owner', async () => {
    mockApi(r => (path(r) === '/api/v1/me' && r.method === 'GET' ? as('member') : path(r) === '/api/v1/modules' ? Response.json({ items: [{ ...off, can_manage: false }] }) : undefined))
    await openApp('/news')
    expect(await screen.findByRole('heading', { level: 1, name: '功能未启用' })).toBeVisible()
    expect(screen.getByText(/需要工作空间的 owner/)).toBeVisible()
    expect(screen.queryByRole('link', { name: '前往应用中心' })).not.toBeInTheDocument()
  })

  it('opens the module page when the module list cannot be loaded (the server still guards it)', async () => {
    mockApi(r => (path(r) === '/api/v1/modules' ? fail(503, 'HTTP_ERROR') : path(r) === '/api/v1/news/today' ? Response.json({ briefing: null, topics: [], unavailable: false, message: null }) : undefined))
    await openApp('/news')
    expect(await screen.findByRole('heading', { level: 1, name: '早报' })).toBeVisible()
  })
})

// 应用中心的页面测试见 tests/app-center.test.ts（T-0211 改版）

describe('module widgets', () => {
  const card: Schema['WidgetOut'] = { ...widget, id: 'w-news', widget_type: 'news_briefing', config: {} }

  it('shows 功能已停用 for a widget of a disabled module without loading it', async () => {
    const requests = mockApi(r => (path(r) === '/api/v1/dashboard/overview' ? Response.json({ ...overview, widgets: [card] }) : path(r) === '/api/v1/modules' ? Response.json({ items: [off] }) : undefined))
    await openApp('/')
    const article = await screen.findByRole('article', { name: 'AI 新闻速览' })
    expect(await within(article).findByText('功能已停用：「新闻早报」在当前工作空间没有启用。')).toBeVisible()
    expect(within(article).getByRole('link', { name: '到应用中心启用 →' })).toHaveAttribute('href', '/settings/modules')
    expect(requests.some(r => path(r).startsWith('/api/v1/news/'))).toBe(false)
  })

  it('treats MODULE_DISABLED from the API as 功能已停用 and rechecks the module list', async () => {
    const requests = mockApi(r => {
      if (path(r) === '/api/v1/dashboard/overview') return Response.json({ ...overview, widgets: [card] })
      // 早报的两个接口（今天、最新一份）都因模块停用返回 MODULE_DISABLED
      if (path(r) === '/api/v1/news/briefings' || path(r) === '/api/v1/news/today') return Response.json({ error: { code: 'MODULE_DISABLED', message: '这个功能在当前工作空间没有启用', request_id: 'req', details: {} } }, { status: 404 })
      return undefined
    })
    await openApp('/')
    const article = await screen.findByRole('article', { name: 'AI 新闻速览' })
    expect(await within(article).findByText(/功能已停用/)).toBeVisible()
    expect(within(article).queryByRole('alert')).not.toBeInTheDocument()
    await vi.waitFor(() => expect(requests.filter(r => path(r) === '/api/v1/modules').length).toBeGreaterThan(1))
  })

  it('does not offer widgets of a disabled module when adding one', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useModulesStore(pinia).items = [off]
    render(WidgetEditor, { props: { bottom: 0, busy: false, error: '' }, global: { plugins: [pinia] } })
    const options = Array.from((screen.getByLabelText('组件类型') as HTMLSelectElement).options).map((option) => option.textContent)
    expect(options).toContain('文本便签')
    expect(options).not.toContain('AI 新闻速览')
    expect(screen.getByText(/通知填写 limit/).textContent).not.toContain('AI 新闻速览')
  })
})
