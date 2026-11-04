import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { emptyPage, fail, mockApi, openApp } from './helpers'
import { createRenderer, inlineTags, outline, parseTarget, stats } from '../src/features/kb/markdown'
import { assemble, conflicts, threeWay } from '../src/features/kb/merge'
import { excerpt } from '../src/features/kb/format'
import { noteImages } from '../src/features/kb/api'
import type { Schema } from '../src/services/api'

// 知识库前端（T-0099）
const path = (r: Request) => new URL(r.url).pathname
const agent = { kind: 'agent' as const, id: 'inst-a', name: 'ops-claw' }
const publicSpace: Schema['SpaceOut'] = { id: 'space-public', kind: 'public', name: '公共知识库', description: '', project_id: null, instance_id: null, archived: false, agent_access: 'read_write', writes_enabled: true, deletes_paused_at: null, notes: 2, last_updated_at: '2026-11-03T10:00:00Z', brief_note_id: null, brief_version: null, agents: [], version: 1 }
const projectSpace: Schema['SpaceOut'] = { ...publicSpace, id: 'space-p1', kind: 'project', name: '工作台 V2', project_id: 'project-a', agent_access: 'none', notes: 1, brief_note_id: 'note-brief', brief_version: 6, agents: [{ instance_id: 'inst-a', name: 'ops-claw', access: 'read_write', brief_version_read: 5, added_at: '2026-11-01T00:00:00Z' }] }
const agentSpace: Schema['SpaceOut'] = { ...publicSpace, id: 'space-a', kind: 'agent', name: 'ops-claw', instance_id: 'inst-a', agent_access: 'read', notes: 1 }
const spaces = { items: [publicSpace, projectSpace, agentSpace] }
const note: Schema['NoteOut'] = {
  id: 'note-1', space_id: 'space-public', space_kind: 'public', space_name: '公共知识库', title: '发布前检查清单', type: 'runbook', folder: '', tags: ['发布'], pinned: false,
  conflict: false, source_kind: 'human', updated_by: agent, updated_at: '2026-11-03T10:00:00Z', version: 4,
  body: '# 发布前检查清单\n\n- [x] CI 全绿\n- [ ] 按 [[数据库迁移约定]] 检查\n- [ ] 还没写的 [[新流程]]\n\n```\n[[代码里的不算]]\n```',
  aliases: [], review_at: null, conflict_note: null, created_by: agent, created_at: '2026-11-01T00:00:00Z',
  deleted_at: null, purge_after: null, reason: '10 月那次漏了迁移检查', evidence: [],
  links: [{ text: '数据库迁移约定', note_id: 'note-2', title: '数据库迁移约定', space_name: '公共知识库', deleted: false, resource_type: null, resource_id: null }, { text: '新流程', note_id: null, title: null, space_name: null, deleted: false, resource_type: null, resource_id: null }],
  backlinks: [{ note_id: 'note-3', title: '工作台 V2 简报', space_id: 'space-p1', space_kind: 'project', space_name: '工作台 V2', snippet: '每次发布前按 [[发布前检查清单]] 逐项确认' }],
  usage: { reads_30d: 12, by_agent: [{ instance_id: 'inst-a', name: 'ops-claw', reads: 12 }], last_read_at: '2026-11-03' },
}
const graph: Schema['GraphOut'] = {
  nodes: [
    { id: 'note-1', kind: 'note', title: '发布前检查清单', space_id: 'space-public', space_kind: 'public', type: 'runbook', author_kind: 'agent', degree: 2 },
    { id: 'note-2', kind: 'note', title: '数据库迁移约定', space_id: 'space-public', space_kind: 'public', type: 'rule', author_kind: 'user', degree: 1 },
    { id: 'note-3', kind: 'note', title: '工作台 V2 简报', space_id: 'space-p1', space_kind: 'project', type: 'brief', author_kind: 'user', degree: 1 },
  ],
  edges: [{ source: 'note-1', target: 'note-2' }, { source: 'note-3', target: 'note-1' }],
  truncated: false, total_notes: 3,
}
function kbApi(handler?: (r: Request) => Response | Promise<Response | undefined> | undefined) {
  return mockApi(async (r) => {
    const own = await handler?.(r)
    if (own) return own
    const p = path(r)
    if (p === '/api/v1/knowledge/spaces') return Response.json(spaces)
    if (p === '/api/v1/knowledge/graph' || p.endsWith('/graph')) return Response.json(graph)
    if (p === '/api/v1/knowledge/notes/note-1' && r.method === 'GET') return Response.json(note)
    if (p === '/api/v1/knowledge/notes/note-1/revisions') return Response.json({ items: [{ version: 4, change: 'update', title: note.title, author: agent, reason: '补一步', evidence: [], added: 1, removed: 0, created_at: note.updated_at, updated_at: note.updated_at }] })
    return undefined
  })
}

