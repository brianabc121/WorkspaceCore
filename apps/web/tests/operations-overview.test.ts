// 运维健康视图（T-0177，二批改造规范第 7 节，计划 Task 6）：健康摘要按完整服务数组分组、异常优先、
// 详情只用接口字段、Grafana 故障不连带指标、指标格式与缺测、多序列、迟到响应，以及存储读写自检只在明确点击后调用。
import { fireEvent, screen, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fail, me, mockApi, openApp, workspace } from './helpers'
import { metrics, opsTime, services } from './operations-fixtures'
import type { Schema } from '../src/services/api'
import { formatMetricValue } from '../src/features/operations/metricFormat'

const path = (r: Request) => new URL(r.url).pathname
type Role = Schema['WorkspaceOut']['role']
type Service = Schema['ServiceStatusOut']
const later = '2026-10-31T03:05:00Z'
const svc = (id: string, name: string, status: Service['status'], over: Partial<Service> = {}): Service => ({ id, name, kind: 'core', status, checked_at: opsTime, reason: null, ...over })
const storage = {
  primary: { name: 'local', configured: true, where: null, missing: [], check: { ok: true, reason: null, checked_at: opsTime } },
  replica: null, files: 3, used_bytes: 1024, quota_bytes: 1024 * 1024 * 1024, pending_replication: 0, failed_replication: 0,
}
const at = (minute: number) => `2026-10-31T02:${String(minute).padStart(2, '0')}:00Z`
const view = (over: Partial<Schema['MetricViewOut']>): Schema['MetricViewOut'] => ({ id: 'request_rate', title: '请求速率', unit: 'requests_per_second', series: [], ...over })
const points = (...values: (number | null)[]) => values.map((value, i) => ({ at: at(50 + i), value }))

