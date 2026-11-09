// 日历页的中文日期 / 时间文字（T-0169）。日期都是显示时区里的 YYYY-MM-DD。
import { addDays, localDateTime } from './time'
import { allDayDates, type CalendarEvent } from './weekLayout'

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

export function weekday(date: string): string { return WEEKDAYS[new Date(`${date}T00:00:00Z`).getUTCDay()]! }
export function isWeekend(date: string): boolean { const day = new Date(`${date}T00:00:00Z`).getUTCDay(); return day === 0 || day === 6 }
const parts = (date: string) => ({ year: Number(date.slice(0, 4)), month: Number(date.slice(5, 7)), day: Number(date.slice(8, 10)) })

export function monthDay(date: string): string { const { month, day } = parts(date); return `${month}月${day}日` }
export function fullDate(date: string): string { const { year, month, day } = parts(date); return `${year}年${month}月${day}日` }
export function monthTitle(date: string): string { const { year, month } = parts(date); return `${year}年${month}月` }

// 区间标题：同月“2026年11月9日—15日”，跨月“2026年11月30日—12月6日”，跨年写全两个年份
export function rangeTitle(first: string, last: string): string {
  const a = parts(first), b = parts(last)
  if (a.year !== b.year) return `${fullDate(first)}—${fullDate(last)}`
  if (a.month !== b.month) return `${fullDate(first)}—${b.month}月${b.day}日`
  return `${fullDate(first)}—${b.day}日`
}

export function clock(instant: number | string, zone: string): string {
  return localDateTime(typeof instant === 'number' ? new Date(instant).toISOString() : instant, zone).slice(11)
}

// 定时日程在显示时区里的时间；不在同一天时带上日期。
// 起止跨过夏令时切换（偏移不同）时在两端注明 UTC 偏移，例如回拨那一小时里的“01:30（UTC−4）– 01:00（UTC−5）”
export function timedRange(event: CalendarEvent, zone: string): string {
  const start = localDateTime(event.start_at, zone), end = localDateTime(event.end_at, zone)
  const from = offsetAt(event.start_at, zone), to = offsetAt(event.end_at, zone), mark = (offset: string) => (from !== to ? `（${offset}）` : '')
  if (start.slice(0, 10) === end.slice(0, 10)) return `${start.slice(11)}${mark(from)} – ${end.slice(11)}${mark(to)}`
  return `${monthDay(start.slice(0, 10))} ${start.slice(11)}${mark(from)} – ${monthDay(end.slice(0, 10))} ${end.slice(11)}${mark(to)}`
}

// 全天日程：显示包含的最后一天（结束日期不含）
export function allDayRange(event: CalendarEvent): string {
  const { start, end } = allDayDates(event), last = addDays(end, -1)
  return start === last ? `${monthDay(start)} ${weekday(start)} · 全天` : `${monthDay(start)} – ${monthDay(last)} · 全天`
}

export function eventWhen(event: CalendarEvent, zone: string): string {
  return event.all_day ? allDayRange(event) : `${monthDay(localDateTime(event.start_at, zone).slice(0, 10))} ${weekday(localDateTime(event.start_at, zone).slice(0, 10))} ${timedRange(event, zone)}`
}

// “UTC+8”“UTC−4”：某一时刻在这个时区的偏移（夏令时前后不同）
const offsetFormats = new Map<string, Intl.DateTimeFormat>()
export function offsetAt(instant: string, zone: string): string {
  try {
    let format = offsetFormats.get(zone)
    if (!format) { format = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'shortOffset' }); offsetFormats.set(zone, format) }
    const offset = (format.formatToParts(new Date(instant)).find((part) => part.type === 'timeZoneName')?.value ?? '').replace(/^GMT/, '')
    return `UTC${offset ? offset.replace('-', '−') : '+0'}`
  } catch { return '' }
}
// 某一天（按 UTC 中午取样）的偏移，工具条上显示
export function zoneOffset(zone: string, date: string): string { return offsetAt(`${date}T12:00:00Z`, zone) }
