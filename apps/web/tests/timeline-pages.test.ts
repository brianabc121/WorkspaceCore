// 任务时间轴（T-0105）：页面、按项目查看、选中详情与操作、入口；任务详情的执行者与执行记录。
import { fireEvent, screen, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fail, me, mockApi, openApp, workspace } from './helpers'
import { project, task } from './sprint3-fixtures'
import type { Schema } from '../src/services/api'

const pathOf = (r: Request) => new URL(r.url).pathname
const iso = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString()
const tokens = (total: number): Schema['TokenCount'] => ({ input: total, output: 0, cache_read: 0, cache_write: 0, total })
const run = (over: Partial<Schema['TaskRunOut']> = {}): Schema['TaskRunOut'] => ({
  id: 'run-1', task_id: '11111111-1111-4111-8111-111111111111', executor_kind: 'instance', user_id: null, instance_id: 'inst-1', executor_name: 'ops-claw', started_at: iso(-60), ended_at: null, end_reason: null,
  started_by: 'user-a', ended_by: null, tokens: tokens(182_340), tokens_shared: false, usage_reported: true,
  waits: [{ execution_id: 'e1', tool: 'complete_task', start: iso(-40), end: iso(-34) }], series: [{ start: iso(-60), tokens: tokens(1000) }, { start: iso(-50), tokens: tokens(3000) }], ...over,
})
const ttask = (over: Partial<Schema['TimelineTask']> = {}): Schema['TimelineTask'] => ({
  id: '11111111-1111-4111-8111-111111111111', project_id: project.id, project_title: project.title, title: '迁移预演', done: false, done_at: null, due_at: iso(-10), planned_start_at: null, started_at: iso(-60),
  assignee_user_id: null, assignee_instance_id: 'inst-1', running: true, ...over,
})
function timeline(over: Partial<Schema['TimelineOut']> = {}): Schema['TimelineOut'] {
  return {
    range_start: iso(-720), range_end: iso(720), now: iso(0), group: 'executor', project_id: null,
    tracks: [{ key: 'instance:inst-1', kind: 'instance', id: 'inst-1', name: 'ops-claw', status: 'online' }, { key: 'unassigned', kind: 'unassigned', id: null, name: '未分配', status: null }],
    tasks: [ttask(), ttask({ id: '22222222-2222-4222-8222-222222222222', title: '更新手册', due_at: iso(120), planned_start_at: iso(60), started_at: null, assignee_instance_id: null, running: false })],
    segments: [
      { kind: 'run', task_id: '11111111-1111-4111-8111-111111111111', track: 'instance:inst-1', start: iso(-60), end: null, run: run() },
      { kind: 'planned', task_id: '22222222-2222-4222-8222-222222222222', track: 'unassigned', start: iso(60), end: iso(120), run: null },
    ],
    usage: { scope: 'workspace', bucket_minutes: 5, reported: true, buckets: [{ start: iso(-30), tokens: tokens(5000) }], total: tokens(549_000) },
    summary: { running: 1, done: 6, tokens: 549_000, overdue: 1 }, truncated: false, ...over,
  }
}
function timelineApi(options: { role?: string; data?: () => Schema['TimelineOut']; override?: (r: Request) => Response | undefined } = {}) {
  return mockApi(async (r) => {
    const result = options.override?.(r)
    if (result) return result
    const path = pathOf(r)
    if (path === '/api/v1/me' && options.role) return Response.json({ ...me, workspaces: [{ ...workspace, role: options.role }] })
    if (path === '/api/v1/tasks/timeline') return Response.json(options.data?.() ?? timeline())
    if (path === '/api/v1/projects' && r.method === 'GET') return Response.json({ items: [project, { ...project, id: '33333333-3333-4333-8333-333333333333', title: '文档' }], next_cursor: null, total_estimate: null })
    if (path === '/api/v1/tasks/11111111-1111-4111-8111-111111111111/runs' && r.method === 'GET') return Response.json({ items: [run()] })
    if (path === '/api/v1/tasks/22222222-2222-4222-8222-222222222222/runs' && r.method === 'GET') return Response.json({ items: [] })
    return undefined
  })
}
// 时间轴按本地的一天显示：固定在当天中午，免得临近午夜时“一小时后”的计划落到第二天（只假 Date，计时器照常）
beforeEach(() => { vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date(2026, 10, 6, 12, 0)) })
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('任务时间轴', () => {
  it('shows tracks, clips, usage and the summary', async () => {
    const requests = timelineApi()
    await openApp('/tasks/timeline')
    const board = await screen.findByRole('region', { name: '时间轴' })
    expect(within(board).getByText('ops-claw')).toBeVisible()
    expect(within(board).getByRole('button', { name: /迁移预演，执行中/ })).toBeVisible()
    expect(within(board).getByRole('button', { name: /更新手册，计划中/ })).toBeVisible()
    expect(within(board).getAllByText(/182k token/).length).toBeGreaterThan(0)
    expect(within(board).getByRole('img', { name: 'token 用量，合计 549000' })).toBeVisible()
    const summary = screen.getByRole('region', { name: '今天的概况' })
    expect(within(summary).getByText('549k')).toBeVisible()
    expect(within(summary).getByText('6')).toBeVisible()
    expect(screen.getByLabelText('图例')).toHaveTextContent('超过截止时间')
    const query = new URL(requests.find((r) => pathOf(r) === '/api/v1/tasks/timeline')!.url).searchParams
    expect(query.get('group')).toBe('executor')
    expect(query.has('project_id')).toBe(false)
    // 表格视图：不只靠颜色
    await fireEvent.click(screen.getByRole('button', { name: '表格视图' }))
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    expect(within(table).getByText('计划中')).toBeVisible()
  })

  it('filters one project through the address and shows token not reported', async () => {
    const requests = timelineApi({ data: () => timeline({ usage: { scope: 'project', bucket_minutes: 5, reported: false, buckets: [], total: tokens(0) } }) })
    const { router } = await openApp('/tasks/timeline')
    await screen.findByRole('region', { name: '时间轴' })
    await screen.findByRole('option', { name: '文档' })
    await fireEvent.update(screen.getByLabelText('项目'), '33333333-3333-4333-8333-333333333333')
    await vi.waitFor(() => expect(router.currentRoute.value.query.project).toBe('33333333-3333-4333-8333-333333333333'))
    await vi.waitFor(() => expect(requests.some((r) => pathOf(r) === '/api/v1/tasks/timeline' && new URL(r.url).searchParams.get('project_id') === '33333333-3333-4333-8333-333333333333')).toBe(true))
    expect(screen.getAllByText('未上报').length).toBeGreaterThan(0)
    await fireEvent.update(screen.getByLabelText('分组'), 'project')
    await vi.waitFor(() => expect(router.currentRoute.value.query.group).toBe('project'))
  })

  it('selects a clip, shows its runs and hands an agent task over with a stable key', async () => {
    let attempts = 0
    const requests = timelineApi({ data: () => timeline({ segments: [{ kind: 'run', task_id: '11111111-1111-4111-8111-111111111111', track: 'instance:inst-1', start: iso(-60), end: iso(-5), run: run({ ended_at: iso(-5), end_reason: 'paused' }) }], tasks: [ttask({ running: false })] }),
      override: (r) => {
        if (pathOf(r) === '/api/v1/tasks/11111111-1111-4111-8111-111111111111/runs' && r.method === 'GET') return Response.json({ items: [run({ ended_at: iso(-5), end_reason: 'paused' })] })
        if (pathOf(r) === '/api/v1/tasks/11111111-1111-4111-8111-111111111111/runs' && r.method === 'POST') { attempts += 1; return attempts === 1 ? fail(504, 'RESULT_UNKNOWN') : Response.json(run({ id: 'run-2' }), { status: 201 }) }
        return undefined
      } })
    const { router } = await openApp('/tasks/timeline')
    await fireEvent.click(await screen.findByRole('button', { name: /迁移预演，已暂停/ }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.task).toBe('11111111-1111-4111-8111-111111111111'))
    const detail = screen.getByRole('complementary', { name: '选中的任务' })
    expect((await within(detail).findAllByText(/已暂停/)).length).toBeGreaterThan(0)
    expect(within(detail).getByRole('img', { name: /每 10 分钟的 token/ })).toBeVisible()
    expect(within(detail).getByRole('link', { name: '打开任务' })).toHaveAttribute('href', '/tasks?task=11111111-1111-4111-8111-111111111111')
    await fireEvent.click(within(detail).getByRole('button', { name: '交给它执行' }))
    expect(await within(detail).findByRole('alert')).toHaveTextContent('再点一次会查询结果')
    await fireEvent.click(within(detail).getByRole('button', { name: '交给它执行' }))
    expect(await within(detail).findByText('已通过 IM 交给它执行')).toBeVisible()
    const posts = requests.filter((r) => r.method === 'POST' && pathOf(r) === '/api/v1/tasks/11111111-1111-4111-8111-111111111111/runs')
    expect(posts).toHaveLength(2)
    expect(posts[0].headers.get('Idempotency-Key')).toBe(posts[1].headers.get('Idempotency-Key'))
    expect(await posts[1].clone().json()).toEqual({ dispatch: true })
  })

  it('stops a running agent run', async () => {
    const requests = timelineApi({ override: (r) => (r.method === 'POST' && pathOf(r) === '/api/v1/tasks/11111111-1111-4111-8111-111111111111/runs/run-1/stop' ? Response.json(run({ ended_at: iso(0), end_reason: 'interrupted' })) : undefined) })
    await openApp('/tasks/timeline?task=11111111-1111-4111-8111-111111111111')
    const detail = await screen.findByRole('complementary', { name: '选中的任务' })
    await fireEvent.click(await within(detail).findByRole('button', { name: '结束这段' }))
    expect(await within(detail).findByText('已结束这段')).toBeVisible()
    const stop = requests.find((r) => pathOf(r).endsWith('/stop'))!
    expect(await stop.clone().json()).toEqual({ reason: 'interrupted' })

  })

  it('terminates a running agent run with a reason and the task id (T-0126)', async () => {
    const prompt = vi.spyOn(window, 'prompt').mockReturnValueOnce(null).mockReturnValueOnce(' 一直在重试同一个命令 ')
    const requests = timelineApi({ override: (r) => (r.method === 'POST' && pathOf(r) === '/api/v1/agents/instances/inst-1/stop' ? Response.json({ id: 's-1', instance_id: 'inst-1', requested_by: 'user-a', requested_by_instance: null, task_id: '11111111-1111-4111-8111-111111111111', reason: '一直在重试同一个命令', status: 'pending', soft_until: iso(5), aborted: null, error: null, created_at: iso(0), finished_at: null }, { status: 201 }) : undefined) })
    await openApp('/tasks/timeline?task=11111111-1111-4111-8111-111111111111')
    const detail = await screen.findByRole('complementary', { name: '选中的任务' })
    await fireEvent.click(await within(detail).findByRole('button', { name: '终止执行' }))
    expect(prompt).toHaveBeenCalledTimes(1)
    expect(requests.some((r) => pathOf(r).endsWith('/stop'))).toBe(false) // 取消了
    await fireEvent.click(within(detail).getByRole('button', { name: '终止执行' }))
    expect(await within(detail).findByText(/已叫停：这一段记为中断/)).toBeVisible()
    const stop = requests.find((r) => r.method === 'POST' && pathOf(r) === '/api/v1/agents/instances/inst-1/stop')!
    expect(await stop.clone().json()).toEqual({ reason: '一直在重试同一个命令', task_id: '11111111-1111-4111-8111-111111111111' })
  })

  it('keeps viewers read-only', async () => {
    timelineApi({ role: 'viewer' })
    await openApp('/tasks/timeline?task=22222222-2222-4222-8222-222222222222')
    const detail = await screen.findByRole('complementary', { name: '选中的任务' })
    expect(await within(detail).findByRole('heading', { name: /更新手册/ })).toBeVisible()
    expect(within(detail).queryByRole('button', { name: '开始' })).toBeNull()
    expect(within(detail).queryByRole('button', { name: '交给它执行' })).toBeNull()
    expect(within(detail).queryByRole('button', { name: '看会话' })).toBeNull()
  })

  it('is reachable from the tasks and projects pages', async () => {
    timelineApi({ override: (r) => (pathOf(r) === '/api/v1/tasks' ? Response.json({ items: [task], next_cursor: null, total_estimate: null }) : undefined) })
    const { router } = await openApp('/tasks?project_id=' + project.id)
    await screen.findByRole('button', { name: task.title })
    await fireEvent.click(within(screen.getByRole('group', { name: '显示方式' })).getByRole('link', { name: '时间轴' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/tasks/timeline'))
    expect(router.currentRoute.value.query.project).toBe(project.id)
    await router.push('/projects')
    const card = await screen.findByRole('region', { name: `项目 ${project.title}` })
    expect(within(card).getByRole('link', { name: '时间轴' })).toHaveAttribute('href', `/tasks/timeline?project=${project.id}`)
  })
})

describe('任务详情的执行者与执行记录', () => {
  it('sets the executor and planned start, and starts a human run', async () => {
    const opened = { ...task, assignee_user_id: null, assignee_instance_id: null }
    const requests = mockApi(async (r) => {
      const path = pathOf(r)
      if (path === '/api/v1/projects') return Response.json({ items: [project], next_cursor: null, total_estimate: null })
      if (path === '/api/v1/tasks' && r.method === 'GET') return Response.json({ items: [opened], next_cursor: null, total_estimate: null })
      if (path === `/api/v1/tasks/${task.id}` && r.method === 'GET') return Response.json(opened)
      if (path === `/api/v1/tasks/${task.id}` && r.method === 'PATCH') return Response.json({ ...opened, assignee_instance_id: 'inst-1', version: 'v2' })
      if (path === '/api/v1/tasks/assignees') return Response.json({ users: [{ id: 'user-a', display_name: '测试用户' }], instances: [{ id: 'inst-1', name: 'ops-claw', status: 'online' }] })
      if (path === `/api/v1/tasks/${task.id}/runs` && r.method === 'GET') return Response.json({ items: [] })
      if (path === `/api/v1/tasks/${task.id}/runs` && r.method === 'POST') return Response.json(run({ executor_kind: 'user', user_id: 'user-a', executor_name: '测试用户', tokens: null, usage_reported: false, waits: [], series: [] }), { status: 201 })
      return undefined
    })
    await openApp(`/tasks?task=${task.id}`)
    const runs = await screen.findByRole('region', { name: '执行记录' })
    expect(await within(runs).findByText('还没有执行记录。')).toBeVisible()
    expect(within(runs).getByRole('link', { name: '在时间轴中查看' }).getAttribute('href')).toContain(`task=${task.id}`)
    await fireEvent.click(within(runs).getByRole('button', { name: '开始' }))
    expect(await within(runs).findByText('已开始')).toBeVisible()
    const start = requests.find((r) => r.method === 'POST' && pathOf(r) === `/api/v1/tasks/${task.id}/runs`)!
    expect(await start.clone().json()).toEqual({ dispatch: false })
    expect(start.headers.get('Idempotency-Key')).toBeTruthy()

    const select = await screen.findByLabelText('执行者')
    await vi.waitFor(() => expect(within(select).getByRole('option', { name: 'ops-claw（在线）' })).toBeTruthy())
    await fireEvent.update(select, 'instance:inst-1')
    await fireEvent.update(screen.getByLabelText('计划开始'), '2026-11-05T09:30')
    await fireEvent.click(screen.getByRole('button', { name: '保存任务' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    const body = await requests.find((r) => r.method === 'PATCH')!.clone().json()
    expect(body.assignee_instance_id).toBe('inst-1')
    expect(body.planned_start_at).toBe(new Date('2026-11-05T09:30').toISOString())
    expect(body).not.toHaveProperty('assignee_user_id')
  })

  it('terminates the agent run from the task drawer (T-0126)', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue('卡在同一个报错上')
    const opened = { ...task, assignee_user_id: null, assignee_instance_id: 'inst-1' }
    const requests = mockApi(async (r) => {
      const path = pathOf(r)
      if (path === '/api/v1/projects') return Response.json({ items: [project], next_cursor: null, total_estimate: null })
      if (path === '/api/v1/tasks' && r.method === 'GET') return Response.json({ items: [opened], next_cursor: null, total_estimate: null })
      if (path === `/api/v1/tasks/${task.id}` && r.method === 'GET') return Response.json(opened)
      if (path === '/api/v1/tasks/assignees') return Response.json({ users: [], instances: [{ id: 'inst-1', name: 'ops-claw', status: 'online' }] })
      if (path === `/api/v1/tasks/${task.id}/runs` && r.method === 'GET') return Response.json({ items: [run({ task_id: task.id })] })
      if (path === '/api/v1/agents/instances/inst-1/stop' && r.method === 'POST') return Response.json({ id: 's-1', instance_id: 'inst-1', requested_by: 'user-a', requested_by_instance: null, task_id: task.id, reason: '卡在同一个报错上', status: 'pending', soft_until: iso(5), aborted: null, error: null, created_at: iso(0), finished_at: null }, { status: 201 })
      return undefined
    })
    await openApp(`/tasks?task=${task.id}`)
    const runs = await screen.findByRole('region', { name: '执行记录' })
    await fireEvent.click(await within(runs).findByRole('button', { name: '终止执行' }))
    expect(await within(runs).findByText(/已叫停：这一段记为中断/)).toBeVisible()
    const stop = requests.find((r) => r.method === 'POST' && pathOf(r) === '/api/v1/agents/instances/inst-1/stop')!
    expect(await stop.clone().json()).toEqual({ reason: '卡在同一个报错上', task_id: task.id })
  })
})
