import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory } from 'vue-router'
import { vi } from 'vitest'
import App from '../src/App.vue'
import { createAppRouter } from '../src/app/router'
import type { Schema } from '../src/services/api'
import { overview } from './dashboard-fixtures'
import { task, project, integration } from './sprint3-fixtures'

export const workspace: Schema['WorkspaceOut'] = { id: 'workspace-a', name: '个人空间', role: 'owner', created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z' }
export const me: Schema['MeResponse'] = { user: { id: 'user-a', email: 'test@example.com', display_name: '测试用户', created_at: '2026-10-30T00:00:00Z' }, workspaces: [workspace] }
export const session: Schema['SessionOut'] = { id: 'session-a', current: true, created_at: '2026-10-30T00:00:00Z', expires_at: '2026-11-30T00:00:00Z', last_seen_at: null }
export const emptyPage = { items: [], next_cursor: null, total_estimate: null }
export const noLogins: Schema['AppLoginList'] = { items: [{ app: 'n8n', configured: false, username: null, password_set: false, login_url: null, updated_at: null, version: null }] }
export const runnerOff: Schema['AgentRunnerOut'] = { enabled: false, limits: { envs: 3, persistent: 2, memory_mb: 16384, cpus: 8, disk_gb: 100, execs_per_hour: 120, run_hours_per_day: 8 }, secrets: [], enabled_at: null, version: 1 }
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
    // 知识库（T-0099）：默认没有笔记
    // 实例的发件记录（T-0118）：默认没有
    if (/^\/api\/v1\/agents\/instances\/[^/]+\/outbox$/.test(path) && request.method === 'GET') return Response.json({ items: [], rejected_replies: [] })
    if (/^\/api\/v1\/agents\/instances\/[^/]+\/wakeups$/.test(path) && request.method === 'GET') return Response.json({ items: [], targets: [] })
    // 实例的运行环境权限（T-0140）：默认关闭
    if (/^\/api\/v1\/agents\/instances\/[^/]+\/runner$/.test(path) && request.method === 'GET') return Response.json(runnerOff)
    if (path === '/api/v1/knowledge/spaces') return Response.json({ items: [] })
    if (path === '/api/v1/knowledge/graph') return Response.json({ nodes: [], edges: [], truncated: false, total_notes: 0 })
    if (path === '/api/v1/knowledge/notes' || path === '/api/v1/knowledge/changes' || path === '/api/v1/knowledge/trash') return Response.json(emptyPage)
    if (path === '/api/v1/dashboard/overview') return Response.json(overview)
    if (path === '/api/v1/tasks') return Response.json({ items: [task], next_cursor: null, total_estimate: 1 })
    if (path === '/api/v1/projects') return Response.json({ items: [project], next_cursor: null, total_estimate: 1 })
    if (path === '/api/v1/integrations') return Response.json({ items: [integration], next_cursor: null, total_estimate: 1 })
    if (path === '/api/v1/app-logins' && request.method === 'GET') return Response.json(noLogins)
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
