import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory } from 'vue-router'
import { vi } from 'vitest'
import App from '../src/App.vue'
import { createAppRouter } from '../src/app/router'
import type { Schema } from '../src/services/api'
import { overview } from './dashboard-fixtures'
import { task, project, integration, sync } from './sprint3-fixtures'

export const workspace: Schema['WorkspaceOut'] = { id: 'workspace-a', name: '个人空间', role: 'owner', created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z' }
export const me: Schema['MeResponse'] = { user: { id: 'user-a', email: 'test@example.com', display_name: '测试用户', created_at: '2026-10-30T00:00:00Z' }, workspaces: [workspace] }
export const session: Schema['SessionOut'] = { id: 'session-a', current: true, created_at: '2026-10-30T00:00:00Z', expires_at: '2026-11-30T00:00:00Z', last_seen_at: null }
export const emptyPage = { items: [], next_cursor: null, total_estimate: null }
export const fail = (status: number, code: string) => Response.json({ error: { code, message: '请求失败', request_id: 'req-test', details: {} } }, { status })
export function mockApi(handler?: (request: Request) => Response | Promise<Response | undefined> | undefined) {
  const requests: Request[] = []
  vi.stubGlobal('fetch', async (request: Request) => {
    requests.push(request)
    const response = await handler?.(request)
    if (response) return response
    const path = new URL(request.url).pathname
    if (path === '/api/v1/setup/status') return Response.json({ setup_required: false })
    if (path === '/api/v1/me' && request.method === 'GET') return Response.json(me)
    if (path === '/api/v1/auth/sessions') return Response.json({ ...emptyPage, items: [session] })
    if (path === '/api/v1/notifications') return Response.json({ ...emptyPage, unread_count: 0 })
    if (path === '/api/v1/im/badges') return Response.json({ im_unread: 0, pending_approvals: 0 })
    if (path === '/api/v1/search') return Response.json(emptyPage)
    if (path === '/api/v1/ai/tool-executions') return Response.json({ ...emptyPage, pending_count: 0 })
    if (path === '/api/v1/audit-logs') return Response.json(emptyPage)
    if (/^\/api\/v1\/workspaces\/[^/]+\/members$/.test(path) && request.method === 'GET') return Response.json({ ...emptyPage, items: [{ user_id: me.user.id, email: me.user.email, display_name: me.user.display_name, role: 'owner', joined_at: '2026-10-30T00:00:00Z', you: true }] })
    if (path === '/api/v1/knowledge/resources' || path === '/api/v1/knowledge/sources' || path === '/api/v1/calendar/events') return Response.json(emptyPage)
    if (path === '/api/v1/dashboard/overview') return Response.json(overview)
    if (path === '/api/v1/tasks') return Response.json({ items: [task], sync, next_cursor: null, total_estimate: 1 })
    if (path === '/api/v1/projects') return Response.json({ items: [project], sync, next_cursor: null, total_estimate: 1 })
    if (path === '/api/v1/integrations') return Response.json({ items: [integration], next_cursor: null, total_estimate: 1 })
    throw new Error(`Unexpected request: ${request.method} ${path}`)
  })
  return requests
}
export async function openApp(path = '/') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createAppRouter(pinia, createMemoryHistory(), queryClient)
  const view = render(App, { global: { plugins: [pinia, router, [VueQueryPlugin, { queryClient }]] } })
  await router.push(path)
  await router.isReady()
  return { ...view, pinia, router, queryClient }
}
