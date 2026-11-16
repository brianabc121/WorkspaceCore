import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import { GB, at, detail, fail, path, readyServer, readySsh, server, serverList, ssh } from './agents-fixtures'
import type { Schema } from '../src/services/api'

// 智能体服务器详情的页头（T-0271）：“连接”格的刷新图标重新测试连接并显示延迟；初始化后显示平台 SSH 采样的
// CPU（核数、总使用率）、内存（已用 / 可用）、硬盘（总量、各个挂载盘），“刷新资源”立即再采一次。
function setup(handler: (r: Request) => Response | Promise<Response | undefined> | undefined) {
  return mockApi(async r => await handler(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [workspace] }) :
    path(r).endsWith('/ops') && r.method === 'GET' ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) : undefined))
}
const writes = (requests: Request[]) => requests.filter(r => r.method !== 'GET')
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); localStorage.clear() })

const disk = (over: Partial<Schema['ServerDiskOut']> = {}): Schema['ServerDiskOut'] => ({ device: '/dev/vda1', fs_type: 'ext4', mount: '/', total: 100 * GB, used: 40 * GB, available: 55 * GB, ...over })
function sampled(over: Partial<Schema['ServerResourceSample']> = {}): Schema['ServerResourcesOut'] {
  const disks = over.disks ?? [disk(), disk({ device: '/dev/vdb', fs_type: 'xfs', mount: '/data', total: 500 * GB, used: 100 * GB, available: 400 * GB })]
  return {
    stale: false, attempted_at: at, error: null,
    sample: {
      sampled_at: at, cores: 8, cpu_percent: 23.4, load1: 0.42, mem_total: 16 * GB, mem_used: 6 * GB, mem_available: 10 * GB, disks,
      storage_total: disks.reduce((n, d) => n + d.total, 0), storage_used: disks.reduce((n, d) => n + d.used, 0), storage_available: disks.reduce((n, d) => n + d.available, 0), ...over,
    },
  }
}
const cell = (meters: HTMLElement, label: string) => within(meters).getAllByRole('listitem').find(li => li.querySelector('.asv-stat-label')?.textContent === label)!

describe('agent server detail: connection latency', () => {
  it('re-tests the connection from the header icon and shows the latency in the header and the checklist', async () => {
    let s = readyServer({ ssh: readySsh({ connection_latency_ms: 42.3 }), version: 6 })
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(detail(s))
      if (path(r) === '/api/v1/agents/servers/srv-1/test-connection') { s = readyServer({ ssh: readySsh({ connection_latency_ms: 8.4, last_connection_at: '2026-11-09T05:00:00Z' }), version: 6 }); return Response.json(detail(s)) }
    })
    await openApp('/agent-servers?server=srv-1')
    const meters = await screen.findByRole('list', { name: '服务器概览' })
    const conn = cell(meters, '连接')
    expect(conn).toHaveTextContent('连接正常延迟 42 ms')
    expect(within(conn).getByText('延迟 42 ms')).toHaveClass('tone-green')
    const checklist = screen.getByRole('region', { name: '登录前提' })
    expect(checklist).toHaveTextContent('连接正常 · 延迟 42 ms')

    await fireEvent.click(within(conn).getByRole('button', { name: '重新检测连接' }))
    await vi.waitFor(() => expect(writes(requests)).toHaveLength(1))
    const sent = writes(requests)[0]!
    expect(path(sent)).toBe('/api/v1/agents/servers/srv-1/test-connection')
    expect(await sent.clone().json()).toEqual({ version: 6 })
    expect(sent.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await screen.findByText('连接正常：已用保存的密码登录服务器，延迟 8.4 ms。')).toBeVisible()
    await vi.waitFor(() => expect(cell(meters, '连接')).toHaveTextContent('延迟 8.4 ms'))
    expect(checklist).toHaveTextContent('连接正常 · 延迟 8.4 ms')
  })

  it('shows a failed test without a latency, and only offers the icon when the platform can log in', async () => {
    let s = readyServer({ ssh: readySsh({ connection_latency_ms: 120 }) })
    setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(detail(s))
      if (path(r) === '/api/v1/agents/servers/srv-1/test-connection') { s = readyServer({ ssh: readySsh({ connection_status: 'unreachable', connection_latency_ms: null }), deployable: false }); return Response.json(detail(s)) }
    })
    const first = await openApp('/agent-servers?server=srv-1')
    const meters = await screen.findByRole('list', { name: '服务器概览' })
    expect(within(cell(meters, '连接')).getByText('延迟 120 ms')).toHaveClass('tone-amber')
    await fireEvent.click(within(cell(meters, '连接')).getByRole('button', { name: '重新检测连接' }))
    expect(await screen.findByText(/测试结果：连不上。/)).toBeVisible()
    await vi.waitFor(() => expect(cell(meters, '连接')).toHaveTextContent('连不上'))
    expect(cell(meters, '连接')).not.toHaveTextContent('延迟')
    first.unmount()

    // 指纹还没确认：不能登录，图标按钮不可用
    s = server({ ssh: ssh({ host_key_confirmed: false }) })
    await openApp('/agent-servers?server=srv-1')
    const locked = await screen.findByRole('list', { name: '服务器概览' })
    expect(within(cell(locked, '连接')).getByRole('button', { name: '重新检测连接' })).toBeDisabled()
  })

  it('has no re-test icon for a server recognized from reports', async () => {
    const s = server({ id: 'srv-auto', kind: 'auto', ssh: null, initialization_status: null, deploy_block_reason: 'not_ssh', reported_at: at, stale: false })
    setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-auto') return Response.json(detail(s))
    })
    await openApp('/agent-servers?server=srv-auto')
    const meters = await screen.findByRole('list', { name: '服务器概览' })
    expect(within(cell(meters, '连接')).queryByRole('button')).not.toBeInTheDocument()
    expect(within(meters).getAllByText('暂无上报')).toHaveLength(3)
    expect(screen.queryByRole('group', { name: '服务器资源' })).not.toBeInTheDocument()
  })
})

