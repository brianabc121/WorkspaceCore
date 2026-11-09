// 容器读数的显示口径（T-0186，docs/v2/container-server-redesign 指标约定第 5 节）：实测与上限分开、未知不补 0、
// 过期带上报时间、未运行写“—”、旧版服务端没有 metrics 时 CPU 写“未采集”、数据卷用 1024 进制单位。
import { describe, expect, it } from 'vitest'
import { ago, binary, cpuReading, memoryReading, summary, volumeReading } from '../src/features/runner/containerMetrics'
import { env, GIB, metrics, MIB, NOW } from './container-fixtures'

describe('containerMetrics', () => {
  it('shows actual CPU against the limit and memory in MiB', () => {
    const e = env()
    expect(cpuReading(e)).toMatchObject({ status: 'ok', value: '0.42', limit: '/ 2 核', percent: 21, bar: 21, note: '' })
    const mem = memoryReading(e)
    expect(mem).toMatchObject({ status: 'ok', value: '640', limit: '/ 2048 MiB', note: '' })
    expect(Math.round(mem.percent!)).toBe(31)
    expect(volumeReading(e)).toMatchObject({ status: 'ok', value: '1.2 GiB', percent: null })
  })

  it('keeps over-limit numbers but caps the bar, and a real zero is 0', () => {
    expect(cpuReading(env({ metrics: metrics({ cpu_used_cores: 3, cpu_percent_of_limit: 150 }) }))).toMatchObject({ value: '3', percent: 150, bar: 100 })
    expect(cpuReading(env({ metrics: metrics({ cpu_used_cores: 0, cpu_percent_of_limit: 0 }) }))).toMatchObject({ status: 'ok', value: '0', percent: 0 })
  })

  it('marks stale readings and keeps their time', () => {
    const r = cpuReading(env({ metrics: metrics({ cpu_status: 'stale', cpu_reported_at: '2026-11-09T09:50:00Z' }) }))
    expect(r).toMatchObject({ status: 'stale', value: '0.42', note: '已过期', at: '2026-11-09T09:50:00Z' })
    expect(memoryReading(env({ metrics: metrics({ memory_status: 'stale', memory_reported_at: null }) }))).toMatchObject({ status: 'stale', at: null })
  })

  it('never turns unknown into 0', () => {
    const r = cpuReading(env({ metrics: metrics({ cpu_used_cores: null, cpu_percent_of_limit: null, cpu_status: 'unavailable', cpu_reported_at: null, cpu_window_seconds: null }) }))
    expect(r).toMatchObject({ status: 'unavailable', value: '未知', percent: null, note: '未采集' })
    expect(volumeReading(env({ metrics: metrics({ data_volume_bytes: null, data_volume_status: 'unavailable' }) }))).toMatchObject({ value: '未知' })
  })

  it('shows — for live readings of environments that are not running, but keeps the volume', () => {
    const stopped = env({ state: 'stopped', metrics: metrics({ cpu_status: 'not_running', cpu_used_cores: null, cpu_percent_of_limit: null, memory_status: 'not_running', memory_used_bytes: null, memory_percent_of_limit: null }) })
    expect(cpuReading(stopped)).toMatchObject({ status: 'not_running', value: '—', percent: null })
    expect(memoryReading(stopped)).toMatchObject({ status: 'not_running', value: '—' })
    expect(volumeReading(stopped)).toMatchObject({ status: 'ok', value: '1.2 GiB' })
    const archived = env({ state: 'archived', metrics: metrics({ data_volume_bytes: null, data_volume_status: 'unavailable' }) })
    expect(volumeReading(archived)).toMatchObject({ value: '—', note: '数据卷已删除' })
  })

  it('falls back to the old fields when the server has no metrics, without inventing CPU', () => {
    const old = env({ metrics: null })
    expect(cpuReading(old)).toMatchObject({ value: '未采集', percent: null })
    expect(memoryReading(old)).toMatchObject({ status: 'legacy', value: '640', note: '时间未知' })
    expect(volumeReading(old)).toMatchObject({ status: 'legacy', value: '1.2 GiB' })
    expect(memoryReading(env({ metrics: null, state: 'stopped' }))).toMatchObject({ value: '—' })
    expect(volumeReading(env({ metrics: null, state: 'archived' }))).toMatchObject({ value: '—' })
  })

  it('formats binary sizes and relative times', () => {
    expect(binary(860 * MIB)).toBe('860 MiB')
    expect(binary(Math.round(2.4 * GIB))).toBe('2.4 GiB')
    expect(binary(0)).toBe('0 B')
    expect(binary(null)).toBe('—')
    expect(binary(-1)).toBe('—')
    expect(ago('2026-11-09T09:59:55Z', NOW)).toBe('刚刚')
    expect(ago('2026-11-09T09:59:30Z', NOW)).toBe('30 秒前')
    expect(ago('2026-11-09T09:57:00Z', NOW)).toBe('3 分钟前')
    expect(ago(null, NOW)).toBe('时间未知')
  })

  it('counts the stat cards from the overview, excluding deleted records', () => {
    expect(summary({ running: 5, stopped: 2, failed: 1, archived: 1, creating: 1, deleted: 4 })).toEqual({ total: 10, running: 5, stopped: 2, attention: 1 })
    expect(summary({})).toEqual({ total: 0, running: 0, stopped: 0, attention: 0 })
    expect(summary(undefined)).toBeNull()
  })
})
