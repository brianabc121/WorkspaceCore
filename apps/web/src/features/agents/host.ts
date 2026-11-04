import { computed } from 'vue'
import { keepPreviousData, useQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf, type Schema } from '../../services/api'

// 服务器状态（T-0113，docs/v2/AGENT_OPS_PLAN.md 第 5 节）。接入包每分钟上报一次，这里每分钟刷新。
// 键以 'agents' 开头，SSE 的 agents 主题会一起刷新。
export type HostRange = Schema['HostStatusOut']['range']
export type HostAlert = Schema['HostStatusOut']['alerts'][number]
const REFRESH_MS = 60_000

function context(scope: string) {
  const auth = useAuthStore(), user = auth.user?.id
  const active = () => user === auth.user?.id && scope === auth.workspaceId
  return { auth, user, header: { 'X-Workspace-Id': scope }, active }
}

export function useHostSummary(scope: string) {
  const c = context(scope)
  return useQuery({
    queryKey: ['agents', c.user, scope, 'host-summary'], enabled: computed(() => c.active() && c.auth.isOwner), refetchInterval: REFRESH_MS,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/host-summary', { params: { header: c.header }, signal })),
  })
}

export function useHostStatus(scope: string, instanceId: () => string, range: () => HostRange, enabled: () => boolean) {
  const c = context(scope)
  return useQuery({
    queryKey: computed(() => ['agents', c.user, scope, 'host', instanceId(), range()]),
    enabled: computed(() => c.active() && c.auth.isOwner && enabled()), refetchInterval: REFRESH_MS, placeholderData: keepPreviousData,
    queryFn: async ({ signal }) => dataOf(await c.auth.api.GET('/api/v1/agents/instances/{instance_id}/host', { params: { header: c.header, path: { instance_id: instanceId() }, query: { range: range() } }, signal })),
  })
}

export const RANGE: Record<HostRange, string> = { '24h': '24 小时', '7d': '7 天', '30d': '30 天' }
export const RANGE_HOURS: Record<HostRange, number> = { '24h': 24, '7d': 7 * 24, '30d': 30 * 24 }
export const ALERT: Record<HostAlert, string> = {
  disk_low: '磁盘可用空间不足 10%',
  memory_low: '内存可用不足 5%',
  load_high: '负载持续偏高（15 分钟负载超过核心数的 2 倍）',
  not_reporting: '30 分钟没有上报',
}
// 小指示标红的阈值，与告警一致（D7）：磁盘可用低于 10%、内存可用低于 5%；CPU 看负载告警
export const DISK_USED_MAX = 90
export const MEMORY_USED_MAX = 95

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  let n = value, unit = 0
  while (n >= 1024 && unit < UNITS.length - 1) { n /= 1024; unit += 1 }
  return `${unit === 0 || n >= 100 ? Math.round(n) : n.toFixed(1)} ${UNITS[unit]}`
}
export const rate = (value: number | null | undefined) => value === null || value === undefined ? '—' : `${bytes(value)}/s`
export const percent = (value: number | null | undefined) => value === null || value === undefined ? '—' : `${value >= 10 ? Math.round(value) : value.toFixed(1)}%`
export const load = (value: number | null | undefined) => value === null || value === undefined ? '—' : value.toFixed(2)

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const days = Math.floor(seconds / 86400), hours = Math.floor((seconds % 86400) / 3600), minutes = Math.floor((seconds % 3600) / 60)
  if (days) return `${days} 天 ${hours} 小时`
  if (hours) return `${hours} 小时 ${minutes} 分`
  return `${minutes} 分钟`
}

// 每小时一个点的时间，按本地时间显示到小时
export function hourLabel(at: string, withDay = true): string {
  const d = new Date(at)
  const hh = String(d.getHours()).padStart(2, '0')
  return withDay ? `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${hh}:00` : `${hh}:00`
}
