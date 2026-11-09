// 容器服务器页（T-0186，docs/v2/container-server-redesign）：名称、主机条、统计卡（overview 全量）、容器表的读数口径、
// 服务端搜索 / 筛选 / 排序 / 分页与匹配总数、旧链接 ?tab=environments、智能体用量。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { openApp } from './helpers'
import { env, host, metrics, overview, params, path, setup } from './container-fixtures'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })
const listRequests = (requests: Request[]) => requests.filter(r => path(r) === '/api/v1/runner/environments' && r.method === 'GET')

describe('容器服务器页', () => {
  it('is called 容器服务器 and shows the host strip, workspace-wide stat cards and five tabs', async () => {
    setup({ data: overview({ host: host({ runtimes: ['runc'] }) }) })
    await openApp('/runner')
    expect(await screen.findByRole('heading', { level: 1, name: '容器服务器' })).toBeVisible()
    const strip = await screen.findByRole('region', { name: '容器服务器主机' })
    expect(within(strip).getByText('容器节点 A')).toBeVisible()
    expect(within(strip).getByText('在线')).toBeVisible()
    expect(strip).toHaveTextContent('16 核 / 32 GiB')
    expect(strip).toHaveTextContent('磁盘 86 GiB / 500 GiB')
    expect(strip).toHaveTextContent('gVisor 未安装')
    expect(within(strip).getByRole('list', { name: '告警' })).toHaveTextContent('没装 gVisor')
    const cards = screen.getByRole('region', { name: '容器统计' })
    // 未删除的环境记录：5 运行 + 2 停止 + 1 失败 + 1 归档 = 9（已删除的 4 个不算）
    expect(within(cards).getByText('容器环境总数').closest('div')).toHaveTextContent('9')
    expect(within(cards).getByText('运行中').closest('div')).toHaveTextContent('5')
    expect(within(cards).getByText('已停止').closest('div')).toHaveTextContent('2')
    expect(within(cards).getByText('需关注').closest('div')).toHaveTextContent('1')
    const tabs = within(screen.getByRole('tablist', { name: '容器服务器' })).getAllByRole('tab').map(t => t.textContent)
    expect(tabs).toEqual(['容器总览', '智能体用量', '环境模板', '执行记录', '服务器设置'])
    expect(screen.getByRole('link', { name: /管理申请权限/ })).toHaveAttribute('href', '/agents')
  })

  it('shows actual CPU and memory against their limits, the data volume, and unknown or stale readings as such', async () => {
    setup({ list: () => ({ total: 4, next_cursor: null, items: [
      env(),
      env({ id: 'e2', untrusted: { purpose: '依赖检查' }, state: 'stopped', lifetime: 'once', metrics: metrics({ cpu_status: 'not_running', cpu_used_cores: null, cpu_percent_of_limit: null, memory_status: 'not_running', memory_used_bytes: null, memory_percent_of_limit: null, data_volume_bytes: 430 * 1024 * 1024 }) }),
      env({ id: 'e3', untrusted: { purpose: '刚启动' }, metrics: metrics({ cpu_status: 'unavailable', cpu_used_cores: null, cpu_percent_of_limit: null, cpu_reported_at: null, memory_status: 'stale' }) }),
      env({ id: 'e4', untrusted: { purpose: '旧版服务端' }, owner_instance_id: null, metrics: null }),
    ] }) })
    await openApp('/runner')
    const table = await screen.findByRole('table')
    const row = (name: string) => within(table).getByRole('row', { name: new RegExp(name) })
    expect(row('前端构建')).toHaveTextContent('0.42 / 2 核')
    expect(row('前端构建')).toHaveTextContent('21%')
    expect(row('前端构建')).toHaveTextContent('640 / 2048 MiB')
    expect(row('前端构建')).toHaveTextContent('31%')
    expect(row('前端构建')).toHaveTextContent('1.2 GiB')
    expect(within(row('前端构建')).getAllByRole('meter')).toHaveLength(2)
    // 停止的环境：CPU / 内存不显示实时数字，数据卷还在
    expect(within(row('依赖检查')).queryAllByRole('meter')).toHaveLength(0)
    expect(row('依赖检查')).toHaveTextContent('430 MiB')
    expect(row('依赖检查')).toHaveTextContent('一次性')
    expect(row('依赖检查')).toHaveTextContent('已停止')
    // 首样本：未知，不是 0；内存过期：保留数字并标出
    expect(row('刚启动')).toHaveTextContent('未知')
    expect(row('刚启动')).toHaveTextContent('已过期')
    // 旧版服务端没有 metrics：CPU 未采集，原智能体已删除不挂到别人名下
    expect(row('旧版服务端')).toHaveTextContent('未采集')
    expect(row('旧版服务端')).toHaveTextContent('原智能体已删除')
    expect(row('旧版服务端')).toHaveTextContent('时间未知')
  })

  it('uses the server total, pages through with cursors and keeps the cards on the workspace-wide numbers', async () => {
    const items = Array.from({ length: 50 }, (_, i) => env({ id: `p1-${i}`, untrusted: { purpose: `批量 ${i}` } }))
    const requests = setup({ list: r => params(r).get('cursor') === 'c2'
      ? { total: 501, next_cursor: 'c3', items: [env({ id: 'p2-0', untrusted: { purpose: '第二页的第一个' } })] }
      : { total: 501, next_cursor: 'c2', items } })
    await openApp('/runner')
    expect(await screen.findByText('当前工作空间 501 个')).toBeVisible()
    expect(screen.getByText('显示 1–50，共 501 个')).toBeVisible()
    expect(params(listRequests(requests)[0]).get('limit')).toBe('50')
    await fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('第二页的第一个')).toBeVisible()
    expect(screen.getByText('显示 51–51，共 501 个')).toBeVisible()
    expect(params(listRequests(requests).at(-1)!).get('cursor')).toBe('c2')
    await fireEvent.click(screen.getByRole('button', { name: '上一页' }))
    expect(await screen.findByText('批量 0')).toBeVisible()
  })

  it('searches, filters and sorts on the server and shows the matching total against the workspace', async () => {
    const requests = setup({ list: r => params(r).get('instance_id') === 'inst-a'
      ? { total: 4, next_cursor: null, items: [env()] }
      : { total: 9, next_cursor: null, items: [env()] } })
    const { router } = await openApp('/runner')
    await screen.findByText('当前工作空间 9 个')
    await fireEvent.update(screen.getByLabelText('按申请智能体筛选'), 'inst-a')
    expect(await screen.findByText('筛选结果 4 个 / 工作空间 9 个')).toBeVisible()
    expect(router.currentRoute.value.query.instance_id).toBe('inst-a')
    await fireEvent.update(screen.getByLabelText('排序'), 'cpu')
    await vi.waitFor(() => expect(listRequests(requests).some(r => params(r).get('sort') === 'cpu' && params(r).get('instance_id') === 'inst-a')).toBe(true))
    expect(screen.getByText(/过期、未知、未运行的排在最后/)).toBeVisible()
    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索容器用途或环境 ID' }), '前端')
    await vi.waitFor(() => expect(listRequests(requests).some(r => params(r).get('q') === '前端')).toBe(true), { timeout: 2000 })
    expect(router.currentRoute.value.query.q).toBe('前端')
  })

  it('does not pretend to page or count everything when an old server gives no total', async () => {
    setup({ list: () => ({ items: [env(), env({ id: 'x2', untrusted: { purpose: '另一个' } })] }) })
    await openApp('/runner')
    expect(await screen.findByText('已加载 2 个，总量未提供')).toBeVisible()
    expect(screen.queryByRole('button', { name: '下一页' })).toBeNull()
  })

  it('maps the old ?tab=environments link to the container overview and keeps the other parameters', async () => {
    const requests = setup()
    const { router } = await openApp('/runner?tab=environments&state=running')
    await vi.waitFor(() => expect(router.currentRoute.value.query.tab).toBeUndefined())
    expect(router.currentRoute.value.query.state).toBe('running')
    expect(await screen.findByRole('tab', { name: '容器总览', selected: true })).toBeVisible()
    await vi.waitFor(() => expect(listRequests(requests).some(r => params(r).get('state') === 'running')).toBe(true))
  })

  it('shows allocated (not measured) usage per agent and jumps to its containers', async () => {
    const requests = setup()
    const { router } = await openApp('/runner?tab=instances')
    const panel = await screen.findByRole('region', { name: '智能体用量' })
    expect(panel).toHaveTextContent('已分配额度 / 上限')
    const row = within(panel).getByRole('row', { name: /开发助手/ })
    expect(row).toHaveTextContent('2 / 3')
    expect(row).toHaveTextContent('4096 / 16384')
    expect(within(panel).getByRole('row', { name: /新闻助手/ })).toHaveTextContent('已关闭')
    await fireEvent.click(within(row).getByRole('button', { name: '查看容器：开发助手' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query).toMatchObject({ instance_id: 'inst-a' }))
    expect(router.currentRoute.value.query.tab).toBeUndefined()
    await vi.waitFor(() => expect(listRequests(requests).some(r => params(r).get('instance_id') === 'inst-a')).toBe(true))
  })

  it('guides to the settings when there is no host yet', async () => {
    setup({ data: overview({ host: null, environments: {}, instances: [], recent: [] }), list: () => ({ items: [], total: 0, next_cursor: null }) })
    const { router } = await openApp('/runner')
    await fireEvent.click(await screen.findByRole('button', { name: '去添加容器服务器' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.tab).toBe('settings'))
    expect(screen.getByRole('region', { name: '容器统计' })).toHaveTextContent('0')
  })

  it('redirects members to forbidden', async () => {
    setup({ role: 'member' })
    const { router } = await openApp('/runner')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
  })
})
