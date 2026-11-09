// 任务页的智能视图、快速添加、列表里勾选完成、看板拖动（T-0084）；项目页进度条
import { fireEvent, screen, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fail, me, mockApi, openApp, workspace } from './helpers'
import { project, task } from './sprint3-fixtures'
import { dueInfo, viewQuery } from '../src/features/tasks/shared'

const pathOf = (r: Request) => new URL(r.url).pathname
const listRequests = (requests: Request[]) => requests.filter((r) => pathOf(r) === '/api/v1/tasks' && r.method === 'GET')
function tasksApi(override?: (r: Request) => Response | Promise<Response> | undefined, items = () => [task]) {
  return mockApi(async (r) => {
    const result = await override?.(r)
    if (result) return result
    if (pathOf(r) === '/api/v1/tasks' && r.method === 'GET') return Response.json({ items: items(), next_cursor: null, total_estimate: null })
    if (pathOf(r) === '/api/v1/projects') return Response.json({ items: [project, { ...project, id: '77777777-7777-4777-8777-777777777778', title: '第二个项目' }], next_cursor: null, total_estimate: null })
  })
}
afterEach(() => { vi.useRealTimers() })

describe('智能视图的时间范围', () => {
  const now = new Date(2026, 10, 2, 15, 30)
  it('turns views into the list filters', () => {
    expect(viewQuery('all', now)).toEqual({ status: 'all' })
    expect(viewQuery('done', now)).toEqual({ status: 'done' })
    expect(viewQuery('today', now)).toEqual({ status: 'open', due_after: new Date(2026, 10, 2).toISOString(), due_before: new Date(2026, 10, 3).toISOString() })
    expect(viewQuery('overdue', now)).toEqual({ status: 'open', due_before: now.toISOString() })
    expect(viewQuery('upcoming', now)).toEqual({ status: 'open', due_after: now.toISOString(), due_before: new Date(now.getTime() + 7 * 864e5).toISOString() })
  })
  it('describes the due time and marks overdue and today', () => {
    expect(dueInfo(null, false, now)).toEqual({ text: '无截止时间', tone: 'none' })
    expect(dueInfo(new Date(2026, 10, 2, 18, 0).toISOString(), false, now)).toEqual({ text: '今天 18:00', tone: 'today' })
    expect(dueInfo(new Date(2026, 10, 3, 9, 5).toISOString(), false, now)).toEqual({ text: '明天 09:05', tone: 'normal' })
    expect(dueInfo(new Date(2026, 9, 30, 9, 0).toISOString(), false, now)).toEqual({ text: '逾期 · 10月30日 09:00', tone: 'overdue' })
    expect(dueInfo(new Date(2026, 9, 30, 9, 0).toISOString(), true, now)).toEqual({ text: '10月30日 09:00', tone: 'normal' })
    expect(dueInfo(new Date(2025, 0, 5, 9, 0).toISOString(), true, now).text).toBe('2025年1月5日 09:00')
  })
})

