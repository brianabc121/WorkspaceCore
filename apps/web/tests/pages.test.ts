import { screen, fireEvent } from '@testing-library/vue'
import { describe, it, expect, vi } from 'vitest'
import { fail, me, mockApi, openApp, session, workspace } from './helpers'

const pathOf = (request: Request) => new URL(request.url).pathname
async function fillLogin() {
  await fireEvent.update(screen.getByLabelText('邮箱'), 'test@example.com')
  await fireEvent.update(screen.getByLabelText('密码'), 'correct-password')
}
describe('Sprint 1 页面', () => {
  it('submits login once, shows rate limits, and can retry successfully with a safe return route', async () => {
    let authenticated = false
    let limited = true
    let resolve: ((response: Response) => void) | undefined
    const requests = mockApi((request) => {
      if (pathOf(request) === '/api/v1/me') return authenticated ? Response.json(me) : fail(401, 'UNAUTHORIZED')
      if (pathOf(request) === '/api/v1/auth/login') {
        if (limited) return new Promise<Response>((done) => { resolve = done })
        authenticated = true
        return Response.json(me)
      }
    })
    const { router } = await openApp('/login?redirect=/settings')
    await fillLogin()
    await fireEvent.submit(screen.getByRole('form', { name: '登录' }))
    await vi.waitFor(() => expect(resolve).toBeDefined())
    expect(screen.getByRole('button', { name: '正在登录…' })).toBeDisabled()
    await fireEvent.submit(screen.getByRole('form', { name: '登录' }))
    resolve!(Response.json({ error: { code: 'RATE_LIMITED', message: '限流', request_id: 'req-1' } }, { status: 429, headers: { 'Retry-After': '30' } }))
    expect(await screen.findByRole('alert')).toHaveTextContent('30 秒')
    expect(requests.filter((r) => pathOf(r) === '/api/v1/auth/login')).toHaveLength(1)
    limited = false
    await fillLogin()
    await fireEvent.submit(screen.getByRole('form', { name: '登录' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/settings'))
    expect(screen.getByText('测试用户')).toBeVisible()
  })
  it('does not send invalid installation input and creates the first session with valid fields', async () => {
    const requests = mockApi((request) => {
      if (pathOf(request) === '/api/v1/setup/status') return Response.json({ setup_required: true })
      if (pathOf(request) === '/api/v1/setup') return Response.json(me, { status: 201 })
    })
    const { router } = await openApp('/setup')
    await fireEvent.submit(screen.getByRole('form', { name: '首次安装' }))
    expect(await screen.findByText('请输入安装凭据')).toBeVisible()
    expect(requests.filter((r) => r.method === 'POST')).toHaveLength(0)
    for (const [label, value] of [['安装凭据', 'test-setup-token'], ['显示名', '测试用户'], ['工作空间名称', '个人空间'], ['邮箱', 'test@example.com'], ['密码', 'correct-password']]) await fireEvent.update(screen.getByLabelText(label!), value!)
    await fireEvent.submit(screen.getByRole('form', { name: '首次安装' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/'))
    const request = requests.find((r) => pathOf(r) === '/api/v1/setup')!
    expect(await request.clone().json()).toEqual({ setup_token: 'test-setup-token', display_name: '测试用户', workspace_name: '个人空间', email: 'test@example.com', password: 'correct-password' })
  })
  it('does not follow an external login redirect', async () => {
    mockApi((r) => pathOf(r) === '/api/v1/me' ? fail(401, 'UNAUTHORIZED') : pathOf(r) === '/api/v1/auth/login' ? Response.json(me) : undefined)
    const { router } = await openApp('/login?redirect=//evil.example')
    await fillLogin()
    await fireEvent.submit(screen.getByRole('form', { name: '登录' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/'))
  })
  it('updates profile and keeps workspace settings read-only for viewers', async () => {
    const requests = mockApi((r) => {
      if (pathOf(r) === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] })
      if (pathOf(r) === '/api/v1/me' && r.method === 'PATCH') return Response.json({ ...me.user, display_name: '更新名字' })
    })
    await openApp('/settings')
    expect(screen.getByLabelText('工作空间名称')).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: '保存工作空间' })).not.toBeInTheDocument()
    await fireEvent.update(screen.getByLabelText('显示名'), '更新名字')
    await fireEvent.submit(screen.getByRole('form', { name: '个人资料' }))
    expect(await screen.findByText('个人资料已保存')).toBeVisible()
    expect(screen.getByText('更新名字', { selector: '.user-label' })).toBeVisible()
    expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(1)
  })
  it('updates only the workspace selected when the form was submitted', async () => {
    let finish: ((response: Response) => void) | undefined
    const requests = mockApi((r) => {
      if (pathOf(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: '另一个空间' }] })
      if (r.method === 'PATCH') return new Promise<Response>((resolve) => { finish = resolve })
    })
    await openApp('/settings')
    await fireEvent.update(screen.getByLabelText('工作空间名称'), '更新空间')
    await fireEvent.submit(screen.getByRole('form', { name: '工作空间设置' }))
    await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    finish!(Response.json({ ...workspace, name: '更新空间' }))
    await vi.waitFor(() => expect(screen.getByLabelText('工作空间名称')).toHaveValue('另一个空间'))
    expect(pathOf(requests.find((r) => r.method === 'PATCH')!)).toBe('/api/v1/workspaces/workspace-a')
  })
  it('changes passwords, clears password inputs, and reloads sessions', async () => {
    const requests = mockApi((r) => pathOf(r) === '/api/v1/auth/password' ? new Response(null, { status: 204 }) : undefined)
    await openApp('/settings/security')
    await screen.findByText('当前会话')
    await fireEvent.update(screen.getByLabelText('当前密码'), 'old-password')
    await fireEvent.update(screen.getByLabelText('新密码'), 'new-password-123')
    await fireEvent.submit(screen.getByRole('form', { name: '修改密码' }))
    expect(await screen.findByText('密码已更新，其他会话已撤销')).toBeVisible()
    expect(screen.getByLabelText('当前密码')).toHaveValue('')
    expect(screen.getByLabelText('新密码')).toHaveValue('')
    await vi.waitFor(() => expect(requests.filter((r) => pathOf(r) === '/api/v1/auth/sessions')).toHaveLength(2))
  })
  it('requires confirmation to revoke the current session and then returns to login', async () => {
    const requests = mockApi((r) => r.method === 'DELETE' ? new Response(null, { status: 204 }) : undefined)
    const { router } = await openApp('/settings/security')
    await fireEvent.click(await screen.findByRole('button', { name: '撤销当前会话' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('撤销后将退出登录')
    expect(requests.filter((r) => r.method === 'DELETE')).toHaveLength(0)
    await fireEvent.click(screen.getByRole('button', { name: '确认撤销' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/login'))
    expect(pathOf(requests.find((r) => r.method === 'DELETE')!)).toBe(`/api/v1/auth/sessions/${session.id}`)
  })
  it('paginates audit records with an opaque cursor and workspace header', async () => {
    const requests = mockApi((r) => {
      if (pathOf(r) !== '/api/v1/audit-logs') return undefined
      return Response.json({ items: [{ id: new URL(r.url).searchParams.has('cursor') ? 'log-b' : 'log-a', action: new URL(r.url).searchParams.has('cursor') ? 'workspace.updated' : 'user.updated', actor_id: 'user-a', resource_type: 'user', resource_id: 'user-a', metadata: {}, created_at: '2026-10-30T00:00:00Z' }], next_cursor: new URL(r.url).searchParams.has('cursor') ? null : 'opaque+/=cursor', total_estimate: null })
    })
    await openApp('/settings/audit')
    expect(await screen.findByText('user.updated')).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '加载更多' }))
    expect(await screen.findByText('workspace.updated')).toBeVisible()
    const pages = requests.filter((r) => pathOf(r) === '/api/v1/audit-logs')
    expect(new URL(pages[1]!.url).searchParams.get('cursor')).toBe('opaque+/=cursor')
    expect(pages[1]!.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument()
  })
  it('shows server authorization failures on audit and supports retry', async () => {
    let forbidden = true
    mockApi((r) => pathOf(r) === '/api/v1/audit-logs' && forbidden ? fail(403, 'FORBIDDEN') : undefined)
    await openApp('/settings/audit')
    expect(await screen.findByRole('alert')).toHaveTextContent('权限')
    forbidden = false
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('暂无审计记录')).toBeVisible()
  })
  it('handles installation completed by another client by moving to login', async () => {
    mockApi((r) => pathOf(r) === '/api/v1/setup/status' ? Response.json({ setup_required: true }) : pathOf(r) === '/api/v1/setup' ? fail(409, 'SETUP_COMPLETED') : undefined)
    const { router } = await openApp('/setup')
    for (const [label, value] of [['安装凭据', 'test-token'], ['显示名', '测试用户'], ['工作空间名称', '个人空间'], ['邮箱', 'test@example.com'], ['密码', 'correct-password']]) await fireEvent.update(screen.getByLabelText(label!), value!)
    await fireEvent.submit(screen.getByRole('form', { name: '首次安装' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/login'))
    expect(screen.getByRole('status')).toHaveTextContent('安装已完成')
  })
  it('shows session loading, empty, and retryable server failures', async () => {
    let finish: ((response: Response) => void) | undefined
    let failed = true
    mockApi((r) => pathOf(r) === '/api/v1/auth/sessions' ? failed ? new Promise<Response>((resolve) => { finish = resolve }) : Response.json({ items: [], next_cursor: null, total_estimate: null }) : undefined)
    await openApp('/settings/security')
    expect(screen.getByRole('status')).toHaveTextContent('正在加载')
    await vi.waitFor(() => expect(finish).toBeDefined())
    finish!(fail(503, 'HTTP_ERROR'))
    expect(await screen.findByRole('alert')).toHaveTextContent('暂时不可用')
    failed = false
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('暂无登录会话')).toBeVisible()
  })
  it('revokes another session after confirmation, refreshes the list, and stays logged in', async () => {
    let revoked = false
    mockApi((r) => {
      if (r.method === 'DELETE') { revoked = true; return new Response(null, { status: 204 }) }
      if (pathOf(r) === '/api/v1/auth/sessions') return Response.json({ items: revoked ? [session] : [session, { ...session, id: 'session-b', current: false }], next_cursor: null, total_estimate: null })
    })
    const { router } = await openApp('/settings/security')
    await fireEvent.click(await screen.findByRole('button', { name: '撤销会话' }))
    await fireEvent.click(screen.getByRole('button', { name: '确认撤销' }))
    expect(await screen.findByText('会话已撤销')).toBeVisible()
    expect(screen.queryByText('其他会话')).not.toBeInTheDocument()
    expect(router.currentRoute.value.path).toBe('/settings/security')
  })
  it('supports profile access without any workspace and does not show audit navigation', async () => {
    mockApi((r) => pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [] }) : undefined)
    await openApp('/settings')
    expect(screen.getByText('你尚未加入任何工作空间，请联系管理员。')).toBeVisible()
    expect(screen.getByLabelText('显示名')).toHaveValue('测试用户')
    expect(screen.queryByRole('link', { name: '审计日志' })).not.toBeInTheDocument()
  })
  it('hides audit records if a refresh discovers that server permission was revoked', async () => {
    let forbidden = false
    mockApi((r) => pathOf(r) === '/api/v1/audit-logs' ? forbidden ? fail(403, 'FORBIDDEN') : Response.json({ items: [{ id: 'log-a', action: 'private.action', actor_id: null, resource_type: null, resource_id: null, metadata: {}, created_at: '2026-10-30T00:00:00Z' }], next_cursor: null, total_estimate: null }) : undefined)
    await openApp('/settings/audit')
    expect(await screen.findByText('private.action')).toBeVisible()
    forbidden = true
    await fireEvent.click(screen.getByRole('button', { name: '刷新记录' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('权限')
    expect(screen.queryByText('private.action')).not.toBeInTheDocument()
  })
})
