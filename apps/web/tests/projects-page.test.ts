// 项目页（T-0082）：工作台自己的项目，member 可以新建 / 改名 / 归档 / 删除，viewer 只读；版本冲突提示重新加载。
import { fireEvent, screen, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fail, me, mockApi, openApp, workspace } from './helpers'
import { project, task } from './sprint3-fixtures'
import type { Schema } from '../src/services/api'

const pathOf = (r: Request) => new URL(r.url).pathname
const page = (items: Schema['ProjectOut'][]) => ({ items, next_cursor: null, total_estimate: null })
function projectsApi(override?: (r: Request) => Response | undefined, list: () => Schema['ProjectOut'][] = () => [project]) {
  return mockApi(async (r) => {
    const result = override?.(r)
    if (result) return result
    if (pathOf(r) === '/api/v1/projects' && r.method === 'GET') return Response.json(page(list()))
    if (pathOf(r) === '/api/v1/tasks') return Response.json({ items: [task], next_cursor: null, total_estimate: null })
    return undefined
  })
}
const region = async (title: string) => screen.findByRole('region', { name: `项目 ${title}` })
afterEach(() => { vi.restoreAllMocks() })

describe('项目页面', () => {
  it('lists projects with task counts and no external links', async () => {
    projectsApi(undefined, () => [project, { ...project, id: 'p2', title: '旧项目', archived: true, open_tasks: 0, done_tasks: 4 }])
    await openApp('/projects')
    const card = await region('发布计划')
    expect(within(card).getByText('进度 0/1')).toBeVisible()
    expect(within(card).getByRole('link', { name: '查看任务' })).toHaveAttribute('href', `/tasks?project_id=${project.id}`)
    expect(within(await region('旧项目')).getByText('已归档')).toBeVisible()
    expect(document.body).not.toHaveTextContent('Vikunja')
    expect(document.body).not.toHaveTextContent('在 Vikunja 中打开')
  })

  it('asks the server for archived projects only when requested', async () => {
    const requests = projectsApi()
    await openApp('/projects'); await region('发布计划')
    const first = new URL(requests.find((r) => pathOf(r) === '/api/v1/projects')!.url).searchParams
    expect(first.get('include_archived')).toBe('false')
    await fireEvent.click(within(screen.getByRole('group', { name: '筛选项目' })).getByRole('button', { name: /^已归档/ }))
    await vi.waitFor(() => expect(requests.filter((r) => pathOf(r) === '/api/v1/projects').some((r) => new URL(r.url).searchParams.get('archived_only') === 'true')).toBe(true))
  })

  it('creates a project after validating the name', async () => {
    let list = [project]
    const created = { ...project, id: 'p-new', title: '新项目', description: '说明', open_tasks: 0 }
    const requests = projectsApi((r) => {
      if (r.method !== 'POST') return undefined
      list = [...list, created]
      return Response.json(created, { status: 201 })
    }, () => list)
    await openApp('/projects'); await region('发布计划')
    await fireEvent.click(screen.getByRole('button', { name: '新建项目' }))
    const dialog = await screen.findByRole('dialog', { name: '新建项目' })
    await fireEvent.click(within(dialog).getByRole('button', { name: '创建项目' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('请输入项目名称')
    await fireEvent.update(within(dialog).getByLabelText('项目名称'), '  新项目 ')
    await fireEvent.update(within(dialog).getByLabelText('项目说明'), '说明')
    await fireEvent.click(within(dialog).getByRole('button', { name: '创建项目' }))
    expect(await screen.findByText('已新建项目「新项目」')).toBeVisible()
    const post = requests.find((r) => r.method === 'POST')!
    expect(pathOf(post)).toBe('/api/v1/projects')
    // owner / admin 默认同时建 IM 项目群（T-0110）
    expect(await post.clone().json()).toEqual({ title: '新项目', description: '说明', im_group: true })
    expect(await region('新项目')).toBeVisible()
  })

  it('lets an admin pick agents and the project group when creating (T-0110)', async () => {
    const created = { ...project, id: 'p-new', title: '新项目' }
    const requests = projectsApi((r) => {
      if (pathOf(r) === '/api/v1/tasks/assignees') return Response.json({ users: [], instances: [{ id: 'i-a', name: '甲', status: 'online', description: '写代码' }, { id: 'i-b', name: '乙', status: 'offline', description: '' }] })
      if (r.method === 'POST') return Response.json(created, { status: 201 })
      return undefined
    })
    await openApp('/projects'); await region('发布计划')
    await fireEvent.click(screen.getByRole('button', { name: '新建项目' }))
    const dialog = await screen.findByRole('dialog', { name: '新建项目' })
    const agentsBox = within(dialog).getByRole('group', { name: '加入的智能体' })
    expect(within(agentsBox).getByText('写代码')).toBeVisible()
    await fireEvent.click(await within(agentsBox).findByLabelText(/甲（在线）/))
    await fireEvent.click(within(agentsBox).getByLabelText(/同时建 IM 项目群/))
    await fireEvent.update(within(dialog).getByLabelText('项目名称'), '新项目')
    await fireEvent.click(within(dialog).getByRole('button', { name: '创建项目' }))
    await screen.findByText('已新建项目「新项目」')
    const post = requests.find((r) => r.method === 'POST')!
    expect(await post.clone().json()).toEqual({ title: '新项目', description: '', im_group: false, instance_ids: ['i-a'] })
  })

  it('shows project agents and the group state, and manages members (T-0110)', async () => {
    const withAgents: Schema['ProjectOut'] = { ...project, agents: [{ instance_id: 'i-a', name: '甲', status: 'online' }], im_group: { status: 'pending', group_id: null, conversation_id: null, error: 'unavailable' } }
    let group: Schema['ProjectImGroup'] = withAgents.im_group
    const requests = projectsApi((r) => {
      const path = pathOf(r)
      if (path === '/api/v1/tasks/assignees') return Response.json({ users: [], instances: [{ id: 'i-a', name: '甲', status: 'online', description: '' }, { id: 'i-b', name: '乙', status: 'online', description: '' }] })
      if (path === `/api/v1/projects/${project.id}/agents` && r.method === 'GET') return Response.json({ items: [{ instance_id: 'i-a', name: '甲', description: '写代码', status: 'online', access: 'read_write', in_group: false }], im_group: group })
      if (path === `/api/v1/projects/${project.id}/agents/i-b` && r.method === 'PUT') return Response.json({ items: [], im_group: group })
      if (path === `/api/v1/projects/${project.id}/im-group` && r.method === 'POST') { group = { status: 'ready', group_id: 'ws1', conversation_id: 'sg_ws1', error: null }; return Response.json({ ...withAgents, im_group: group }) }
      return undefined
    }, () => [withAgents])
    await openApp('/projects')
    const card = await region('发布计划')
    expect(within(card).getByLabelText('发布计划 的智能体')).toHaveTextContent('智能体：甲 · 项目群还没建好')
    await fireEvent.click(within(card).getByRole('button', { name: '项目 发布计划 的成员' }))
    const dialog = await screen.findByRole('dialog', { name: '发布计划 的成员' })
    expect(await within(dialog).findByText('写代码')).toBeVisible()
    expect(within(dialog).getByText(/项目群还没建好：OpenIM 暂时不可用/)).toBeVisible()
    await fireEvent.update(await within(dialog).findByLabelText('加入智能体'), 'i-b')
    await fireEvent.click(within(dialog).getByRole('button', { name: '加入' }))
    expect(await within(dialog).findByText('「乙」已加入项目')).toBeVisible()
    expect(requests.some((r) => r.method === 'PUT' && pathOf(r).endsWith('/agents/i-b'))).toBe(true)
    await fireEvent.click(within(dialog).getByRole('button', { name: '立即重试' }))
    expect(await within(dialog).findByText('已重新同步项目群')).toBeVisible()
    expect(await within(dialog).findByRole('link', { name: '打开项目群' })).toHaveAttribute('href', '/im?c=sg_ws1')
  })

  it('hides agent controls from members (T-0110)', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    projectsApi((r) => pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'member' }] }) : undefined)
    await openApp('/projects'); await region('发布计划')
    await fireEvent.click(screen.getByRole('button', { name: '新建项目' }))
    const dialog = await screen.findByRole('dialog', { name: '新建项目' })
    expect(within(dialog).queryByRole('group', { name: '加入的智能体' })).toBeNull()
  })

  it('renames with the version and only the changed fields', async () => {
    const requests = projectsApi((r) => r.method === 'PATCH' ? Response.json({ ...project, title: '发布 2.0', version: 2 }) : undefined)
    await openApp('/projects')
    await fireEvent.click(within(await region('发布计划')).getByRole('button', { name: '编辑项目 发布计划' }))
    const dialog = await screen.findByRole('dialog', { name: '编辑项目' })
    expect(within(dialog).getByLabelText('项目名称')).toHaveValue('发布计划')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存项目' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('没有需要保存的修改')
    await fireEvent.update(within(dialog).getByLabelText('项目名称'), '发布 2.0')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存项目' }))
    await screen.findByText('项目已保存')
    const patch = requests.find((r) => r.method === 'PATCH')!
    expect(pathOf(patch)).toBe(`/api/v1/projects/${project.id}`)
    expect(await patch.clone().json()).toEqual({ version: 1, title: '发布 2.0' })
  })

  it('archives and deletes after confirming, warning about the tasks', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const requests = projectsApi((r) => r.method === 'PATCH' ? Response.json({ ...project, archived: true, version: 2 }) : r.method === 'DELETE' ? new Response(null, { status: 204 }) : undefined)
    await openApp('/projects')
    await fireEvent.click(within(await region('发布计划')).getByRole('button', { name: '归档项目 发布计划' }))
    await screen.findByText('已归档项目「发布计划」，任务保留')
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 1, archived: true })

    confirm.mockReturnValueOnce(false)
    await fireEvent.click(within(await region('发布计划')).getByRole('button', { name: '删除项目 发布计划' }))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('它的 1 个任务会一起删除'))
    expect(requests.some((r) => r.method === 'DELETE')).toBe(false)
    confirm.mockReturnValueOnce(true)
    await fireEvent.click(within(await region('发布计划')).getByRole('button', { name: '删除项目 发布计划' }))
    await screen.findByText('已删除项目「发布计划」')
    const remove = requests.find((r) => r.method === 'DELETE')!
    expect(new URL(remove.url).searchParams.get('version')).toBe('1')
  })

  it('asks to reload after a version conflict', async () => {
    projectsApi((r) => r.method === 'PATCH' ? fail(409, 'VERSION_CONFLICT') : undefined)
    await openApp('/projects')
    await fireEvent.click(within(await region('发布计划')).getByRole('button', { name: '归档项目 发布计划' }))
    expect(await screen.findByText('项目已在别处修改，请重新加载后再试。')).toBeVisible()
    expect(screen.getByRole('button', { name: '新建项目' })).toBeDisabled()
    await fireEvent.click(screen.getByRole('button', { name: '重新加载项目' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '新建项目' })).toBeEnabled())
  })

  it('is read-only for viewers', async () => {
    const requests = projectsApi((r) => pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] }) : undefined)
    await openApp('/projects')
    const card = await region('发布计划')
    expect(screen.queryByRole('button', { name: '新建项目' })).not.toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: /编辑|归档|删除/ })).not.toBeInTheDocument()
    expect(screen.getByText('当前角色只读。')).toBeVisible()
    expect(requests.some((r) => r.method !== 'GET')).toBe(false)
  })

  it('opens the new-project form from the create menu', async () => {
    projectsApi()
    await openApp('/projects?create=1')
    expect(await screen.findByRole('dialog', { name: '新建项目' })).toBeVisible()
  })
})

