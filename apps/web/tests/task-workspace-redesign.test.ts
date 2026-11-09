// 任务页改造（T-0167，docs/v2/pages-redesign 规范第 4 节）：状态标签用服务端计数、按状态筛选、
// 打开详情不自动标已查看、标为已查看只带当前轮次、轮次变化重新读取、标记 / 解除阻塞、viewer 只能标自己的已查看、
// 地址里的状态与任务可以恢复、宽屏时详情在右侧并排。
import { fireEvent, screen, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fail, me, mockApi, openApp, workspace } from './helpers'
import { project, task } from './sprint3-fixtures'
import type { Schema } from '../src/services/api'

const pathOf = (r: Request) => new URL(r.url).pathname
const summary: Schema['TaskSummary'] = { todo: 8, running: 4, awaiting_view: 3, blocked: 2, done: 5, total: 22, as_of: '2026-11-09T00:00:00Z' }
const awaiting: Schema['TaskOut'] = { ...task, id: '99999999-9999-4999-8999-999999999991', title: '首页改版方案', done: true, done_at: '2026-11-09T01:00:00Z', work_state: 'awaiting_view', completion_seq: 2, result_viewed: false }
const blocked: Schema['TaskOut'] = { ...task, id: '99999999-9999-4999-8999-999999999992', title: 'GitHub集成验证', work_state: 'blocked', blocker: { reason: '等待补充 GitHub 仓库访问权限 <b>x</b>', blocked_at: '2026-11-09T01:00:00Z', blocked_by: 'user-a', blocked_by_name: 'brian' } }

function api(override?: (r: Request) => Response | Promise<Response> | undefined, items: () => Schema['TaskOut'][] = () => [task, awaiting, blocked]) {
  return mockApi(async (r) => {
    const result = await override?.(r)
    if (result) return result
    const path = pathOf(r)
    if (path === '/api/v1/tasks') return Response.json({ items: items(), next_cursor: null, total_estimate: null })
    if (path === '/api/v1/tasks/summary') return Response.json(summary)
    const one = items().find((t) => path === `/api/v1/tasks/${t.id}`)
    if (one && r.method === 'GET') return Response.json(one)
    if (path.endsWith('/events')) return Response.json({ items: [{ id: 'e1', kind: 'completed', actor_kind: 'instance', actor_name: '设计助手', note: '方案已写进任务 README', created_at: '2026-11-09T01:00:00Z' }] })
    if (path === '/api/v1/projects') return Response.json({ items: [project], next_cursor: null, total_estimate: 1 })
  })
}

afterEach(() => { vi.unstubAllGlobals() })

