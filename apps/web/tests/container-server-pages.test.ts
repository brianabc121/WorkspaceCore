// 容器服务器页（T-0186；T-0277 按智能体服务器页的样式重排）：名称、统计卡（overview 全量）、服务器表与筛选、服务器详情（读数、告警、这台服务器上的容器）、
// 容器表的读数口径、服务端搜索 / 筛选 / 排序 / 分页与匹配总数、旧链接 ?tab=environments / ?tab=settings、智能体用量。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { openApp } from './helpers'
import { env, host, metrics, overview, params, path, setup } from './container-fixtures'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })
const listRequests = (requests: Request[]) => requests.filter(r => path(r) === '/api/v1/runner/environments' && r.method === 'GET')

describe('容器服务器页', () => {
  it('is called 容器服务器 and shows the server table, workspace-wide stat cards and four tabs', async () => {
    setup({ data: overview({ host: host({ runtimes: ['runc'] }) }) })
    await openApp('/runner')
    expect(await screen.findByRole('heading', { level: 1, name: '容器服务器' })).toBeVisible()
    expect(screen.getByRole('button', { name: '刷新' })).toBeVisible()
    expect(screen.getByRole('button', { name: '添加服务器' })).toBeVisible()
    const cards = await screen.findByRole('region', { name: '容器统计' })
    expect(within(cards).getByText('服务器').closest('div')).toHaveTextContent('1')
    expect(within(cards).getByText('在线').closest('div')).toHaveTextContent('1')
    // 未删除的环境记录：5 运行 + 2 停止 + 1 失败 + 1 归档 = 9（已删除的 4 个不算）
    expect(within(cards).getByText('容器环境').closest('div')).toHaveTextContent('9')
    expect(within(cards).getByText('运行中').closest('div')).toHaveTextContent('5')
    expect(within(cards).getByRole('note')).toHaveTextContent('1 个容器环境失败，需要关注。')
    const list = screen.getByRole('region', { name: '服务器列表' })
    const row = within(list).getByRole('row', { name: /容器节点 A/ })
    expect(row).toHaveTextContent('内网入口')
    expect(row).toHaveTextContent('16 核 · 32 GiB · 磁盘 500 GiB')
    expect(row).toHaveTextContent('没装 gVisor')
    expect(row).toHaveTextContent('在线')
    expect(row).toHaveTextContent('1 条告警')
    expect(within(list).getByText('显示 1 台，共 1 台')).toBeVisible()
    const tabs = within(screen.getByRole('tablist', { name: '容器服务器' })).getAllByRole('tab').map(t => t.textContent)
    expect(tabs).toEqual(['全部容器', '智能体用量', '环境模板', '执行记录'])
  })

  it('opens a server with its readings, alerts and only its own containers, and closes it again', async () => {
    const requests = setup({ data: overview({ host: host({ runtimes: ['runc'] }) }) })
    const { router } = await openApp('/runner')
    await fireEvent.click(within(await screen.findByRole('region', { name: '服务器列表' })).getByRole('button', { name: '查看：容器节点 A' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ host_id: 'host-1' }))
    const head = await screen.findByRole('region', { name: '容器服务器：容器节点 A' })
    expect(head).toHaveTextContent('Docker 29.9.1 · runner-agent 0.2.0 · 内网入口')
    const stats = within(head).getByRole('list', { name: '服务器概览' })
    expect(stats).toHaveTextContent('16 核')
    expect(stats).toHaveTextContent('负载 9%')
    expect(stats).toHaveTextContent('1 分钟负载 1.50')
    expect(stats).toHaveTextContent('12 GiB / 32 GiB')
    expect(stats).toHaveTextContent('86 GiB / 500 GiB')
    expect(stats).toHaveTextContent('没装 gVisor')
    expect(head).not.toHaveTextContent('MCP 管理器')  // 旧版 runner-agent 不报这个数量：不显示
    expect(within(head).getByRole('list', { name: '正在提醒的告警' })).toHaveTextContent('没装 gVisor')
    expect(within(head).getByRole('group', { name: '服务器读数' })).toHaveTextContent('磁盘里：镜像 30 GiB · 容器可写层 5 GiB · 数据卷 40 GiB')
    // 这台服务器上的容器：按 host_id 筛选；下面的四个标签收起来
    expect(await screen.findByRole('region', { name: '这台服务器上的容器' })).toBeVisible()
    await vi.waitFor(() => expect(listRequests(requests).some(r => params(r).get('host_id') === 'host-1')).toBe(true))
    expect(screen.queryByRole('tablist', { name: '容器服务器' })).not.toBeInTheDocument()
    await fireEvent.click(within(head).getByRole('button', { name: '关闭服务器详情' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({}))
    expect(await screen.findByRole('tab', { name: '全部容器', selected: true })).toBeVisible()
  })

  // T-0234：MCP 管理器是工作台另行部署的，不算“不是工作台建的容器”；runner-agent 单独报它们的数量
  it('mentions MCP managers apart from containers the workspace did not create', async () => {
    setup({ data: overview({ host: host({ report: { ...host().report, containers: 4, unlabeled_containers: 2, mcp_manager_containers: 1 } }) }) })
    await openApp('/runner?host_id=host-1')
    const head = await screen.findByRole('region', { name: '容器服务器：容器节点 A' })
    expect(within(head).getByRole('list', { name: '正在提醒的告警' })).toHaveTextContent('服务器上有 2 个不是工作台建的容器：容器服务器最好专用，工作台不会碰它们。另有 1 个 MCP 管理器（不受这里管理）。')
    expect(within(head).getByRole('list', { name: '服务器概览' })).toHaveTextContent('4 个另有 1 个 MCP 管理器')
  })

  it('raises no alert when the only other containers are MCP managers', async () => {
    setup({ data: overview({ host: host({ report: { ...host().report, containers: 0, unlabeled_containers: 0, mcp_manager_containers: 3 } }) }) })
    await openApp('/runner?host_id=host-1')
    const head = await screen.findByRole('region', { name: '容器服务器：容器节点 A' })
    expect(within(head).queryByRole('list', { name: '正在提醒的告警' })).not.toBeInTheDocument()
    expect(head).not.toHaveTextContent('不是工作台建的容器')
    expect(within(head).getByRole('list', { name: '服务器概览' })).toHaveTextContent('另有 3 个 MCP 管理器')
  })

  it('filters the server table by name and status', async () => {
    setup({ data: overview({ host: null, hosts: [host(), host({ id: 'host-2', name: '香港节点', status: 'offline' }), host({ id: 'host-3', name: '新机器', status: 'pending', runtimes: [], report: {}, cpus: null, mem_bytes: null, disk_bytes: null, disk_free_bytes: null, last_seen_at: null })] }) })
    await openApp('/runner')
    const list = await screen.findByRole('region', { name: '服务器列表' })
    const names = () => within(list).getAllByRole('row').slice(1).map(r => within(r).getByRole('rowheader').textContent)
    expect(names()).toEqual(['容器节点 A', '香港节点', '新机器'])
    expect(within(list).getByRole('row', { name: /新机器/ })).toHaveTextContent('等待上报')
    expect(within(list).getByRole('button', { name: '部署：新机器' })).toBeVisible()
    await fireEvent.update(within(list).getByLabelText('按状态筛选服务器'), 'attention')
    expect(names()).toEqual(['香港节点'])
    await fireEvent.update(within(list).getByLabelText('按状态筛选服务器'), 'all')
    await fireEvent.update(within(list).getByRole('searchbox', { name: '搜索服务器名称' }), '新')
    expect(names()).toEqual(['新机器'])
    expect(within(list).getByText('显示 1 台，共 3 台')).toBeVisible()
  })

  it('shows actual CPU and memory against their limits, the data volume, and unknown or stale readings as such', async () => {
    setup({ list: () => ({ total: 4, next_cursor: null, items: [
      env(),
      env({ id: 'e2', untrusted: { purpose: '依赖检查' }, state: 'stopped', lifetime: 'once', metrics: metrics({ cpu_status: 'not_running', cpu_used_cores: null, cpu_percent_of_limit: null, memory_status: 'not_running', memory_used_bytes: null, memory_percent_of_limit: null, data_volume_bytes: 430 * 1024 * 1024 }) }),
      env({ id: 'e3', untrusted: { purpose: '刚启动' }, metrics: metrics({ cpu_status: 'unavailable', cpu_used_cores: null, cpu_percent_of_limit: null, cpu_reported_at: null, memory_status: 'stale' }) }),
      env({ id: 'e4', untrusted: { purpose: '旧版服务端' }, owner_instance_id: null, metrics: null }),
    ] }) })
    await openApp('/runner')
    const table = await within(await screen.findByRole('region', { name: '容器列表' })).findByRole('table')
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

  it('maps the old ?tab=environments link to all containers and keeps the other parameters', async () => {
    const requests = setup()
    const { router } = await openApp('/runner?tab=environments&state=running')
    await vi.waitFor(() => expect(router.currentRoute.value.query.tab).toBeUndefined())
    expect(router.currentRoute.value.query.state).toBe('running')
    expect(await screen.findByRole('tab', { name: '全部容器', selected: true })).toBeVisible()
    await vi.waitFor(() => expect(listRequests(requests).some(r => params(r).get('state') === 'running')).toBe(true))
  })

  it('opens the server from the old ?tab=settings link (the first one when none is named)', async () => {
    setup({ data: overview({ host: null, hosts: [host(), host({ id: 'host-2', name: '容器节点 B' })] }) })
    const view = await openApp('/runner?tab=settings')
    await vi.waitFor(() => expect(view.router.currentRoute.value.query).toEqual({ host_id: 'host-1' }))
    expect(await screen.findByRole('region', { name: '容器服务器：容器节点 A' })).toBeVisible()
    view.unmount()
    const second = await openApp('/runner?tab=settings&host_id=host-2')
    await vi.waitFor(() => expect(second.router.currentRoute.value.query).toEqual({ host_id: 'host-2' }))
    expect(await screen.findByRole('region', { name: '容器服务器：容器节点 B' })).toBeVisible()
  })

  it('says so when the server in the link is gone', async () => {
    setup()
    await openApp('/runner?host_id=missing')
    expect(await screen.findByText('找不到这台容器服务器：可能已被移除，或你已无权访问。')).toBeVisible()
  })

  it('jumps from the failed note to the failed containers', async () => {
    setup()
    const { router } = await openApp('/runner?host_id=host-1')
    await fireEvent.click(within(await screen.findByRole('region', { name: '容器统计' })).getByRole('button', { name: '查看' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ state: 'failed' }))
    expect(await screen.findByRole('tab', { name: '全部容器', selected: true })).toBeVisible()
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

  it('says there is no server yet and opens the add form', async () => {
    setup({ data: overview({ host: null, environments: {}, instances: [], recent: [] }), list: () => ({ items: [], total: 0, next_cursor: null }) })
    await openApp('/runner')
    expect(await within(await screen.findByRole('region', { name: '服务器列表' })).findByText(/还没有容器服务器/)).toBeVisible()
    expect(within(screen.getByRole('region', { name: '容器统计' })).getByText('服务器').closest('div')).toHaveTextContent('0')
    await fireEvent.click(screen.getByRole('button', { name: '添加服务器' }))
    expect(await screen.findByRole('form', { name: '添加容器服务器' })).toBeVisible()
  })

  it('redirects members to forbidden', async () => {
    setup({ role: 'member' })
    const { router } = await openApp('/runner')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
  })
})
