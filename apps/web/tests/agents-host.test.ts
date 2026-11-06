import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// T-0113：实例卡片里的“服务器状态”（小指示、当前值、趋势图与表格视图）。
const GB = 1024 ** 3
const now = new Date(), hour = (n: number) => new Date(Math.floor(now.getTime() / 3_600_000) * 3_600_000 - n * 3_600_000).toISOString()
const path = (r: Request) => new URL(r.url).pathname
const instance: Schema['AgentInstanceOut'] = { id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read', max_role: 'member', status: 'online', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: hour(0), reported_version: '2026.9.36', plugin_version: '0.3.3', bundle_issued_at: hour(0), im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null }, mail: { accounts: [], read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 }, bundle_outdated: false, version: 3, created_at: hour(0), updated_at: hour(0), credentials: [], secret_grants: [] }
const summary = (over: Partial<Schema['HostSummaryItem']> = {}): Schema['HostSummaryItem'] => ({ instance_id: 'inst-a', reported_at: hour(0), stale: false, cpu_percent: 25, mem_used_percent: 50, disk_used_percent: 95, alerts: ['disk_low'], ...over })
const point = (n: number, over: Partial<Schema['HostPoint']> = {}): Schema['HostPoint'] => ({ at: hour(n), cpu_avg: 20 + n, cpu_max: 40 + n, load_avg: 0.5, load_max: 1.5, mem_avg: 50, mem_max: 55, data_avg: 95, root_avg: 40, rx_avg: 2048, rx_max: 4096, tx_avg: 1024, tx_max: 3072, ...over })
const current: Schema['HostCurrent'] = { reported_at: hour(0), stale: false, cpu_model: 'Test CPU', cores: 4, cpu_percent: 25, load: [0.5, 0.4, 0.3], mem_total: 16 * GB, mem_available: 8 * GB, mem_used: 8 * GB, mem_limit: null, swap_total: 0, swap_used: 0, data_disk: { total: 100 * GB, free: 5 * GB, used: 95 * GB, used_percent: 95 }, root_disk: { total: 200 * GB, free: 120 * GB, used: 80 * GB, used_percent: 40 }, rx_rate: 1536, tx_rate: 512, uptime_seconds: 3 * 86400 + 4 * 3600 }
const status = (range: Schema['HostStatusOut']['range'], over: Partial<Schema['HostStatusOut']> = {}): Schema['HostStatusOut'] => ({ instance_id: 'inst-a', reported: true, current, range, points: [point(3), point(2), point(1, { cpu_avg: null, cpu_max: null })], alerts: ['disk_low'], ...over })

function setup(host: (r: Request) => Response | undefined, items: Schema['HostSummaryItem'][] = [summary()]) {
  return mockApi(r =>
    host(r) ?? (
      path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
      path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
      path(r) === '/api/v1/agents/instances' ? Response.json({ items: [instance], next_cursor: null, total_estimate: 1 }) :
      path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
      path(r) === '/api/v1/agents/instances/inst-a/ssh' ? Response.json({ target: null, job: null, warning: null }) :
      path(r) === '/api/v1/agents/host-summary' ? Response.json({ items }) :
      path(r).endsWith('/ops') ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) : undefined))
}
async function section() {
  await openApp('/agents')
  const card = await screen.findByRole('article', { name: '实例：alpha' })
  return within(card).getByRole('region', { name: '服务器状态' })
}
afterEach(() => vi.restoreAllMocks())

