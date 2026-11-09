import type { Schema } from '../../services/api'

// 运维指标的显示格式（T-0177，二批改造规范第 7.2 节）。只在显示层换算一次，原始数据仍是接口的单位：
// - requests_per_second → “1.2 req/s”；seconds → 不到 1 秒乘 1000 显示毫秒（“184 ms”），否则“1.25 s”；
// - ratio → 乘 100 一次显示百分比（“1.2%”）；count（例如服务在线数量）原样显示，不变成百分比；
// - null 或不是有限数（缺测、除数为 0）→ “—”，不补 0。
export type MetricUnit = Schema['MetricViewOut']['unit']

const formats = new Map<number, Intl.NumberFormat>()
function plain(value: number, digits: number) {
  let format = formats.get(digits)
  if (!format) {
    format = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits })
    formats.set(digits, format)
  }
  // -0 显示成 0
  return format.format(value === 0 ? 0 : value)
}
// 按量级保留小数：≥100 不留，≥10 一位，≥1 两位，更小三位
function digitsFor(value: number) {
  const size = Math.abs(value)
  return size >= 100 ? 0 : size >= 10 ? 1 : size >= 1 ? 2 : 3
}

export function formatMetricValue(value: number | null | undefined, unit: MetricUnit): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  switch (unit) {
    case 'requests_per_second': return `${plain(value, digitsFor(value))} req/s`
    case 'seconds': {
      if (Math.abs(value) >= 1) return `${plain(value, digitsFor(value))} s`
      const ms = value * 1000
      return `${plain(ms, digitsFor(ms))} ms`
    }
    case 'ratio': {
      const percent = value * 100
      return `${plain(percent, digitsFor(percent))}%`
    }
    default: return plain(value, Number.isInteger(value) ? 0 : 2)
  }
}

// 纵轴刻度：取“好看”的步长（1、2、2.5、5 × 10ⁿ）；数量（count）的步长至少为 1
export function niceTicks(low: number, high: number, unit: MetricUnit, count = 4): number[] {
  if (!Number.isFinite(low) || !Number.isFinite(high)) return []
  if (high <= low) high = low + (unit === 'count' ? 1 : Math.abs(low) || 1)
  const raw = (high - low) / count
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const normalized = raw / magnitude
  let step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10) * magnitude
  if (unit === 'count') step = Math.max(1, Math.ceil(step))
  const start = Math.floor(low / step) * step, end = Math.ceil(high / step) * step
  const ticks: number[] = []
  for (let i = 0; start + i * step <= end + step / 1e6 && i < 50; i++) ticks.push(Number((start + i * step).toPrecision(12)))
  return ticks
}

export const RANGE_LABELS: Record<Schema['MetricsOut']['range'], string> = { '1h': '最近 1 小时', '6h': '最近 6 小时', '24h': '最近 24 小时', '7d': '最近 7 天' }

export function stepLabel(seconds: number) {
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`
  if (seconds % 60 === 0) return `${seconds / 60} 分钟`
  return `${seconds} 秒`
}
