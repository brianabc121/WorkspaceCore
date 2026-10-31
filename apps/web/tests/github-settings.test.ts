import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, emptyPage, fail, me, workspace } from './helpers'
import { integration } from './sprint3-fixtures'
import { github, checked } from './github-fixtures'
const path = (r: Request) => new URL(r.url).pathname
function settings(override?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => await override?.(r) ?? (path(r) === '/api/v1/integrations' ? Response.json({ ...emptyPage, items: [integration, github] }) : undefined))
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
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(document.body).not.toHaveTextContent('github_pat_synthetic-token')
  })
  it('shows each repository and four permission checks with actionable codes', async () => {
    settings((r) => path(r).endsWith('/test') ? Response.json(checked) : undefined)
    await openApp('/settings/integrations'); await screen.findByText(github.name)
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
    await openApp('/settings/integrations'); await screen.findByText(github.name)
    await fireEvent.click(within(screen.getByRole('heading', { name: github.name }).closest('section')!).getByRole('button', { name: '修改连接' }))
    expect(screen.getByLabelText('GitHub Token')).toHaveValue('')
    await fireEvent.update(screen.getByLabelText('GitHub 仓库列表'), 'team/new')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 1, repositories: ['team/new'] })
  })
  it('shows saved Vikunja addresses and explicitly resets public_url to default', async () => {
    const requests = settings((r) => r.method === 'PATCH' ? Response.json(integration) : undefined)
    await openApp('/settings/integrations'); await screen.findByText(integration.base_url)
    expect(screen.getByText(integration.public_url)).toBeVisible()
    await fireEvent.click(within(screen.getByRole('heading', { name: integration.name }).closest('section')!).getByRole('button', { name: '修改连接' }))
    await fireEvent.click(screen.getByLabelText('恢复默认浏览器地址'))
    await fireEvent.click(screen.getByRole('button', { name: '保存连接' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 1, public_url: null })
  })
  it('hides admin controls from a member', async () => {
    const requests = settings((r) => path(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'member' }] }) : undefined)
    await openApp('/settings/integrations')
    expect(screen.getByRole('heading', { name: '无权访问' })).toBeVisible()
    expect(requests.some((r) => path(r) === '/api/v1/integrations')).toBe(false)
  })
  it('hides cached settings and closes editor after write access loss', async () => {
    settings((r) => r.method === 'PATCH' ? fail(403, 'FORBIDDEN') : undefined)
    await openApp('/settings/integrations'); await screen.findByText(github.name)
    await fireEvent.click(within(screen.getByRole('heading', { name: github.name }).closest('section')!).getByRole('button', { name: '修改连接' }))
    await fireEvent.update(screen.getByLabelText('GitHub 仓库列表'), 'team/new')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByText(github.name)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新加载连接' })).toBeVisible()
  })
})