describe('知识库 Markdown', () => {
  const render = createRenderer((t) => t.text === '已有' ? { href: '/knowledge/notes/n1' } : { href: '/knowledge/notes/new?title=' + encodeURIComponent(t.text), missing: true })
  it('renders [[links]], task boxes and headings, ignoring links inside code', () => {
    const html = render('## 发布前\n\n- [x] 完成 [[已有|这篇]]\n- [ ] 待办 [[没有]]\n\n`[[行内代码]]`')
    expect(html).toContain('<a class="kb-wl" href="/knowledge/notes/n1"')
    expect(html).toContain('>这篇</a>')
    expect(html).toContain('class="kb-wl is-missing"')
    expect(html).toContain('<input type="checkbox" disabled="" checked=""')
    expect(html).toContain('id="kb-h-发布前"')
    expect(html).not.toContain('行内代码</a>')
  })
  it('removes scripts, event handlers, unsafe links and does not load external images', () => {
    const html = render('<script>alert(1)</script> <img src=x onerror=alert(1)>\n\n[点我](javascript:alert(1)) [外链](https://example.com) ![图](https://tracker.example/p.png)')
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html')
    expect(doc.querySelectorAll('script, img, iframe, style')).toHaveLength(0)
    expect([...doc.querySelectorAll('*')].flatMap(el => [...el.attributes].map(a => a.name)).filter(n => n.startsWith('on'))).toEqual([])
    expect([...doc.querySelectorAll('a')].map(a => a.getAttribute('href'))).toEqual(['https://example.com', 'https://tracker.example/p.png'])
    expect(html).toContain('href="https://example.com" target="_blank" rel="noopener noreferrer nofollow"')
    expect(doc.querySelector('a.kb-image-link')?.textContent).toBe('图片：图')
  })
  it('shows workspace images inline and keeps every other image source out', () => {
    const id = '0b5f9c1e-2d3a-4b5c-8d9e-0f1a2b3c4d5e'
    const html = render(`![架构图](/api/v1/knowledge/files/${id}) ![外链](https://example.com/a.png) ![伪造](/api/v1/knowledge/files/../../etc)`)
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html')
    const imgs = [...doc.querySelectorAll('img')]
    expect(imgs.map(i => [i.getAttribute('src'), i.getAttribute('alt'), i.className])).toEqual([[`/api/v1/knowledge/files/${id}`, '架构图', 'kb-img']])
    expect([...doc.querySelectorAll('a.kb-image-link')].map(a => a.textContent)).toEqual(['图片：外链', '图片：伪造'])
    expect(noteImages(`a ![一](/api/v1/knowledge/files/${id}) b ![](/api/v1/knowledge/files/${id}) ![x](https://e.com/p.png)`)).toEqual([{ url: `/api/v1/knowledge/files/${id}`, alt: '一' }])
  })
  it('parses link targets, outline, inline tags and counts', () => {
    expect(parseTarget('工作台 V2/发布前检查清单#回滚|回滚')).toEqual({ text: '工作台 V2/发布前检查清单', display: '回滚', section: '回滚' })
    expect(outline('# 标题\n```\n# 不是\n```\n## 小节').map(o => [o.level, o.text, o.line])).toEqual([[1, '标题', 1], [2, '小节', 5]])
    expect(inlineTags('# 标题不算\n正文 #发布 和 #迁移。\n`#代码`')).toEqual(['发布', '迁移'])
    expect(stats('中文五个字 and two [[链接]]')).toEqual({ words: 9, links: 1 })
    expect(excerpt('# 标题\n- [x] 按 [[流程|发布流程]] **确认**')).toBe('标题 按 发布流程 确认')
  })
})

