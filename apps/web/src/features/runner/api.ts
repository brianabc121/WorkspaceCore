import { computed, ref, type Ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { ApiError, dataOf, type Schema } from '../../services/api'

// 运行服务器页（T-0140，RUNNER_PLAN 第 11 节，owner / admin）：总览、环境、模板、运行记录、设置。
// 查询的 key 以 'runner' 开头；写操作之后刷新全部 'runner' 查询与实例卡片（'agents'）。
// /api/v1/runner/* 不会自动带 X-Workspace-Id，每次请求都显式传。
export type RunnerEnv = Schema['RunnerEnvAdminOut']
export type RunnerTemplate = Schema['RunnerTemplateAdminOut']
export type RunnerRun = Schema['RunnerRunAdminOut']
export type RunnerHost = Schema['RunnerHostOut']

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, enabled: computed(() => active() && auth.canManage) }
}

export function useRunnerOverview(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: ['runner', c.user, scope, 'overview'], enabled: c.enabled, refetchInterval: 30_000,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/runner/overview', { params: { header: c.header }, signal })) })
}

export type EnvFilter = { state?: RunnerEnv['state']; lifetime?: RunnerEnv['lifetime']; instance_id?: string; include_deleted?: boolean }
export function useRunnerEnvironments(scope: string, filter: Ref<EnvFilter>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => ['runner', c.user, scope, 'environments', filter.value]), enabled: c.enabled, refetchInterval: 30_000,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/runner/environments', { params: { header: c.header, query: clean(filter.value) }, signal })) })
}

export function useRunnerTemplates(scope: string) {
  const c = context(scope)
  return useQuery({ queryKey: ['runner', c.user, scope, 'templates'], enabled: c.enabled,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/runner/templates', { params: { header: c.header }, signal })),
    refetchInterval: query => query.state.data?.items.some(t => t.build_status === 'building' || t.build_status === 'pending') ? 5000 : false })
}

export type RunFilter = { environment_id?: string; instance_id?: string }
export function useRunnerRuns(scope: string, filter: Ref<RunFilter>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => ['runner', c.user, scope, 'runs', filter.value]), enabled: c.enabled,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/runner/runs', { params: { header: c.header, query: { ...clean(filter.value), limit: 100 } }, signal })),
    refetchInterval: query => query.state.data?.items.some(r => r.status === 'running' || r.status === 'queued') ? 5000 : false })
}

export function useRunnerRun(scope: string, runId: Ref<string | null>) {
  const c = context(scope)
  return useQuery({ queryKey: computed(() => ['runner', c.user, scope, 'run', runId.value]), enabled: computed(() => c.enabled.value && !!runId.value),
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/runner/runs/{run_id}', { params: { header: c.header, path: { run_id: runId.value! } }, signal })) })
}

function clean<T extends Record<string, unknown>>(value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined && v !== '' && v !== false)) as Partial<T>
}

// 一次只做一个写操作；成功后刷新运行服务器与实例卡片的查询
export function useRunnerAction(scope: string) {
  const auth = useAuthStore(), cache = useQueryClient(), user = auth.user?.id
  const busy = ref(false), error = ref(''), notice = ref('')
  const active = () => auth.user?.id === user && auth.workspaceId === scope
  const refresh = () => cache.invalidateQueries({ predicate: q => ['runner', 'agents'].includes(String(q.queryKey[0])) && q.queryKey[1] === user && q.queryKey[2] === scope })
  async function act(effect: () => Promise<unknown>, success = '') {
    if (busy.value || !active()) return false
    busy.value = true; error.value = ''; notice.value = ''
    try { await effect(); if (!active()) return false; notice.value = success; await refresh(); return true }
    catch (cause) { if (active()) { error.value = runnerError(cause); if (cause instanceof ApiError && cause.code === 'WRITE_UNKNOWN') await refresh() } return false }
    finally { busy.value = false }
  }
  return { busy, error, notice, act, refresh, header: { 'X-Workspace-Id': scope } }
}

export function runnerError(error: unknown): string {
  if (!(error instanceof ApiError)) return '请求失败，请稍后重试。'
  const messages: Record<string, string> = {
    VERSION_CONFLICT: '已被其他人修改，请刷新后再操作。',
    RUNNER_EXISTS: '这个工作空间已经有一台运行服务器了。',
    RUNNER_HAS_ENVIRONMENTS: '还有没删除的环境：先在“环境”里删除，再移除运行服务器。',
    AGENT_INGRESS_NOT_CONFIGURED: '这个入口还没有配置（AGENT_INTRANET_URL / AGENT_PUBLIC_URL），不能接入运行服务器。',
    ENV_UNAVAILABLE: error.message || '环境现在的状态做不了这个操作。',
    SNAPSHOT_NOT_FOUND: '这个环境没有可用的快照了，不能恢复。',
    RUNNER_UNAVAILABLE: '运行服务器不在线。',
    TEMPLATE_NOT_READY: '模板还没有构建好。',
    VALIDATION_FAILED: error.message || '输入不符合要求，请检查后重试。',
    FORBIDDEN: '只有 owner / admin 能管理运行服务器。',
    NOT_FOUND: '记录不存在，或你已无权访问。',
    WRITE_UNKNOWN: '连接中断，结果未知：已刷新，请看最新状态再决定要不要重试。',
  }
  return messages[error.code] ?? (error.status === 0 ? '网络连接失败，请检查连接后重试。' : error.message || '请求失败，请稍后重试。')
}