describe('任务页的快速操作', () => {
  it('filters by state chips, due range and sort, and sends the matching parameters', async () => {
    const requests = tasksApi()
    await openApp('/tasks'); await screen.findByRole('button', { name: '准备发布' })
    const chips = screen.getByRole('group', { name: '按状态筛选任务' })
    expect(within(chips).getByRole('button', { name: /^全部/ })).toHaveAttribute('aria-pressed', 'true')
    await fireEvent.update(screen.getByLabelText('筛选截止时间'), 'today')
    await vi.waitFor(() => expect(listRequests(requests)).toHaveLength(2))
    let query = new URL(listRequests(requests)[1]!.url).searchParams
    expect(query.get('status')).toBe('open')
    expect(new Date(query.get('due_before')!).getTime() - new Date(query.get('due_after')!).getTime()).toBeGreaterThanOrEqual(23 * 36e5)
    await fireEvent.update(screen.getByLabelText('筛选截止时间'), '')
    await fireEvent.click(within(chips).getByRole('button', { name: /^阻塞/ }))
    await vi.waitFor(() => expect(listRequests(requests)).toHaveLength(4))
    query = new URL(listRequests(requests)[3]!.url).searchParams
    expect(query.get('status')).toBe('all'); expect(query.get('work_state')).toBe('blocked'); expect(query.has('due_after')).toBe(false)
    expect(within(chips).getByRole('button', { name: /^阻塞/ })).toHaveAttribute('aria-pressed', 'true')
    await fireEvent.update(screen.getByLabelText('排序'), 'priority')
    await vi.waitFor(() => expect(listRequests(requests)).toHaveLength(5))
    expect(new URL(listRequests(requests)[4]!.url).searchParams.get('sort')).toBe('priority')
  })

  it('completes a task from the list with its version and keeps the new version for the next toggle', async () => {
    let current = task
    const requests = tasksApi((r) => { if (r.method === 'PATCH') { current = { ...task, done: true, done_at: '2026-11-02T00:00:00Z', version: 'v2' }; return Response.json(current) } }, () => [current])
    await openApp('/tasks')
    const box = await screen.findByRole('checkbox', { name: '完成：准备发布' })
    expect(box).not.toBeChecked()
    await fireEvent.click(box)
    await vi.waitFor(() => expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(1))
    const write = requests.find((r) => r.method === 'PATCH')!
    expect(pathOf(write)).toBe(`/api/v1/tasks/${task.id}`)
    expect(write.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(await write.clone().json()).toEqual({ version: 'opaque-version', done: true })
    expect(await screen.findByRole('checkbox', { name: '取消完成：准备发布' })).toBeChecked()
    expect(screen.getByRole('status', { name: '' })).toHaveTextContent('已完成：准备发布')
    expect(screen.queryByText('任务操作已完成')).not.toBeInTheDocument()
    // 下一次勾选用新版本
    await fireEvent.click(screen.getByRole('checkbox', { name: '取消完成：准备发布' }))
    await vi.waitFor(() => expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(2))
    expect(await requests.filter((r) => r.method === 'PATCH')[1]!.clone().json()).toEqual({ version: 'v2', done: false })
  })

  it('shows a failed toggle as unchecked and keeps the operation panel', async () => {
    tasksApi((r) => r.method === 'PATCH' ? fail(409, 'VERSION_CONFLICT') : undefined)
    await openApp('/tasks')
    await fireEvent.click(await screen.findByRole('checkbox', { name: '完成：准备发布' }))
    expect(await screen.findByRole('button', { name: '重新加载任务' })).toBeVisible()
    await vi.waitFor(() => expect(screen.getByRole('checkbox', { name: '完成：准备发布' })).not.toBeChecked())
  })

  it('quick-adds a task to the chosen project and, in the today view, due today', async () => {
    const requests = tasksApi((r) => r.method === 'POST' ? Response.json({ ...task, id: 'new-task', title: '写周报' }, { status: 201 }) : undefined)
    await openApp('/tasks'); await screen.findByRole('button', { name: '准备发布' })
    const form = screen.getByRole('form', { name: '快速添加任务' })
    expect(within(form).getByLabelText('添加到项目')).toHaveValue(project.id)
    await fireEvent.update(screen.getByLabelText('筛选截止时间'), 'today')
    await fireEvent.update(within(form).getByLabelText('快速添加'), '  写周报 ')
    await fireEvent.update(within(form).getByLabelText('添加到项目'), '77777777-7777-4777-8777-777777777778')
    await fireEvent.submit(form)
    await vi.waitFor(() => expect(requests.filter((r) => r.method === 'POST')).toHaveLength(1))
    const write = requests.find((r) => r.method === 'POST')!
    expect(write.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    const body = await write.clone().json()
    expect(body).toMatchObject({ title: '写周报', project_id: '77777777-7777-4777-8777-777777777778', priority: 0, labels: [], description: '' })
    const due = new Date(body.due_at)
    expect(due.getHours()).toBe(23); expect(due.toDateString()).toBe(new Date().toDateString())
    await vi.waitFor(() => expect(within(form).getByLabelText('快速添加')).toHaveValue(''))
    expect(screen.getByRole('status', { name: '' })).toHaveTextContent('已添加任务：写周报')
  })

  it('keeps the quick-add title when the result is unknown, so the same request can be looked up', async () => {
    tasksApi((r) => r.method === 'POST' ? fail(504, 'RESULT_UNKNOWN') : undefined)
    await openApp('/tasks'); await screen.findByRole('button', { name: '准备发布' })
    const form = screen.getByRole('form', { name: '快速添加任务' })
    await fireEvent.update(within(form).getByLabelText('快速添加'), '写周报')
    await fireEvent.submit(form)
    expect(await screen.findByRole('button', { name: '查询原请求结果' })).toBeVisible()
    expect(within(form).getByLabelText('快速添加')).toHaveValue('写周报')
    expect(within(form).getByRole('button', { name: '添加' })).toBeDisabled()
  })

  it('moves a card to the other board column to change its state', async () => {
    let current = task
    const requests = tasksApi((r) => { if (r.method === 'PATCH') { current = { ...task, done: true, version: 'v2' }; return Response.json(current) } }, () => [current])
    await openApp('/tasks'); await screen.findByRole('button', { name: '准备发布' })
    await fireEvent.click(screen.getByRole('button', { name: '看板' }))
    const card = within(screen.getByRole('region', { name: '未完成' })).getByRole('button', { name: '准备发布' }).closest('article')!
    expect(card).toHaveAttribute('draggable', 'true')
    const target = screen.getByRole('region', { name: '已完成' })
    await fireEvent.dragStart(card)
    await fireEvent.dragOver(target)
    expect(target).toHaveClass('is-drop-target')
    await fireEvent.drop(target)
    await vi.waitFor(() => expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(1))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 'opaque-version', done: true })
    expect(await within(screen.getByRole('region', { name: '已完成' })).findByRole('button', { name: '准备发布' })).toBeVisible()
  })

  it('does not change anything when a card is dropped on its own column', async () => {
    const requests = tasksApi()
    await openApp('/tasks'); await screen.findByRole('button', { name: '准备发布' })
    await fireEvent.click(screen.getByRole('button', { name: '看板' }))
    const column = screen.getByRole('region', { name: '未完成' })
    await fireEvent.dragStart(within(column).getByRole('button', { name: '准备发布' }).closest('article')!)
    await fireEvent.drop(column)
    expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(0)
  })

  it('highlights overdue tasks', async () => {
    tasksApi(undefined, () => [{ ...task, due_at: '2020-01-01T08:00:00Z' }])
    await openApp('/tasks')
    expect(await screen.findByText(/^逾期 · /)).toHaveClass('due-overdue')
  })

  it('gives viewers no checkboxes, quick add or dragging', async () => {
    tasksApi((r) => pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] }) : undefined)
    await openApp('/tasks'); await screen.findByRole('button', { name: '准备发布' })
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: '快速添加任务' })).not.toBeInTheDocument()
    expect(screen.getByText('待办', { selector: '.ws-state' })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '看板' }))
    expect(within(screen.getByRole('region', { name: '未完成' })).getByRole('button', { name: '准备发布' }).closest('article')).toHaveAttribute('draggable', 'false')
  })
})

describe('项目进度', () => {
  it('shows each project’s completion as a progress bar', async () => {
    mockApi((r) => pathOf(r) === '/api/v1/projects' ? Response.json({ items: [{ ...project, open_tasks: 1, done_tasks: 3 }, { ...project, id: 'p2', title: '空项目', open_tasks: 0, done_tasks: 0 }], next_cursor: null, total_estimate: null }) : undefined)
    await openApp('/projects')
    expect(await screen.findByRole('progressbar', { name: '发布计划 完成进度' })).toHaveAttribute('aria-valuenow', '75')
    // 没有任务的项目不画进度条，也不显示 100% 或 0%（规范第 5 节）
    expect(screen.queryByRole('progressbar', { name: '空项目 完成进度' })).toBeNull()
    expect(within(screen.getByRole('region', { name: '项目 空项目' })).getByText('尚无任务')).toBeVisible()
  })
})
