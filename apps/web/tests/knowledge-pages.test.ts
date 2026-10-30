import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, emptyPage, me, workspace, fail } from './helpers'
import { bookmark, source } from './sprint4-fixtures'
import { project, task } from './sprint3-fixtures'
const path = (r: Request) => new URL(r.url).pathname
function knowledge(override?: (r: Request) => Response | Promise<Response | undefined> | undefined) {
  return mockApi(async (r) => {
    const response = await override?.(r)
    if (response) return response
    if (path(r) === '/api/v1/knowledge/sources') return Response.json({ ...emptyPage, items: [source] })
    if (path(r) === '/api/v1/knowledge/resources') return Response.json({ ...emptyPage, items: [bookmark] })
  })
}
describe('知识入口与收藏', () => {
  it('shows only bookmark metadata and safe links without fetching AFFiNE', async () => {
    const requests = knowledge((r) => path(r) === '/api/v1/knowledge/resources' ? Response.json({ ...emptyPage, items: [{ ...bookmark, note: '<img src=x onerror=alert(1)>' }] }) : undefined)
    await openApp('/knowledge')
    expect(await screen.findByText('发布文档')).toBeVisible()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeVisible()
    expect(document.querySelector('.knowledge-list img')).toBeNull()
    expect(screen.getByRole('link', { name: /在 AFFiNE 中打开/ })).toHaveAttribute('href', bookmark.url)
    expect(requests.every((r) => new URL(r.url).origin === window.location.origin)).toBe(true)
  })
  it('validates the document origin then creates a bookmark with resource associations', async () => {
    let saved = false
    const requests = knowledge(async (r) => {
      if (r.method === 'POST') { saved = true; return Response.json({ ...bookmark, title: '新文档' }, { status: 201 }) }
      if (saved && path(r) === '/api/v1/knowledge/resources') return Response.json({ ...emptyPage, items: [{ ...bookmark, title: '新文档' }] })
    })
    await openApp('/knowledge'); await screen.findByText('发布文档')
    await fireEvent.click(screen.getByRole('button', { name: '新增收藏' }))
    await fireEvent.update(screen.getByLabelText('收藏标题'), '新文档')
    await fireEvent.update(screen.getByLabelText('文档链接'), 'https://other.example.com/workspace/team/release')
    await fireEvent.click(screen.getByRole('button', { name: '保存收藏' }))
    expect(await screen.findByText(/文档地址必须属于/)).toBeVisible()
    expect(requests.filter((r) => r.method === 'POST')).toHaveLength(0)
    await fireEvent.update(screen.getByLabelText('文档链接'), bookmark.url)
    await vi.waitFor(() => expect(screen.getByLabelText('关联项目与任务')).toHaveTextContent(project.title))
    await fireEvent.update(screen.getByLabelText('关联项目与任务'), project.id)
    await fireEvent.click(screen.getByRole('button', { name: '保存收藏' }))
    expect(await screen.findByText('新文档')).toBeVisible()
    const request = requests.find((r) => r.method === 'POST')!
    expect(request.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(await request.clone().json()).toMatchObject({ title: '新文档', url: bookmark.url, linked_resource_ids: [project.id] })
  })
  it('preserves unloaded associations and does not send visibility for another creator', async () => {
    const linked = { resource_id: '20000000-0000-4000-8000-000000000001', resource_type: 'task' as const, title: '较旧关联任务', source_url: task.source_url }
    const second = { ...linked, resource_id: '20000000-0000-4000-8000-000000000002', title: '第二个旧任务' }
    const requests = knowledge((r) => {
      if (r.method === 'PATCH') return Response.json(bookmark)
      if (path(r) === '/api/v1/knowledge/resources') return Response.json({ ...emptyPage, items: [{ ...bookmark, owner_id: 'another-user', links: [linked, second] }] })
    })
    await openApp('/knowledge'); await screen.findByText('发布文档')
    await fireEvent.click(screen.getByRole('button', { name: '编辑收藏 发布文档' }))
    expect(screen.getByLabelText('收藏可见性')).toBeDisabled()
    expect([...(screen.getByLabelText('关联项目与任务') as HTMLSelectElement).selectedOptions].map((option) => option.value)).toEqual([linked.resource_id, second.resource_id])
    await fireEvent.update(screen.getByLabelText('收藏标题'), '新标题')
    await fireEvent.click(screen.getByRole('button', { name: '保存收藏' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    const body = await requests.find((r) => r.method === 'PATCH')!.clone().json()
    expect(body).toMatchObject({ version: 2, title: '新标题', linked_resource_ids: [linked.resource_id, second.resource_id] })
    expect(body.visibility).toBeUndefined()
  })
  it('filters by projected project, encodes opaque cursors and resets pagination', async () => {
    const requests = knowledge((r) => {
      if (path(r) !== '/api/v1/knowledge/resources') return
      const next = new URL(r.url).searchParams.has('cursor')
      return Response.json({ ...emptyPage, items: [next ? { ...bookmark, id: 'next', title: '下一页文档' } : bookmark], next_cursor: next ? null : 'cursor+/=' })
    })
    await openApp('/knowledge'); await screen.findByText('发布文档')
    await fireEvent.click(screen.getByRole('button', { name: '加载更多收藏' }))
    expect(await screen.findByText('下一页文档')).toBeVisible()
    expect(new URL(requests.filter((r) => path(r).endsWith('/resources'))[1]!.url).searchParams.get('cursor')).toBe('cursor+/=')
    await fireEvent.update(screen.getByLabelText('关联项目筛选'), project.id)
    await vi.waitFor(() => expect(screen.queryByText('下一页文档')).not.toBeInTheDocument())
    const query = new URL(requests.filter((r) => path(r).endsWith('/resources')).at(-1)!.url).searchParams
    expect(query.get('linked_resource_id')).toBe(project.id); expect(query.has('cursor')).toBe(false)
  })
  it('requires confirmation for bookmark deletion and sends its version', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const requests = knowledge((r) => r.method === 'DELETE' ? new Response(null, { status: 204 }) : undefined)
    await openApp('/knowledge'); await screen.findByText('发布文档')
    await fireEvent.click(screen.getByRole('button', { name: '删除收藏 发布文档' }))
    expect(requests.filter((r) => r.method === 'DELETE')).toHaveLength(0)
    confirm.mockReturnValue(true)
    await fireEvent.click(screen.getByRole('button', { name: '删除收藏 发布文档' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'DELETE')).toBe(true))
    expect(new URL(requests.find((r) => r.method === 'DELETE')!.url).searchParams.get('version')).toBe('2')
  })
  it('shows server validation reasons without echoing the rejected URL', async () => {
    knowledge((r) => r.method === 'POST' ? Response.json({ error: { code: 'VALIDATION_FAILED', message: 'rejected', request_id: 'req', details: { reason: 'not_a_document' } } }, { status: 422 }) : undefined)
    await openApp('/knowledge'); await screen.findByText('发布文档')
    await fireEvent.click(screen.getByRole('button', { name: '新增收藏' }))
    await fireEvent.update(screen.getByLabelText('收藏标题'), '新文档'); await fireEvent.update(screen.getByLabelText('文档链接'), bookmark.url)
    await fireEvent.click(screen.getByRole('button', { name: '保存收藏' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/文档链接|功能页/)
  })
  it('keeps conflicts locked until a successful reload closes the stale editor', async () => {
    const requests = knowledge((r) => r.method === 'PATCH' ? fail(409, 'VERSION_CONFLICT') : undefined)
    await openApp('/knowledge'); await screen.findByText('发布文档')
    await fireEvent.click(screen.getByRole('button', { name: '编辑收藏 发布文档' }))
    await fireEvent.update(screen.getByLabelText('收藏标题'), '冲突修改')
    await fireEvent.click(screen.getByRole('button', { name: '保存收藏' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '保存收藏' })).toBeDisabled())
    await fireEvent.click(screen.getByRole('button', { name: '重新加载知识' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '新增收藏' })).toBeEnabled()
    expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(1)
  })
  it('hides cached documents after access is revoked', async () => {
    let revoked = false
    knowledge((r) => path(r) === '/api/v1/knowledge/resources' && revoked ? fail(403, 'FORBIDDEN') : undefined)
    await openApp('/knowledge'); await screen.findByText('发布文档')
    revoked = true; await fireEvent.click(screen.getByRole('button', { name: '刷新收藏' }))
    await vi.waitFor(() => expect(screen.queryByText('发布文档')).not.toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('没有查看')
  })
  it('does not retain a prior workspace editor or late result in a viewer workspace', async () => {
    let finish: ((r: Response) => void) | undefined
    const requests = knowledge((r) => {
      if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: '只读空间', role: 'viewer' }] })
      if (path(r) === '/api/v1/knowledge/resources') return Response.json({ ...emptyPage, items: [{ ...bookmark, title: r.headers.get('X-Workspace-Id') === 'workspace-b' ? 'B文档' : 'A文档' }] })
      if (r.method === 'PATCH') return new Promise<Response>((resolve) => { finish = resolve })
    })
    await openApp('/knowledge'); await screen.findByText('A文档')
    await fireEvent.click(screen.getByRole('button', { name: '编辑收藏 A文档' })); await fireEvent.update(screen.getByLabelText('收藏标题'), 'A修改')
    await fireEvent.click(screen.getByRole('button', { name: '保存收藏' })); await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    expect(await screen.findByText('B文档')).toBeVisible(); finish!(Response.json(bookmark))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: '新增收藏' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '管理知识来源' })).not.toBeInTheDocument()
    expect(requests.find((r) => r.method === 'PATCH')!.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })
  it('configures the source and explains cascade deletion of all bookmarks', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const requests = knowledge((r) => r.method === 'PATCH' ? Response.json(source) : undefined)
    await openApp('/knowledge'); await screen.findByText('发布文档')
    await fireEvent.click(screen.getByRole('button', { name: '管理知识来源' }))
    await fireEvent.update(screen.getByLabelText('AFFiNE 外部地址'), 'https://docs.example.com/affine')
    await fireEvent.click(screen.getByRole('button', { name: '保存知识来源' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toMatchObject({ version: 1, base_url: 'https://docs.example.com/affine' })
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await fireEvent.click(screen.getByRole('button', { name: '管理知识来源' })); await fireEvent.click(screen.getByRole('button', { name: '删除知识来源' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/1.*收藏.*私有/))
    expect(requests.some((r) => r.method === 'DELETE')).toBe(false)
  })
  it('loads related documents from the task projection id', async () => {
    const requests = knowledge((r) => path(r) === `/api/v1/tasks/${task.id}` ? Response.json(task) : undefined)
    await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: task.title }))
    expect(await within(screen.getByRole('dialog')).findByText('发布文档')).toBeVisible()
    const read = requests.find((r) => path(r) === '/api/v1/knowledge/resources')!
    expect(new URL(read.url).searchParams.get('linked_resource_id')).toBe(task.id)
  })
  it('opens the knowledge page filtered to a selected project', async () => {
    const requests = knowledge()
    await openApp('/projects')
    await fireEvent.click(await screen.findByRole('link', { name: '相关文档' }))
    expect(await screen.findByText('发布文档')).toBeVisible()
    const read = requests.find((r) => path(r) === '/api/v1/knowledge/resources')!
    expect(new URL(read.url).searchParams.get('linked_resource_id')).toBe(project.id)
  })
})