function operations(role: Role, override?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => {
    const result = await override?.(r)
    if (result) return result
    if (path(r) === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: [{ ...workspace, role }] })
    if (path(r) === '/api/v1/operations/services') return Response.json(services)
    if (path(r) === '/api/v1/operations/metrics') return Response.json(metrics)
    if (path(r) === '/api/v1/operations/storage') return Response.json(storage)
    return undefined
  })
}
const summary = () => screen.getByRole('region', { name: '服务健康摘要' })
const tile = (label: string) => within(summary()).getByText(label).closest('div')!.querySelector('dd')!
const rows = () => within(screen.getByRole('table', { name: '服务状态列表' })).getAllByRole('row').slice(1).map((row) => row.getAttribute('aria-label'))
const pickRange = (label: string) => fireEvent.click(within(screen.getByRole('group', { name: '查看时间范围' })).getByRole('button', { name: label }))

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('运维：服务健康', () => {
  it('groups the full service list, keeps unknown and not configured apart and sorts problems first', async () => {
    const list = { generated_at: later, items: [
      svc('api', 'API', 'ok', { checked_at: later }),
      svc('worker', 'Worker', 'degraded', { reason: 'heartbeat_overdue' }),
      svc('postgres', 'PostgreSQL', 'down', { reason: 'timeout' }),
      svc('redis', 'Redis', 'unknown', { checked_at: null }),
      svc('prometheus', 'Prometheus', 'not_configured', { kind: 'monitoring', checked_at: null, reason: 'not_deployed' }),
      // 停用的连接：未配置，不算故障
      svc('integration:1', 'GitHub', 'not_configured', { kind: 'integration', checked_at: null, reason: 'disabled' }),
      svc('openim', 'OpenIM', 'ok', { kind: 'agents' }),
    ] }
    operations('owner', (r) => (path(r) === '/api/v1/operations/services' ? Response.json(list) : undefined))
    await openApp('/operations')
    await screen.findByRole('row', { name: 'API' })
    expect(tile('服务')).toHaveTextContent('7')
    expect(tile('正常')).toHaveTextContent('2')
    expect(tile('需关注')).toHaveTextContent('2')
    expect(tile('未检查')).toHaveTextContent('1')
    expect(tile('未配置')).toHaveTextContent('2')
    // 最近检查是接口里最新的 checked_at，不写“刚刚”
    expect(tile('最近检查').querySelector('time')).toHaveAttribute('datetime', later)
    expect(document.body).not.toHaveTextContent('刚刚')
    expect(rows()).toEqual(['PostgreSQL', 'Worker', 'Redis', 'API', 'OpenIM', 'GitHub', 'Prometheus'])
    expect(screen.getByRole('status')).toHaveTextContent('2 个服务需要关注：PostgreSQL、Worker。')
  })

  it('does not hard-code the service count', async () => {
    operations('owner', (r) => (path(r) === '/api/v1/operations/services' ? Response.json({ generated_at: opsTime, items: [svc('api', 'API', 'ok'), svc('worker', 'Worker', 'ok'), svc('redis', 'Redis', 'ok')] }) : undefined))
    await openApp('/operations')
    await screen.findByRole('row', { name: 'Redis' })
    expect(tile('服务')).toHaveTextContent('3')
    expect(tile('正常')).toHaveTextContent('3')
    expect(tile('需关注')).toHaveTextContent('0')
    expect(screen.queryByText(/个服务需要关注/)).toBeNull()
    // 没有异常：详情面板不挑一个正常服务冒充“异常详情”
    const detail = screen.getByRole('region', { name: '服务详情' })
    expect(within(detail).getByRole('heading', { name: '服务详情' })).toBeVisible()
    expect(detail).toHaveTextContent('当前没有降级或不可用的服务')
  })

  it('shows the selected problem from interface fields only, without a last-success time', async () => {
    operations('owner')
    await openApp('/operations')
    const detail = await screen.findByRole('region', { name: '服务详情' })
    await within(detail).findByText('PostgreSQL')
    expect(within(detail).getByRole('heading', { name: '异常详情' })).toBeVisible()
    expect(detail).toHaveTextContent('检查超时')
    expect(detail.querySelector('time')).toHaveAttribute('datetime', opsTime)
    expect(document.body).not.toHaveTextContent('上次成功')
    // 点表格里的服务换详情
    await fireEvent.click(within(screen.getByRole('row', { name: 'Redis' })).getByRole('button', { name: 'Redis' }))
    expect(within(detail).getByText('Redis')).toBeVisible()
    expect(within(detail).getByRole('heading', { name: '服务详情' })).toBeVisible()
    expect(detail).toHaveTextContent('暂无记录')
    expect(screen.getByRole('row', { name: 'Redis' })).toHaveAttribute('aria-selected', 'true')
  })

  it('keeps the metrics readable when Grafana is down but Prometheus is fine', async () => {
    const list = { generated_at: opsTime, items: [svc('api', 'API', 'ok'), svc('prometheus', 'Prometheus', 'ok', { kind: 'monitoring' }), svc('grafana', 'Grafana', 'down', { kind: 'monitoring', reason: 'timeout' })] }
    operations('member', (r) => (path(r) === '/api/v1/operations/services' ? Response.json(list) : undefined))
    await openApp('/operations')
    expect(await screen.findByRole('region', { name: 'API P95 延迟' })).toBeVisible()
    await vi.waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('1 个服务需要关注：Grafana。Prometheus 采集正常，预设指标仍可查看。'))
    const detail = screen.getByRole('region', { name: '服务详情' })
    expect(detail).toHaveTextContent('Grafana')
    expect(detail).toHaveTextContent('Prometheus 正常，本页的预设指标照常显示')
    expect(within(screen.getByRole('region', { name: 'API P95 延迟' })).queryByText(/暂不可用/)).toBeNull()
  })

  it('shows no storage to viewers and clears services after a 403', async () => {
    let revoked = false
    const requests = operations('viewer', (r) => (revoked && path(r) === '/api/v1/operations/services' ? fail(403, 'FORBIDDEN') : undefined))
    await openApp('/operations')
    await screen.findByRole('row', { name: 'API' })
    expect(screen.queryByRole('region', { name: '存储状态' })).toBeNull()
    expect(requests.some((r) => path(r).startsWith('/api/v1/operations/storage'))).toBe(false)
    revoked = true
    await fireEvent.click(screen.getByRole('button', { name: '刷新运维数据' }))
    await vi.waitFor(() => expect(screen.queryByRole('row', { name: 'API' })).toBeNull())
    expect(tile('服务')).toHaveTextContent('—')
    expect(screen.queryByRole('region', { name: '服务详情' })).toBeNull()
    expect(screen.getAllByText(/无权查看运维数据/).length).toBeGreaterThan(0)
  })
})