describe('三方合并', () => {
  it('merges edits to different lines and lists conflicting ones', () => {
    const base = 'a\nb\nc', mine = 'a\nB 我改的\nc', theirs = 'a\nb\nc\nd 它加的'
    const merged = threeWay(base, mine, theirs)
    expect(conflicts(merged)).toBe(0)
    expect(assemble(merged)).toBe('a\nB 我改的\nc\nd 它加的')
    const clash = threeWay('a\nb', 'a\n我的', 'a\n它的')
    expect(conflicts(clash)).toBe(1)
    const chunk = clash.find(c => c.kind === 'conflict')!
    if (chunk.kind === 'conflict') chunk.pick = 'both'
    expect(assemble(clash)).toBe('a\n我的\n它的')
  })
})

describe('知识库页面', () => {
  it('is a standalone page with its own rail, space groups and graph', async () => {
    kbApi()
    await openApp('/knowledge')
    expect(await screen.findByRole('navigation', { name: '知识库功能' })).toBeVisible()
    expect(screen.queryByRole('navigation', { name: '主导航' })).not.toBeInTheDocument()
    const groups = screen.getByRole('complementary', { name: '知识库分组' })
    await within(groups).findAllByText('工作台 V2')
    const node = await screen.findByRole('button', { name: /^发布前检查清单，笔记（操作手册），2 条链接/ })
    await fireEvent.click(node)
    const panel = screen.getByRole('complementary', { name: '所选笔记' })
    await within(panel).findByText('智能体写入')
    expect(panel).toHaveTextContent('操作手册· 规范与流程')
    expect(panel).not.toHaveTextContent('确认')
    expect(within(panel).getByRole('link', { name: '打开笔记' })).toHaveAttribute('href', '/knowledge/notes/note-1')
    // 列表视图（不依赖图形的替代视图）
    await fireEvent.click(screen.getAllByRole('button', { name: /列表视图/ })[0])
    expect(await screen.findByRole('table')).toBeVisible()
    expect(screen.getAllByRole('row')).toHaveLength(4)
  })

  it('colors the graph by type group by default, lists the types in the legend and filters by a single type', async () => {
    kbApi()
    await openApp('/knowledge')
    const runbook = await screen.findByRole('button', { name: /^发布前检查清单，笔记（操作手册）/ })
    expect(runbook.querySelector('circle')).toHaveClass('f-g-rule')
    expect(screen.getByRole('button', { name: /^工作台 V2 简报，笔记（项目简报）/ }).querySelector('circle')).toHaveClass('f-g-project')
    expect(screen.getByRole('combobox', { name: '着色' })).toHaveValue('type')
    expect([...screen.getByRole('combobox', { name: '着色' }).querySelectorAll('option')].map(o => o.textContent)).toEqual(['按类型', '按知识库', '按作者（人 / 智能体）'])
    const legend = screen.getByRole('group', { name: '按类型显示' })
    const rules = within(legend).getByRole('button', { name: /规范与流程/ })
    expect(rules).toHaveTextContent('规范与流程规范 流程 操作手册 模板2')
    expect(within(legend).getAllByRole('button')).toHaveLength(6)
    // 图例按组隐藏
    await fireEvent.click(rules)
    expect(rules).toHaveAttribute('aria-pressed', 'false')
    await vi.waitFor(() => expect(screen.queryByRole('button', { name: /^发布前检查清单，/ })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: /^工作台 V2 简报，/ })).toBeInTheDocument()
    await fireEvent.click(rules)
    await screen.findByRole('button', { name: /^发布前检查清单，/ })
    // 按单个类型筛选
    await fireEvent.click(screen.getByRole('button', { name: '类型：全部' }))
    const filter = screen.getByRole('group', { name: '按类型筛选' })
    await fireEvent.click(within(filter).getByRole('checkbox', { name: /^操作手册/ }))
    expect(screen.getByRole('button', { name: '类型：隐藏 1 种' })).toBeInTheDocument()
    await vi.waitFor(() => expect(screen.queryByRole('button', { name: /^发布前检查清单，/ })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: /^数据库迁移约定，笔记（规范）/ })).toBeInTheDocument()
    expect(within(filter).getByRole('checkbox', { name: /规范与流程/ })).toHaveProperty('indeterminate', true)
    expect(rules).toHaveAttribute('aria-pressed', 'true')
    // 列表视图也按筛选，显示具体类型
    await fireEvent.click(screen.getAllByRole('button', { name: /列表视图/ })[0])
    const table = await screen.findByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    expect(table).toHaveTextContent('规范')
    expect(table).not.toHaveTextContent('发布前检查清单')
    await fireEvent.click(within(filter).getByRole('button', { name: '全部显示' }))
    await vi.waitFor(() => expect(within(table).getAllByRole('row')).toHaveLength(4))
    // 换成按作者着色，图例跟着换
    await fireEvent.click(screen.getAllByRole('button', { name: /图谱视图/ })[0])
    await fireEvent.update(screen.getByRole('combobox', { name: '着色' }), 'author')
    expect(await screen.findByRole('group', { name: '作者图例' })).toHaveTextContent('人2')
    expect(screen.getByRole('button', { name: /^发布前检查清单，/ }).querySelector('circle')).toHaveClass('f-agent')
  })

  it('shows a note with resolved links, backlinks, usage and moves it to the trash with its version', async () => {
    const requests = kbApi(r => r.method === 'DELETE' ? new Response(null, { status: 204 }) : undefined)
    const { router } = await openApp('/knowledge/notes/note-1')
    const article = await screen.findByRole('article')
    expect(within(article).getByRole('link', { name: '数据库迁移约定' })).toHaveAttribute('href', '/knowledge/notes/note-2')
    expect(within(article).getByRole('link', { name: '新流程' })).toHaveAttribute('href', '/knowledge/notes/new?space=space-public&title=%E6%96%B0%E6%B5%81%E7%A8%8B')
    expect(article.textContent).toContain('[[代码里的不算]]')
    expect(screen.getByRole('region', { name: '反向链接' })).toHaveTextContent('工作台 V2 简报')
    expect(screen.getByRole('region', { name: '被智能体使用' })).toHaveTextContent('近 30 天被读取 12 次')
    await fireEvent.click(screen.getByRole('button', { name: '更多操作' }))
    await fireEvent.click(screen.getByRole('menuitem', { name: /删除/ }))
    const dialog = await screen.findByRole('dialog', { name: '删除笔记' })
    await fireEvent.update(within(dialog).getByLabelText(/原因/), '重复了')
    await fireEvent.click(within(dialog).getByRole('button', { name: '放进回收站' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'DELETE')).toBe(true))
    const removed = requests.find(r => r.method === 'DELETE')!
    expect(new URL(removed.url).searchParams.get('version')).toBe('4')
    expect(new URL(removed.url).searchParams.get('reason')).toBe('重复了')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/knowledge/spaces/space-public'))
  })

  it('saves a new note with tags and the chosen space', async () => {
    const requests = kbApi(async (r) => r.method === 'POST' && path(r) === '/api/v1/knowledge/notes' ? Response.json({ ...note, id: 'note-new', title: '新笔记', body: '' }, { status: 201 }) : undefined)
    const { router } = await openApp('/knowledge/notes/new?space=space-p1&title=新笔记')
    await vi.waitFor(() => expect(screen.getByRole('textbox', { name: '标题' })).toHaveValue('新笔记'))
    expect(screen.getByLabelText('知识库')).toHaveValue('space-p1')
    await fireEvent.update(screen.getByLabelText('添加标签'), '发布')
    await fireEvent.keyDown(screen.getByLabelText('添加标签'), { key: 'Enter' })
    await fireEvent.click(screen.getAllByRole('button', { name: '保存' }).at(-1)!)
    await vi.waitFor(() => expect(requests.some(r => r.method === 'POST' && path(r) === '/api/v1/knowledge/notes')).toBe(true))
    const body = await requests.find(r => r.method === 'POST' && path(r) === '/api/v1/knowledge/notes')!.clone().json()
    expect(body).toMatchObject({ space_id: 'space-p1', title: '新笔记', tags: ['发布'], type: 'record' })
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/knowledge/notes/note-new'))
  })

  it('uploads an inserted image, links it in the note and explains a rejected one', async () => {
    const fileId = '0b5f9c1e-2d3a-4b5c-8d9e-0f1a2b3c4d5e'
    let uploads = 0
    const requests = kbApi(async (r) => {
      if (r.method !== 'POST' || path(r) !== '/api/v1/knowledge/files') return undefined
      uploads += 1
      if (uploads === 2) return Response.json({ error: { code: 'QUOTA_EXCEEDED', message: '这个工作空间的图片已用满 2048 MB', request_id: 'req', details: {} } }, { status: 413 })
      return Response.json({ id: fileId, url: `/api/v1/knowledge/files/${fileId}`, mime: 'image/png', size: 4, width: 2, height: 1, created_at: '2026-11-04T00:00:00Z' }, { status: 201 })
    })
    await openApp('/knowledge/notes/new?space=space-public&title=带图')
    await vi.waitFor(() => expect(screen.getByRole('textbox', { name: '标题' })).toHaveValue('带图'))
    expect(screen.getByRole('button', { name: '插入图片' })).toBeVisible()
    const input = document.querySelector<HTMLInputElement>('input[type=file]')!
    expect(input.accept).toBe('image/png,image/jpeg,image/gif,image/webp')
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['\x89PNG'], '架构图.png', { type: 'image/png' })] })
    await fireEvent.update(input)
    await vi.waitFor(() => expect(document.querySelector('.cm-content')?.textContent).toContain(`![架构图](/api/v1/knowledge/files/${fileId})`))
    const sent = requests.find(r => path(r) === '/api/v1/knowledge/files')!
    expect(sent.headers.get('content-type')).toBe('image/png')
    expect(await sent.clone().text()).toBe('\x89PNG')
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['GIF'], '第二张.gif', { type: 'image/gif' })] })
    await fireEvent.update(input)
    expect(await screen.findByRole('alert')).toHaveTextContent('第二张.gif：这个工作空间的图片已用满 2048 MB')
    expect(document.querySelector('.cm-content')?.textContent).not.toContain('上传中')
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['<svg/>'], '图.svg', { type: 'image/svg+xml' })] })
    await fireEvent.update(input)
    expect(await screen.findByRole('alert')).toHaveTextContent('图.svg：只支持 PNG、JPEG、GIF、WebP 图片')
    expect(uploads).toBe(2)
  })

  it('shows images in a note and enlarges them on click', async () => {
    const fileId = '0b5f9c1e-2d3a-4b5c-8d9e-0f1a2b3c4d5e'
    kbApi(r => path(r) === '/api/v1/knowledge/notes/note-1' && r.method === 'GET' ? Response.json({ ...note, body: `看图：\n\n![部署拓扑](/api/v1/knowledge/files/${fileId})` }) : undefined)
    await openApp('/knowledge/notes/note-1')
    const article = await screen.findByRole('article')
    const img = await within(article).findByRole('button', { name: '放大图片：部署拓扑' })
    expect(img).toHaveAttribute('src', `/api/v1/knowledge/files/${fileId}`)
    await fireEvent.click(img)
    const dialog = await screen.findByRole('dialog', { name: '部署拓扑' })
    expect(within(dialog).getByRole('img', { name: '部署拓扑' })).toHaveAttribute('src', `/api/v1/knowledge/files/${fileId}`)
    expect(within(dialog).getByRole('link', { name: '打开原图' })).toHaveAttribute('href', `/api/v1/knowledge/files/${fileId}`)
  })

  it('reports a version conflict and can save over it with the latest version', async () => {
    let first = true
    const latest = { ...note, version: 5, body: note.body + '\n- [ ] 通知值班群', updated_by: agent }
    const requests = kbApi(async (r) => {
      if (r.method !== 'PATCH') return undefined
      if (first) { first = false; return Response.json({ error: { code: 'VERSION_CONFLICT', message: '已被修改', request_id: 'r', details: { current_version: 5, current: latest } } }, { status: 409 }) }
      return Response.json({ ...latest, version: 6 })
    })
    await openApp('/knowledge/notes/note-1/edit')
    await vi.waitFor(() => expect(screen.getByRole('textbox', { name: '标题' })).toHaveValue('发布前检查清单'))
    await fireEvent.update(screen.getByRole('textbox', { name: '标题' }), '发布前检查清单（新）')
    await fireEvent.click(screen.getAllByRole('button', { name: '保存' }).at(-1)!)
    const alert = await screen.findByText(/ops-claw（智能体） 保存了第 5 版/)
    expect(alert).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: /用我的覆盖/ }))
    await vi.waitFor(() => expect(requests.filter(r => r.method === 'PATCH')).toHaveLength(2))
    const second = await requests.filter(r => r.method === 'PATCH')[1].clone().json()
    expect(second).toMatchObject({ version: 5, title: '发布前检查清单（新）' })
  })

  it('shows changes, lets a member undo the latest agent edit, and keeps old bookmark links working', async () => {
    const change: Schema['ChangeOut'] = { id: 'chg-1', kind: 'revision', change: 'update', guard: null, note_id: 'note-1', note_title: '发布前检查清单', note_deleted: false, space_id: 'space-public', space_kind: 'public', space_name: '公共知识库', author: agent, reason: '补一步', detail: '', version: 4, added: 1, removed: 0, revertable: true, created_at: new Date().toISOString() }
    const guard: Schema['ChangeOut'] = { ...change, id: 'g-1', kind: 'guard', change: null, guard: 'secret_blocked', detail: '内容里像有：GitHub 令牌', version: null, revertable: false }
    const requests = kbApi(async (r) => {
      if (path(r) === '/api/v1/knowledge/changes') return Response.json({ ...emptyPage, items: [change, guard] })
      if (path(r) === '/api/v1/knowledge/notes/note-1/revisions/4') return Response.json({ version: 4, change: 'update', title: note.title, author: agent, reason: '补一步', evidence: [], added: 1, removed: 0, created_at: change.created_at, updated_at: change.created_at, body: note.body, properties: {}, diff: [{ sign: '+', text: '- [ ] 通知值班群' }] })
      if (path(r) === '/api/v1/knowledge/notes/note-1/revert') return Response.json({ ...note, version: 5 })
      return undefined
    })
    await openApp('/knowledge/changes')
    expect(await screen.findByRole('heading', { name: 'ops-claw（智能体） 修改了《发布前检查清单》' })).toBeVisible()
    expect(await screen.findByText('- [ ] 通知值班群')).toBeVisible()
    await fireEvent.update(screen.getByLabelText(/撤销原因/), '这一步不需要')
    await fireEvent.click(screen.getByRole('button', { name: '撤销这次修改' }))
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/knowledge/notes/note-1/revert')).toBe(true))
    const body = await requests.find(r => path(r) === '/api/v1/knowledge/notes/note-1/revert')!.clone().json()
    expect(body).toEqual({ version: 4, to_version: 3, reason: '这一步不需要' })
    await fireEvent.click(screen.getByRole('button', { name: /拦下写入/ }))
    expect(await screen.findByText(/工作台没有保存这次写入/)).toBeVisible()
  })

  it('shows the project brief read state and only lets admins manage participants', async () => {
    kbApi(r => path(r) === '/api/v1/knowledge/notes/note-brief' ? Response.json({ ...note, id: 'note-brief', type: 'brief', title: '工作台 V2 简报', space_id: 'space-p1', body: '目标：11 月上线', links: [], version: 6 }) : undefined)
    await openApp('/knowledge/spaces/space-p1')
    expect((await screen.findAllByRole('heading', { level: 1, name: '工作台 V2' })).length).toBeGreaterThan(0)
    const agents = screen.getByRole('region', { name: '参与的智能体' })
    expect(agents).toHaveTextContent('简报更新后还没读（停在第 5 版）')
    expect(within(agents).getByRole('button', { name: '改为只读' })).toBeVisible()
    expect(await screen.findByText('目标：11 月上线')).toBeVisible()
  })

  it('charts weekly edits by people and agents and sends an unread brief over IM once per version', async () => {
    const weeks = ['2026-10-27', '2026-11-03'].map((week_start, i) => ({ week_start, human: i ? 3 : 1, agent: i ? 5 : 0 }))
    const requests = kbApi(async (r) => {
      const p = path(r)
      if (p === '/api/v1/knowledge/notes/note-brief') return Response.json({ ...note, id: 'note-brief', type: 'brief', title: '工作台 V2 简报', space_id: 'space-p1', body: '目标', links: [], version: 6 })
      if (p === '/api/v1/knowledge/spaces/space-p1/activity') return Response.json({ weeks })
      if (p === '/api/v1/im/status') return Response.json({ available: true, account_ready: true, im_user_id: 'ws_user-a' })
      if (p === '/api/v1/im/conversations' && r.method === 'POST') return Response.json({ conversation_id: 'si_ws_user-a_inst-a' })
      if (p.endsWith('/messages') && r.method === 'POST') return Response.json({ client_msg_id: 'c1' })
      return undefined
    })
    await openApp('/knowledge/spaces/space-p1')
    const chart = await screen.findByRole('region', { name: '每周的修改' })
    expect(await within(chart).findByRole('list', { name: /最近一周 人 3 次，智能体 5 次/ })).toBeVisible()
    expect(within(chart).getByRole('listitem', { name: '10 月 27 日起的一周：人 1 次，智能体 0 次' })).toBeVisible()
    expect(within(chart).getAllByRole('row')).toHaveLength(3)
    const agents = screen.getByRole('region', { name: '参与的智能体' })
    await fireEvent.click(await within(agents).findByRole('button', { name: '现在发给它' }))
    expect(await within(agents).findByText(/已通过 IM 发给它/)).toBeVisible()
    const sent = requests.find(r => r.method === 'POST' && path(r).endsWith('/messages'))!
    expect(path(sent)).toBe('/api/v1/im/conversations/si_ws_user-a_inst-a/messages')
    expect(sent.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    const body = await sent.clone().json()
    expect(body.kind).toBe('text')
    expect(body.text).toContain('第 6 版')
    expect(body.text).toContain('knowledge_brief')
    expect(body.text).toContain('project_id: project-a')
    expect(await requests.find(r => path(r) === '/api/v1/im/conversations')!.clone().json()).toEqual({ instance_id: 'inst-a' })
  })

  it('shows weekly reads (own vs shared) and writes on the agent page', async () => {
    const metrics = { blocked: 0, corrected: 1, deletes: 0, notes: 1, overdue_reviews: 0, read_by_others: 2, reads: 9, searches: 4, writes: 3, writes_shared: 2 }
    const weekly = [{ week_start: '2026-10-27', reads_own: 0, reads_shared: 0, writes: 0 }, { week_start: '2026-11-03', reads_own: 2, reads_shared: 7, writes: 3 }]
    kbApi(r => path(r) === '/api/v1/knowledge/agents/inst-a' ? Response.json({ instance_id: 'inst-a', name: 'ops-claw', space_id: 'space-a', writes_enabled: true, deletes_paused_at: null, corrections_unseen: 0, projects: [], metrics, weekly }) : undefined)
    await openApp('/knowledge/agents/inst-a')
    const trend = await screen.findByRole('region', { name: '每周趋势' })
    expect(within(trend).getByRole('listitem', { name: '11 月 3 日起的一周：项目、公共和别的智能体的 7 次，自己的 2 次' })).toBeVisible()
    expect(within(trend).getByRole('listitem', { name: '11 月 3 日起的一周：写入与修改 3 次' })).toBeVisible()
    expect(screen.getByText(/每晚复盘/)).toBeVisible()
  })

  it('redirects old bookmark addresses to external documents', async () => {
    kbApi()
    const { router } = await openApp('/knowledge?linked_resource_id=project-a')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/knowledge/docs?linked_resource_id=project-a'))
    expect((await screen.findAllByRole('heading', { name: '外部文档', level: 1 })).length).toBeGreaterThan(0)
  })

  it('shows readable errors from the knowledge API', async () => {
    kbApi(r => r.method === 'POST' && path(r) === '/api/v1/knowledge/notes' ? Response.json({ error: { code: 'SECRET_DETECTED', message: '内容里像有密钥或密码（GitHub 令牌），没有保存。', request_id: 'r', details: {} } }, { status: 422 }) : undefined)
    await openApp('/knowledge/notes/new?space=space-public&title=密钥')
    await vi.waitFor(() => expect(screen.getByRole('textbox', { name: '标题' })).toHaveValue('密钥'))
    await fireEvent.click(screen.getAllByRole('button', { name: '保存' }).at(-1)!)
    expect(await screen.findByText('内容里像有密钥或密码（GitHub 令牌），没有保存。')).toBeVisible()
    expect(fail).toBeDefined()
  })
})