export const STATE: Record<RunnerEnv['state'], string> = {
  creating: '创建中', running: '运行中', stopped: '已停止', archived: '已归档', pending_delete: '待删除', deleted: '已删除', failed: '失败',
}
export const LIFETIME: Record<RunnerEnv['lifetime'], string> = { once: '一次性', session: '会话', persistent: '常驻' }
export const RUNTIME: Record<RunnerEnv['runtime'], string> = { gvisor: 'gVisor', runc: 'runc', kvm: 'runc + KVM' }
export const USER: Record<RunnerEnv['user'], string> = { user: '普通用户', root: 'root' }
export const NETWORK: Record<RunnerEnv['network'], string> = { none: '不联网', allowlist: '只放行名单', all: '全部外网' }
export const HOST_STATUS: Record<RunnerHost['status'], string> = { pending: '等待接入', online: '在线', offline: '离线', disabled: '已停用' }
export const BUILD: Record<RunnerTemplate['build_status'], string> = { pending: '等待构建', building: '构建中', ready: '已就绪', failed: '构建失败' }
export const RUN_STATUS: Record<RunnerRun['status'], string> = {
  queued: '排队中', running: '执行中', succeeded: '成功', failed: '失败', timeout: '超时', unknown: '结果未知', cancelled: '已取消',
}

// 非默认的选项（方案 8.1）：隔离变弱或常驻，页面上标出来
export function riskyChoices(item: { runtime: RunnerEnv['runtime']; user: RunnerEnv['user']; network: RunnerEnv['network'] }): string[] {
  const out: string[] = []
  if (item.runtime !== 'gvisor') out.push(`运行时 ${RUNTIME[item.runtime]}`)
  if (item.user === 'root') out.push('root 用户')
  if (item.network === 'all') out.push('全部外网')
  return out
}

export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let n = value, i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1 }
  return `${n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`
}

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value < 60) return `${Math.round(value)} 秒`
  if (value < 3600) return `${Math.round(value / 60)} 分钟`
  return `${(value / 3600).toFixed(1)} 小时`
}

// 回收策略（runner_specs.DEFAULT_POLICY）：分组显示，单位写在标签里
export const POLICY_FIELDS: { key: string; label: string; group: string; step?: number }[] = [
  { key: 'session_idle_minutes', label: '会话：空闲多少分钟停止', group: '空闲分档' },
  { key: 'session_archive_hours', label: '会话：停止多少小时归档', group: '空闲分档' },
  { key: 'session_max_hours', label: '会话：最长存活（小时）', group: '空闲分档' },
  { key: 'persistent_idle_minutes', label: '常驻：空闲多少分钟停止', group: '空闲分档' },
  { key: 'persistent_archive_days', label: '常驻：多少天没用归档', group: '空闲分档' },
  { key: 'startup_factor', label: '空闲停止至少是启动耗时的几倍', group: '空闲分档' },
  { key: 'max_idle_minutes', label: '空闲停止最多等多少分钟', group: '空闲分档' },
  { key: 'failed_once_keep_minutes', label: '失败的一次性环境保留（分钟）', group: '删除' },
  { key: 'grace_minutes', label: '一次性、会话的待删除宽限期（分钟）', group: '删除' },
  { key: 'persistent_grace_hours', label: '常驻的待删除宽限期（小时）', group: '删除' },
  { key: 'archive_retention_days', label: '归档 / 删除后记录与快照保留（天）', group: '删除' },
  { key: 'snapshot_max_gb', label: '单个快照上限（GB）', group: '删除' },
  { key: 'mem_high', label: '内存高水位（0 ~ 1）', group: '资源水位', step: 0.01 },
  { key: 'mem_low', label: '内存回收到（0 ~ 1）', group: '资源水位', step: 0.01 },
  { key: 'disk_high', label: '磁盘高水位（0 ~ 1）', group: '资源水位', step: 0.01 },
  { key: 'disk_low', label: '磁盘回收到（0 ~ 1）', group: '资源水位', step: 0.01 },
  { key: 'disk_emergency', label: '磁盘告急（0 ~ 1）', group: '资源水位', step: 0.01 },
]

export const LIMIT_FIELDS: { key: string; label: string; step?: number }[] = [
  { key: 'envs', label: '同时运行的环境' },
  { key: 'persistent', label: '其中常驻' },
  { key: 'memory_mb', label: '内存合计（MB）' },
  { key: 'cpus', label: 'CPU 合计（核）', step: 0.5 },
  { key: 'disk_gb', label: '磁盘合计（GB）' },
  { key: 'execs_per_hour', label: '每小时执行命令' },
  { key: 'run_hours_per_day', label: '每天运行时长（小时）', step: 0.5 },
]

function csrf() {
  const value = document.cookie.split('; ').find(item => item.startsWith('ws_csrf='))?.slice(8)
  try { return value ? decodeURIComponent(value) : '' } catch { return '' }
}

// 接入包是 zip（运行服务器凭证只出现在这一次下载里），用 fetch 下载交给浏览器
export async function downloadRunnerBundle(scope: string): Promise<string> {
  let response: Response
  try {
    response = await fetch(new Request(`${window.location.origin}/api/v1/runner/host/bundle`, {
      method: 'POST', credentials: 'same-origin', headers: { 'X-Workspace-Id': scope, 'X-CSRF-Token': csrf() },
    }))
  } catch { throw new ApiError(0, 'WRITE_UNKNOWN', '请求未完成') }
  if (!response.ok) {
    let detail: Partial<Schema['ErrorBody']> = {}
    try { detail = ((await response.json()) as Schema['ErrorResponse']).error ?? {} } catch { /* gateway HTML */ }
    throw new ApiError(response.status, detail.code ?? 'HTTP_ERROR', detail.message ?? '请求失败', detail.request_id ?? null)
  }
  const blob = await response.blob()
  const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')?.[1] ?? 'myworkspace-runner.zip'
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = name; document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return name
}
