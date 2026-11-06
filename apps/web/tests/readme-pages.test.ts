// 项目与任务的 README（T-0122）：项目页默认显示 README、自动部分带角标；任务抽屉的 README 视图；项目卡片的“打开”。
import { fireEvent, screen, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockApi, openApp } from './helpers'
import { project, task } from './sprint3-fixtures'
import type { Schema } from '../src/services/api'

const pathOf = (r: Request) => new URL(r.url).pathname
const author = { kind: 'system' as const, id: null, name: '工作台' }
const body = (title: string, auto: string) => `# ${title}\n\n## 简介\n\n把 2.0 发出去。\n\n## 成员\n<!-- myws:auto 成员 -->\n${auto}\n<!-- /myws:auto -->\n\n## 约定\n\n- 小步提交\n`
const note = (over: Partial<Schema['NoteOut']> = {}): Schema['NoteOut'] => ({
  id: 'note-readme', space_id: 'space-p1', space_kind: 'project', space_name: project.title, title: 'README', type: 'readme', folder: '', tags: [], pinned: true,
  conflict: false, source_kind: 'system', updated_by: author as unknown as Schema['NoteOut']['updated_by'], updated_at: '2026-11-06T08:00:00Z', version: 3,
  body: body(project.title, '- 管理员：测试用户\n- 智能体：\n  - 「甲」（读写）'), aliases: [], review_at: null, conflict_note: null,
  created_by: author as unknown as Schema['NoteOut']['created_by'], created_at: '2026-11-06T08:00:00Z', deleted_at: null, purge_after: null, reason: '工作台自动更新', evidence: [],
  links: [], backlinks: [], usage: { reads_30d: 0, by_agent: [], last_read_at: null }, ...over,
})
afterEach(() => { vi.restoreAllMocks() })

describe('README 页面', () => {
  it('opens a project on its README with auto-updated sections marked', async () => {
    const requests = mockApi(async (r) => {
      const path = pathOf(r)
      if (path === `/api/v1/projects/${project.id}`) return Response.json(project)
      if (path === `/api/v1/projects/${project.id}/readme`) return Response.json({ note: note(), project_id: project.id, task_id: null, auto_sections: ['成员'] })
      return undefined
    })
    await openApp(`/projects/${project.id}`)
    const readme = await screen.findByRole('region', { name: 'README' })
    expect(within(readme).getByRole('heading', { name: '简介' })).toBeVisible()
    const auto = within(readme).getByLabelText('自动更新：成员')
    expect(auto).toHaveTextContent('自动更新')
    expect(auto).toHaveTextContent('「甲」（读写）')
    expect(within(readme).getByText('小步提交').closest('[aria-label^="自动更新"]')).toBeNull()
    expect(within(readme).getByRole('link', { name: '编辑' })).toHaveAttribute('href', '/knowledge/notes/note-readme/edit')
    const tabs = screen.getByRole('group', { name: '项目' })
    expect(within(tabs).getByRole('button', { name: 'README' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(tabs).getByRole('link', { name: '任务' })).toHaveAttribute('href', `/tasks?project_id=${project.id}`)
    expect(within(tabs).getByRole('link', { name: '知识库' })).toHaveAttribute('href', '/knowledge/spaces/space-p1')
    expect(requests.some((r) => pathOf(r).endsWith('/readme'))).toBe(true)
  })

  it('keeps viewers from editing', async () => {
    mockApi(async (r) => {
      const path = pathOf(r)
      if (path === '/api/v1/me') return Response.json({ user: { id: 'user-a', email: 'test@example.com', display_name: '测试用户', created_at: '2026-10-30T00:00:00Z' }, workspaces: [{ id: 'workspace-a', name: '个人空间', role: 'viewer', created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z' }] })
      if (path === `/api/v1/projects/${project.id}`) return Response.json(project)
      if (path === `/api/v1/projects/${project.id}/readme`) return Response.json({ note: note(), project_id: project.id, task_id: null, auto_sections: ['成员'] })
      return undefined
    })
    await openApp(`/projects/${project.id}`)
    const readme = await screen.findByRole('region', { name: 'README' })
    expect(within(readme).queryByRole('link', { name: '编辑' })).toBeNull()
    expect(within(readme).getByRole('link', { name: '在知识库中查看' })).toBeVisible()
  })

  it('shows the task README in the task drawer', async () => {
    const taskNote = note({ id: 'note-task', title: `${task.title}（任务 88888888）`, folder: '任务导览', pinned: false, body: body(task.title, '- 执行者：还没有分配') })
    const requests = mockApi(async (r) => {
      const path = pathOf(r)
      if (path === '/api/v1/projects') return Response.json({ items: [project], next_cursor: null, total_estimate: null })
      if (path === '/api/v1/tasks' && r.method === 'GET') return Response.json({ items: [task], next_cursor: null, total_estimate: null })
      if (path === `/api/v1/tasks/${task.id}` && r.method === 'GET') return Response.json(task)
      if (path === '/api/v1/tasks/assignees') return Response.json({ users: [], instances: [] })
      if (path === `/api/v1/tasks/${task.id}/runs`) return Response.json({ items: [] })
      if (path === `/api/v1/tasks/${task.id}/readme`) return Response.json({ note: taskNote, project_id: project.id, task_id: task.id, auto_sections: ['成员'] })
      return undefined
    })
    await openApp(`/tasks?task=${task.id}`)
    const views = await screen.findByRole('group', { name: '任务详情视图' })
    expect(within(views).getByRole('button', { name: '详情' })).toHaveAttribute('aria-pressed', 'true')
    expect(requests.some((r) => pathOf(r).endsWith('/readme'))).toBe(false) // 打开 README 视图才请求
    await fireEvent.click(within(views).getByRole('button', { name: 'README' }))
    const readme = await screen.findByRole('region', { name: 'README' })
    expect(within(readme).getByLabelText('自动更新：成员')).toHaveTextContent('还没有分配')
    expect(screen.queryByRole('form', { name: '任务编辑' })).toBeNull()
    await fireEvent.click(within(views).getByRole('button', { name: '详情' }))
    expect(await screen.findByRole('form', { name: '任务编辑' })).toBeVisible()
  })

  it('links each project card to its page', async () => {
    mockApi(async (r) => (pathOf(r) === '/api/v1/projects' ? Response.json({ items: [project], next_cursor: null, total_estimate: null }) : undefined))
    await openApp('/projects')
    const link = await screen.findByRole('link', { name: `打开项目 ${project.title}` })
    expect(link).toHaveAttribute('href', `/projects/${project.id}`)
  })
})
