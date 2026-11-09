import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { fail, me, mockApi, openApp, workspace } from './helpers'
import { useAuthStore } from '../src/stores/auth'
import type { Schema } from '../src/services/api'

// 知识总览（T-0173，二批四页改造规范 4.1–4.3）：统计只用服务端的全量数字；按 ID 选笔记；旧的 ?focus= 链接进完整图谱；
// 迟到的旧响应不覆盖当前范围；局部关联卸载时停掉力模拟与监听，并有列表替代。

// 记下力模拟的 stop：卸载时应再停一次
const sims = vi.hoisted(() => ({ stopped: 0 }))
vi.mock('d3-force', async (importOriginal) => {
  const actual = await importOriginal<typeof import('d3-force')>()
  return {
    ...actual,
    forceSimulation: (...args: Parameters<typeof actual.forceSimulation>) => {
      const sim = actual.forceSimulation(...args)
      const stop = sim.stop.bind(sim)
      sim.stop = () => { sims.stopped += 1; return stop() }
      return sim
    },
  }
})

const path = (r: Request) => new URL(r.url).pathname
const params = (r: Request) => new URL(r.url).searchParams
const user = { kind: 'user' as const, id: 'user-a', name: '测试用户' }
const agent = { kind: 'agent' as const, id: 'inst-a', name: '设计助手' }
const publicSpace: Schema['SpaceOut'] = { id: 'space-public', kind: 'public', name: '公共知识库', description: '', project_id: null, instance_id: null, archived: false, agent_access: 'read_write', writes_enabled: true, deletes_paused_at: null, notes: 2, last_updated_at: '2026-11-09T02:00:00Z', brief_note_id: null, brief_version: null, agents: [], version: 1 }
const projectSpace: Schema['SpaceOut'] = { ...publicSpace, id: 'space-p1', kind: 'project', name: '工作台改版', project_id: 'project-a', agent_access: 'none', notes: 1 }
const archivedSpace: Schema['SpaceOut'] = { ...publicSpace, id: 'space-old', kind: 'project', name: '旧项目', project_id: 'project-old', archived: true, agent_access: 'none', notes: 1 }
const spaces = { items: [publicSpace, projectSpace, archivedSpace] }

function summary(id: string, title: string, space: Schema['SpaceOut'], extra: Partial<Schema['NoteSummary']> = {}): Schema['NoteSummary'] {
  return { id, space_id: space.id, space_kind: space.kind, space_name: space.name, title, type: 'design', folder: '', tags: [], pinned: false, conflict: false, source_kind: 'human', updated_by: user, updated_at: '2026-11-09T02:00:00Z', version: 3, ...extra }
}
function full(note: Schema['NoteSummary'], body: string, extra: Partial<Schema['NoteOut']> = {}): Schema['NoteOut'] {
  return { ...note, body, aliases: [], review_at: null, conflict_note: null, created_by: note.updated_by, created_at: '2026-11-01T00:00:00Z', deleted_at: null, purge_after: null, reason: '', evidence: [], links: [], backlinks: [], usage: { reads_30d: 0, by_agent: [], last_read_at: null }, ...extra }
}
const counts = (note_count: number, space_count = 3, link_count = 0, updated_note_count = 0) => ({ note_count, space_count, link_count, updated_note_count })
const overviewOf = (s: ReturnType<typeof counts>, recent: Schema['NoteSummary'][]): Schema['KnowledgeOverviewOut'] => ({ summary: s, recent_notes: recent, generated_at: '2026-11-09T02:30:00Z' })
const node = (id: string, title: string, space: Schema['SpaceOut'], degree: number, type: Schema['GraphNode']['type'] = 'design'): Schema['GraphNode'] => ({ id, kind: 'note', title, space_id: space.id, space_kind: space.kind, type, author_kind: 'user', degree })

