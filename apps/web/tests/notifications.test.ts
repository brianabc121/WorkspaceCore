import { describe, it, expect, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import { notifications, notification, path } from './sprint5-fixtures'
import { overview, widget } from './dashboard-fixtures'
import { useAuthStore } from '../src/stores/auth'
import { registry, parseWidget } from '../src/widgets/registry'

describe('notifications', () => {
  it('shows unread count and plain body with application navigation for an unknown type', async () => {
    mockApi(r => path(r) === '/api/v1/notifications' ? Response.json(notifications) : undefined)
    await openApp('/operations'); await fireEvent.click(await screen.findByRole('button', { name: '通知，2 条未读' }))
    const list = await screen.findByRole('region', { name: '通知列表' })
    expect(within(list).getByText(notification.body)).toBeVisible(); expect(list.querySelector('img')).toBeNull()
    expect(within(list).getByRole('link', { name: notification.title })).toHaveAttribute('href', '/operations')
  })
  it.each(['//evil.test', '/\\evil.test', 'https://evil.test', '/\nevil'])('rejects unsafe notification path %s', async link => {
    mockApi(r => path(r) === '/api/v1/notifications' ? Response.json({ ...notifications, items: [{ ...notification, link }] }) : undefined)
    await openApp('/'); await fireEvent.click(await screen.findByRole('button', { name: '通知，2 条未读' }))
    expect(within(await screen.findByRole('region', { name: '通知列表' })).queryByRole('link')).not.toBeInTheDocument()
  })
  it('marks one and all for a viewer, sends exact scope/CSRF and updates the shared count', async () => {
    document.cookie = 'ws_csrf=notice-csrf'; let count = 2
    const requests = mockApi(async r => {
      if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] })
      if (path(r) === '/api/v1/dashboard/overview') return Response.json({ ...overview, summary: { ...overview.summary, unread_notifications: count } })
      if (path(r) === '/api/v1/notifications') {
        if (r.method === 'PATCH') { count--; return Response.json({ updated: 1, unread_count: count }) }
        return Response.json({ ...notifications, unread_count: count, items: count === 0 ? [] : [{ ...notification, read_at: count === 1 ? '2026-10-31T04:01:00Z' : null }] })
      }
    })
    const { queryClient } = await openApp('/'); await fireEvent.click(await screen.findByRole('button', { name: '通知，2 条未读' }))
    await fireEvent.click(await screen.findByRole('button', { name: '标为已读：服务告警' }))
    await screen.findByRole('button', { name: '通知，1 条未读' }); await vi.waitFor(() => expect(screen.getByRole('button', { name: '全部标为已读' })).toBeEnabled()); await fireEvent.click(screen.getByRole('button', { name: '全部标为已读' }))
    await screen.findByRole('button', { name: '通知，0 条未读' })
    const writes = requests.filter(r => r.method === 'PATCH')
    expect(await writes[0]!.clone().json()).toEqual({ ids: ['notice-a'] }); expect(await writes[1]!.clone().json()).toEqual({ all: true })
    expect(writes.every(r => r.headers.get('X-Workspace-Id') === 'workspace-a' && r.headers.get('X-CSRF-Token') === 'notice-csrf')).toBe(true)
    expect(queryClient.getQueryData<typeof overview>(['overview', 'user-a', 'workspace-a'])?.summary.unread_notifications).toBe(0)
  })
  it('supports pagination/filter and uses contract notification card defaults', async () => {
    expect(registry.notifications?.configSchema.parse({})).toEqual({ limit: 5, unread_only: true })
    expect(parseWidget({ ...widget, widget_type: 'notifications', config: { limit: 21 } })).toBeNull()
    const requests = mockApi(r => {
      if (path(r) === '/api/v1/dashboard/overview') return Response.json({ ...overview, widgets: [{ ...widget, widget_type: 'notifications', config: { limit: 2, unread_only: true } }] })
      if (path(r) === '/api/v1/notifications') return Response.json({ ...notifications, next_cursor: new URL(r.url).searchParams.has('cursor') ? null : 'next-a' })
    })
    await openApp('/'); expect(await screen.findByRole('region', { name: '首页通知列表' })).toHaveTextContent('服务告警')
    await fireEvent.click(await screen.findByRole('button', { name: '通知，2 条未读' })); await screen.findByRole('region', { name: '通知列表' })
    await fireEvent.click(screen.getByRole('button', { name: '更多通知' })); await vi.waitFor(() => expect(requests.some(r => new URL(r.url).searchParams.get('cursor') === 'next-a')).toBe(true))
    await fireEvent.click(screen.getByLabelText('只看未读'))
    await vi.waitFor(() => expect(requests.some(r => new URL(r.url).searchParams.get('unread_only') === 'true' && new URL(r.url).searchParams.get('limit') === '30')).toBe(true))
    expect(requests.some(r => new URL(r.url).searchParams.get('limit') === '2' && new URL(r.url).searchParams.get('unread_only') === 'true')).toBe(true)
  })
  it('shows stale timestamp then removes cached notification content on denial', async () => {
    let status = 200
    mockApi(r => path(r) === '/api/v1/notifications' ? status === 200 ? Response.json(notifications) : fail(status, 'HTTP_ERROR') : undefined)
    const { queryClient } = await openApp('/'); await fireEvent.click(await screen.findByRole('button', { name: '通知，2 条未读' })); await screen.findByText(notification.body)
    status = 503; await queryClient.invalidateQueries({ queryKey: ['notifications'] })
    expect(await screen.findByText(/通知数据可能过期.*截至/)).toBeVisible()
    status = 403; await queryClient.invalidateQueries({ queryKey: ['notifications'] })
    await vi.waitFor(() => expect(screen.queryByText(notification.body)).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: /通知，无法获取未读数/ })).toBeVisible()
  })
  it('closes old panel on workspace changes and ignores delayed old notifications', async () => {
    let finish!: (r: Response) => void
    mockApi(r => {
      if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B' }] })
      if (path(r) === '/api/v1/notifications') return r.headers.get('X-Workspace-Id') === 'workspace-b' ? Response.json({ ...notifications, items: [], unread_count: 7 }) : new Promise<Response>(resolve => { finish = resolve })
    })
    const { pinia } = await openApp('/'); await vi.waitFor(() => expect(finish).toBeDefined()); useAuthStore(pinia).selectWorkspace('workspace-b')
    await screen.findByRole('button', { name: '通知，7 条未读' }); finish(Response.json(notifications))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '通知，7 条未读' })).toBeVisible())
    expect(screen.queryByText(notification.body)).not.toBeInTheDocument()
  })
})
