import { screen, fireEvent } from '@testing-library/vue'
import { describe, it, expect, vi } from 'vitest'
import { fail, me, mockApi, openApp, workspace } from './helpers'
import { overview, widget, layout } from './dashboard-fixtures'

const pathOf = (r: Request) => new URL(r.url).pathname
describe('仪表盘首页', () => {
  async function importOther() {
    await fireEvent.click(await screen.findByRole('button', { name: '导入 JSON' }))
    await fireEvent.update(screen.getByLabelText('布局 JSON'), JSON.stringify({ format: 'my-workspace/dashboard', format_version: 1, title: '其他仪表盘', widgets: [] }))
    await fireEvent.click(screen.getByRole('button', { name: '创建新仪表盘' }))
    await screen.findByText('已创建新仪表盘')
  }
  it('does not repeat a committed widget creation when refreshing metadata fails', async () => {
    const other = { ...overview.dashboard, id: 'other', is_default: false, title: '其他仪表盘' }
    const requests = mockApi((r) => {
      if (pathOf(r) === '/api/v1/dashboard/overview') return Response.json(overview)
      if (pathOf(r) === '/api/v1/dashboards' && r.method === 'POST') return Response.json(other, { status: 201 })
      if (pathOf(r) === '/api/v1/dashboards/other/widgets') return r.method === 'POST' ? Response.json({ ...widget, dashboard_id: 'other' }, { status: 201 }) : Response.json({ items: [], next_cursor: null, total_estimate: 0 })
      if (pathOf(r) === '/api/v1/dashboards') return fail(503, 'HTTP_ERROR')
    })
    await openApp(); await importOther()
    await fireEvent.click(await screen.findByRole('button', { name: '添加组件' }))
    await fireEvent.click(screen.getByRole('button', { name: '保存组件' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('操作已完成，但刷新失败')
    await vi.waitFor(() => expect(screen.queryByRole('dialog', { name: '添加组件' })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '添加组件' })).toBeDisabled()
    expect(requests.filter((r) => r.method === 'POST' && pathOf(r).endsWith('/widgets'))).toHaveLength(1)
  })
  it('keeps the conflict lock when reloading a selected dashboard fails', async () => {
    const other = { ...overview.dashboard, id: 'other', is_default: false, title: '其他仪表盘' }
    mockApi((r) => {
      if (pathOf(r) === '/api/v1/dashboard/overview') return Response.json(overview)
      if (pathOf(r) === '/api/v1/dashboards' && r.method === 'POST') return Response.json(other, { status: 201 })
      if (pathOf(r) === '/api/v1/dashboards/other/widgets') return Response.json({ items: [{ ...widget, dashboard_id: 'other' }], next_cursor: null, total_estimate: 1 })
      if (r.method === 'PATCH') return fail(409, 'VERSION_CONFLICT')
      if (pathOf(r) === '/api/v1/dashboards') return fail(503, 'HTTP_ERROR')
    })
    await openApp(); await importOther()
    await fireEvent.click(await screen.findByRole('button', { name: '编辑布局' }))
    await fireEvent.update(screen.getByLabelText('桌面宽度'), '5')
    await fireEvent.click(screen.getByRole('button', { name: '保存布局' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('已被其他人修改')
    await fireEvent.click(screen.getByRole('button', { name: '重新加载最新版本' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '保存布局' })).toBeDisabled())
    expect(screen.getByRole('button', { name: '重新加载最新版本' })).toBeVisible()
  })
  it('isolates unavailable and invalid widgets without hiding a valid local note', async () => {
    mockApi((r) => pathOf(r) === '/api/v1/dashboard/overview' ? Response.json({ ...overview, widgets: [...overview.widgets, { ...widget, id: 'unknown', widget_type: 'constructor', config: { text: 'SHOULD NOT RENDER' } }] }) : undefined)
    await openApp()
    expect(await screen.findByText('欢迎便签')).toBeVisible()
    expect(screen.getByText(/任务模块暂不可用/)).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent('不受支持')
    expect(screen.queryByText('SHOULD NOT RENDER')).not.toBeInTheDocument()
    expect(screen.queryByText('0', { selector: '.summary-number' })).not.toBeInTheDocument()
  })
  it('keeps widget content on screen while the overview refreshes in the background', async () => {
    // T-0048：SSE 建立后的失效会立即重新拉取首页；已有数据时组件不能闪成“正在加载组件…”
    let calls = 0
    let finish: ((r: Response) => void) | undefined
    mockApi((r) => {
      if (pathOf(r) !== '/api/v1/dashboard/overview') return undefined
      calls += 1
      return calls === 1 ? Response.json(overview) : new Promise<Response>((resolve) => { finish = resolve })
    })
    await openApp()
    expect(await screen.findByText(/任务模块暂不可用/)).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '刷新首页' }))
    await vi.waitFor(() => expect(finish).toBeDefined())
    expect(screen.getByText(/任务模块暂不可用/)).toBeVisible()
    expect(screen.queryByText('正在加载组件…')).not.toBeInTheDocument()
    finish!(Response.json(overview))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '刷新首页' })).toBeEnabled())
    expect(screen.getByText(/任务模块暂不可用/)).toBeVisible()
  })
  it('shows a loading state until the first overview arrives', async () => {
    let finish: ((r: Response) => void) | undefined
    mockApi((r) => pathOf(r) === '/api/v1/dashboard/overview' ? new Promise<Response>((resolve) => { finish = resolve }) : undefined)
    await openApp()
    await vi.waitFor(() => expect(finish).toBeDefined())
    expect(await screen.findByText('正在加载…')).toBeVisible()
    finish!(Response.json(overview))
    expect(await screen.findByText(/任务模块暂不可用/)).toBeVisible()
    expect(screen.queryByText('正在加载…')).not.toBeInTheDocument()
  })
  it('shows stale module data with its timestamp instead of calling it fresh', async () => {
    mockApi((r) => pathOf(r) === '/api/v1/dashboard/overview' ? Response.json({ ...overview, summary: { ...overview.summary, pending_tasks: 7 }, modules: { tasks: { status: 'stale', as_of: overview.generated_at, retryable: true } } }) : undefined)
    await openApp()
    expect(await screen.findByText(/数据可能已过期/)).toBeVisible()
    expect(screen.getByText('7', { selector: '.summary-number' })).toBeVisible()
    expect(screen.getByRole('button', { name: '重试组件' })).toBeVisible()
  })
  it('saves the full layout once with the read version and reloads saved positions', async () => {
    let current = structuredClone(overview)
    let finish: ((r: Response) => void) | undefined
    const requests = mockApi((r) => {
      if (pathOf(r) === '/api/v1/dashboard/overview') return Response.json(current)
      if (r.method === 'PATCH') return new Promise<Response>((resolve) => { finish = resolve })
    })
    await openApp()
    await fireEvent.click(await screen.findByRole('button', { name: '编辑布局' }))
    await fireEvent.update(screen.getAllByLabelText('桌面宽度')[0]!, '5')
    await fireEvent.click(screen.getByRole('button', { name: '保存布局' }))
    await vi.waitFor(() => expect(finish).toBeDefined())
    expect(screen.getByRole('button', { name: '正在保存…' })).toBeDisabled()
    const write = requests.find((r) => r.method === 'PATCH')!
    const body = await write.clone().json()
    expect(body.version).toBe(1)
    expect(body.layout).toHaveLength(2)
    expect(body.layout[0]).toEqual({ widget_id: widget.id, layout: { ...layout, desktop: { ...layout.desktop, w: 5 } } })
    expect(write.headers.get('X-Workspace-Id')).toBe('workspace-a')
    current = { ...current, dashboard: { ...current.dashboard, version: 2 }, widgets: current.widgets.map((w) => ({ ...w, layout: body.layout.find((p: { widget_id: string }) => p.widget_id === w.id).layout })) }
    finish!(Response.json(current.dashboard))
    expect(await screen.findByText('布局已保存')).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '编辑布局' }))
    expect(screen.getAllByLabelText('桌面宽度')[0]).toHaveValue(5)
  })
  it('stops on version conflict and lets the user reload without overwriting', async () => {
    let version = 1
    const requests = mockApi((r) => {
      if (pathOf(r) === '/api/v1/dashboard/overview') return Response.json({ ...overview, dashboard: { ...overview.dashboard, version } })
      if (r.method === 'PATCH') { version = 2; return fail(409, 'VERSION_CONFLICT') }
    })
    await openApp()
    await fireEvent.click(await screen.findByRole('button', { name: '编辑布局' }))
    await fireEvent.update(screen.getAllByLabelText('桌面宽度')[0]!, '5')
    await fireEvent.click(screen.getByRole('button', { name: '保存布局' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('已被其他人修改')
    expect(screen.getByRole('button', { name: '保存布局' })).toBeDisabled()
    await fireEvent.click(screen.getByRole('button', { name: '取消编辑' }))
    await fireEvent.click(screen.getByRole('button', { name: '导出 JSON' }))
    await fireEvent.click(screen.getByRole('button', { name: '关闭对话框' }))
    expect(screen.getByRole('button', { name: '编辑布局' })).toBeDisabled()
    await fireEvent.click(screen.getByRole('button', { name: '重新加载最新版本' }))
    await vi.waitFor(() => expect(screen.getByText('版本 2')).toBeVisible())
    expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(1)
  })
  it('gives viewers a read-only dashboard without edit or import controls', async () => {
    mockApi((r) => pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] }) : pathOf(r) === '/api/v1/dashboard/overview' ? Response.json(overview) : undefined)
    await openApp()
    await screen.findByText('欢迎便签')
    expect(screen.queryByRole('button', { name: '编辑布局' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '导入 JSON' })).not.toBeInTheDocument()
  })
  it('does not mix workspace rows or search results when an old save completes late', async () => {
    let finish: ((r: Response) => void) | undefined
    const requests = mockApi((r) => {
      if (pathOf(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: '空间 B', role: 'viewer' }] })
      if (pathOf(r) === '/api/v1/dashboard/overview') return Response.json({ ...overview, widgets: [{ ...widget, config: { text: r.headers.get('X-Workspace-Id') === 'workspace-b' ? 'B 的便签' : 'A 的便签' } }] })
      if (r.method === 'PATCH') return new Promise<Response>((resolve) => { finish = resolve })
    })
    await openApp()
    await fireEvent.click(await screen.findByRole('button', { name: '编辑布局' }))
    await fireEvent.update(screen.getByLabelText('桌面宽度'), '5')
    await fireEvent.click(screen.getByRole('button', { name: '保存布局' }))
    await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    expect(await screen.findByText('B 的便签')).toBeVisible()
    finish!(Response.json({ ...overview.dashboard, version: 2 }))
    await vi.waitFor(() => expect(screen.queryByText('A 的便签')).not.toBeInTheDocument())
    await fireEvent.keyDown(window, { key: 'k', metaKey: true })
    await fireEvent.update(screen.getByLabelText('搜索页面与组件'), 'A 的便签')
    expect(screen.getByText('没有匹配的页面或组件。')).toBeVisible()
    expect(screen.queryByRole('button', { name: '编辑布局' })).not.toBeInTheDocument()
    expect(requests.find((r) => r.method === 'PATCH')!.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })
  it('opens local search with Ctrl+K and finds both pages and current widgets', async () => {
    mockApi((r) => pathOf(r) === '/api/v1/dashboard/overview' ? Response.json(overview) : undefined)
    const { router } = await openApp()
    await screen.findByText('欢迎便签')
    await fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    expect(screen.getByRole('dialog', { name: '搜索当前工作空间' })).toBeVisible()
    await vi.waitFor(() => expect(screen.getByLabelText('搜索页面与组件')).toHaveFocus())
    await fireEvent.update(screen.getByLabelText('搜索页面与组件'), '便签')
    expect(screen.getByRole('button', { name: /文本便签/ })).toBeVisible()
    await fireEvent.update(screen.getByLabelText('搜索页面与组件'), '安全设置')
    await fireEvent.click(screen.getByRole('button', { name: '安全设置' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/settings/security'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  it('validates import before writing and imports into a new dashboard', async () => {
    const requests = mockApi((r) => {
      if (pathOf(r) === '/api/v1/dashboard/overview') return Response.json(overview)
      if (pathOf(r) === '/api/v1/dashboards' && r.method === 'POST') return Response.json({ ...overview.dashboard, id: 'new-dashboard', title: '导入首页', is_default: false }, { status: 201 })
      if (pathOf(r) === '/api/v1/dashboards/new-dashboard/widgets') return Response.json({ items: [widget], next_cursor: null, total_estimate: 1 })
    })
    await openApp()
    await fireEvent.click(await screen.findByRole('button', { name: '导入 JSON' }))
    await fireEvent.update(screen.getByLabelText('布局 JSON'), '{"format_version":2}')
    await fireEvent.click(screen.getByRole('button', { name: '创建新仪表盘' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('格式或组件配置不符合契约')
    expect(requests.filter((r) => r.method === 'POST')).toHaveLength(0)
    await fireEvent.update(screen.getByLabelText('布局 JSON'), JSON.stringify({ format: 'my-workspace/dashboard', format_version: 1, title: '导入首页', widgets: [{ widget_type: 'note', config: { text: 'new' }, layout }] }))
    await fireEvent.click(screen.getByRole('button', { name: '创建新仪表盘' }))
    expect(await screen.findByText('已创建新仪表盘')).toBeVisible()
    expect(await requests.find((r) => r.method === 'POST')!.clone().json()).toEqual({ title: '导入首页', widgets: [{ widget_type: 'note', config: { text: 'new' }, layout }] })
  })
})
