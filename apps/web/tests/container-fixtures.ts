// 容器服务器页测试共用的数据（T-0186 / T-0187）。数字只是测试用的例子，不是默认数据。
import type { Schema } from '../src/services/api'
import { mockApi, me, workspace } from './helpers'

export const at = '2026-11-09T09:59:00Z'
export const NOW = new Date('2026-11-09T10:00:00Z').getTime()
export const MIB = 1024 * 1024
export const GIB = 1024 ** 3
export const path = (r: Request) => new URL(r.url).pathname
export const params = (r: Request) => new URL(r.url).searchParams
export const as = (role: string) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })

export const spec = (over: Partial<Schema['RunnerSpecOut']> = {}): Schema['RunnerSpecOut'] => ({
  image: 'python:3.12-slim', steps: [], start_command: null, runtime: 'gvisor', user: 'user', network: 'none', domains: [], memory_mb: 2048, cpus: 2, pids: 1024, disk_gb: 20,
  env: {}, secrets: [], persist: [], lifetime: 'session', ...over,
})
export const host = (over: Partial<Schema['RunnerHostOut']> = {}): Schema['RunnerHostOut'] => ({
  backend: 'docker', node_name: null, runtime_metadata: {},
  id: 'host-1', name: '容器节点 A', status: 'online', ingress: 'intranet', token_prefix: 'mwr_abcdefgh', cpus: 16, mem_bytes: 32 * GIB, disk_bytes: 500 * GIB, disk_free_bytes: 414 * GIB,
  kvm: false, runtimes: ['runc', 'runsc'], docker_version: '29.9.1', agent_version: '0.2.0', last_seen_at: at,
  report: { mem_bytes: 32 * GIB, mem_free_bytes: 20 * GIB, disk_bytes: 500 * GIB, disk_free_bytes: 414 * GIB, images_bytes: 30 * GIB, containers_bytes: 5 * GIB, volumes_bytes: 40 * GIB, unlabeled_containers: 0, load: 1.5, at },
  policy: { session_idle_minutes: 15, mem_high: 0.85, disk_high: 0.8, disk_emergency: 0.95, snapshot_max_gb: 5 }, version: 4, created_at: at, ...over,
})
export const metrics = (over: Partial<Schema['RunnerEnvMetrics']> = {}): Schema['RunnerEnvMetrics'] => ({
  cpu_used_cores: 0.42, cpu_percent_of_limit: 21, cpu_window_seconds: 60, cpu_reported_at: at, cpu_status: 'ok',
  memory_used_bytes: 640 * MIB, memory_percent_of_limit: 31.25, memory_reported_at: at, memory_status: 'ok',
  data_volume_bytes: Math.round(1.2 * GIB), data_volume_reported_at: at, data_volume_status: 'ok', ...over,
})
export const env = (over: Partial<Schema['RunnerEnvAdminOut']> = {}): Schema['RunnerEnvAdminOut'] => ({
  runtime_backend: 'docker', host_id: 'host-1', placement: {},
  id: '0a1b2c3d-0000-4000-8000-000000000001', untrusted: { purpose: '前端构建' }, lifetime: 'session', state: 'running', state_reason: null, template: 'node', reuse_key: null, owner: '开发助手', mine: false,
  runtime: 'gvisor', user: 'user', network: 'none', memory_mb: 2048, cpus: 2, delete_when: [], share: 'none', project_id: null, pinned: false, use_count: 3, created_at: at, last_used_at: at,
  next_step: '空闲 15 分钟后停止', wake_seconds: 2.5, owner_instance_id: 'inst-a', template_id: 'tpl-1', spec: spec(), mem_bytes: 640 * MIB, disk_bytes: Math.round(1.2 * GIB), egress: {},
  retention_score: 12.5, retention_infinite: false, snapshot_at: null, snapshot_bytes: null, started_at: at, stopped_at: null, archived_at: null, deleted_at: null, delete_after: null, version: 7,
  metrics: metrics(), ...over,
})
export const overview = (over: Partial<Schema['RunnerOverview']> = {}): Schema['RunnerOverview'] => ({
  host: host(), environments: { running: 5, stopped: 2, failed: 1, archived: 1, deleted: 4 },
  instances: [
    { instance_id: 'inst-a', name: '开发助手', enabled: true, running: 2, persistent: 0, memory_mb: 4096, cpus: 3, limits: { envs: 3, persistent: 2, memory_mb: 16384, cpus: 8 } },
    { instance_id: 'inst-b', name: '新闻助手', enabled: false, running: 1, persistent: 1, memory_mb: 1024, cpus: 1, limits: { envs: 3, persistent: 2, memory_mb: 16384, cpus: 8 } },
  ],
  recent: [{ environment_id: 'env-9', purpose: '依赖检查', state: 'stopped', reason: '空闲 15 分钟，自动停止', at }], ...over,
})
export const run = (over: Partial<Schema['RunnerRunAdminOut']> = {}): Schema['RunnerRunAdminOut'] => ({
  run_id: 'run-1', environment_id: env().id, status: 'running', exit_code: null, untrusted: { output: '' }, output_truncated: false, seconds: null, started_at: at, finished_at: null,
  instance: '开发助手', command: 'npm run build', timeout_seconds: 600, ...over,
})

export type ListPage = { items: Schema['RunnerEnvAdminOut'][]; total?: number | null; next_cursor?: string | null }
export function setup(options: {
  role?: string
  data?: Schema['RunnerOverview']
  list?: (r: Request) => ListPage
  detail?: (id: string, r: Request) => Response
  handler?: (r: Request) => Response | undefined
} = {}) {
  return mockApi(async r => {
    const result = options.handler?.(r)
    if (result) return result
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return as(options.role ?? 'admin')
    if (p === '/api/v1/runner/overview') return Response.json(options.data ?? overview())
    if (p === '/api/v1/runner/environments' && r.method === 'GET') {
      const page = options.list?.(r) ?? { items: [env()], total: 1, next_cursor: null }
      return Response.json(page)
    }
    const detail = /^\/api\/v1\/runner\/environments\/([^/]+)$/.exec(p)
    if (detail && r.method === 'GET') return options.detail ? options.detail(detail[1], r) : Response.json(env({ id: detail[1] }))
    if (p === '/api/v1/runner/runs') return Response.json({ items: [] })
    if (p === '/api/v1/runner/templates') return Response.json({ items: [] })
    return undefined
  })
}
