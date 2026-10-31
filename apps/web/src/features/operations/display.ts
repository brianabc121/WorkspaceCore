import type { Schema } from '../../services/api'
export const serviceLabels: Record<Schema['ServiceStatusOut']['status'], string> = { ok: '正常', degraded: '降级', down: '不可用', unknown: '尚未检查', not_configured: '未部署 / 未配置' }
export function serviceReason(item: Schema['ServiceStatusOut']): string {
  const reasons: Record<string, string> = { timeout: '检查超时', unreachable: '无法连接服务', rate_limited: '服务限流中', token_rejected: '授权失效，请管理员更新连接', not_synced: '尚未完成同步', disabled: '连接已停用', not_deployed: '组件尚未部署', not_configured: '尚未配置连接', not_checked: '尚未完成检查', not_ready: '服务尚未就绪', heartbeat_overdue: 'Worker 心跳已过期', heartbeat_unreadable: '无法读取 Worker 心跳', no_heartbeat: '尚无 Worker 心跳', sync_overdue: '同步已过期', source_unavailable: '外部服务暂不可用', missing_permission: '缺少读取权限' }
  return item.reason ? reasons[item.reason] ?? (item.status === 'ok' ? '' : '请稍后刷新检查结果') : ''
}
export function metricText(value: number | null, unit: Schema['MetricViewOut']['unit']): string {
  if (value === null || !Number.isFinite(value)) return '无数据'
  const number = (unit === 'ratio' ? value * 100 : value).toLocaleString(undefined, { maximumFractionDigits: 4 })
  return number + ({ seconds: ' s', requests_per_second: ' req/s', ratio: '%', count: '' })[unit]
}
export function metricSegments(points: Schema['MetricPoint'][]): { x: number; y: number }[][] {
  const valid = points.filter((point) => point.value !== null && Number.isFinite(point.value))
  if (!valid.length) return []
  const times = points.map((point) => Date.parse(point.at)), start = Math.min(...times), span = Math.max(...times) - start || 1
  const low = Math.min(0, ...valid.map((point) => point.value!)), high = Math.max(...valid.map((point) => point.value!)), scale = high - low || 1
  const segments: { x: number; y: number }[][] = []; let current: { x: number; y: number }[] = []
  for (const point of points) {
    if (point.value === null || !Number.isFinite(point.value)) { if (current.length) segments.push(current); current = []; continue }
    current.push({ x: 8 + (Date.parse(point.at) - start) / span * 304, y: 102 - (point.value - low) / scale * 94 })
  }
  if (current.length) segments.push(current)
  return segments
}
