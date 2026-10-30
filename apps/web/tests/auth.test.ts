import { screen, fireEvent } from '@testing-library/vue'
import { describe, it, expect, vi } from 'vitest'
import { fail, me, mockApi, openApp } from './helpers'
import { useAuthStore } from '../src/stores/auth'

describe('认证与工作空间', () => {
  it('checks setup before me and sends anonymous protected visits to login', async () => {
    const requests = mockApi((request) => new URL(request.url).pathname === '/api/v1/me' ? fail(401, 'UNAUTHORIZED') : undefined)
    const { router } = await openApp('/settings/security')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/settings/security')
    expect(requests.map((r) => new URL(r.url).pathname)).toEqual(['/api/v1/setup/status', '/api/v1/me'])
    expect(screen.getByRole('heading', { name: '登录', level: 1 })).toBeVisible()
  })
  it('requires first installation before any protected page, without requesting me', async () => {
    const requests = mockApi(() => Response.json({ setup_required: true }))
    const { router } = await openApp('/settings')
    expect(router.currentRoute.value.path).toBe('/setup')
    expect(requests).toHaveLength(1)
    expect(screen.getByLabelText('安装凭据')).toHaveAttribute('type', 'password')
  })
  it('shows retryable bootstrap errors instead of pretending the user is anonymous', async () => {
    let offline = true
    mockApi(() => offline ? new Response('unavailable', { status: 503 }) : undefined)
    const { router } = await openApp('/settings')
    expect(router.currentRoute.value.path).toBe('/connection-error')
    expect(screen.getByRole('alert')).toBeVisible()
    offline = false
    await fireEvent.click(screen.getByRole('button', { name: '重新连接' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/settings'))
    expect(screen.getByText('测试用户')).toBeVisible()
  })
  it('clears session data and redirects when a protected API returns 401', async () => {
    mockApi((request) => new URL(request.url).pathname === '/api/v1/auth/sessions' ? fail(401, 'UNAUTHORIZED') : undefined)
    const { router, pinia, queryClient } = await openApp('/settings/security')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/login'))
    expect(useAuthStore(pinia).user).toBeNull()
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
  })
  it('switches workspace context and hides audit navigation for a viewer', async () => {
    mockApi((request) => new URL(request.url).pathname === '/api/v1/me' ? Response.json({ ...me, workspaces: [...me.workspaces, { ...me.workspaces[0], id: 'workspace-b', name: '只读空间', role: 'viewer' }] }) : undefined)
    const { pinia } = await openApp()
    expect(screen.getByRole('link', { name: '审计日志' })).toBeVisible()
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    expect(useAuthStore(pinia).workspaceId).toBe('workspace-b')
    expect(screen.queryByRole('link', { name: '审计日志' })).not.toBeInTheDocument()
  })
  it('denies audit deep links to members without sending an audit request', async () => {
    const requests = mockApi((request) => new URL(request.url).pathname === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...me.workspaces[0], role: 'member' }] }) : undefined)
    await openApp('/settings/audit')
    expect(screen.getByRole('heading', { name: '无权访问', level: 1 })).toBeVisible()
    expect(requests.some((r) => new URL(r.url).pathname === '/api/v1/audit-logs')).toBe(false)
  })
})