describe('agent server detail: CPU, memory and disks', () => {
  it('shows cores and total CPU, used / available memory, total storage and every mounted disk, and refreshes them', async () => {
    let s = readyServer({ resources: sampled(), version: 3 })
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(detail(s))
      if (path(r) === '/api/v1/agents/servers/srv-1/resources/refresh') {
        s = readyServer({ resources: sampled({ cpu_percent: 51, sampled_at: '2026-11-09T05:00:00Z', disks: [disk({ used: 92 * GB, available: 3 * GB })] }), version: 3 })
        return Response.json(detail(s))
      }
    })
    const first = await openApp('/agent-servers?server=srv-1')
    const meters = await screen.findByRole('list', { name: '服务器概览' })
    expect(cell(meters, 'CPU')).toHaveTextContent('CPU23%8 核1 分钟负载 0.42')
    expect(cell(meters, '内存')).toHaveTextContent('内存6.0 GB / 16.0 GB已用 38% · 可用 10.0 GB')
    expect(cell(meters, '硬盘')).toHaveTextContent('硬盘140 GB / 600 GB可用 455 GB · 2 个挂载盘')
    const res = screen.getByRole('group', { name: '服务器资源' })
    expect(res).toHaveTextContent('平台用 SSH 每分钟采样一次 · 最近采样')
    const disks = within(res).getAllByRole('listitem')
    expect(disks).toHaveLength(2)
    expect(disks[1]).toHaveTextContent('/data/dev/vdb · xfs已用 100 GB（20%） · 可用 400 GB · 共 500 GB')
    expect(disks[0]).not.toHaveClass('is-danger')

    await fireEvent.click(within(res).getByRole('button', { name: '刷新资源' }))
    await vi.waitFor(() => expect(writes(requests)).toHaveLength(1))
    expect(path(writes(requests)[0]!)).toBe('/api/v1/agents/servers/srv-1/resources/refresh')
    expect(await screen.findByText('资源已刷新')).toBeVisible()
    await vi.waitFor(() => expect(cell(meters, 'CPU')).toHaveTextContent('CPU51%'))
    // 只剩一块盘，用到 92%：标红
    const [only] = within(res).getAllByRole('listitem')
    expect(only).toHaveClass('is-danger')
    expect(cell(meters, '硬盘')).toHaveClass('is-danger')
    expect(cell(meters, '硬盘')).toHaveTextContent('1 个挂载盘')
    first.unmount()

    // 列表的“系统 / 资源”列也用采样
    await openApp('/agent-servers')
    const table = await screen.findByRole('table')
    expect(within(table).getByRole('row', { name: /香港节点 A/ })).toHaveTextContent('8 核 / 16 GB')
  })

  it('explains each state instead of drawing zeros, and reports a failed refresh without changing the numbers', async () => {
    let s = server({ ssh: ssh({ host_key_confirmed: true, host_key_confirmed_at: at, connection_status: 'connected' }), deploy_block_reason: 'not_initialized' })
    setup(r => {
      if (path(r) === '/api/v1/agents/servers') return Response.json(serverList([s]))
      if (path(r) === '/api/v1/agents/servers/srv-1' && r.method === 'GET') return Response.json(detail(s))
      if (path(r) === '/api/v1/agents/servers/srv-1/resources/refresh') return fail(503, 'METRICS_STALE')
    })
    // 还没初始化：写“初始化后显示”，刷新资源不可用
    const first = await openApp('/agent-servers?server=srv-1')
    let meters = await screen.findByRole('list', { name: '服务器概览' })
    expect(within(meters).getAllByText('初始化后显示')).toHaveLength(3)
    expect(meters).not.toHaveTextContent('0%')
    let res = screen.getByRole('group', { name: '服务器资源' })
    expect(within(res).getByRole('button', { name: '刷新资源' })).toBeDisabled()
    expect(res).toHaveTextContent('初始化完成后，平台每分钟用 SSH 采样一次 CPU、内存与硬盘。')
    first.unmount()

    // 初始化了，最近一次采样失败、从来没成功过
    s = readyServer({ resources: { sample: null, stale: true, attempted_at: at, error: 'unavailable' } })
    const second = await openApp('/agent-servers?server=srv-1')
    meters = await screen.findByRole('list', { name: '服务器概览' })
    expect(within(meters).getAllByText('采样失败')).toHaveLength(3)
    res = screen.getByRole('group', { name: '服务器资源' })
    expect(res).toHaveTextContent('最近一次采样失败：连不上服务器、登录失败或命令出错')
    second.unmount()

    // 有过成功的采样、这次刷新失败：保留原来的数值，说明原因
    s = readyServer({ resources: { ...sampled(), stale: true, error: 'unavailable' } })
    await openApp('/agent-servers?server=srv-1')
    meters = await screen.findByRole('list', { name: '服务器概览' })
    expect(cell(meters, 'CPU')).toHaveTextContent('23%')
    res = screen.getByRole('group', { name: '服务器资源' })
    expect(res).toHaveTextContent('（已过期） · 最近一次采样失败')
    await fireEvent.click(within(res).getByRole('button', { name: '刷新资源' }))
    expect(await screen.findByText(/这次没有采到资源/)).toBeVisible()
    expect(cell(meters, 'CPU')).toHaveTextContent('23%')
  })
})
