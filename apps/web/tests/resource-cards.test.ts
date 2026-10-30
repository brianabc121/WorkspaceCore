import { render, fireEvent, screen } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { createAppRouter } from '../src/app/router'
import { useAuthStore } from '../src/stores/auth'
import WidgetFrame from '../src/widgets/WidgetFrame.vue'
import { registry, parseWidget } from '../src/widgets/registry'
import { mockApi, openApp, emptyPage, me, workspace, fail } from './helpers'
import { widget, overview } from './dashboard-fixtures'
import { source, bookmark, calendarEvent } from './sprint4-fixtures'
import type { Schema } from '../src/services/api'
async function card(type = 'recent_docs', module: Schema['ModuleStatus']['status'] = 'ok', config = {}) {
  const pinia = createPinia(); setActivePinia(pinia)
  const auth = useAuthStore(pinia)
  auth.acceptSession({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B空间' }] })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createAppRouter(pinia, createMemoryHistory(), queryClient)
  const view = render(WidgetFrame, { props: { widget: { ...widget, widget_type: type, config }, overview: { ...overview, modules: { knowledge: { status: module, as_of: source.updated_at, retryable: true }, calendar: { status: module, as_of: source.updated_at, retryable: true } } } }, global: { plugins: [pinia, router, [VueQueryPlugin, { queryClient }]] } })
  await router.push('/'); await router.isReady()
  return { ...view, auth, queryClient }
}
describe('知识与日历首页组件', () => {
  it('refreshes healthy resource cards and advances the upcoming range with 刷新首页', async () => {
    const initialTime = new Date('2026-10-30T02:00:00Z')
    vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(initialTime)
    try {
      let changed = false
      const widgets = [{ ...widget, id: 'docs-card', widget_type: 'recent_docs', config: { limit: 5 } }, { ...widget, id: 'agenda-card', widget_type: 'calendar_agenda', config: { days: 7, limit: 5 } }]
      const requests = mockApi((r) => {
        const path = new URL(r.url).pathname
        if (path === '/api/v1/dashboard/overview') return Response.json({ ...overview, widgets, modules: { knowledge: { status: 'ok', retryable: false }, calendar: { status: 'ok', retryable: false } } })
        if (path === '/api/v1/knowledge/resources') return Response.json({ ...emptyPage, items: [{ ...bookmark, title: changed ? '刷新后的文档' : '刷新前的文档' }] })
        if (path === '/api/v1/calendar/events') return Response.json({ ...emptyPage, items: [{ ...calendarEvent, title: changed ? '刷新后的日程' : '刷新前的日程' }] })
      })
      await openApp('/'); await screen.findByText('刷新前的文档'); await screen.findByText('刷新前的日程')
      changed = true; vi.setSystemTime(new Date('2026-10-30T03:00:00Z'))
      await fireEvent.click(screen.getByRole('button', { name: '刷新首页' }))
      expect(await screen.findByText('刷新后的文档')).toBeVisible()
      expect(await screen.findByText('刷新后的日程')).toBeVisible()
      expect(screen.queryByText('刷新前的文档')).not.toBeInTheDocument()
      const reads = requests.filter((r) => new URL(r.url).pathname === '/api/v1/calendar/events')
      expect(reads).toHaveLength(2)
      expect(new URL(reads[1]!.url).searchParams.get('start')).toBe('2026-10-30T03:00:00.000Z')
    } finally { vi.useRealTimers() }
  })
  it('registers validated defaults and rejects out-of-range configuration', () => {
    expect(registry.recent_docs.configSchema.parse({})).toEqual({ limit: 5 })
    expect(registry.calendar_agenda.configSchema.parse({})).toEqual({ days: 7, limit: 5 })
    expect(parseWidget({ ...widget, widget_type: 'calendar_agenda', config: { days: 15 } })).toBeNull()
    expect(parseWidget({ ...widget, widget_type: 'recent_docs', config: { limit: 21 } })).toBeNull()
  })
  it('loads scoped recent metadata and respects the configured count', async () => {
    const requests = mockApi((r) => new URL(r.url).pathname === '/api/v1/knowledge/resources' ? Response.json({ ...emptyPage, items: [bookmark, { ...bookmark, id: 'second', title: '第二文档' }] }) : undefined)
    await card('recent_docs', 'ok', { limit: 1 })
    expect(await screen.findByText('发布文档')).toBeVisible()
    expect(screen.queryByText('第二文档')).not.toBeInTheDocument()
    const read = requests.find((r) => new URL(r.url).pathname === '/api/v1/knowledge/resources')!
    expect(read.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(new URL(read.url).searchParams.get('sort')).toBe('updated')
    expect(new URL(read.url).searchParams.get('limit')).toBe('1')
  })
  it('requests a bounded calendar range and shows event timezone', async () => {
    const requests = mockApi((r) => new URL(r.url).pathname === '/api/v1/calendar/events' ? Response.json({ ...emptyPage, items: [calendarEvent] }) : undefined)
    await card('calendar_agenda', 'ok', { days: 3, limit: 2 })
    expect(await screen.findByText('发布评审')).toBeVisible()
    expect(screen.getByText(/Asia\/Shanghai/)).toBeVisible()
    const query = new URL(requests.find((r) => new URL(r.url).pathname === '/api/v1/calendar/events')!.url).searchParams
    expect(Date.parse(query.get('end')!) - Date.parse(query.get('start')!)).toBe(3 * 86400000)
    expect(query.get('limit')).toBe('2')
  })
  it('shows loading while a list is pending', async () => {
    let finish: ((response: Response) => void) | undefined
    mockApi((r) => new URL(r.url).pathname === '/api/v1/knowledge/resources' ? new Promise<Response>((resolve) => { finish = resolve }) : undefined)
    await card()
    expect(screen.getByRole('status')).toHaveTextContent('正在加载')
    finish!(Response.json(emptyPage))
    expect(await screen.findByText('暂无收藏文档')).toBeVisible()
  })
  it('keeps a failed refresh visibly stale while retaining cached metadata', async () => {
    let broken = false
    mockApi((r) => new URL(r.url).pathname === '/api/v1/knowledge/resources' ? broken ? fail(503, 'HTTP_ERROR') : Response.json({ ...emptyPage, items: [bookmark] }) : undefined)
    const { queryClient } = await card(); await screen.findByText('发布文档')
    broken = true; await queryClient.invalidateQueries()
    expect(await screen.findByText(/数据可能已过期/)).toBeVisible()
    expect(screen.getByText('发布文档')).toBeVisible()
  })
  it('shows a fetch error instead of an empty result and lets the user retry', async () => {
    let broken = true
    mockApi((r) => new URL(r.url).pathname === '/api/v1/knowledge/resources' ? broken ? fail(503, 'HTTP_ERROR') : Response.json(emptyPage) : undefined)
    await card(); expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.queryByText('暂无收藏文档')).not.toBeInTheDocument()
    broken = false; await fireEvent.click(screen.getByRole('button', { name: '重试组件数据' }))
    expect(await screen.findByText('暂无收藏文档')).toBeVisible()
  })
  it('hides cached metadata when access is revoked', async () => {
    let revoked = false
    mockApi((r) => new URL(r.url).pathname === '/api/v1/knowledge/resources' ? revoked ? fail(403, 'FORBIDDEN') : Response.json({ ...emptyPage, items: [bookmark] }) : undefined)
    const { queryClient } = await card(); await screen.findByText('发布文档')
    revoked = true; await queryClient.invalidateQueries()
    await vi.waitFor(() => expect(screen.queryByText('发布文档')).not.toBeInTheDocument())
    expect(screen.getByText(/无权查看此组件/)).toBeVisible()
  })
  it.each(['unauthorized', 'unavailable'] as const)('does not present module %s as an empty list', async (status) => {
    const requests = mockApi()
    await card('recent_docs', status)
    expect(screen.getByText(status === 'unauthorized' ? /无权查看此组件/ : /知识模块暂不可用/)).toBeVisible()
    expect(screen.queryByText('暂无收藏文档')).not.toBeInTheDocument()
    expect(requests.some((r) => new URL(r.url).pathname === '/api/v1/knowledge/resources')).toBe(false)
  })
  it('uses a new scope and drops the old card after a workspace switch', async () => {
    const requests = mockApi((r) => new URL(r.url).pathname === '/api/v1/knowledge/resources' ? Response.json({ ...emptyPage, items: [{ ...bookmark, title: r.headers.get('X-Workspace-Id') === 'workspace-b' ? 'B文档' : 'A文档' }] }) : undefined)
    const { auth } = await card(); await screen.findByText('A文档')
    auth.selectWorkspace('workspace-b')
    expect(await screen.findByText('B文档')).toBeVisible(); expect(screen.queryByText('A文档')).not.toBeInTheDocument()
    expect(requests.filter((r) => new URL(r.url).pathname === '/api/v1/knowledge/resources').map((r) => r.headers.get('X-Workspace-Id'))).toEqual(['workspace-a', 'workspace-b'])
  })
})
