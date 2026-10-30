import { screen, fireEvent } from '@testing-library/vue'
import { describe, it, expect, vi } from 'vitest'
import { fail, me, mockApi, openApp, workspace } from './helpers'
import { useAuthStore } from '../src/stores/auth'

const pathOf = (request: Request) => new URL(request.url).pathname
describe('会话与工作空间隔离', () => {
  it('ignores a successful profile response that arrives after logout', async () => {
    let finish: ((response: Response) => void) | undefined
    mockApi((r) => {
      if (r.method === 'PATCH') return new Promise<Response>((resolve) => { finish = resolve })
      if (pathOf(r) === '/api/v1/auth/logout') return new Response(null, { status: 204 })
    })
    const { router, pinia } = await openApp('/settings')
    await fireEvent.update(screen.getByLabelText('显示名'), '旧会话更新')
    await fireEvent.submit(screen.getByRole('form', { name: '个人资料' }))
    await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.click(screen.getByRole('button', { name: '登出' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/login'))
    finish!(Response.json({ ...me.user, display_name: '旧会话更新' }))
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(useAuthStore(pinia).user).toBeNull()
  })
  it('does not expire a new session when an old request returns 401 late', async () => {
    let finish: ((response: Response) => void) | undefined
    mockApi((r) => r.method === 'PATCH' ? new Promise<Response>((resolve) => { finish = resolve }) : undefined)
    const { pinia } = await openApp('/settings')
    const auth = useAuthStore(pinia)
    const result = auth.api.PATCH('/api/v1/me', { body: { display_name: 'name' } }).catch((error: unknown) => error)
    await vi.waitFor(() => expect(finish).toBeDefined())
    auth.clearSession()
    auth.acceptSession({ ...me, user: { ...me.user, id: 'new-user' } })
    finish!(fail(401, 'UNAUTHORIZED'))
    await result
    expect(auth.user?.id).toBe('new-user')
  })
  it('discards old audit pages and fetches the selected workspace without mixing rows', async () => {
    const requests = mockApi((r) => {
      if (pathOf(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: '空间 B' }] })
      if (pathOf(r) === '/api/v1/audit-logs') return Response.json({ items: [{ id: r.headers.get('X-Workspace-Id'), action: r.headers.get('X-Workspace-Id') === 'workspace-a' ? 'first.workspace' : 'second.workspace', actor_id: null, resource_type: null, resource_id: null, metadata: {}, created_at: '2026-10-30T00:00:00Z' }], next_cursor: null, total_estimate: null })
    })
    await openApp('/settings/audit')
    expect(await screen.findByText('first.workspace')).toBeVisible()
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    expect(await screen.findByText('second.workspace')).toBeVisible()
    expect(screen.queryByText('first.workspace')).not.toBeInTheDocument()
    expect(requests.filter((r) => pathOf(r) === '/api/v1/audit-logs').map((r) => r.headers.get('X-Workspace-Id'))).toEqual(['workspace-a', 'workspace-b'])
  })
  it('keeps the rotated password session when an old request later returns 401', async () => {
    let delayMe = false
    let finish: ((response: Response) => void) | undefined
    mockApi((r) => {
      if (delayMe && pathOf(r) === '/api/v1/me') return new Promise<Response>((resolve) => { finish = resolve })
      if (pathOf(r) === '/api/v1/auth/password') return new Response(null, { status: 204 })
    })
    const { router, pinia } = await openApp('/settings/security')
    const auth = useAuthStore(pinia)
    delayMe = true
    const oldRequest = auth.api.GET('/api/v1/me').catch((error: unknown) => error)
    await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前密码'), 'old-password')
    await fireEvent.update(screen.getByLabelText('新密码'), 'new-password-123')
    await fireEvent.submit(screen.getByRole('form', { name: '修改密码' }))
    await screen.findByText('密码已更新，其他会话已撤销')
    finish!(fail(401, 'UNAUTHORIZED'))
    await oldRequest
    expect(auth.user?.id).toBe('user-a')
    expect(router.currentRoute.value.path).toBe('/settings/security')
  })
  it('keeps focus inside session confirmation and restores it on cancel', async () => {
    mockApi()
    await openApp('/settings/security')
    const trigger = await screen.findByRole('button', { name: '撤销当前会话' })
    await fireEvent.click(trigger)
    const cancel = screen.getByRole('button', { name: '取消' })
    const confirm = screen.getByRole('button', { name: '确认撤销' })
    expect(cancel).toHaveFocus()
    await fireEvent.keyDown(cancel, { key: 'Tab', shiftKey: true })
    expect(confirm).toHaveFocus()
    await fireEvent.keyDown(confirm, { key: 'Tab' })
    expect(cancel).toHaveFocus()
    await fireEvent.keyDown(cancel, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})
