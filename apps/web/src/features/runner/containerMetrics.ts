import type { Schema } from '../../services/api'
import type { RunnerEnv } from './api'

// 容器服务器的读数怎么显示（T-0186，docs/v2/container-server-redesign 指标约定第 5 节）：
// - CPU 是 runner-agent 用累计 CPU 时间差分出的窗口平均核数，配置的 cpus 是上限，两者分开写（“0.42 / 2 核”）；
// - 内存是 Docker memory_stats.usage（含页缓存），限额 memory_mb 按 MiB；
// - 数据卷只是环境的数据卷，不含镜像与容器可写层，没有“磁盘配额百分比”。
// 读不到就写“未知 / 未采集”，不补 0；过期的数字保留但标出“已过期”和上报时间；不在运行的环境 CPU / 内存写“—”。
// 旧版服务端没有 metrics：CPU 写“未采集”，内存 / 数据卷用旧字段并标“时间未知”。
export type MetricStatus = Schema['RunnerEnvMetrics']['cpu_status']
export type ReadingStatus = MetricStatus | 'legacy'

export type Reading = {
  status: ReadingStatus
  /** 表格里的主文字：“0.42”“640”“1.2 GiB”“未知”“—” */
  value: string
  /** “/ 2 核”“/ 2048 MiB”；没有上限时为空 */
  limit: string
  /** 占上限的百分比（可以超过 100）；没有时为 null */
  percent: number | null
  /** 进度条长度（0 ~ 100） */
  bar: number
  /** 状态说明：“已过期”“未采集”“未运行”“时间未知”；实时为空 */
  note: string
  at: string | null
}

const KIB = 1024, MIB = 1024 * 1024, GIB = 1024 ** 3, TIB = 1024 ** 4

/** 二进制单位（1024 进制，写 KiB / MiB / GiB，不把 GiB 写成 GB） */
export function binary(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || bytes < 0) return '—'
  if (bytes < KIB) return `${bytes} B`
  const [unit, size] = bytes >= TIB ? ['TiB', TIB] : bytes >= GIB ? ['GiB', GIB] : bytes >= MIB ? ['MiB', MIB] : ['KiB', KIB]
  const n = bytes / (size as number)
  return `${n >= 100 ? Math.round(n) : Number(n.toFixed(1))} ${unit}`
}

export function mib(bytes: number): string {
  return String(Math.round(bytes / MIB))
}

export function cores(value: number): string {
  if (value === 0) return '0'
  if (value >= 10) return value.toFixed(1)
  return value < 0.01 ? '<0.01' : Number(value.toFixed(2)).toString()
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '时间未知'
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (seconds < 10) return '刚刚'
  if (seconds < 60) return `${seconds} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  return `${Math.floor(seconds / 86400)} 天前`
}

const bar = (percent: number | null) => (percent === null ? 0 : Math.max(0, Math.min(100, percent)))
const NOTE: Record<ReadingStatus, string> = { ok: '', stale: '已过期', unavailable: '未采集', not_running: '未运行', legacy: '时间未知' }

export function cpuReading(env: RunnerEnv): Reading {
  const m = env.metrics
  const limit = `/ ${cores(env.cpus)} 核`
  if (!m) return { status: 'unavailable', value: '未采集', limit: '', percent: null, bar: 0, note: '这个版本没有 CPU 读数', at: null }
  if (m.cpu_status === 'not_running') return { status: 'not_running', value: '—', limit: '', percent: null, bar: 0, note: NOTE.not_running, at: null }
  if (m.cpu_used_cores === null || m.cpu_status === 'unavailable') {
    return { status: 'unavailable', value: '未知', limit, percent: null, bar: 0, note: NOTE.unavailable, at: null }
  }
  const percent = m.cpu_percent_of_limit
  return { status: m.cpu_status, value: cores(m.cpu_used_cores), limit, percent, bar: bar(percent), note: NOTE[m.cpu_status], at: m.cpu_reported_at }
}

export function memoryReading(env: RunnerEnv): Reading {
  const m = env.metrics
  const limit = `/ ${env.memory_mb} MiB`
  if (!m) {
    if (env.state !== 'running' || env.mem_bytes === null || env.mem_bytes < 0) return { status: 'not_running', value: '—', limit: '', percent: null, bar: 0, note: '', at: null }
    const percent = env.memory_mb > 0 ? env.mem_bytes / (env.memory_mb * MIB) * 100 : null
    return { status: 'legacy', value: mib(env.mem_bytes), limit, percent, bar: bar(percent), note: NOTE.legacy, at: null }
  }
  if (m.memory_status === 'not_running') return { status: 'not_running', value: '—', limit: '', percent: null, bar: 0, note: NOTE.not_running, at: null }
  if (m.memory_used_bytes === null || m.memory_status === 'unavailable') {
    return { status: 'unavailable', value: '未知', limit, percent: null, bar: 0, note: NOTE.unavailable, at: null }
  }
  const percent = m.memory_percent_of_limit
  return { status: m.memory_status, value: mib(m.memory_used_bytes), limit, percent, bar: bar(percent), note: NOTE[m.memory_status], at: m.memory_reported_at }
}

export function volumeReading(env: RunnerEnv): Reading {
  const m = env.metrics
  if (!m) {
    if (env.state === 'archived' || env.state === 'deleted' || env.disk_bytes === null || env.disk_bytes < 0) {
      return { status: 'unavailable', value: '—', limit: '', percent: null, bar: 0, note: '', at: null }
    }
    return { status: 'legacy', value: binary(env.disk_bytes), limit: '', percent: null, bar: 0, note: NOTE.legacy, at: null }
  }
  if (m.data_volume_bytes === null || m.data_volume_status === 'unavailable' || m.data_volume_status === 'not_running') {
    const gone = env.state === 'archived' || env.state === 'deleted'
    return { status: 'unavailable', value: gone ? '—' : '未知', limit: '', percent: null, bar: 0, note: gone ? '数据卷已删除' : NOTE.unavailable, at: null }
  }
  return { status: m.data_volume_status, value: binary(m.data_volume_bytes), limit: '', percent: null, bar: 0, note: NOTE[m.data_volume_status], at: m.data_volume_reported_at }
}

export const SORT: Record<'last_used' | 'cpu' | 'memory', string> = { last_used: '最近使用', cpu: 'CPU 实际用量', memory: '内存实际用量' }

export const STATE_TONE: Record<RunnerEnv['state'], string> = {
  creating: 'blue', running: 'green', stopped: 'muted', archived: 'muted', pending_delete: 'amber', deleted: 'muted', failed: 'red',
}

/** 用途旁边显示的短 ID（选择、详情与地址里始终用完整 UUID） */
export const shortId = (id: string) => `env-${id.replace(/-/g, '').slice(0, 6)}`

/** 统计卡：本工作空间未删除的环境记录（含已归档，不是此刻 Docker 里的容器数）；需关注 = 失败 */
export function summary(counts: Record<string, number> | undefined) {
  if (!counts) return null
  const n = (state: string) => Number(counts[state] ?? 0)
  const total = Object.entries(counts).filter(([state]) => state !== 'deleted').reduce((sum, [, count]) => sum + Number(count), 0)
  return { total, running: n('running'), stopped: n('stopped'), attention: n('failed') }
}
