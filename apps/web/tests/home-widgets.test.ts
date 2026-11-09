// 首页改版（T-0164，docs/v2/HOME_PLAN.md）：工作概况、任务进展、我的待办、提醒与日程，以及它们共用的时间文字。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { me, mockApi, openApp, workspace } from './helpers'
import { overview, widget, layout } from './dashboard-fixtures'
import { project, task } from './sprint3-fixtures'
import type { Schema } from '../src/services/api'
import { dayClock, relativeTime, reminderRange, todoTag } from '../src/widgets/home/format'

const path = (r: Request) => new URL(r.url).pathname
const home = (...cards: Schema['WidgetOut'][]) => ({ ...overview, widgets: cards })
const card = (id: string, widget_type: string, config: Record<string, unknown> = {}): Schema['WidgetOut'] => ({ ...widget, id, widget_type, config, layout })

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('home time helpers', () => {
  const now = new Date(2026, 10, 9, 15, 30)
  it('formats relative update times', () => {
    expect(relativeTime(new Date(2026, 10, 9, 15, 29, 40).toISOString(), now)).toBe('刚刚')
    expect(relativeTime(new Date(2026, 10, 9, 15, 20).toISOString(), now)).toBe('10分钟前')
    expect(relativeTime(new Date(2026, 10, 9, 12, 0).toISOString(), now)).toBe('3小时前')
    expect(relativeTime(new Date(2026, 10, 8, 9, 5).toISOString(), now)).toBe('昨天 09:05')
    expect(relativeTime(new Date(2026, 9, 1, 9, 5).toISOString(), now)).toBe('10月1日')
    expect(relativeTime(new Date(2025, 9, 1, 9, 5).toISOString(), now)).toBe('2025年10月1日')
    expect(relativeTime('not a date', now)).toBe('时间不可用')
  })
  it('labels todos by deadline and urgency', () => {
    expect(todoTag({ due_at: new Date(2026, 10, 9, 9, 0).toISOString(), priority: 0 }, now)).toEqual({ text: '已逾期', tone: 'danger' })
    expect(todoTag({ due_at: null, priority: 4 }, now)).toEqual({ text: '紧急', tone: 'danger' })
    expect(todoTag({ due_at: new Date(2026, 10, 9, 23, 0).toISOString(), priority: 5 }, now)).toEqual({ text: '紧急', tone: 'danger' })
    expect(todoTag({ due_at: new Date(2026, 10, 9, 23, 0).toISOString(), priority: 1 }, now)).toEqual({ text: '今天', tone: 'accent' })
    expect(todoTag({ due_at: new Date(2026, 10, 10, 9, 0).toISOString(), priority: 0 }, now)).toEqual({ text: '明天', tone: 'neutral' })
    expect(todoTag({ due_at: new Date(2026, 10, 20, 9, 0).toISOString(), priority: 0 }, now)).toEqual({ text: '11月20日', tone: 'neutral' })
    expect(todoTag({ due_at: null, priority: 3 }, now)).toBeNull()
  })
  it('writes day and clock text and the reminder range', () => {
    expect(dayClock(new Date(2026, 10, 9, 23, 15).toISOString(), now)).toBe('今天 23:15')
    expect(dayClock(new Date(2026, 10, 10, 9, 0).toISOString(), now)).toBe('明天 09:00')
    expect(dayClock(new Date(2026, 10, 8, 9, 0).toISOString(), now)).toBe('昨天 09:00')
    expect(dayClock(new Date(2026, 10, 12, 11, 0).toISOString(), now)).toBe('11月12日 11:00')
    expect(reminderRange(2, now)).toEqual({ start: now.toISOString(), end: new Date(2026, 10, 11).toISOString() })
    expect(reminderRange(1, now).end).toBe(new Date(2026, 10, 10).toISOString())
  })
})