describe('运维：指标', () => {
  it('formats each unit once and shows missing values as —', () => {
    expect(formatMetricValue(null, 'seconds')).toBe('—')
    expect(formatMetricValue(Number.NaN, 'ratio')).toBe('—')
    expect(formatMetricValue(1.2, 'requests_per_second')).toBe('1.2 req/s')
    expect(formatMetricValue(0.184, 'seconds')).toBe('184 ms')
    expect(formatMetricValue(0.012, 'ratio')).toBe('1.2%')
    expect(formatMetricValue(8, 'count')).toBe('8')
    expect(formatMetricValue(0, 'seconds')).toBe('0 ms')
    expect(formatMetricValue(1.25, 'seconds')).toBe('1.25 s')
  })

  it('breaks lines at missing samples and shows — when the latest sample is missing', async () => {
    const data = { ...metrics, views: [
      view({ series: [{ label: '', points: points(1, null, 3) }] }),
      view({ id: 'latency_p95', title: '延迟 P95', unit: 'seconds', series: [{ label: '', points: points(0.2, 0.3, null) }] }),
    ] }
    operations('member', (r) => (path(r) === '/api/v1/operations/metrics' ? Response.json(data) : undefined))
    await openApp('/operations')
    const rate = await screen.findByRole('region', { name: '请求速率' })
    expect(rate.querySelectorAll('svg [data-segment]')).toHaveLength(2)
    expect(within(within(rate).getByRole('list', { name: '请求速率 当前值' })).getByText('3 req/s')).toBeVisible()
    expect(within(within(rate).getByRole('table')).getAllByRole('cell').map((c) => c.textContent)).toEqual(['1 req/s', '—', '3 req/s'])
    const latency = screen.getByRole('region', { name: '延迟 P95' })
    expect(within(within(latency).getByRole('list', { name: '延迟 P95 当前值' })).getByText('—')).toBeVisible()
    expect(within(latency).getByRole('list', { name: '延迟 P95 当前值' })).not.toHaveTextContent('300 ms')
    // 纵轴刻度带单位
    expect([...latency.querySelectorAll('svg text')].some((t) => /ms$/.test(t.textContent ?? ''))).toBe(true)
  })

  it('keeps every series with its own label instead of averaging P95, and leaves counts as counts', async () => {
    const data = { ...metrics, views: [
      view({ id: 'latency_p95', title: '延迟 P95', unit: 'seconds', series: [{ label: 'api', points: points(0.1, 0.2) }, { label: 'worker', points: points(0.5, 0.4) }] }),
      view({ id: 'services_up', title: '服务在线', unit: 'count', series: [{ label: '', points: points(7, 8) }] }),
    ] }
    operations('member', (r) => (path(r) === '/api/v1/operations/metrics' ? Response.json(data) : undefined))
    await openApp('/operations')
    const p95 = await screen.findByRole('region', { name: '延迟 P95' })
    const current = within(p95).getByRole('list', { name: '延迟 P95 当前值' })
    expect(within(current).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['api200 ms', 'worker400 ms'])
    expect(p95).not.toHaveTextContent('300 ms')
    expect(within(within(p95).getByRole('list', { name: '延迟 P95 图例' })).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['api', 'worker'])
    expect(p95.querySelectorAll('svg [data-series]')).toHaveLength(2)
    expect(within(within(p95).getByRole('table')).getAllByRole('columnheader').map((th) => th.textContent)).toEqual(['采样时间', 'api', 'worker'])
    const up = screen.getByRole('region', { name: '服务在线' })
    expect(within(within(up).getByRole('list', { name: '服务在线 当前值' })).getByText('8')).toBeVisible()
    expect(up).not.toHaveTextContent('800%')
  })

  it('does not let a late response of the previous range replace the new range', async () => {
    let finish: ((r: Response) => void) | undefined
    operations('member', (r) => {
      if (path(r) !== '/api/v1/operations/metrics') return undefined
      const range = new URL(r.url).searchParams.get('range')
      if (range === '1h') return new Promise<Response>((resolve) => { finish = resolve })
      return Response.json({ ...metrics, range: '7d', step_seconds: 3600, views: [view({ title: '七天的请求速率', series: [{ label: '', points: points(5, 6) }] })] })
    })
    await openApp('/operations')
    await vi.waitFor(() => expect(finish).toBeDefined())
    await pickRange('7 天')
    expect(await screen.findByRole('region', { name: '七天的请求速率' })).toBeVisible()
    finish!(Response.json({ ...metrics, views: [view({ title: '一小时的请求速率', series: [{ label: '', points: points(1, 2) }] })] }))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(screen.queryByRole('region', { name: '一小时的请求速率' })).toBeNull()
    expect(screen.getByRole('region', { name: '七天的请求速率' })).toHaveTextContent('最近 7 天 · 每 1 小时一个点')
  })

  it.each([
    ['stale', true, /数据可能过期（截至/],
    ['unavailable', false, /指标暂不可用/],
  ] as const)('shows Prometheus %s honestly instead of zeros', async (status, cached, message) => {
    operations('member', (r) => (path(r) === '/api/v1/operations/metrics' ? Response.json({ ...metrics, views: cached ? metrics.views : [], sync: { status, as_of: cached ? opsTime : null, reason: 'timeout' } }) : undefined))
    await openApp('/operations')
    expect(await screen.findByText(message)).toBeVisible()
    if (cached) expect(screen.getByRole('region', { name: 'API P95 延迟' })).toBeVisible()
    else {
      expect(screen.queryByRole('region', { name: 'API P95 延迟' })).toBeNull()
      expect(document.body).not.toHaveTextContent(/\b0 (ms|req\/s)/)
    }
  })
})

