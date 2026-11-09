// 项目页改造（T-0168，docs/v2/pages-redesign 规范第 5 节）：真实进度（0 任务不显示 100%、待查看仍算完成）、
// 状态计数、需要关注筛选、本周完成的时间窗、最近动态按纯文本显示且读取失败不影响卡片。
import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { fail, mockApi, openApp } from './helpers'
import { project } from './sprint3-fixtures'
import type { Schema } from '../src/services/api'

const pathOf = (r: Request) => new URL(r.url).pathname
const states = (over: Partial<Schema['ProjectTaskStates']> = {}): Schema['ProjectTaskStates'] => ({ todo: 0, running: 0, awaiting_view: 0, blocked: 0, done: 0, ...over })
const busy: Schema['ProjectOut'] = { ...project, id: 'p-busy', title: 'Workspace改版', description: '统一首页与日常工作页面的信息层级。', open_tasks: 8, done_tasks: 12, task_states: states({ running: 3, awaiting_view: 3, blocked: 2, todo: 3, done: 9 }), last_activity_at: new Date(Date.now() - 120_000).toISOString() }
const empty: Schema['ProjectOut'] = { ...project, id: 'p-empty', title: '空项目', open_tasks: 0, done_tasks: 0, task_states: states() }

function api(override?: (r: Request) => Response | undefined) {
  return mockApi(async (r) => {
    const result = override?.(r)
    if (result) return result
    const path = pathOf(r)
    if (path === '/api/v1/projects') return Response.json({ items: [busy, empty], next_cursor: null, total_estimate: null })
    if (path === '/api/v1/projects/summary') return Response.json({ active: 6, attention: 2, completed: 18, as_of: '2026-11-09T00:00:00Z' })
    if (path === '/api/v1/projects/activity') return Response.json({ items: [{ id: 'e1', kind: 'blocked', actor_kind: 'user', actor_name: '开发助手', note: '等待访问权限 <b>x</b>', task_id: 't1', task_title: 'GitHub集成验证', project_id: busy.id, project_title: busy.title, created_at: new Date().toISOString() }], next_cursor: null })
  })
}

describe('项目页（四页改造）', () => {
  it('shows real progress and state counts, never 100% for an empty project', async () => {
    api()
    await openApp('/projects')
    const card = await screen.findByRole('region', { name: '项目 Workspace改版' })
    expect(within(card).getByText('进度 12/20')).toBeVisible()
    expect(within(card).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '60')
    expect(within(card).getByText('进行中 3')).toBeVisible()
    expect(within(card).getByText('待查看 3')).toBeVisible()
    expect(within(card).getByText('阻塞 2')).toBeVisible()
    expect(within(card).getByText(/最近更新 2分钟前/)).toBeVisible()
    const blank = screen.getByRole('region', { name: '项目 空项目' })
    expect(within(blank).getByText('尚无任务')).toBeVisible()
    expect(within(blank).queryByRole('progressbar')).toBeNull()
    expect(within(blank).queryByText(/100%/)).toBeNull()
  })

  it('shows the summary for this week and filters projects that need attention', async () => {
    const requests = api()
    await openApp('/projects')
    const tiles = await screen.findByRole('list', { name: '项目统计' })
    await vi.waitFor(() => expect(within(tiles).getAllByRole('listitem').map((li) => li.textContent?.replace(/\s+/g, ''))).toEqual(['未归档项目6', '需要关注2', '本周完成18项']))
    const summary = new URL(requests.find((r) => pathOf(r) === '/api/v1/projects/summary')!.url).searchParams
    const after = new Date(summary.get('completed_after')!), before = new Date(summary.get('completed_before')!)
    expect(after.getDay()).toBe(1); expect(after.getHours()).toBe(0)
    expect(before.getTime() - after.getTime()).toBeGreaterThanOrEqual(167 * 36e5)
    await fireEvent.click(within(screen.getByRole('group', { name: '筛选项目' })).getByRole('button', { name: /^需要关注/ }))
    await vi.waitFor(() => expect(requests.some((r) => pathOf(r) === '/api/v1/projects' && new URL(r.url).searchParams.get('attention_only') === 'true')).toBe(true))
  })

  it('lists recent activity as plain text and keeps the cards when it fails', async () => {
    api()
    await openApp('/projects')
    const activity = await screen.findByRole('region', { name: '最近动态' })
    expect(await within(activity).findByText(/：等待访问权限 <b>x<\/b>/)).toBeVisible()
    expect(activity.querySelector('b')).toBeNull()
    expect(within(activity).getByRole('link', { name: 'GitHub集成验证' })).toHaveAttribute('href', '/tasks?task=t1')
  })

  it('keeps project cards visible when the activity cannot be read', async () => {
    api((r) => pathOf(r) === '/api/v1/projects/activity' ? fail(503, 'HTTP_ERROR') : undefined)
    await openApp('/projects')
    expect(await screen.findByRole('region', { name: '项目 Workspace改版' })).toBeVisible()
    const activity = screen.getByRole('region', { name: '最近动态' })
    expect(await within(activity).findByRole('alert')).toHaveTextContent('动态暂时读不到')
  })
})