describe('任务页（四页改造）', () => {
  it('shows server counts on the state chips and filters by work_state', async () => {
    const requests = api()
    await openApp('/tasks')
    const chips = await screen.findByRole('group', { name: '按状态筛选任务' })
    await vi.waitFor(() => expect(within(chips).getAllByRole('button').map((b) => b.textContent?.replace(/\s+/g, ''))).toEqual(['全部22', '待办8', '进行中4', '已完成待查看3', '阻塞2', '已完成5']))
    await fireEvent.click(within(chips).getByRole('button', { name: /^已完成待查看/ }))
    await vi.waitFor(() => expect(requests.filter((r) => pathOf(r) === '/api/v1/tasks').length).toBeGreaterThan(1))
    const last = new URL(requests.filter((r) => pathOf(r) === '/api/v1/tasks').at(-1)!.url).searchParams
    expect(last.get('work_state')).toBe('awaiting_view'); expect(last.get('status')).toBe('all')
    // 总数来自服务端计数，不是当前页的条数
    expect(await screen.findByText(/共 3 项/)).toBeVisible()
  })

  it('restores the state chip from the address', async () => {
    const requests = api()
    await openApp('/tasks?state=blocked')
    const chips = await screen.findByRole('group', { name: '按状态筛选任务' })
    expect(within(chips).getByRole('button', { name: /^阻塞/ })).toHaveAttribute('aria-pressed', 'true')
    await vi.waitFor(() => expect(requests.some((r) => pathOf(r) === '/api/v1/tasks' && new URL(r.url).searchParams.get('work_state') === 'blocked')).toBe(true))
  })

  it('does not mark a result viewed on open; marks it explicitly with the completion round', async () => {
    const requests = api((r) => r.method === 'PUT' ? Response.json({ ...awaiting, work_state: 'done', result_viewed: true }) : undefined)
    const { router } = await openApp(`/tasks?task=${awaiting.id}`)
    const callout = await screen.findByRole('note', { name: '待查看' })
    expect(await within(callout).findByText('完成说明：方案已写进任务 README')).toBeVisible()
    expect(requests.some((r) => r.method === 'PUT')).toBe(false)
    expect(router.currentRoute.value.query.task).toBe(awaiting.id)
    await fireEvent.click(within(callout).getByRole('button', { name: '标为已查看' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PUT')).toBe(true))
    const put = requests.find((r) => r.method === 'PUT')!
    expect(pathOf(put)).toBe(`/api/v1/tasks/${awaiting.id}/result-view`)
    expect(await put.clone().json()).toEqual({ completion_seq: 2 })
    expect(await screen.findByText(/已标为已查看（只影响你自己）/)).toBeVisible()
  })

  it('explains a changed completion round instead of marking the new result', async () => {
    api((r) => r.method === 'PUT' ? fail(409, 'COMPLETION_CHANGED') : undefined)
    await openApp(`/tasks?task=${awaiting.id}`)
    const callout = await screen.findByRole('note', { name: '待查看' })
    await fireEvent.click(within(callout).getByRole('button', { name: '标为已查看' }))
    expect(await screen.findByText(/完成结果已经变化，已重新读取/)).toBeVisible()
  })

  it('shows the block reason as text and lets members change or clear it', async () => {
    const requests = api((r) => r.method === 'PATCH' ? Response.json({ ...blocked, work_state: 'todo', blocker: null, version: 'v2' }) : undefined)
    await openApp(`/tasks?task=${blocked.id}`)
    const reason = await screen.findByRole('note', { name: '阻塞原因' })
    expect(within(reason).getByText('等待补充 GitHub 仓库访问权限 <b>x</b>')).toBeVisible()
    expect(reason.querySelector('b')).toBeNull()
    await fireEvent.click(within(reason).getByRole('button', { name: '解除阻塞' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    const patch = requests.find((r) => r.method === 'PATCH')!
    expect(pathOf(patch)).toBe(`/api/v1/tasks/${blocked.id}/block`)
    expect(patch.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(await patch.clone().json()).toEqual({ version: 'opaque-version', blocked: false, reason: '' })
  })

  it('requires a reason to mark a task blocked', async () => {
    const requests = api((r) => r.method === 'PATCH' ? Response.json({ ...task, work_state: 'blocked', blocker: { reason: '等发布窗口', blocked_at: '2026-11-09T01:00:00Z', blocked_by: 'user-a', blocked_by_name: 'brian' } }) : undefined)
    await openApp(`/tasks?task=${task.id}`)
    await fireEvent.click(await screen.findByRole('button', { name: '标记阻塞' }))
    const form = screen.getByRole('form', { name: '阻塞原因' })
    expect(within(form).getByRole('button', { name: '标记阻塞' })).toBeDisabled()
    await fireEvent.update(within(form).getByLabelText('阻塞原因'), '等发布窗口')
    await fireEvent.click(within(form).getByRole('button', { name: '标记阻塞' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 'opaque-version', blocked: true, reason: '等发布窗口' })
  })

  it('lets viewers mark their own view but not block or edit', async () => {
    const requests = api((r) => {
      if (pathOf(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] })
      if (r.method === 'PUT') return Response.json({ ...awaiting, work_state: 'done', result_viewed: true })
    })
    await openApp(`/tasks?task=${awaiting.id}`)
    const callout = await screen.findByRole('note', { name: '待查看' })
    expect(screen.queryByRole('button', { name: '标记阻塞' })).not.toBeInTheDocument()
    await fireEvent.click(within(callout).getByRole('button', { name: '标为已查看' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PUT')).toBe(true))
  })

  it('places the details beside the list on wide screens', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: /1440|1280|761/.test(query), media: query, addEventListener: () => {}, removeEventListener: () => {} }))
    api()
    await openApp(`/tasks?task=${blocked.id}`)
    const side = await screen.findByRole('complementary', { name: '任务详情' })
    expect(await within(side).findByRole('heading', { name: 'GitHub集成验证' })).toBeVisible()
    await fireEvent.click(within(side).getByRole('button', { name: '关闭任务详情' }))
    await vi.waitFor(() => expect(screen.queryByRole('complementary', { name: '任务详情' })).not.toBeInTheDocument())
  })
})