describe('server status', () => {
  it('shows the three indicators with the over-threshold one marked in words, then the current values and trends', async () => {
    const requests = setup(r => path(r) === '/api/v1/agents/instances/inst-a/host' ? Response.json(status(new URL(r.url).searchParams.get('range') as Schema['HostStatusOut']['range'])) : undefined)
    const host = await section()
    const meters = await within(host).findByRole('list', { name: '当前使用率' })
    expect(within(meters).getByText(/CPU/)).toHaveTextContent('CPU 25%')
    const disk = within(meters).getByText(/磁盘/)
    expect(disk).toHaveTextContent('磁盘 95% · 空间不足')
    expect(disk.closest('li')).toHaveClass('is-danger')
    expect(within(meters).getByText(/内存/).closest('li')).not.toHaveClass('is-danger')
    // 收起时不取详情
    expect(requests.some(r => path(r).endsWith('/host'))).toBe(false)
    await fireEvent.click(within(host).getByRole('button', { name: '查看服务器状态' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/host') && new URL(r.url).searchParams.get('range') === '24h')).toBe(true))
    expect(await within(host).findByText('磁盘可用空间不足 10%')).toBeVisible()
    const cpu = within(host).getByRole('region', { name: 'CPU' })
    expect(within(cpu).getByText('25%')).toBeVisible()
    expect(within(cpu).getByText(/4 核 · Test CPU/)).toBeVisible()
    expect(within(cpu).getByText(/负载 0.50 \/ 0.40 \/ 0.30/)).toBeVisible()
    expect(within(host).getByRole('region', { name: '内存' })).toHaveTextContent('8.0 GB / 16.0 GB')
    const data = within(host).getByRole('region', { name: '接入包目录所在分区' })
    expect(data).toHaveTextContent('已用 95% · 可用 5.0 GB · 空间不足')
    expect(within(host).getByRole('region', { name: '网络' })).toHaveTextContent('下载 1.5 KB/s')
    expect(within(host).getByRole('region', { name: '开机时长与上报' })).toHaveTextContent('3 天 4 小时')
    // 一张图一条轴：CPU 使用率和负载分开
    expect(within(host).getByText('CPU 使用率')).toBeVisible()
    expect(within(host).getByText('负载（1 分钟）')).toBeVisible()
    // 表格视图：最近的在前，缺的值显示 —
    const details = within(host).getAllByText('以表格查看')[0].closest('details')!
    details.open = true
    await fireEvent(details, new Event('toggle'))
    const table = within(host).getByRole('table', { name: /CPU 使用率/ })
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(4)
    expect(within(rows[1]).getAllByRole('cell').map(c => c.textContent)).toEqual(['—', '—'])
    expect(within(rows[3]).getAllByRole('cell').map(c => c.textContent)).toEqual(['23%', '43%'])
    await fireEvent.click(within(host).getByRole('button', { name: '最近 7 天' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/host') && new URL(r.url).searchParams.get('range') === '7d')).toBe(true))
    expect(within(host).getByRole('button', { name: '最近 7 天' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('reads the hour under the pointer or keyboard in a tooltip', async () => {
    setup(r => path(r) === '/api/v1/agents/instances/inst-a/host' ? Response.json(status('24h')) : undefined)
    const host = await section()
    await fireEvent.click(within(host).getByRole('button', { name: '查看服务器状态' }))
    const plot = await within(host).findByRole('group', { name: /^网络，最近 24 小时/ })
    await fireEvent.focus(plot)
    let tip = within(plot).getByRole('status')
    expect(tip).toHaveTextContent('下载2.0 KB/s')
    expect(tip).toHaveTextContent('上传峰值3.0 KB/s')
    await fireEvent.keyDown(plot, { key: 'Home' })
    tip = within(plot).getByRole('status')
    expect(tip).toHaveTextContent(`${new Date(hour(3)).getHours().toString().padStart(2, '0')}:00`)
    await fireEvent.blur(plot)
    expect(within(plot).queryByRole('status')).not.toBeInTheDocument()
  })

  it('explains how to get data when the server has never reported', async () => {
    setup(r => path(r) === '/api/v1/agents/instances/inst-a/host' ? Response.json(status('24h', { reported: false, current: null, points: [], alerts: [] })) : undefined,
      [summary({ reported_at: null, stale: true, cpu_percent: null, mem_used_percent: null, disk_used_percent: null, alerts: [] })])
    const host = await section()
    expect(await within(host).findByText('还没有收到这台服务器的状态。')).toBeVisible()
    await fireEvent.click(within(host).getByRole('button', { name: '查看服务器状态' }))
    expect(await within(host).findByText(/按接入包 CHECKLIST.md 在 crontab 里加一行/)).toBeVisible()
  })
})
