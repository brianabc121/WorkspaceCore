import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, emptyPage, fail, me, workspace } from './helpers'
import { github, checked, developerRoutes } from './github-fixtures'
const path = (r: Request) => new URL(r.url).pathname
// 集成设置页还会读取仓库投影与逐仓库权限快照（T-0192），用开发者页的默认样例
function settings(override?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => await override?.(r) ?? (path(r) === '/api/v1/integrations' ? Response.json({ ...emptyPage, items: [github] }) : developerRoutes(path(r))))
}
describe('GitHub 集成设置', () => {
  it('creates a fine-grained connection with deduplicated repositories', async () => {
    const requests = settings((r) => path(r) === '/api/v1/integrations' ? r.method === 'POST' ? Response.json(github, { status: 201 }) : Response.json(emptyPage) : undefined)
    await openApp('/settings/integrations'); await fireEvent.click(await screen.findByRole('button', { name: '新建 GitHub 连接' }))
    await fireEvent.update(screen.getByLabelText('GitHub Token'), 'ghp_classic-token')
    await fireEvent.update(screen.getByLabelText('GitHub 仓库列表'), 'Team/Workbench\nteam/workbench\nteam/api')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    expect(await screen.findByText(/细粒度.*github_pat_/)).toBeVisible()
    expect(requests.filter((r) => r.method === 'POST')).toHaveLength(0)
    await fireEvent.update(screen.getByLabelText('GitHub Token'), 'github_pat_synthetic-token')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'POST')).toBe(true))
    const write = requests.find((r) => r.method === 'POST')!
    expect(await write.clone().json()).toMatchObject({ provider: 'github', api_token: 'github_pat_synthetic-token', repositories: ['team/workbench', 'team/api'] })
    expect(await write.clone().json()).not.toHaveProperty('base_url')
    expect(write.headers.get('X-Workspace-Id')).toBe('workspace-a')
    // 保存后编辑表单关闭（T-0192 起表单在右侧一栏，不再是对话框）
    await vi.waitFor(() => expect(screen.queryByLabelText('GitHub Token')).not.toBeInTheDocument())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('github_pat_synthetic-token')
  })
  it('guides token creation, lists accessible repositories and checks permissions right after saving', async () => {
    const listing = { repositories: ['Team/Workbench', 'team/api', 'team/docs'], complete: true }
    const requests = settings((r) => {
      if (path(r) === '/api/v1/integrations/github/repositories') return Response.json(listing)
      if (path(r) === '/api/v1/integrations') return r.method === 'POST' ? Response.json(github, { status: 201 }) : Response.json(emptyPage)
      if (path(r).endsWith('/test')) return Response.json({ ...checked, ok: true, status: 'active', warnings: [], repositories: [] })
      return undefined
    })
    await openApp('/settings/integrations'); await fireEvent.click(await screen.findByRole('button', { name: '新建 GitHub 连接' }))
    const steps = screen.getByRole('region', { name: '生成 GitHub Token 的步骤' })
    const link = within(steps).getByRole('link', { name: 'GitHub 生成 Token 页面' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    const url = new URL(link.getAttribute('href')!)
    expect(url.origin + url.pathname).toBe('https://github.com/settings/personal-access-tokens/new')
    expect(Object.fromEntries(url.searchParams)).toMatchObject({ expires_in: '90', issues: 'read', pull_requests: 'read', actions: 'read' })
    expect(url.searchParams.has('contents')).toBe(false) // 只要只读的三项，不要代码读写
    // 还没有 Token 时不能读取
    expect(screen.getByRole('button', { name: '读取可访问的仓库' })).toBeDisabled()
    await fireEvent.update(screen.getByLabelText('GitHub Token'), 'github_pat_synthetic-token')
    await fireEvent.click(screen.getByRole('button', { name: '读取可访问的仓库' }))
    const picker = await screen.findByRole('group', { name: /Token 能访问的仓库/ })
    const boxes = within(picker).getAllByRole('checkbox')
    expect(boxes).toHaveLength(3)
    expect(boxes.every((box) => (box as HTMLInputElement).checked)).toBe(true) // 默认全选
    const listed = requests.find((r) => path(r) === '/api/v1/integrations/github/repositories')!
    expect(await listed.clone().json()).toEqual({ api_token: 'github_pat_synthetic-token' })
    expect(listed.headers.get('X-Workspace-Id')).toBe('workspace-a')
    await fireEvent.click(within(picker).getByRole('checkbox', { name: 'team/docs' }))
    expect(screen.getByLabelText('GitHub 仓库列表')).toHaveValue('Team/Workbench\nteam/api')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(requests.some((r) => path(r).endsWith('/test'))).toBe(true))
    const write = requests.find((r) => path(r) === '/api/v1/integrations' && r.method === 'POST')!
    expect(await write.clone().json()).toMatchObject({ repositories: ['team/workbench', 'team/api'] })
    expect((await write.clone().json()).token_expires_at).toBeTruthy() // 按链接里的 90 天预填
    expect(requests.find((r) => path(r).endsWith('/test'))!.url).toContain(github.id)
    expect(await screen.findByRole('region', { name: '连接检查结果' })).toBeVisible()
    expect(await screen.findByText(/已自动检查权限/)).toBeVisible()
    expect(document.body).not.toHaveTextContent('github_pat_synthetic-token')
  })
  it('lists repositories with the saved token and reports GitHub errors', async () => {
    let reject = true
    const requests = settings((r) => path(r) === '/api/v1/integrations/github/repositories' ? (reject ? fail(502, 'INTEGRATION_UNAUTHORIZED') : Response.json({ repositories: ['team/workbench', 'team/new'], complete: false })) : undefined)
    // T-0192：修改连接在页头，名称同时出现在状态条与右侧配置里
    await openApp('/settings/integrations'); await screen.findByRole('heading', { name: github.name })
    await fireEvent.click(screen.getByRole('button', { name: '修改连接' }))
    await fireEvent.click(screen.getByRole('button', { name: '读取可访问的仓库' }))
    expect(await screen.findByRole('alert')).toBeVisible()
    reject = false
    await fireEvent.click(screen.getByRole('button', { name: '读取可访问的仓库' }))
    const picker = await screen.findByRole('group', { name: /Token 能访问的仓库/ })
    expect(within(picker).getByRole('checkbox', { name: 'team/workbench' })).toBeChecked() // 已保存的仓库保持勾选
    expect(within(picker).getByRole('checkbox', { name: 'team/new' })).not.toBeChecked()
    expect(within(picker).getByText(/只列出了前面一部分/)).toBeVisible()
    const listed = requests.filter((r) => path(r) === '/api/v1/integrations/github/repositories')
    expect(await listed.at(-1)!.clone().json()).toEqual({}) // 不重新发送 Token，用已保存的
  })
  it('shows each repository and four permission checks with actionable codes', async () => {
    settings((r) => path(r).endsWith('/test') ? Response.json(checked) : undefined)
    await openApp('/settings/integrations'); await screen.findByRole('heading', { name: github.name })
    await fireEvent.click(within(screen.getByRole('heading', { name: github.name }).closest('section')!).getByRole('button', { name: '测试连接' }))
    const results = await screen.findByRole('region', { name: '连接检查结果' })
    expect(within(results).getByText('team/workbench')).toBeVisible()
    for (const label of ['仓库元数据', 'Issues', 'Pull requests', 'Actions']) expect(within(results).getByText(new RegExp(label))).toBeVisible()
    expect(within(results).getByText(/missing_permission/)).toHaveTextContent(/缺少.*权限/)
    expect(within(results).getByText(/not_supported/)).toHaveTextContent(/不支持/)
    expect(within(results).getByText('请缩小多余权限')).toBeVisible()
  })
  it('edits repositories without replaying saved token or URLs', async () => {
    const requests = settings((r) => r.method === 'PATCH' ? Response.json(github) : undefined)
    await openApp('/settings/integrations'); await screen.findByRole('heading', { name: github.name })
    await fireEvent.click(screen.getByRole('button', { name: '修改连接' }))
    expect(screen.getByLabelText('更换细粒度 Token')).toHaveValue('')
    await fireEvent.update(screen.getByLabelText('GitHub 仓库列表'), 'team/new')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 1, repositories: ['team/new'] })
  })
  it('hides admin controls from a member', async () => {
    const requests = settings((r) => path(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'member' }] }) : undefined)
    await openApp('/settings/integrations')
    expect(screen.getByRole('heading', { name: '无权访问' })).toBeVisible()
    expect(requests.some((r) => path(r) === '/api/v1/integrations')).toBe(false)
  })
  it('hides cached settings and closes editor after write access loss', async () => {
    settings((r) => r.method === 'PATCH' ? fail(403, 'FORBIDDEN') : undefined)
    await openApp('/settings/integrations'); await screen.findByRole('heading', { name: github.name })
    await fireEvent.click(screen.getByRole('button', { name: '修改连接' }))
    await fireEvent.update(screen.getByLabelText('GitHub 仓库列表'), 'team/new')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(screen.queryByLabelText('GitHub 仓库列表')).not.toBeInTheDocument())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(github.name)
    expect(screen.queryByRole('table', { name: '仓库权限核验' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新加载连接' })).toBeVisible()
  })
})