describe('工作概况 widget', () => {
  it('shows the four task counts and the upcoming reminders as linked tiles without a card frame', async () => {
    const now = new Date(2026, 10, 9, 22, 0)
    vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(now)
    const events = [1, 2, 3].map((n) => ({ id: `e${n}`, title: `日程${n}`, all_day: false, start_at: new Date(2026, 10, 9 + n, 9).toISOString(), end_at: new Date(2026, 10, 9 + n, 10).toISOString(), start_date: null, end_date: null, timezone: 'UTC', description: '', location: '', owner_id: null, version: 1, created_at: now.toISOString(), updated_at: now.toISOString() }))
    const requests = mockApi((r) => {
      if (path(r) === '/api/v1/dashboard/overview') return Response.json(home(card('w-summary', 'work_summary')))
      if (path(r) === '/api/v1/tasks/progress') return Response.json({ counts: { all: 17, todo: 8, in_progress: 4, review: 3, blocked: 2 }, attention: 5, items: [] })
      if (path(r) === '/api/v1/calendar/events') return Response.json({ items: events, next_cursor: null, total_estimate: 3 })
      return undefined
    })
    await openApp('/')
    const article = await screen.findByRole('article', { name: '工作概况' })
    expect(article).toHaveClass('is-bare')
    expect(article).not.toHaveClass('panel')
    const tiles = within(article).getByRole('list', { name: '工作概况' })
    await vi.waitFor(() => expect(within(tiles).getByRole('link', { name: /待办事项\s*8/ })).toHaveAttribute('href', '/tasks'))
    expect(within(tiles).getByRole('link', { name: /进行中\s*4/ })).toHaveAttribute('href', '/tasks/timeline')
    expect(within(tiles).getByRole('link', { name: /已完成待查看\s*3/ })).toBeVisible()
    expect(within(tiles).getByRole('link', { name: /阻塞任务\s*2/ })).toBeVisible()
    await vi.waitFor(() => expect(within(tiles).getByRole('link', { name: /近期提醒\s*3/ })).toHaveAttribute('href', '/calendar'))
    const range = new URL(requests.find((r) => path(r) === '/api/v1/calendar/events')!.url).searchParams
    expect(range.get('start')).toBe(now.toISOString())
    expect(range.get('end')).toBe(new Date(2026, 10, 11).toISOString())
    const progress = requests.find((r) => path(r) === '/api/v1/tasks/progress')!
    expect(progress.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })
  it('shows a dash instead of 0 when a count cannot be read', async () => {
    mockApi((r) => {
      if (path(r) === '/api/v1/dashboard/overview') return Response.json(home(card('w-summary', 'work_summary')))
      if (path(r) === '/api/v1/tasks/progress') return Response.json({ error: { code: 'HTTP_ERROR', message: '请求失败', request_id: 'req', details: {} } }, { status: 503 })
      return undefined
    })
    await openApp('/')
    const article = await screen.findByRole('article', { name: '工作概况' })
    expect(await within(article).findByRole('alert')).toBeVisible()
    expect(within(article).getByRole('link', { name: /待办事项\s*—/ })).toBeVisible()
  })
})

describe('任务进展 widget', () => {
  const item = (over: Partial<Schema['TaskProgressItem']>): Schema['TaskProgressItem'] => ({ id: 't', title: '任务', project_id: project.id, project_title: 'Workspace', status: 'todo', executor: null, due_at: null, updated_at: new Date().toISOString(), blocker: null, ...over })
  const items = [
    item({ id: 'review-1', title: '首页改版方案', status: 'review', executor: { kind: 'instance', id: 'i1', name: '设计助手' } }),
    item({ id: 'blocked-1', title: 'GitHub集成验证', status: 'blocked', executor: { kind: 'instance', id: 'i2', name: '开发助手' }, blocker: { issue_id: 'issue-1', number: 3, title: '等待补充访问权限 <b>x</b>', count: 2 } }),
    item({ id: 'running-1', title: '新闻早报整理', status: 'in_progress', executor: { kind: 'instance', id: 'i3', name: '资讯助手' } }),
    item({ id: 'todo-1', title: '整理本周工作计划', status: 'todo', executor: { kind: 'user', id: 'user-a', name: 'brian' } }),
  ]
  it('shows the attention badge, status tabs and one action per row', async () => {
    const requests = mockApi((r) => {
      if (path(r) === '/api/v1/dashboard/overview') return Response.json(home(card('w-progress', 'task_progress')))
      if (path(r) === '/api/v1/tasks/progress') {
        const status = new URL(r.url).searchParams.get('status')
        return Response.json({ counts: { all: 4, todo: 1, in_progress: 1, review: 1, blocked: 1 }, attention: 2, items: status === 'blocked' ? [items[1]] : items })
      }
      return undefined
    })
    const { router } = await openApp('/')
    const article = await screen.findByRole('article', { name: '任务进展' })
    expect(await within(article).findByText('需要你关注 2 项')).toBeVisible()
    const tabs = within(article).getByRole('group', { name: '按状态筛选任务' })
    expect(within(tabs).getAllByRole('button').map((b) => b.textContent?.replace(/\s+/g, ''))).toEqual(['全部4', '待办1', '进行中1', '待查看1', '阻塞1'])
    expect(within(tabs).getByRole('button', { name: /全部/ })).toHaveAttribute('aria-pressed', 'true')
    const table = within(article).getByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(5)
    // 阻塞 Issue 的标题来自人或智能体：按纯文本显示
    expect(within(table).getByText('等待补充访问权限 <b>x</b>（共 2 个阻塞）')).toBeVisible()
    expect(table.querySelector('b')).toBeNull()
    expect(within(table).getByText('设计助手')).toBeVisible()
    expect(within(table).getByText('已完成 · 待查看')).toBeVisible()
    expect(within(table).getByRole('link', { name: '查看结果：首页改版方案' })).toHaveAttribute('href', '/tasks?task=review-1')
    expect(within(table).getByRole('link', { name: '处理阻塞：GitHub集成验证' })).toHaveAttribute('href', '/issues/issue-1')
    expect(within(table).getByRole('link', { name: '查看进展：新闻早报整理' })).toHaveAttribute('href', `/tasks/timeline?task=running-1&project=${project.id}`)
    expect(within(table).getByRole('link', { name: '打开任务：整理本周工作计划' })).toHaveAttribute('href', '/tasks?task=todo-1')
    expect(within(article).getByRole('link', { name: '查看全部 4 项任务' })).toHaveAttribute('href', '/tasks')
    expect(new URL(requests.find((r) => path(r) === '/api/v1/tasks/progress' && new URL(r.url).searchParams.get('status') === 'all')!.url).searchParams.get('limit')).toBe('7')

    await fireEvent.click(within(tabs).getByRole('button', { name: /阻塞/ }))
    await vi.waitFor(() => expect(within(article).getByRole('table').querySelectorAll('tbody tr')).toHaveLength(1))
    expect(within(tabs).getByRole('button', { name: /阻塞/ })).toHaveAttribute('aria-pressed', 'true')
    await fireEvent.click(within(article).getByRole('link', { name: 'GitHub集成验证' }))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/tasks?task=blocked-1'))
  })
  it('hides the badge and explains an empty list', async () => {
    mockApi((r) => path(r) === '/api/v1/dashboard/overview' ? Response.json(home(card('w-progress', 'task_progress'))) : undefined)
    await openApp('/')
    const article = await screen.findByRole('article', { name: '任务进展' })
    expect(await within(article).findByText(/还没有需要跟进的任务/)).toBeVisible()
    expect(within(article).queryByText(/需要你关注/)).toBeNull()
  })
})

describe('我的待办 widget', () => {
  const now = new Date()
  const todayTask: Schema['TaskOut'] = { ...task, id: 'mine-1', title: '确认首页改版方案', priority: 0, due_at: new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59).toISOString(), assignee_user_id: me.user.id }
  const urgentTask: Schema['TaskOut'] = { ...task, id: 'mine-2', title: '补充GitHub仓库权限', priority: 4, due_at: null, assignee_user_id: me.user.id }
  function api(role: Schema['WorkspaceOut']['role'] = 'owner') {
    return mockApi((r) => {
      if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [{ ...workspace, role }] })
      if (path(r) === '/api/v1/dashboard/overview') return Response.json(home(card('w-todos', 'my_todos')))
      if (path(r) === '/api/v1/tasks' && r.method === 'GET') return Response.json({ items: new URL(r.url).searchParams.get('assignee') === 'me' ? [todayTask, urgentTask] : [], next_cursor: null, total_estimate: null })
      if (path(r) === '/api/v1/tasks/mine-1' && r.method === 'PATCH') return Response.json({ ...todayTask, done: true, done_at: now.toISOString(), version: '2' })
      if (path(r) === '/api/v1/tasks' && r.method === 'POST') return Response.json({ ...task, id: 'new-1', title: '整理明日工作重点', assignee_user_id: me.user.id }, { status: 201 })
      return undefined
    })
  }
  it('lists my open tasks with tags and completes one with an idempotent write', async () => {
    const requests = api()
    await openApp('/')
    const article = await screen.findByRole('article', { name: '我的待办' })
    const list = await within(article).findByRole('list', { name: '我的待办' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('确认首页改版方案今天')
    expect(rows[1]).toHaveTextContent('补充GitHub仓库权限紧急')
    const read = new URL(requests.find((r) => path(r) === '/api/v1/tasks' && new URL(r.url).searchParams.get('assignee') === 'me')!.url).searchParams
    expect(Object.fromEntries(read)).toMatchObject({ assignee: 'me', status: 'open', sort: 'due_at', limit: '5' })

    await fireEvent.click(within(rows[0]!).getByRole('checkbox', { name: '完成：确认首页改版方案' }))
    expect(await within(article).findByText('已完成：确认首页改版方案')).toBeVisible()
    const patch = requests.find((r) => r.method === 'PATCH')!
    expect(path(patch)).toBe('/api/v1/tasks/mine-1')
    expect(patch.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(patch.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(await patch.clone().json()).toEqual({ version: todayTask.version, done: true })
  })
  it('adds a todo assigned to me in the chosen project', async () => {
    const requests = api()
    await openApp('/')
    const article = await screen.findByRole('article', { name: '我的待办' })
    await fireEvent.click(await within(article).findByRole('button', { name: '添加待办' }))
    const form = within(article).getByRole('form', { name: '添加待办' })
    await vi.waitFor(() => expect(within(form).getByLabelText('放进哪个项目')).toHaveValue(project.id))
    await fireEvent.update(within(form).getByLabelText('待办内容'), '  整理明日工作重点 ')
    await fireEvent.click(within(form).getByRole('button', { name: '添加' }))
    expect(await within(article).findByText('已添加待办：整理明日工作重点')).toBeVisible()
    const post = requests.find((r) => r.method === 'POST' && path(r) === '/api/v1/tasks')!
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await post.clone().json()).toMatchObject({ title: '整理明日工作重点', project_id: project.id, assignee_user_id: me.user.id, priority: 0, due_at: null })
    expect(within(article).queryByRole('form', { name: '添加待办' })).toBeNull()
  })
  it('is read-only for viewers', async () => {
    const requests = api('viewer')
    await openApp('/')
    const article = await screen.findByRole('article', { name: '我的待办' })
    const box = await within(article).findByRole('checkbox', { name: '完成：确认首页改版方案' })
    expect(box).toBeDisabled()
    expect(within(article).queryByRole('button', { name: '添加待办' })).toBeNull()
    expect(requests.some((r) => r.method !== 'GET')).toBe(false)
  })
})

describe('提醒与日程 widget', () => {
  it('adds a reminder with the calendar form and reloads the agenda', async () => {
    const created: Schema['CalendarEventOut'][] = []
    const requests = mockApi((r) => {
      if (path(r) === '/api/v1/dashboard/overview') return Response.json(home(card('w-agenda', 'calendar_agenda', { days: 7, limit: 5 })))
      if (path(r) === '/api/v1/calendar/events' && r.method === 'GET') return Response.json({ items: created, next_cursor: null, total_estimate: created.length })
      if (path(r) === '/api/v1/calendar/events' && r.method === 'POST') {
        return r.clone().json().then((body: Schema['CalendarEventCreate']) => {
          const event = { id: 'new-event', title: body.title, description: '', location: '', all_day: false, start_at: body.start_at!, end_at: body.end_at!, start_date: null, end_date: null, timezone: body.timezone, owner_id: me.user.id, version: 1, created_at: body.start_at!, updated_at: body.start_at! }
          created.push(event)
          return Response.json(event, { status: 201 })
        })
      }
      return undefined
    })
    await openApp('/')
    const article = await screen.findByRole('article', { name: '提醒与日程' })
    expect(await within(article).findByText('7 天内没有日程。')).toBeVisible()
    await fireEvent.click(within(article).getByRole('button', { name: '添加提醒' }))
    const dialog = await screen.findByRole('dialog', { name: '添加提醒' })
    await fireEvent.update(within(dialog).getByLabelText('日程标题'), '查看发布结果')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存日程' }))
    expect(await within(article).findByText('已添加：查看发布结果')).toBeVisible()
    await vi.waitFor(() => expect(screen.queryByRole('dialog', { name: '添加提醒' })).toBeNull())
    expect(await within(article).findByText('查看发布结果')).toBeVisible()
    const post = requests.find((r) => r.method === 'POST')!
    expect(await post.clone().json()).toMatchObject({ title: '查看发布结果', all_day: false })
  })
})
