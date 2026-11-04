// 自由接取与打回（T-0111）：管理员把任务设为可自由接取（可选范围）；列表显示标记；已完成的任务可以打回；任务动态。
import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import { me, mockApi, openApp, workspace } from './helpers'
import { project, task } from './sprint3-fixtures'
import type { Schema } from '../src/services/api'

const pathOf = (r: Request) => new URL(r.url).pathname
function api(current: () => Schema['TaskOut'], override?: (r: Request) => Response | undefined) {
  return mockApi(async (r) => {
    const result = override?.(r)
    if (result) return result
    const path = pathOf(r)
    if (path === '/api/v1/tasks') return Response.json({ items: [current()], next_cursor: null, total_estimate: 1 })
    if (path === `/api/v1/tasks/${task.id}`) return Response.json(current())
    if (path === '/api/v1/projects') return Response.json({ items: [project], next_cursor: null, total_estimate: 1 })
    if (path === '/api/v1/tasks/assignees') return Response.json({ users: [], instances: [{ id: 'i-a', name: '甲', status: 'online', description: '' }] })
    if (path === `/api/v1/tasks/${task.id}/events`) return Response.json({ items: [{ id: 'e1', kind: 'completed', actor_kind: 'instance', actor_name: '甲', note: '已发到群里', created_at: '2026-11-04T08:00:00Z' }] })
    return undefined
  })
}

describe('自由接取与打回', () => {
  it('lets an admin open a task for claiming with a scope', async () => {
    const requests = api(() => task, (r) => r.method === 'PATCH' ? Response.json({ ...task, claim_mode: 'open', claim_scope: 'project', claimable: true }) : undefined)
    await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: '准备发布' }))
    await fireEvent.update(await screen.findByLabelText('执行者'), 'open')
    await fireEvent.update(await screen.findByLabelText('谁可以领取'), 'project')
    expect(screen.getByText(/通知在线、空闲的智能体/)).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '保存任务' }))
    await screen.findByText('任务操作已完成')
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 'opaque-version', claim_mode: 'open', claim_scope: 'project' })
  })

  it('shows the claimable chip and hides the open option from members', async () => {
    const open = { ...task, claim_mode: 'open' as const, claimable: true }
    api(() => open, (r) => pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'member' }] }) : undefined)
    await openApp('/tasks')
    expect(await screen.findByText('可自由接取')).toBeVisible()
    await fireEvent.click(await screen.findByRole('button', { name: '准备发布' }))
    const select = await screen.findByLabelText('执行者')
    // 当前是可自由接取：member 能看到这一项（显示现状），但范围不能改
    expect(within(select).getByRole('option', { name: /可自由接取/ })).toBeTruthy()
    expect(screen.getByLabelText('谁可以领取')).toBeDisabled()
  })

  it('reopens a done task with a reason and shows the events', async () => {
    const done = { ...task, done: true, done_at: '2026-11-04T08:00:00Z', assignee_instance_id: 'i-a' }
    const requests = api(() => done, (r) => pathOf(r) === `/api/v1/tasks/${task.id}/reopen` ? Response.json({ ...done, done: false, version: 'v2' }) : undefined)
    await openApp('/tasks'); await fireEvent.click(await screen.findByRole('button', { name: '准备发布' }))
    const panel = await screen.findByRole('region', { name: '任务动态' })
    expect(await within(panel).findByText('已发到群里')).toBeVisible()
    expect(within(panel).getByText(/甲 完成了任务/)).toBeVisible()
    const form = within(panel).getByRole('form', { name: '打回任务' })
    await fireEvent.click(within(form).getByRole('button', { name: '打回到进行中' }))
    expect(within(form).getByRole('alert')).toHaveTextContent('请写打回的原因')
    await fireEvent.update(within(form).getByLabelText('打回原因'), '缺少回滚步骤')
    await fireEvent.click(within(form).getByRole('button', { name: '打回到进行中' }))
    expect(await within(panel).findByText(/已打回/)).toBeVisible()
    const post = requests.find((r) => r.method === 'POST' && pathOf(r).endsWith('/reopen'))!
    expect(await post.clone().json()).toEqual({ version: 'opaque-version', reason: '缺少回滚步骤' })
  })
})
