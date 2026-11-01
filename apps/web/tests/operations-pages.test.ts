import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import { services, metrics, opsTime } from './operations-fixtures'
const path = (r: Request) => new URL(r.url).pathname
function operations(override?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => await override?.(r) ?? (path(r) === '/api/v1/operations/services' ? Response.json(services) : path(r) === '/api/v1/operations/metrics' ? Response.json(metrics) : undefined))
}
describe('运维页', () => {
  it('hides old-range metrics while the selected range is loading', async () => {
    let finish: ((r: Response) => void) | undefined
    operations((r) => path(r).endsWith('/metrics') && new URL(r.url).searchParams.get('range') === '6h' ? new Promise<Response>((resolve) => { finish = resolve }) : undefined)
    await openApp('/operations'); await screen.findByRole('region', { name: 'API P95 延迟' })
    await fireEvent.update(screen.getByLabelText('查看时间范围'), '6h')
    expect(await screen.findByText('正在加载指标…')).toBeVisible()
    expect(screen.queryByRole('region', { name: 'API P95 延迟' })).not.toBeInTheDocument()
    await vi.waitFor(() => expect(finish).toBeDefined())
    finish!(Response.json({ ...metrics, range: '6h', views: metrics.views.map(view => ({ ...view, title: '最近六小时延迟' })) }))
    expect(await screen.findByRole('region', { name: '最近六小时延迟' })).toBeVisible()
  })
  it('shows an initial metric read error instead of empty data', async () => {
    operations((r) => path(r).endsWith('/metrics') ? fail(503, 'HTTP_ERROR') : undefined)
    await openApp('/operations'); expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.queryByText('暂无指标数据。')).not.toBeInTheDocument()
  })
  it('distinguishes all service states and shows checks without echoing raw reasons', async () => {
    const requests = operations((r) => path(r).endsWith('/services') ? Response.json({ ...services, items: [...services.items, { id: 'custom', name: '可选服务', kind: 'integration', status: 'down', checked_at: opsTime, reason: 'secret=must-not-appear' }] }) : undefined)
    await openApp('/operations')
    expect(await within(await screen.findByRole('region', { name: 'API' })).findByText('正常')).toBeVisible()
    expect(within(screen.getByRole('region', { name: 'Worker' })).getByText('降级')).toBeVisible()
    expect(within(screen.getByRole('region', { name: 'PostgreSQL' })).getByText('不可用')).toBeVisible()
    expect(within(screen.getByRole('region', { name: 'Redis' })).getByText('尚未检查')).toBeVisible()
    expect(within(screen.getByRole('region', { name: 'Prometheus' })).getByText('未部署 / 未配置')).toBeVisible()
    expect(screen.getByRole('region', { name: 'API' }).querySelector('time')).toHaveAttribute('datetime', opsTime)
    expect(document.body).not.toHaveTextContent('secret=must-not-appear')
    expect(requests.filter((r) => path(r).includes('/operations/')).every((r) => r.headers.get('X-Workspace-Id') === 'workspace-a')).toBe(true)
    expect(requests.every((r) => new URL(r.url).origin === window.location.origin)).toBe(true)
  })
  it('shows OpenIM and OpenClaw instances as their own group with readable reasons', async () => {
    operations((r) => path(r).endsWith('/services') ? Response.json({ ...services, items: [...services.items,
      { id: 'openim', name: 'OpenIM', kind: 'agents', status: 'degraded', checked_at: opsTime, reason: 'inbox_backlog' },
      { id: 'agent:a', name: '实例甲', kind: 'agents', provider: 'openclaw', status: 'down', checked_at: opsTime, reason: 'agent_offline' },
      { id: 'agent:b', name: '实例乙', kind: 'agents', provider: 'openclaw', status: 'not_configured', checked_at: null, reason: 'agent_disabled' }] }) : undefined)
    await openApp('/operations')
    const openim = await screen.findByRole('region', { name: 'OpenIM' })
    expect(within(openim).getByText('OpenClaw 与 IM')).toBeVisible()
    expect(within(openim).getByText('IM 回调积压，消息投影可能落后')).toBeVisible()
    expect(within(screen.getByRole('region', { name: '实例甲' })).getByText('实例离线（IM 未连接）')).toBeVisible()
    expect(within(screen.getByRole('region', { name: '实例乙' })).getByText('实例已停用')).toBeVisible()
  })
  it('splits metric trends at null samples, preserves genuine zero and links Grafana safely', async () => {
    operations(); await openApp('/operations')
    const view = await screen.findByRole('region', { name: 'API P95 延迟' })
    expect(within(view).getByText('无数据')).toBeVisible(); expect(within(view).getByText('0 s')).toBeVisible()
    expect(view.querySelectorAll('svg polyline')).toHaveLength(2)
    const link = screen.getByRole('link', { name: /在 Grafana 中查看/ })
    expect(link).toHaveAttribute('href', metrics.grafana_url); expect(link).toHaveAttribute('target', '_blank'); expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(document.querySelector('iframe')).toBeNull()
  })
  it('requests only preset IDs and range when controls change', async () => {
    const requests = operations(); await openApp('/operations'); await screen.findByRole('region', { name: 'API P95 延迟' })
    await fireEvent.update(screen.getByLabelText('查看时间范围'), '7d')
    await fireEvent.update(screen.getByLabelText('预设指标视图'), 'latency_p99')
    await vi.waitFor(() => expect(requests.filter((r) => path(r).endsWith('/metrics')).some((r) => { const p = new URL(r.url).searchParams; return p.get('range') === '7d' && p.getAll('views').includes('latency_p99') })).toBe(true))
    for (const read of requests.filter((r) => path(r).endsWith('/metrics'))) expect([...new URL(read.url).searchParams.keys()].every((key) => ['range', 'views'].includes(key))).toBe(true)
  })
  it.each([['not_configured', '未部署监控'], ['unavailable', '指标暂不可用'], ['stale', '数据可能过期']] as const)('does not show %s as an ordinary empty result', async (status, message) => {
    operations((r) => path(r).endsWith('/metrics') ? Response.json({ ...metrics, views: status === 'stale' ? metrics.views : [], sync: { status, as_of: status === 'stale' ? opsTime : null } }) : undefined)
    await openApp('/operations'); expect(await screen.findByText(new RegExp(message))).toBeVisible()
    expect(screen.queryByText('暂无指标数据。')).not.toBeInTheDocument()
  })
  it('retains clearly stale cached results and their original timestamps after refresh failure', async () => {
    let broken = false
    operations((r) => broken && path(r).includes('/operations/') ? fail(503, 'HTTP_ERROR') : undefined)
    await openApp('/operations'); await screen.findByRole('region', { name: 'API P95 延迟' })
    broken = true; await fireEvent.click(screen.getByRole('button', { name: '刷新运维数据' }))
    expect(await screen.findAllByText(/数据可能过期.*截至/)).not.toHaveLength(0)
    expect(screen.getByRole('region', { name: 'API' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'API P95 延迟' })).toBeVisible()
    expect([...document.querySelectorAll('time')].some((el) => el.dateTime === opsTime)).toBe(true)
  })
  it('hides both cached operations sections and Grafana when workspace access is revoked', async () => {
    let revoked = false
    operations((r) => revoked && path(r).endsWith('/metrics') ? fail(403, 'FORBIDDEN') : undefined)
    await openApp('/operations'); await screen.findByRole('region', { name: 'API P95 延迟' })
    revoked = true; await fireEvent.click(screen.getByRole('button', { name: '刷新运维数据' }))
    await vi.waitFor(() => expect(screen.queryByRole('region', { name: 'API' })).not.toBeInTheDocument())
    expect(screen.queryByRole('region', { name: 'API P95 延迟' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Grafana/ })).not.toBeInTheDocument()
    await fireEvent.update(screen.getByLabelText('查看时间范围'), '6h')
    expect(screen.queryByRole('region', { name: 'API' })).not.toBeInTheDocument()
    revoked = false; await fireEvent.click(screen.getByRole('button', { name: '刷新运维数据' }))
    expect(await screen.findByRole('region', { name: 'API' })).toBeVisible()
  })
  it('ignores late service data and links from an old workspace', async () => {
    let finish: ((r: Response) => void) | undefined
    operations((r) => {
      if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B空间', role: 'viewer' }] })
      if (path(r).endsWith('/services')) return r.headers.get('X-Workspace-Id') === 'workspace-a' ? new Promise<Response>((resolve) => { finish = resolve }) : Response.json({ ...services, items: [{ ...services.items[0], name: 'B API' }] })
      if (path(r).endsWith('/metrics')) return Response.json({ ...metrics, grafana_url: r.headers.get('X-Workspace-Id') === 'workspace-a' ? 'https://a.example.com' : null })
    })
    await openApp('/operations'); await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b'); await screen.findByRole('region', { name: 'B API' })
    finish!(Response.json(services)); await Promise.resolve()
    expect(screen.queryByRole('region', { name: 'API' })).not.toBeInTheDocument()
    expect(document.querySelector('a[href="https://a.example.com"]')).toBeNull()
  })
  it.each([null, 'javascript:alert(1)'])('does not render a Grafana entry for %s', async (grafana_url) => {
    operations((r) => path(r).endsWith('/metrics') ? Response.json({ ...metrics, grafana_url }) : undefined)
    await openApp('/operations'); await screen.findByRole('region', { name: 'API P95 延迟' })
    expect(screen.queryByRole('link', { name: /Grafana/ })).not.toBeInTheDocument()
  })
})
