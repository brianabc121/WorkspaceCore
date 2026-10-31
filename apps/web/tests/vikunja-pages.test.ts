import { fireEvent, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, me, workspace, fail } from './helpers'
import { integration, project, task, sync } from './sprint3-fixtures'
const pathOf = (r: Request) => new URL(r.url).pathname
function vikunja(override?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => {
    const result = await override?.(r)
    if (result) return result
    const path = pathOf(r)
    if (path === '/api/v1/tasks') return Response.json({ items: [task], sync, next_cursor: null, total_estimate: 1 })
    if (path === `/api/v1/tasks/${task.id}`) return Response.json(task)
    if (path === '/api/v1/projects') return Response.json({ items: [project], sync, next_cursor: null, total_estimate: 1 })
    if (path === '/api/v1/integrations') return Response.json({ items: [integration], next_cursor: null, total_estimate: 1 })
  })
}
describe('Vikunja 页面', () => {
  it('paginates tasks with the opaque cursor and drops it on filter changes', async () => {
    const requests = vikunja((r) => {
      if (pathOf(r) !== '/api/v1/tasks') return
      const second = new URL(r.url).searchParams.has('cursor')
      return Response.json({ items: [second ? { ...task, id: 'task-b', title: '第二页任务' } : task], sync, next_cursor: second ? null : 'opaque+/=cursor', total_estimate: 2 })
    })
    await openApp('/tasks')
    await fireEvent.click(await screen.findByRole('button', { name: '加载更多任务' }))
    expect(await screen.findByRole('button', { name: '第二页任务' })).toBeVisible()
    const read = requests.filter((r) => pathOf(r) === '/api/v1/tasks')
    expect(new URL(read[1]!.url).searchParams.get('cursor')).toBe('opaque+/=cursor')
    await fireEvent.update(screen.getByLabelText('任务状态'), 'done')
    await fireEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    await vi.waitFor(() => expect(screen.queryByRole('button', { name: '第二页任务' })).not.toBeInTheDocument())
    const last = requests.filter((r) => pathOf(r) === '/api/v1/tasks').at(-1)!
    expect(new URL(last.url).searchParams.has('cursor')).toBe(false)
  })
  it('does not show an old workspace write result in the newly selected workspace', async () => {
    let finish: ((r: Response) => void) | undefined
    const requests = vikunja((r) => {
      if (pathOf(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: '只读空间', role: 'viewer' }] })
      if (pathOf(r) === '/api/v1/tasks') return Response.json({ items: [{ ...task, title: r.headers.get('X-Workspace-Id') === 'workspace-b' ? 'B任务' : 'A任务' }], sync, next_cursor: null, total_estimate: 1 })
      if (r.method === 'PATCH') return new Promise<Response>((resolve) => { finish = resolve })
    })
    await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: 'A任务' }))
    await screen.findByLabelText('任务标题'); await fireEvent.update(screen.getByLabelText('任务标题'), 'A更新')
    await fireEvent.click(screen.getByRole('button', { name: '保存任务' }))
    await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    expect(await screen.findByRole('button', { name: 'B任务' })).toBeVisible()
    finish!(Response.json({ ...task, title: 'A更新' }))
    await vi.waitFor(() => expect(screen.queryByText('任务操作已完成')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: '新建任务' })).not.toBeInTheDocument()
    expect(requests.find((r) => r.method === 'PATCH')!.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })
  it('recovers from a conflict when the task has since been deleted', async () => {
    let gone = false
    vikunja((r) => {
      if (r.method === 'PATCH') { gone = true; return fail(409, 'VERSION_CONFLICT') }
      if (gone && pathOf(r) === `/api/v1/tasks/${task.id}`) return fail(404, 'NOT_FOUND')
      if (gone && pathOf(r) === '/api/v1/tasks') return Response.json({ items: [], sync, next_cursor: null, total_estimate: 0 })
    })
    await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: '准备发布' }))
    await screen.findByLabelText('任务标题'); await fireEvent.update(screen.getByLabelText('任务标题'), '新版')
    await fireEvent.click(screen.getByRole('button', { name: '保存任务' }))
    await fireEvent.click(await screen.findByRole('button', { name: '重新加载任务' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '新建任务' })).toBeEnabled()
    expect(screen.getByRole('alert')).toHaveTextContent('任务已不存在或无权访问')
  })
  it('renders task list and board with a clearly labelled external source', async () => {
    vikunja(); await openApp('/tasks')
    expect(await screen.findByRole('button', { name: '准备发布' })).toBeVisible()
    expect(screen.getByText('Vikunja · REL-42')).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '看板' }))
    expect(screen.getByRole('region', { name: '未完成' })).toBeVisible()
    expect(screen.getByRole('region', { name: '已完成' })).toBeVisible()
  })
  it('sends filters to the server and resets the paging cursor', async () => {
    const requests = vikunja(); await openApp('/tasks')
    await screen.findByRole('button', { name: '准备发布' })
    await fireEvent.update(screen.getByLabelText('任务状态'), 'done')
    await fireEvent.update(screen.getByLabelText('筛选项目'), project.id)
    await fireEvent.update(screen.getByLabelText('最低截止时间'), '2026-10-01T10:00')
    await fireEvent.update(screen.getByLabelText('优先级筛选'), '3')
    await fireEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    await vi.waitFor(() => expect(requests.filter((r) => pathOf(r) === '/api/v1/tasks')).toHaveLength(2))
    const query = new URL(requests.filter((r) => pathOf(r) === '/api/v1/tasks')[1]!.url).searchParams
    expect(query.get('status')).toBe('done'); expect(query.get('project_id')).toBe(project.id)
    expect(query.getAll('priority')).toEqual(['3']); expect(query.has('due_after')).toBe(true); expect(query.has('cursor')).toBe(false)
  })
  it('saves an edit made while the task refetches in the background', async () => {
    // T-0049：SSE 失效触发的重新拉取期间点“保存任务”，提交不能被静默丢掉
    let reads = 0
    let hold: ((r: Response) => void) | undefined
    const requests = vikunja((r) => {
      if (r.method === 'PATCH') return Response.json({ ...task, title: '发布新版' })
      if (pathOf(r) !== `/api/v1/tasks/${task.id}`) return
      reads += 1
      return reads === 1 ? Response.json(task) : new Promise<Response>((resolve) => { hold = resolve })
    })
    const { queryClient } = await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: '准备发布' }))
    await fireEvent.update(await screen.findByLabelText('任务标题'), '发布新版')
    void queryClient.invalidateQueries({ queryKey: ['task'] })
    await vi.waitFor(() => expect(hold).toBeDefined())
    expect(screen.getByRole('button', { name: '保存任务' })).toBeEnabled()
    await fireEvent.click(screen.getByRole('button', { name: '保存任务' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 'opaque-version', title: '发布新版' })
    hold!(Response.json(task))
  })
  it('keeps unsaved edits when a newer version arrives and saves against the version the user opened', async () => {
    let reads = 0
    const requests = vikunja((r) => {
      if (r.method === 'PATCH') return fail(409, 'VERSION_CONFLICT')
      if (pathOf(r) !== `/api/v1/tasks/${task.id}`) return
      reads += 1
      return Response.json(reads === 1 ? task : { ...task, description: '别处改的说明', version: 'newer-version' })
    })
    const { queryClient } = await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: '准备发布' }))
    await fireEvent.update(await screen.findByLabelText('任务标题'), '我的新标题')
    await queryClient.invalidateQueries({ queryKey: ['task'] })
    expect(await screen.findByText(/这个任务刚在别处更新过。你的修改还在/)).toBeVisible()
    expect(screen.getByLabelText('任务标题')).toHaveValue('我的新标题')
    await fireEvent.click(screen.getByRole('button', { name: '保存任务' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    // 按打开时的版本提交：别处的修改由后端的版本检查发现（409），不会被这次保存悄悄覆盖
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 'opaque-version', title: '我的新标题' })
    expect(await screen.findByRole('button', { name: '重新加载任务' })).toBeVisible()
  })
  it('shows the newer version when the user has not changed anything yet', async () => {
    let reads = 0
    vikunja((r) => {
      if (pathOf(r) !== `/api/v1/tasks/${task.id}`) return
      reads += 1
      return Response.json(reads === 1 ? task : { ...task, title: '别处改的标题', version: 'newer-version' })
    })
    const { queryClient } = await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: '准备发布' }))
    expect(await screen.findByLabelText('任务标题')).toHaveValue('准备发布')
    await queryClient.invalidateQueries({ queryKey: ['task'] })
    await vi.waitFor(() => expect(screen.getByLabelText('任务标题')).toHaveValue('别处改的标题'))
    expect(screen.queryByText(/这个任务刚在别处更新过/)).not.toBeInTheDocument()
  })
  it('edits only changed task fields, keeps version opaque, and renders descriptions as text', async () => {
    const requests = vikunja((r) => r.method === 'PATCH' ? Response.json({ ...task, title: '发布新版' }) : undefined)
    await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: '准备发布' }))
    await screen.findByLabelText('任务标题')
    await fireEvent.update(screen.getByLabelText('任务标题'), '发布新版')
    await fireEvent.click(screen.getByRole('button', { name: '保存任务' }))
    expect(await screen.findByText('任务操作已完成')).toBeVisible()
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 'opaque-version', title: '发布新版' })
  })
  it('keeps unknown creations locked and exposes same-request result lookup after closing the drawer', async () => {
    let count = 0
    const requests = vikunja((r) => r.method === 'POST' ? ++count === 1 ? fail(504, 'RESULT_UNKNOWN') : Response.json(task, { status: 201 }) : undefined)
    await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: '新建任务' }))
    await vi.waitFor(() => expect(screen.getByLabelText('任务项目')).toHaveTextContent('发布计划'))
    await fireEvent.update(screen.getByLabelText('任务标题'), '准备发布')
    await fireEvent.update(screen.getByLabelText('任务项目'), project.id)
    await fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    expect(await screen.findByText(/操作结果未知/)).toBeVisible()
    expect(screen.getByRole('button', { name: '创建任务' })).toBeDisabled()
    await fireEvent.click(screen.getByRole('button', { name: '关闭对话框' }))
    await fireEvent.click(screen.getByRole('button', { name: '查询原请求结果' }))
    expect(await screen.findByText('任务操作已完成')).toBeVisible()
    const writes = requests.filter((r) => r.method === 'POST')
    expect(writes).toHaveLength(2)
    expect(writes[0]!.headers.get('Idempotency-Key')).toBe(writes[1]!.headers.get('Idempotency-Key'))
  })
  it('hides task writes and integration configuration for viewers', async () => {
    const requests = vikunja((r) => pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] }) : undefined)
    const { router } = await openApp('/tasks'); await screen.findByRole('button', { name: '准备发布' })
    expect(screen.queryByRole('button', { name: '新建任务' })).not.toBeInTheDocument()
    await router.push('/settings/integrations')
    expect(screen.getByRole('heading', { name: '无权访问' })).toBeVisible()
    expect(requests.filter((r) => pathOf(r) === '/api/v1/integrations')).toHaveLength(0)
  })
  it('keeps saved base URL and token out of form values and saves replacement credentials once', async () => {
    const requests = vikunja((r) => r.method === 'PATCH' ? Response.json(integration) : undefined)
    await openApp('/settings/integrations')
    await screen.findByText('我的 Vikunja')
    await fireEvent.click(screen.getByRole('button', { name: '修改连接' }))
    expect(screen.getByLabelText('服务地址')).toHaveValue('')
    expect(screen.getByLabelText('API Token')).toHaveValue('')
    expect(screen.getByText('http://private-vikunja:3456')).toBeVisible()
    await fireEvent.update(screen.getByLabelText('API Token'), 'tk_test-replacement')
    await fireEvent.click(screen.getByRole('button', { name: '保存连接' }))
    expect(await screen.findByText('连接已保存，请测试连接')).toBeVisible()
    const write = requests.find((r) => r.method === 'PATCH')!
    expect((await write.clone().json()).api_token).toBe('tk_test-replacement')
    expect((await write.clone().json()).base_url).toBeUndefined()
    expect(document.body).not.toHaveTextContent('tk_test-replacement')
  })
  it('requires a read refresh before retrying a credential update with an invalid success response', async () => {
    const requests = vikunja((r) => r.method === 'PATCH' ? new Response('{invalid', { status: 200 }) : undefined)
    await openApp('/settings/integrations'); await screen.findByText('我的 Vikunja')
    await fireEvent.click(screen.getByRole('button', { name: '修改连接' }))
    await fireEvent.update(screen.getByLabelText('API Token'), 'tk_test-replacement')
    await fireEvent.click(screen.getByRole('button', { name: '保存连接' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '保存连接' })).toBeDisabled())
    await fireEvent.click((await screen.findAllByRole('button', { name: '重新加载连接' })).at(-1)!)
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(1)
  })
})