const home = summary('note-home', '首页改版方案', projectSpace, { updated_by: agent, tags: ['首页', '改版'] })
const spec = summary('note-spec', '设计规范', publicSpace, { type: 'rule' })
const contract = summary('note-api', '接口契约', publicSpace, { type: 'decision' })
const homeNote = full(home, '# 首页改版方案\n\n聚焦提升工作效率与**信息可达性**，见 [[设计规范]]。', {
  links: [{ text: '设计规范', note_id: spec.id, title: spec.title, space_name: spec.space_name, deleted: false, resource_type: null, resource_id: null }],
  backlinks: [{ note_id: contract.id, title: contract.title, space_id: publicSpace.id, space_kind: 'public', space_name: publicSpace.name, snippet: '按 [[首页改版方案]] 实施' }],
})
const homeGraph: Schema['GraphOut'] = {
  nodes: [node(home.id, home.title, projectSpace, 2), node(spec.id, spec.title, publicSpace, 1, 'rule'), node(contract.id, contract.title, publicSpace, 1, 'decision')],
  edges: [{ source: home.id, target: spec.id }, { source: contract.id, target: home.id }],
  truncated: false, total_notes: 3,
}

function api(handler?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => {
    const own = await handler?.(r)
    if (own) return own
    const p = path(r)
    if (p === '/api/v1/knowledge/spaces') return Response.json(spaces)
    if (p === '/api/v1/knowledge/overview') return Response.json(overviewOf(counts(2105, 7, 4321, 12), [home, spec, contract]))
    if (p === `/api/v1/knowledge/notes/${home.id}/graph`) return Response.json(homeGraph)
    if (p === `/api/v1/knowledge/notes/${home.id}`) return Response.json(homeNote)
    if (p === `/api/v1/knowledge/notes/${spec.id}`) return Response.json(full(spec, '设计规范正文'))
    if (p === `/api/v1/knowledge/notes/${spec.id}/graph`) return Response.json({ ...homeGraph, nodes: homeGraph.nodes.slice(0, 2), edges: homeGraph.edges.slice(0, 1) })
    // 总览不能去拿全量图谱顶替统计
    if (p === '/api/v1/knowledge/graph') return Response.json({ ...homeGraph, total_notes: 3 })
    return undefined
  })
}
const stat = (name: string) => screen.findByRole('group', { name })

