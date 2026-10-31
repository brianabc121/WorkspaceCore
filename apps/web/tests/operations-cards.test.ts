import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, fail } from './helpers'
import { overview, widget } from './dashboard-fixtures'
import { services, metrics } from './operations-fixtures'
import { registry, parseWidget } from '../src/widgets/registry'
import type { Schema } from '../src/services/api'
const path = (r: Request) => new URL(r.url).pathname
function card(type = 'service_health', config = {}, module: Schema['ModuleStatus'] = { status: 'ok', as_of: null, retryable: false }, override?: (r: Request) => Response | undefined) {
  return mockApi((r) => override?.(r) ?? (path(r) === '/api/v1/dashboard/overview' ? Response.json({ ...overview, widgets: [{ ...widget, widget_type: type, config }], modules: { operations: module } }) : path(r).endsWith('/services') ? Response.json(services) : path(r).endsWith('/metrics') ? Response.json(metrics) : undefined))
}
describe('运维首页组件', () => {
  it.each([['not_configured', '未部署监控'], ['unavailable', '指标暂不可用']] as const)('preserves metric %s when the independent overview refresh fails', async (status, text) => {
    let overviewFailed = false
    card('api_latency', {}, undefined, (r) => path(r) === '/api/v1/dashboard/overview' && overviewFailed ? fail(503, 'HTTP_ERROR') : path(r).endsWith('/metrics') ? Response.json({ ...metrics, views: [], sync: { status, as_of: null } }) : undefined)
    await openApp('/'); await screen.findByText(new RegExp(text))
    overviewFailed = true; await fireEvent.click(screen.getByRole('button', { name: '刷新首页' }))
    await screen.findAllByRole('alert')
    expect(screen.getByText(new RegExp(text))).toBeVisible()
    expect(screen.queryByText(/数据可能过期.*时间未知/)).not.toBeInTheDocument()
  })
  it.each(['services', 'metrics'])('latches denial from %s across both cards, config changes and late successes', async (endpoint) => {
    let revoked = false
    const waiting: { finish: (r: Response) => void; data: unknown }[] = []
    const widgets = [{ ...widget, id: 'health', widget_type: 'service_health', config: {} }, { ...widget, id: 'latency', widget_type: 'api_latency', config: {} }]
    const dashboard = { ...overview, widgets, modules: { operations: { status: 'ok', as_of: services.generated_at, retryable: false } } }
    mockApi((r) => {
      if (path(r) === '/api/v1/dashboard/overview') return Response.json(dashboard)
      if (!path(r).startsWith('/api/v1/operations/')) return
      const data = path(r).endsWith('/services') ? services : { ...metrics, range: new URL(r.url).searchParams.get('range') || '1h' }
      if (!revoked) return Response.json(data)
      if (path(r).endsWith('/' + endpoint)) return fail(403, 'FORBIDDEN')
      return new Promise<Response>((finish) => { waiting.push({ finish, data }) })
    })
    const { queryClient } = await openApp('/')
    await screen.findByRole('region', { name: 'API' }); await screen.findByRole('region', { name: 'API P95 延迟' })
    revoked = true
    const refresh = queryClient.invalidateQueries({ queryKey: ['operations'] })
    try {
      await vi.waitFor(() => expect(waiting.length).toBeGreaterThan(0))
      await vi.waitFor(() => expect(screen.queryByRole('region', { name: 'API' })).not.toBeInTheDocument())
      expect(screen.queryByRole('region', { name: 'API P95 延迟' })).not.toBeInTheDocument()
      queryClient.setQueryData(['overview', 'user-a', 'workspace-a'], { ...dashboard, widgets: [widgets[0], { ...widgets[1], config: { range: '6h', quantile: 'p95' } }] })
      await Promise.resolve(); await Promise.resolve()
      for (const pending of waiting) pending.finish(Response.json(pending.data))
      await refresh
      expect(screen.queryByRole('region', { name: 'API' })).not.toBeInTheDocument()
      expect(screen.queryByRole('region', { name: 'API P95 延迟' })).not.toBeInTheDocument()
      revoked = false
      await fireEvent.click((await screen.findAllByRole('button', { name: '重新验证运维权限' }))[0]!)
      expect(await screen.findByRole('region', { name: 'API' })).toBeVisible()
      expect(await screen.findByRole('region', { name: 'API P95 延迟' })).toBeVisible()
    } finally { for (const pending of waiting) pending.finish(Response.json(pending.data)) }
  })
  it('registers exact contract defaults and validates limits/ranges/quantiles', () => {
    expect(registry.service_health?.configSchema.parse({})).toEqual({ limit: 8 })
    expect(registry.api_latency?.configSchema.parse({})).toEqual({ range: '1h', quantile: 'p95' })
    expect(parseWidget({ ...widget, widget_type: 'service_health', config: { limit: 21 } })).toBeNull()
    expect(parseWidget({ ...widget, widget_type: 'api_latency', config: { range: '7d' } })).toBeNull()
    expect(parseWidget({ ...widget, widget_type: 'api_latency', config: { quantile: 'p90' } })).toBeNull()
  })
  it('limits services while reading their BFF data', async () => {
    const requests = card('service_health', { limit: 1 }); await openApp('/')
    expect(await screen.findByRole('region', { name: 'API' })).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Worker' })).not.toBeInTheDocument()
    expect(requests.some((r) => path(r).endsWith('/services') && r.headers.get('X-Workspace-Id') === 'workspace-a')).toBe(true)
  })
  it('reads services and reports core-down even when overview module is unavailable', async () => {
    const requests = card('service_health', {}, { status: 'unavailable', as_of: null, reason: 'service_down', retryable: true }); await openApp('/')
    expect(await screen.findByText('有核心服务不可用')).toBeVisible()
    expect(await screen.findByRole('region', { name: 'PostgreSQL' })).toHaveTextContent('不可用')
    expect(requests.some((r) => path(r).endsWith('/services'))).toBe(true)
  })
  it('requests only the selected latency quantile/range and preserves null values', async () => {
    const requests = card('api_latency', { range: '6h', quantile: 'p99' }, undefined, (r) => path(r).endsWith('/metrics') ? Response.json({ ...metrics, views: metrics.views.map(view => ({ ...view, id: 'latency_p99', title: 'API P99 延迟' })) }) : undefined); await openApp('/')
    const view = await screen.findByRole('region', { name: 'API P99 延迟' })
    expect(within(view).getByText('无数据')).toBeVisible()
    const params = new URL(requests.find((r) => path(r).endsWith('/metrics'))!.url).searchParams
    expect(params.get('range')).toBe('6h'); expect(params.getAll('views')).toEqual(['latency_p99'])
  })
  it('does not fetch or show content for an unauthorized overview module', async () => {
    const requests = card('service_health', {}, { status: 'unauthorized', as_of: null, retryable: false }); await openApp('/')
    expect(await screen.findByText(/无权查看运维数据/)).toBeVisible()
    expect(screen.queryByRole('region', { name: 'API' })).not.toBeInTheDocument()
    expect(requests.some((r) => path(r).includes('/operations/'))).toBe(false)
  })
  it('does not label an empty service response as healthy', async () => {
    card('service_health', {}, undefined, (r) => path(r).endsWith('/services') ? Response.json({ ...services, items: [] }) : undefined); await openApp('/')
    expect(await screen.findByText('暂无可显示的服务状态。')).toBeVisible()
    expect(screen.queryByText('全部服务正常')).not.toBeInTheDocument()
  })
  it('shows monitoring not deployed instead of a normal empty metrics card', async () => {
    card('api_latency', {}, undefined, (r) => path(r).endsWith('/metrics') ? Response.json({ ...metrics, views: [], sync: { status: 'not_configured', as_of: null } }) : undefined); await openApp('/')
    expect(await screen.findByText(/未部署监控/)).toBeVisible()
    expect(screen.queryByText('暂无指标数据。')).not.toBeInTheDocument()
  })
  it('refreshes service data with 刷新首页', async () => {
    let changed = false
    card('service_health', {}, undefined, (r) => path(r).endsWith('/services') ? Response.json({ ...services, items: [{ ...services.items[0], name: changed ? '更新后的 API' : 'API' }] }) : undefined)
    await openApp('/'); await screen.findByRole('region', { name: 'API' })
    changed = true; await fireEvent.click(screen.getByRole('button', { name: '刷新首页' }))
    expect(await screen.findByRole('region', { name: '更新后的 API' })).toBeVisible()
  })
  it('keeps stale data with timestamp on refresh failure and hides it after access denial', async () => {
    let status = 200
    card('service_health', {}, undefined, (r) => path(r).endsWith('/services') && status !== 200 ? fail(status, status === 403 ? 'FORBIDDEN' : 'HTTP_ERROR') : undefined)
    const { queryClient } = await openApp('/'); await screen.findByRole('region', { name: 'API' })
    status = 503; await queryClient.invalidateQueries({ queryKey: ['operations'] })
    expect(await screen.findByText(/数据可能过期.*截至/)).toBeVisible()
    expect(screen.getByRole('region', { name: 'API' })).toBeVisible()
    status = 403; await queryClient.invalidateQueries({ queryKey: ['operations'] })
    await vi.waitFor(() => expect(screen.queryByRole('region', { name: 'API' })).not.toBeInTheDocument())
    expect(screen.getByText(/无权查看运维数据/)).toBeVisible()
  })
})
