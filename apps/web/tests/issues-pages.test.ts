// 项目里的 Issues（T-0124，方案 8.8）：项目页的 Issues 标签（筛选、新建先选类型再套模板）、
// 详情（安全渲染、#编号与 @、时间线、关闭要原因、管理员的锁定与隐藏、打开即已读）、任务抽屉的“讨论”。
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { me, mockApi, openApp } from './helpers'
import { project, task } from './sprint3-fixtures'
import type { Schema } from '../src/services/api'

const pathOf = (r: Request) => new URL(r.url).pathname
const at = '2026-11-06T08:00:00Z'
const owner: Schema['Actor'] = { kind: 'user', id: 'user-a', name: '测试用户' }
const agentA: Schema['Actor'] = { kind: 'instance', id: 'i-a', name: '甲' }
const issue = (over: Partial<Schema['ProjectIssueOut']> = {}): Schema['ProjectIssueOut'] => ({
  id: 'issue-1', project_id: project.id, number: 1, title: '接口怎么约定', kind: 'contract', labels: ['api'], state: 'open', close_reason: null,
  duplicate_of: null, locked: false, author: owner, assignees: [agentA], tasks: [], comment_count: 1, unread: true, agent_paused: false,
  closed_at: null, last_activity_at: at, created_at: at, version: 4, ...over,
})
const detail = (over: Partial<Schema['IssueDetail']> = {}): Schema['IssueDetail'] => ({
  ...issue(), body: '## 调用方\n\n见 #2，@甲 看一下。\n\n<script>alert(1)</script><img src=x onerror="alert(2)">\n\n`#3 不是引用`',
  comments: [
    { id: 'c-1', reply_to: null, body: '字段名用下划线', author: agentA, edited_at: null, hidden: false, created_at: '2026-11-06T08:02:00Z' },
    { id: 'c-2', reply_to: null, body: null, author: agentA, edited_at: null, hidden: true, created_at: '2026-11-06T08:03:00Z' },
  ],
  events: [
    { id: 'e-1', kind: 'opened', actor: owner, data: { kind: 'contract' }, created_at: '2026-11-06T08:00:00Z' },
    { id: 'e-2', kind: 'assigned', actor: owner, data: { instance_id: 'i-a' }, created_at: '2026-11-06T08:01:00Z' },
  ],
  pulls: [{ url: 'https://github.com/team/app/pull/7', title: '加用户接口', state: 'open', merged: false }],
  can_close: true, can_moderate: true, ...over,
})
const templates = { items: [{ kind: 'contract', label: '接口约定', body: '## 调用方\n\n\n## 提供方\n' }, { kind: 'bug', label: '缺陷', body: '## 现象\n' }] }
afterEach(() => { vi.restoreAllMocks() })