describe('知识总览', () => {
  it('shows_server_totals_not_graph_size', async () => {
    let failFirst = true
    const requests = api((r) => {
      if (path(r) === '/api/v1/knowledge/overview' && failFirst) { failFirst = false; return fail(500, 'INTERNAL_ERROR') }
      return undefined
    })
    await openApp('/knowledge')
    // 读不到统计：四个数都是“—”（未知），给重试；不拿局部图谱的 3 篇顶替
    expect(await stat('笔记：未知')).toHaveTextContent('—')
    expect(screen.getByRole('group', { name: '本周更新：未知' })).toBeInTheDocument()
    const alert = await screen.findByText(/统计没有读到，数字显示为/)
    await fireEvent.click(within(alert).getByRole('button', { name: '重试' }))
    expect(await stat('笔记：2,105 篇')).toBeVisible()
    expect(screen.getByRole('group', { name: '知识库：7 个' })).toBeVisible()
    expect(screen.getByRole('group', { name: '笔记关联：4,321 条' })).toBeVisible()
    expect(screen.getByRole('group', { name: '本周更新：12 篇' })).toBeVisible()
    // 局部关联只有 3 个节点：图上写明是“当前关联”，不是全部
    const preview = screen.getByRole('region', { name: '知识关联' })
    await vi.waitFor(() => expect(preview).toHaveTextContent('以《首页改版方案》为中心的一层关联 · 3 篇笔记'))
    expect(preview).toHaveTextContent('当前关联')
    expect(preview).toHaveTextContent('只画与它直接相连的笔记；全部知识库共 2,105 篇')
    expect(within(preview).getByRole('list', { name: '图例（当前关联）' })).toHaveTextContent('规范与流程1')
    expect(screen.queryByRole('group', { name: '笔记：3 篇' })).not.toBeInTheDocument()
    expect(requests.some((r) => path(r) === '/api/v1/knowledge/graph')).toBe(false)
    // 时间窗：显示时区的周一 00:00 到下周一 00:00，带时区传给服务端
    const sent = params(requests.filter((r) => path(r) === '/api/v1/knowledge/overview').at(-1)!)
    const after = new Date(sent.get('updated_after')!), before = new Date(sent.get('updated_before')!)
    expect(sent.get('updated_after')).toMatch(/Z$/)
    expect([after.getDay(), after.getHours(), after.getMinutes()]).toEqual([1, 0, 0])
    expect([before.getDay(), before.getHours()]).toEqual([1, 0])
    expect(Math.round((before.getTime() - after.getTime()) / 86400000)).toBe(7)
    expect(sent.get('recent_limit')).toBe('6')
    expect(sent.has('space_id')).toBe(false)
    // 详情：真实的作者、标签、摘要与关联（纯文本，不渲染 Markdown）
    const detail = screen.getByRole('region', { name: '所选笔记' })
    await within(detail).findByRole('heading', { name: '首页改版方案' })
    expect(detail).toHaveTextContent('设计助手（智能体）')
    expect(detail).toHaveTextContent('#首页')
    expect(detail).toHaveTextContent('聚焦提升工作效率与信息可达性，见 设计规范。')
    expect(detail).toHaveTextContent('共 2 篇')
    expect(within(detail).getByRole('link', { name: '打开笔记' })).toHaveAttribute('href', `/knowledge/notes/${home.id}`)
    // 最近更新：服务端给的顺序，每篇一行
    const recent = screen.getByRole('region', { name: '最近更新' })
    expect(within(recent).getAllByRole('row')).toHaveLength(4)
  })

  it('same_title_uses_id_and_clears_lost_note', async () => {
    const weekly = summary('note-weekly-a', '周报', publicSpace)
    const twin = summary('note-weekly-b', '周报', projectSpace)
    const requests = api((r) => {
      const p = path(r)
      if (p === '/api/v1/knowledge/overview') return Response.json(overviewOf(counts(2, 3, 0, 2), [weekly, twin]))
      if (p === `/api/v1/knowledge/notes/${weekly.id}`) return Response.json(full(weekly, '公共知识库里的周报'))
      if (p === `/api/v1/knowledge/notes/${weekly.id}/graph`) return Response.json({ nodes: [node(weekly.id, '周报', publicSpace, 0)], edges: [], truncated: false, total_notes: 1 })
      if (p.startsWith(`/api/v1/knowledge/notes/${twin.id}`)) return fail(404, 'NOT_FOUND')
      return undefined
    })
    await openApp('/knowledge')
    const detail = await screen.findByRole('region', { name: '所选笔记' })
    expect(await within(detail).findByText('公共知识库里的周报')).toBeVisible()
    const rows = within(screen.getByRole('region', { name: '最近更新' })).getAllByRole('button', { name: '周报' })
    expect(rows.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false'])
    // 点第二篇同名笔记：按 ID 取它，读不到就清掉详情，不显示第一篇的内容
    await fireEvent.click(rows[1])
    await vi.waitFor(() => expect(requests.some((r) => path(r) === `/api/v1/knowledge/notes/${twin.id}`)).toBe(true))
    expect(await within(detail).findByText('这篇笔记已删除或你没有权限查看。')).toBeVisible()
    expect(detail).not.toHaveTextContent('公共知识库里的周报')
    expect(within(detail).queryByRole('link', { name: '打开笔记' })).not.toBeInTheDocument()
    expect(rows.map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'true'])
    expect(await screen.findByText('这篇笔记已删除或你没有权限查看，关联不再显示。')).toBeVisible()
    // 回到第一篇：照常显示
    await fireEvent.click(rows[0])
    expect(await within(detail).findByText('公共知识库里的周报')).toBeVisible()
  })

  it('skips a recent note that can no longer be read when choosing the default', async () => {
    const gone = summary('note-gone', '刚删的笔记', publicSpace)
    api((r) => {
      const p = path(r)
      if (p === '/api/v1/knowledge/overview') return Response.json(overviewOf(counts(2), [gone, spec]))
      if (p.startsWith(`/api/v1/knowledge/notes/${gone.id}`)) return fail(404, 'NOT_FOUND')
      return undefined
    })
    await openApp('/knowledge')
    const detail = await screen.findByRole('region', { name: '所选笔记' })
    expect(await within(detail).findByRole('heading', { name: '设计规范' })).toBeVisible()
    expect(detail).not.toHaveTextContent('刚删的笔记')
  })

  it('legacy_focus_keeps_graph_link', async () => {
    api()
    const { router } = await openApp('/knowledge?focus=space-p1&from=agent')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/knowledge/graph?focus=space-p1&from=agent'))
    const groups = await screen.findByRole('complementary', { name: '知识库分组' })
    // 知识库列表的按钮（role=listitem）：focus 的那个是按下状态
    const focused = (await within(groups).findAllByText('工作台改版')).map((el) => el.closest('button')).find(Boolean)
    expect(focused).toHaveAttribute('aria-pressed', 'true')
    const rail = screen.getByRole('navigation', { name: '知识库功能' })
    expect(within(rail).getByRole('link', { name: '知识总览' })).toHaveAttribute('href', '/knowledge')
    expect(within(rail).getByRole('link', { name: '知识图谱' })).toHaveAttribute('href', '/knowledge/graph')
    expect(within(rail).getByRole('link', { name: '知识图谱' })).toHaveAttribute('aria-current', 'page')
    // 总览：局部关联可以回到完整图谱；手机底部导航的四个入口不变
    await router.push('/knowledge')
    const preview = await screen.findByRole('region', { name: '知识关联' })
    expect(within(preview).getByRole('link', { name: '完整图谱' })).toHaveAttribute('href', '/knowledge/graph')
    expect(within(rail).getByRole('link', { name: '知识总览' })).toHaveAttribute('aria-current', 'page')
    const mobile = screen.getByRole('navigation', { name: '知识库导航' })
    expect(within(mobile).getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')])).toEqual([['知识库', '/knowledge'], ['图谱', '/knowledge/graph'], ['笔记', '/knowledge/notes'], ['变更', '/knowledge/changes']])
    expect(within(mobile).getByRole('link', { name: '知识库' })).toHaveAttribute('aria-current', 'page')
  })

  it('late_old_workspace_response_is_ignored', async () => {
    const releases: ((response: Response) => void)[] = []
    const late = (r: Request) => new Promise<Response>((resolve) => { releases.push(resolve); void r })
    const requests = api((r) => {
      const p = path(r), ws = r.headers.get('X-Workspace-Id'), space = params(r).get('space_id')
      if (p === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: '空间 B' }] })
      if (p !== '/api/v1/knowledge/overview') return undefined
      if (ws === 'workspace-a') return late(r)
      if (space === projectSpace.id) return late(r)
      if (space === publicSpace.id) return Response.json(overviewOf(counts(2, 1, 1, 1), []))
      return Response.json(overviewOf(counts(20, 3, 5, 4), []))
    })
    const { pinia } = await openApp('/knowledge')
    await vi.waitFor(() => expect(releases).toHaveLength(1))
    useAuthStore(pinia).selectWorkspace('workspace-b')
    expect(await stat('笔记：20 篇')).toBeVisible()
    // 旧工作空间的响应迟到：只落进它自己的查询键，不显示到空间 B
    releases[0](Response.json(overviewOf(counts(999, 9, 9, 9), [])))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(screen.getByRole('group', { name: '笔记：20 篇' })).toBeVisible()
    expect(screen.queryByRole('group', { name: '笔记：999 篇' })).not.toBeInTheDocument()
    // 快速切知识库：先点的那个响应迟到，也不覆盖后点的
    const groups = screen.getByRole('complementary', { name: '知识库分组' })
    const spaceButton = async (name: string) => (await within(groups).findByText(name)).closest('button')!
    await fireEvent.click(await spaceButton('工作台改版'))
    await vi.waitFor(() => expect(releases).toHaveLength(2))
    await fireEvent.click(await spaceButton('公共知识库'))
    expect(await stat('笔记：2 篇')).toBeVisible()
    expect(screen.getByText('范围：公共知识库')).toBeVisible()
    releases[1](Response.json(overviewOf(counts(777, 1, 7, 7), [])))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(screen.getByRole('group', { name: '笔记：2 篇' })).toBeVisible()
    expect(screen.queryByRole('group', { name: '笔记：777 篇' })).not.toBeInTheDocument()
    const sent = requests.filter((r) => path(r) === '/api/v1/knowledge/overview').map((r) => [r.headers.get('X-Workspace-Id'), params(r).get('space_id')])
    expect(sent).toEqual([['workspace-a', null], ['workspace-b', null], ['workspace-b', projectSpace.id], ['workspace-b', publicSpace.id]])
  })

  it('preview_disposes_simulation_and_has_list_fallback', async () => {
    let observed = 0, disconnected = 0
    vi.stubGlobal('ResizeObserver', class { observe() { observed += 1 } unobserve() {} disconnect() { disconnected += 1 } })
    const requests = api()
    const { router } = await openApp('/knowledge')
    const preview = await screen.findByRole('region', { name: '知识关联' })
    expect(await within(preview).findByRole('button', { name: /^首页改版方案，笔记（设计）/ })).toBeInTheDocument()
    // 文字替代：当前关联的笔记列表；点一篇换成以它为中心
    await fireEvent.click(within(preview).getByRole('button', { name: '列表' }))
    const list = within(preview).getByRole('list', { name: '当前关联的笔记' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(list).toHaveTextContent('首页改版方案（中心）')
    await fireEvent.click(within(list).getByRole('button', { name: '设计规范' }))
    await vi.waitFor(() => expect(requests.some((r) => path(r) === `/api/v1/knowledge/notes/${spec.id}/graph` && params(r).get('depth') === '1')).toBe(true))
    expect(await within(screen.getByRole('region', { name: '所选笔记' })).findByRole('heading', { name: '设计规范' })).toBeVisible()
    await fireEvent.click(within(preview).getByRole('button', { name: '列表' }))
    expect(await within(preview).findByRole('button', { name: /^设计规范，笔记（规范）/ })).toBeInTheDocument()
    expect(observed).toBeGreaterThan(0)
    // 离开总览：图卸载，断开尺寸监听，停掉力模拟
    const stoppedBefore = sims.stopped
    await router.push('/knowledge/changes')
    await vi.waitFor(() => expect(screen.queryByRole('region', { name: '知识关联' })).not.toBeInTheDocument())
    expect(disconnected).toBeGreaterThan(0)
    expect(sims.stopped).toBeGreaterThan(stoppedBefore)
  })

  it('shows edit for people on archived spaces as before, and only reading for viewers', async () => {
    const old = summary('note-old', '旧项目复盘', archivedSpace, { type: 'review' })
    api((r) => {
      const p = path(r)
      if (p === '/api/v1/knowledge/overview') return Response.json(overviewOf(counts(1, 1), [old]))
      if (p === `/api/v1/knowledge/notes/${old.id}`) return Response.json(full(old, '复盘内容'))
      if (p === `/api/v1/knowledge/notes/${old.id}/graph`) return Response.json({ nodes: [node(old.id, old.title, archivedSpace, 0, 'review')], edges: [], truncated: false, total_notes: 1 })
      return undefined
    })
    await openApp('/knowledge')
    const detail = await screen.findByRole('region', { name: '所选笔记' })
    await within(detail).findByText('复盘内容')
    expect(detail).toHaveTextContent('已归档')
    expect(screen.getByRole('region', { name: '最近更新' })).toHaveTextContent('旧项目 · 已归档')
    // 归档知识库只限制智能体；人照现有规则可以编辑
    expect(within(detail).getByRole('link', { name: '编辑' })).toHaveAttribute('href', `/knowledge/notes/${old.id}/edit`)
    expect(screen.getAllByRole('link', { name: '新建笔记' }).length).toBeGreaterThan(0)
  })

  it('lets a viewer read the overview without edit or create actions', async () => {
    api((r) => (path(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] }) : undefined))
    await openApp('/knowledge')
    const detail = await screen.findByRole('region', { name: '所选笔记' })
    await within(detail).findByRole('heading', { name: '首页改版方案' })
    expect(within(detail).getByRole('link', { name: '打开笔记' })).toBeVisible()
    expect(within(detail).queryByRole('link', { name: '编辑' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '新建笔记' })).not.toBeInTheDocument()
    expect(await stat('笔记：2,105 篇')).toBeVisible()
  })
})