describe('任务页的项目筛选与定位（T-0082）', () => {
  it('filters by ?project_id= and opens ?task= in the drawer', async () => {
    const requests = projectsApi((r) => pathOf(r) === `/api/v1/tasks/${task.id}` ? Response.json(task) : undefined)
    await openApp(`/tasks?project_id=${project.id}&task=${task.id}`)
    expect(await screen.findByRole('dialog', { name: '任务详情' })).toBeVisible()
    const list = requests.find((r) => pathOf(r) === '/api/v1/tasks')!
    expect(new URL(list.url).searchParams.get('project_id')).toBe(project.id)
    expect(await screen.findByLabelText('任务标签')).toHaveValue('发布')
  })

  it('sends labels when creating a task', async () => {
    const requests = projectsApi((r) => r.method === 'POST' ? Response.json({ ...task, id: 't2', title: '写周报', labels: ['周报', '紧急'] }, { status: 201 }) : undefined)
    await openApp('/tasks'); await screen.findByRole('button', { name: '准备发布' })
    await fireEvent.click(screen.getByRole('button', { name: '新建任务' }))
    await fireEvent.update(await screen.findByLabelText('任务标题'), '写周报')
    await fireEvent.update(screen.getByLabelText('任务项目'), project.id)
    await fireEvent.update(screen.getByLabelText('任务标签'), '周报， 紧急、周报')
    await fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'POST')).toBe(true))
    const body = await requests.find((r) => r.method === 'POST')!.clone().json()
    expect(body.labels).toEqual(['周报', '紧急'])
  })
})