describe('运维：只读刷新与存储自检', () => {
  it('never runs the storage self-check on load, refresh, range switches or background refetches; one click runs it once', async () => {
    const requests = operations('admin', (r) => (path(r) === '/api/v1/operations/storage/check' ? Response.json({ ...storage, primary: { ...storage.primary, check: { ok: true, reason: null, checked_at: later } } }) : undefined))
    const { queryClient } = await openApp('/operations')
    const panel = await screen.findByRole('region', { name: '存储状态' })
    await within(panel).findByText('本机（卷 kb-files）')
    const checks = () => requests.filter((r) => path(r) === '/api/v1/operations/storage/check').length
    await fireEvent.click(screen.getByRole('button', { name: '刷新运维数据' }))
    await pickRange('6 小时')
    await pickRange('7 天')
    await queryClient.invalidateQueries({ queryKey: ['operations'] })
    await queryClient.refetchQueries({ queryKey: ['operations'] })
    await vi.waitFor(() => expect(requests.filter((r) => path(r) === '/api/v1/operations/storage').length).toBeGreaterThan(1))
    expect(checks()).toBe(0)
    expect(requests.filter((r) => path(r).startsWith('/api/v1/operations/')).every((r) => r.method === 'GET')).toBe(true)
    // 说明会写入、读取、删除探测对象；明确点击一次才调用一次
    expect(panel).toHaveTextContent('写入、读取并删除一个探测对象')
    await fireEvent.click(within(panel).getByRole('button', { name: '执行存储读写自检' }))
    await vi.waitFor(() => expect(checks()).toBe(1))
    expect(requests.find((r) => path(r) === '/api/v1/operations/storage/check')!.method).toBe('POST')
  })

  it('says 未配置副本 instead of treating a missing replica as healthy', async () => {
    operations('owner')
    await openApp('/operations')
    const panel = await screen.findByRole('region', { name: '存储状态' })
    expect(await within(panel).findByText('未配置副本')).toBeVisible()
    expect(panel).not.toHaveTextContent('副本正常')
  })

  it.each([undefined, null, 'javascript:alert(1)', 'ftp://grafana.example.com'])('shows no Grafana entry for %s', async (grafana_url) => {
    operations('owner', (r) => (path(r) === '/api/v1/operations/metrics' ? Response.json({ ...metrics, grafana_url }) : undefined))
    await openApp('/operations')
    await screen.findByRole('region', { name: 'API P95 延迟' })
    expect(screen.queryByRole('link', { name: /Grafana/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Grafana/ })).toBeNull()
  })

  it('links a valid Grafana URL in a new window', async () => {
    operations('owner')
    await openApp('/operations')
    const link = await screen.findByRole('link', { name: /在 Grafana 中查看/ })
    expect(link).toHaveAttribute('href', metrics.grafana_url)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })
})