describe('Issues 页面', () => {
  it('lists, filters and opens a new issue from a template', async () => {
    const requests = mockApi(async (r) => {
      const path = pathOf(r)
      if (path === `/api/v1/projects/${project.id}`) return Response.json(project)
      if (path === `/api/v1/projects/${project.id}/issues` && r.method === 'GET') return Response.json({ items: [issue()], open_count: 1, closed_count: 2 })
      if (path === '/api/v1/issue-templates') return Response.json(templates)
      if (path === `/api/v1/projects/${project.id}/issues` && r.method === 'POST') return Response.json(detail({ id: 'issue-9', number: 9, unread: false }), { status: 201 })
      if (path === '/api/v1/issues/issue-9') return Response.json(detail({ id: 'issue-9', number: 9, unread: false }))
      return undefined
    })
    await openApp(`/projects/${project.id}?tab=issues`)
    const issues = await screen.findByRole('region', { name: 'Issues' })
    expect(await within(issues).findByRole('link', { name: /#1 接口怎么约定/ })).toHaveAttribute('href', '/issues/issue-1')
    expect(within(issues).getByRole('button', { name: '未关闭 1' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(issues).getByRole('button', { name: '已关闭 2' })).toBeVisible()
    expect(within(issues).getByLabelText('有未读动态')).toBeVisible()
    await fireEvent.update(within(issues).getByLabelText('类型'), 'bug')
    await waitFor(() => expect(requests.some((r) => pathOf(r).endsWith('/issues') && new URL(r.url).searchParams.get('kind') === 'bug')).toBe(true))

    await fireEvent.click(within(issues).getByRole('button', { name: '新建 Issue' }))
    const form = await screen.findByRole('form', { name: '新建 Issue' })
    await fireEvent.click(within(form).getByLabelText('接口约定'))
    const body = within(form).getByLabelText(/正文/) as HTMLTextAreaElement
    await waitFor(() => expect(body.value).toContain('## 提供方'))
    await fireEvent.update(within(form).getByLabelText('标题'), '用户接口的字段')
    await fireEvent.update(within(form).getByLabelText(/标签/), 'api，后端')
    await fireEvent.click(within(form).getByRole('button', { name: '开 Issue' }))
    const created = await waitFor(() => { const found = requests.find((r) => r.method === 'POST' && pathOf(r) === `/api/v1/projects/${project.id}/issues`); expect(found).toBeTruthy(); return found! })
    expect(await created.clone().json()).toMatchObject({ title: '用户接口的字段', kind: 'contract', labels: ['api', '后端'], task_ids: [] })
    expect(await screen.findByRole('article', { name: 'Issue #9' })).toBeVisible()
  })

  it('renders an issue safely with a merged timeline and moderation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = mockApi(async (r) => {
      const path = pathOf(r)
      if (path === '/api/v1/issues/issue-1' && r.method === 'GET') return Response.json(detail())
      if (path === '/api/v1/issues/issue-1/read') return new Response(null, { status: 204 })
      if (path === '/api/v1/issues/issue-1' && r.method === 'PATCH') return Response.json(detail({ state: 'closed', close_reason: 'wontfix', version: 5 }))
      if (path === '/api/v1/issues/issue-1/comments/c-1/hide') return Response.json({ ...detail().comments[0], body: null, hidden: true })
      return undefined
    })
    await openApp('/issues/issue-1')
    const view = await screen.findByRole('article', { name: 'Issue #1' })
    const body = within(view).getByRole('region', { name: '正文' })
    expect(body.querySelector('script')).toBeNull()
    expect(body.querySelector('img')).toBeNull()
    expect(within(body).getByRole('link', { name: '#2' })).toHaveAttribute('href', `/projects/${project.id}/issues/2`)
    expect(within(body).queryByRole('link', { name: '#3' })).toBeNull() // 代码里的不算
    expect(body.querySelector('.issue-mention')?.textContent).toBe('@甲')
    const timeline = within(view).getByRole('list', { name: '时间线' })
    const entries = within(timeline).getAllByRole('listitem').map((li) => li.textContent ?? '')
    expect(entries[0]).toContain('开了这个 Issue')
    expect(entries[1]).toContain('指派给 智能体「甲」')
    expect(entries[2]).toContain('字段名用下划线')
    expect(entries[3]).toContain('这条评论已被管理员隐藏')
    const side = within(view).getByRole('complementary', { name: 'Issue 信息' })
    expect(within(side).getByRole('link', { name: '加用户接口' })).toHaveAttribute('href', 'https://github.com/team/app/pull/7')
    await waitFor(() => expect(requests.some((r) => r.method === 'POST' && pathOf(r) === '/api/v1/issues/issue-1/read')).toBe(true))

    const close = within(side).getByRole('form', { name: '关闭 Issue' })
    await fireEvent.update(within(close).getByLabelText('关闭原因'), 'wontfix')
    await fireEvent.click(within(close).getByRole('button', { name: '关闭' }))
    const patch = await waitFor(() => { const found = requests.find((r) => r.method === 'PATCH'); expect(found).toBeTruthy(); return found! })
    expect(await patch.clone().json()).toEqual({ version: 4, state: 'closed', close_reason: 'wontfix' })
    await fireEvent.click(within(timeline).getAllByRole('button', { name: '隐藏' })[0])
    await waitFor(() => expect(requests.some((r) => pathOf(r).endsWith('/comments/c-1/hide'))).toBe(true))
    expect(confirm).not.toHaveBeenCalled()
  })

  it('keeps viewers to reading', async () => {
    mockApi(async (r) => {
      const path = pathOf(r)
      if (path === '/api/v1/me') return Response.json({ ...me, workspaces: [{ ...me.workspaces[0], role: 'viewer' }] })
      if (path === `/api/v1/projects/${project.id}`) return Response.json(project)
      if (path === `/api/v1/projects/${project.id}/issues`) return Response.json({ items: [issue()], open_count: 1, closed_count: 0 })
      if (path === '/api/v1/issues/issue-1') return Response.json(detail({ unread: false, can_close: false, can_moderate: false }))
      return undefined
    })
    await openApp(`/projects/${project.id}?tab=issues`)
    const issues = await screen.findByRole('region', { name: 'Issues' })
    await within(issues).findByRole('link', { name: /#1/ })
    expect(within(issues).queryByRole('button', { name: '新建 Issue' })).toBeNull()
    await fireEvent.click(within(issues).getByRole('link', { name: /#1/ }))
    const view = await screen.findByRole('article', { name: 'Issue #1' })
    expect(within(view).queryByRole('form', { name: '评论' })).toBeNull()
    expect(within(view).queryByRole('form', { name: '关闭 Issue' })).toBeNull()
    expect(within(view).queryByRole('button', { name: '锁定' })).toBeNull()
  })

  it('shows linked issues in the task drawer and links new ones to the task', async () => {
    const blocker = issue({ id: 'issue-2', number: 2, title: '等数据库账号', kind: 'blocker', unread: false })
    const requests = mockApi(async (r) => {
      const path = pathOf(r)
      if (path === '/api/v1/projects') return Response.json({ items: [project], next_cursor: null, total_estimate: null })
      if (path === '/api/v1/tasks' && r.method === 'GET') return Response.json({ items: [task], next_cursor: null, total_estimate: null })
      if (path === `/api/v1/tasks/${task.id}` && r.method === 'GET') return Response.json(task)
      if (path === '/api/v1/tasks/assignees') return Response.json({ users: [], instances: [] })
      if (path === `/api/v1/tasks/${task.id}/runs`) return Response.json({ items: [] })
      if (path === `/api/v1/tasks/${task.id}/issues`) return Response.json({ items: [blocker], open_count: 1, closed_count: 0 })
      if (path === '/api/v1/issue-templates') return Response.json(templates)
      if (path === `/api/v1/projects/${project.id}/issues` && r.method === 'POST') return Response.json(detail({ id: 'issue-3', number: 3 }), { status: 201 })
      if (path === '/api/v1/issues/issue-3') return Response.json(detail({ id: 'issue-3', number: 3, unread: false }))
      return undefined
    })
    await openApp(`/tasks?task=${task.id}`)
    const views = await screen.findByRole('group', { name: '任务详情视图' })
    expect(requests.some((r) => pathOf(r).endsWith('/issues'))).toBe(false) // 打开“讨论”才请求
    await fireEvent.click(within(views).getByRole('button', { name: '讨论' }))
    const discussion = await screen.findByRole('region', { name: '讨论' })
    expect(await within(discussion).findByRole('link', { name: '#2 等数据库账号' })).toHaveAttribute('href', '/issues/issue-2')
    expect(within(discussion).getByText(/智能体完成不了这个任务/)).toBeVisible()
    await fireEvent.click(within(discussion).getByRole('button', { name: '新建 Issue' }))
    const form = await within(discussion).findByRole('form', { name: '新建 Issue' })
    await fireEvent.click(within(form).getByLabelText('缺陷'))
    await fireEvent.update(within(form).getByLabelText('标题'), '发布脚本报错')
    await fireEvent.click(within(form).getByRole('button', { name: '开 Issue' }))
    const created = await waitFor(() => { const found = requests.find((r) => r.method === 'POST' && pathOf(r).endsWith('/issues')); expect(found).toBeTruthy(); return found! })
    expect(await created.clone().json()).toMatchObject({ kind: 'bug', title: '发布脚本报错', task_ids: [task.id] })
  })
})
