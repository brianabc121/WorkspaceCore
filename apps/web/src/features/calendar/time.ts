import type { Schema } from '../../services/api'
const formatters = new Map<string, Intl.DateTimeFormat>()
export function validZone(zone: string): boolean {
  try { new Intl.DateTimeFormat('en', { timeZone: zone }).format(); return !!zone.trim() } catch { return false }
}
export function browserZone() { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' }
export function localDateTime(value: string, zone: string): string {
  let formatter = formatters.get(zone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    formatters.set(zone, formatter)
  }
  const parts = Object.fromEntries(formatter.formatToParts(new Date(value)).map((p) => [p.type, p.value]))
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}
export function dateInZone(value: string, zone: string): string { return localDateTime(value, zone).slice(0, 10) }
export function addDays(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(value.getTime()) || value.toISOString().slice(0, 10) !== date) throw new Error('日期无效。')
  value.setUTCDate(value.getUTCDate() + days)
  return value.toISOString().slice(0, 10)
}
export function zonedInstant(value: string, zone: string): string {
  const wall = Date.parse(`${value}:00Z`)
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) || !Number.isFinite(wall) || new Date(wall).toISOString().slice(0, 16) !== value || !validZone(zone)) throw new Error('日期、时间或 IANA 时区无效。')
  // Inspect offsets on both sides of a transition; the earlier matching instant
  // is deterministic when the clock repeats, and gaps have no matching instant.
  const candidates = new Set<number>()
  for (const hours of [-36, -24, -12, 0, 12, 24, 36]) {
    const sample = wall + hours * 3600000
    const offset = Date.parse(`${localDateTime(new Date(sample).toISOString(), zone)}:00Z`) - sample
    const candidate = wall - offset
    if (localDateTime(new Date(candidate).toISOString(), zone) === value) candidates.add(candidate)
  }
  if (!candidates.size) throw new Error('该当地时间因夏令时调整而不存在，请选择其他时间。')
  return new Date(Math.min(...candidates)).toISOString()
}
export function weekDates(date: string): string[] {
  const weekday = new Date(`${addDays(date, 0)}T00:00:00Z`).getUTCDay()
  const start = addDays(date, -((weekday + 6) % 7))
  return Array.from({ length: 7 }, (_, index) => addDays(start, index))
}
export function eventDates(event: Schema['CalendarEventOut'], zone: string): string[] {
  const start = event.all_day && event.start_date ? event.start_date : dateInZone(event.start_at, zone)
  const last = event.all_day && event.end_date ? addDays(event.end_date, -1) : dateInZone(new Date(Date.parse(event.end_at) - 1).toISOString(), zone)
  const result: string[] = []
  for (let day = start; day <= last && result.length < 94; day = addDays(day, 1)) result.push(day)
  return result
}
export function eventTime(event: Schema['CalendarEventOut']): string {
  return event.all_day ? `全天 · ${event.start_date} 至 ${event.end_date}（结束日期不含）· ${event.timezone}` : `${localDateTime(event.start_at, event.timezone).replace('T', ' ')} — ${localDateTime(event.end_at, event.timezone).replace('T', ' ')} · ${event.timezone}`
}
