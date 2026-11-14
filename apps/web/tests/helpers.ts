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
import { overviewOf } from './agents-fixtures'

export const workspace: Schema['WorkspaceOut'] = { id: 'workspace-a', name: '个人空间', role: 'owner', created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z' }
export const me: Schema['MeResponse'] = { user: { id: 'user-a', email: 'test@example.com', display_name: '测试用户', created_at: '2026-10-30T00:00:00Z' }, workspaces: [workspace] }
export const session: Schema['SessionOut'] = { id: 'session-a', current: true, created_at: '2026-10-30T00:00:00Z', expires_at: '2026-11-30T00:00:00Z', last_seen_at: null }
export const emptyPage = { items: [], next_cursor: null, total_estimate: null }
export const emptySummary: Schema['TaskSummary'] = { todo: 0, running: 0, awaiting_view: 0, blocked: 0, done: 0, total: 0, as_of: '2026-10-30T00:00:00Z' }
export const emptyProgress: Schema['TaskProgressOut'] = { counts: { all: 0, todo: 0, running: 0, awaiting_view: 0, blocked: 0 }, attention: 0, items: [] }
export const runnerOff: Schema['AgentRunnerOut'] = { enabled: false, limits: { envs: 3, persistent: 2, memory_mb: 16384, cpus: 8, disk_gb: 100, execs_per_hour: 120, run_hours_per_day: 8 }, secrets: [], enabled_at: null, version: 1 }
// 插拔式模块（T-0159）：默认只有新闻早报，已启用
export const newsModule: Schema['ModuleOut'] = { key: 'news', name: '新闻早报', description: '每天的新闻早报、热榜、RSS、搜索与收藏；智能体可以查新闻。依赖 TrendRadar。', version: '1.0.0', enabled: true, state_version: 0, default_enabled: true, ready: true, missing: [], depends_on: [], dependents: [], tools: ['news_latest', 'news_search', 'news_briefing'], widgets: ['news_briefing'], can_manage: true }
export const emptyNavigation: Schema['NavigationOut'] = { version: 0, sections: [], pages: [], placements: {}, section_order: [], embed_origins: [], can_manage: true }
export const mcCapabilities: Schema['ModelConnectionCapabilities'] = {
  enabled: true, supported_protocols: ['openai_compatible'],
  official_presets: [{ provider: 'openai', label: 'OpenAI', protocol: 'openai_compatible', base_url: 'https://api.openai.com/v1', source: 'https://platform.openai.com/docs/api-reference/introduction' }],
  limits: { max_models: 1000, check_per_minute: 5, page_max: 100, check_fresh_seconds: 900 },
}
export const fail = (status: number, code: string) => Response.json({ error: { code, message: '请求失败', request_id: 'req-test', details: {} } }, { status })
export function mockApi(handler?: (request: Request) => Response | Promise<Response | undefined> | undefined) {
  const requests: Request[] = []
  vi.stubGlobal('fetch', async (request: Request) => {
    requests.push(request)
    const response = await handler?.(request)
    if (response) return response
    const path = new URL(request.url).pathname
    if (path === '/api/v1/setup/status') return Response.json({ setup_required: false })
    // OpenClaw 智能体总览与单个实例（T-0182）：测试没单独给出时，用同一个测试的实例列表拼出来（summary 按这些实例统计）
    const single = /^\/api\/v1\/agents\/instances\/([^/]+)$/.exec(path)
    if ((path === '/api/v1/agents/overview' || single) && request.method === 'GET') {
      const listed = await handler?.(new Request(new URL('/api/v1/agents/instances', request.url)))
      const items = listed?.ok ? ((await listed.json()) as Schema['AgentInstancePage']).items : []
      if (!single) return Response.json(overviewOf(items))
      const found = items.find(item => item.id === single[1])
      return found ? Response.json(found) : fail(404, 'NOT_FOUND')
    }
    if (path === '/api/v1/me' && request.method === 'GET') return Response.json(me)
    if (path === '/api/v1/auth/sessions') return Response.json({ ...emptyPage, items: [session] })
    if (path === '/api/v1/notifications') return Response.json({ ...emptyPage, unread_count: 0 })
    if (path === '/api/v1/modules' && request.method === 'GET') return Response.json({ items: [newsModule] })
    // 侧栏布局（T-0161）：默认没有保存过
    if (path === '/api/v1/navigation' && request.method === 'GET') return Response.json(emptyNavigation)
    if (path === '/api/v1/im/badges') return Response.json({ im_unread: 0, pending_approvals: 0 })
    if (path === '/api/v1/search') return Response.json(emptyPage)
    if (path === '/api/v1/ai/tool-executions') return Response.json({ ...emptyPage, pending_count: 0 })
    // AI 审批汇总（T-0191）：默认没有调用记录
    if (path === '/api/v1/ai/tool-executions/summary') return Response.json({ pending_count: 0, expiring_count: 0, critical_pending_count: 0, exception_count: 0, as_of: new Date().toISOString(), period: new URL(request.url).searchParams.get('period') ?? 'all', expiring_window_seconds: 180, instances: [], tools: [] })
    if (path === '/api/v1/audit-logs') return Response.json(emptyPage)
    if (/^\/api\/v1\/workspaces\/[^/]+\/members$/.test(path) && request.method === 'GET') return Response.json({ ...emptyPage, items: [{ user_id: me.user.id, email: me.user.email, display_name: me.user.display_name, role: 'owner', joined_at: '2026-10-30T00:00:00Z', you: true }] })
    if (path === '/api/v1/knowledge/resources' || path === '/api/v1/knowledge/sources' || path === '/api/v1/calendar/events') return Response.json(emptyPage)
    // 知识库（T-0099）：默认没有笔记
    // 实例的发件记录（T-0118）：默认没有
    if (/^\/api\/v1\/agents\/instances\/[^/]+\/outbox$/.test(path) && request.method === 'GET') return Response.json({ items: [], rejected_replies: [] })
    if (/^\/api\/v1\/agents\/instances\/[^/]+\/wakeups$/.test(path) && request.method === 'GET') return Response.json({ items: [], targets: [] })
    // 智能体服务器（T-0147、T-0181）：默认没有服务器，实例没有部署
    if (path === '/api/v1/agents/servers' && request.method === 'GET') return Response.json({ items: [], unassigned: [], summary: { server_count: 0, ready_count: 0, successful_deployments: 0, not_initialized_count: 0, needs_credentials_count: 0, deployment_counts_partial: false, generated_at: '2026-11-09T00:00:00Z' } })
    if (/^\/api\/v1\/agents\/instances\/[^/]+\/deployment$/.test(path) && request.method === 'GET') return Response.json({ deployment: null, server: null, job: null, capacity_warning: null })
    if (path === '/api/v1/runner/hosts/candidates' && request.method === 'GET') return Response.json({ items: [] })
    if (/^\/api\/v1\/runner\/hosts\/[^/]+\/ssh$/.test(path) && request.method === 'GET') return Response.json({ target: null, job: null, warning: null })
    // 两个服务器页面上的“MCP 管理器”区块（T-0234，大模型客户端模块）：默认这台服务器上没有工作台部署的管理器
    if (path === '/api/v1/llm_bridge/installs' && request.method === 'GET') return Response.json({ managers: [], available: true })
    if (/^\/api\/v1\/agents\/instances\/[^/]+\/runner\/capabilities$/.test(path) && request.method === 'GET') return Response.json({ instance_id: path.split('/')[5], held_count: 0, default_limit: 5, unlimited_active: false, grants: [], pending_approval_ids: [] })
    // 实例的运行环境权限（T-0140）：默认关闭
    if (/^\/api\/v1\/agents\/instances\/[^/]+\/runner$/.test(path) && request.method === 'GET') return Response.json(runnerOff)
    // 中转站管理（T-0206）：默认没有连接、没有迁移项、没有智能体分配；只开放 OpenAI 兼容协议与 OpenAI 官方预设
    if (path === '/api/v1/model-connections' && request.method === 'GET') return Response.json({ items: [], next_cursor: null, total: 0 })
    if (path === '/api/v1/model-connections/summary') return Response.json({ total: 0, check_passed: 0, check_passed_inference: 0, needs_attention: 0, non_model: 0, unique_granted_instances: 0 })
    if (path === '/api/v1/model-connections/capabilities') return Response.json(mcCapabilities)
    if (path === '/api/v1/model-connections/migration/entries') return Response.json({ items: [], next_cursor: null, total: 0, counts: {} })
    if (path === '/api/v1/agents/model-assignments') return Response.json({ items: [], next_cursor: null, total: 0 })
    if (path === '/api/v1/knowledge/spaces') return Response.json({ items: [] })
    if (path === '/api/v1/knowledge/graph') return Response.json({ nodes: [], edges: [], truncated: false, total_notes: 0 })
    // 知识总览（T-0173）：默认没有笔记
    if (path === '/api/v1/knowledge/overview') return Response.json({ summary: { note_count: 0, space_count: 0, link_count: 0, updated_note_count: 0 }, recent_notes: [], generated_at: '2026-11-03T10:00:00Z' })
    if (path === '/api/v1/knowledge/notes' || path === '/api/v1/knowledge/changes' || path === '/api/v1/knowledge/trash') return Response.json(emptyPage)
    if (path === '/api/v1/dashboard/overview') return Response.json(overview)
    // 首页改版（T-0164）：任务进展默认没有任务，今天没有早报主题
    if (path === '/api/v1/tasks/progress') return Response.json(emptyProgress)
    if (path === '/api/v1/tasks/summary') return Response.json(emptySummary)
    if (path === '/api/v1/projects/summary') return Response.json({ active: 0, attention: 0, completed: 0, as_of: '2026-10-30T00:00:00Z' })
    if (path === '/api/v1/projects/activity') return Response.json({ items: [], next_cursor: null })
    if (path === '/api/v1/tasks/assignees') return Response.json({ users: [], instances: [] })
    if (path === '/api/v1/news/today') return Response.json({ briefing: null, topics: [], unavailable: false, message: null })
    if (path === '/api/v1/news/briefings') return Response.json(emptyPage)
    if (path === '/api/v1/tasks') return Response.json({ items: [task], next_cursor: null, total_estimate: 1 })
    if (path === '/api/v1/projects') return Response.json({ items: [project], next_cursor: null, total_estimate: 1 })
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
