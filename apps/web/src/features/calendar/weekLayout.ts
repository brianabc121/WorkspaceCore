// 日历周视图与日程列表的纯布局函数（T-0169，四页改造规范 §6）。
// - 定时日程按“显示时区”的每一天 [当地 0 点, 次日当地 0 点) 裁剪；跨日日程每天各画一段。
// - 纵向位置用当地挂钟分钟（0–1440），与左侧时间刻度对齐；夏令时当天（23 / 25 小时）也不漂移。
//   重复的那一小时（秋季回拨）里，结束挂钟早于开始时，按真实经过的分钟数画高度。
// - 重叠的日程并列（列号 / 总列数）；超过上限的折叠成“另有 N 项”，不彼此覆盖。
// - 全天日程按它自己 IANA 时区的本地日期（start_date ~ end_date，结束日不含）放在顶部全天区域，
//   不用 UTC 时间加 24 小时推算，所以显示时区与事件时区相差很大时也不会错日。
// - 只读取传入的数据，不修改原始 UTC 时间。
import type { Schema } from '../../services/api'
import { addDays, dateInZone, localDateTime, zonedInstant } from './time'

export type CalendarEvent = Schema['CalendarEventOut']
export type DueTask = Schema['TaskOut']

export const DAY_MINUTES = 1440

// 显示时区里的日期区间 [start, end)，日期都是 YYYY-MM-DD
export interface DateRange { start: string; end: string }

export function rangeDates(range: DateRange): string[] {
  const days: string[] = []
  for (let day = addDays(range.start, 0); day < range.end && days.length < 100; day = addDays(day, 1)) days.push(day)
  return days
}

export function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000)
}

// 当地一天的开始：通常是 00:00；少数时区在午夜切换夏令时（00:00 不存在），取当天第一个存在的时刻
export function dayStart(date: string, zone: string): number {
  for (const time of ['00:00', '00:30', '01:00', '01:30', '02:00']) {
    try { return Date.parse(zonedInstant(`${date}T${time}`, zone)) } catch { /* 这一刻因夏令时不存在，试下一个 */ }
  }
  throw new Error('日期或时区无效。')
}

export interface DayFrame { date: string; start: number; end: number; hours: number }

// 每一天在显示时区里的起止时刻（UTC 毫秒）与实际小时数（夏令时当天是 23 或 25）
export function dayFrames(range: DateRange, zone: string): DayFrame[] {
  const dates = rangeDates(range)
  const starts = [...dates, range.end].map((date) => dayStart(date, zone))
  return dates.map((date, index) => ({ date, start: starts[index]!, end: starts[index + 1]!, hours: (starts[index + 1]! - starts[index]!) / 3600000 }))
}

export function wallMinutes(instant: number, zone: string): number {
  const time = localDateTime(new Date(instant).toISOString(), zone).slice(11)
  return Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5))
}

// 全天日程的本地日期区间（结束日不含）；缺日期时按事件自己的时区从 UTC 时刻换算
export function allDayDates(event: CalendarEvent): DateRange {
  const start = event.start_date ?? dateInZone(event.start_at, event.timezone)
  const end = event.end_date ?? dateInZone(event.end_at, event.timezone)
  return { start, end: end > start ? end : addDays(start, 1) }
}

function instants(event: CalendarEvent) {
  const start = Date.parse(event.start_at), end = Date.parse(event.end_at)
  return { start, end: end > start ? end : start }
}

function overlaps(start: number, end: number, frame: DayFrame) {
  return start === end ? start >= frame.start && start < frame.end : start < frame.end && end > frame.start
}

export interface TimedSegment {
  event: CalendarEvent
  date: string
  dayIndex: number
  // 当天裁剪后的真实时刻（UTC 毫秒）
  start: number
  end: number
  // 纵向位置：当地挂钟分钟。bottom 至少比 top 多 minMinutes，太短的日程也看得见、点得到
  top: number
  bottom: number
  startsBefore: boolean
  endsAfter: boolean
  column: number
  columns: number
}

// 同一组重叠日程里超出列数上限、折叠起来的部分
export interface CollapsedGroup { date: string; dayIndex: number; top: number; bottom: number; column: number; columns: number; events: CalendarEvent[] }

export interface DayColumn extends DayFrame { segments: TimedSegment[]; collapsed: CollapsedGroup[] }

export type LaneEntry = { kind: 'event'; event: CalendarEvent } | { kind: 'task'; task: DueTask }
type LaneDraft = LaneEntry & { key: string; startIndex: number; span: number; startsBefore: boolean; endsAfter: boolean }
export type LaneItem = LaneDraft & { row: number }

export interface WeekLayout {
  days: DayColumn[]
  // 顶部全天区域：可见的条（全天日程与任务截止）、可见行数、每天折叠的项数
  lane: LaneItem[]
  laneRows: number
  laneHidden: number[]
}

export interface WeekLayoutOptions {
  // 任务截止：只读派生，和全天日程一起放在顶部全天区域
  tasks?: DueTask[]
  // 一组重叠日程最多并排几列；超过时最后一列显示“另有 N 项”
  maxColumns?: number
  // 全天区域最多显示几行
  maxLaneRows?: number
  // 最短的画出高度（分钟）
  minMinutes?: number
}

function placeColumns(segments: TimedSegment[], maxColumns: number, date: string, dayIndex: number) {
  const sorted = [...segments].sort((a, b) => a.top - b.top || b.bottom - a.bottom || a.start - b.start || a.event.id.localeCompare(b.event.id))
  const visible: TimedSegment[] = [], collapsed: CollapsedGroup[] = []
  let cluster: TimedSegment[] = [], ends: number[] = [], clusterEnd = -1
  const flush = () => {
    if (!cluster.length) return
    const columns = ends.length
    if (columns <= maxColumns) {
      for (const segment of cluster) visible.push({ ...segment, columns })
    } else {
      const keep = Math.max(1, maxColumns - 1), hidden = cluster.filter((segment) => segment.column >= keep)
      for (const segment of cluster) if (segment.column < keep) visible.push({ ...segment, columns: maxColumns })
      collapsed.push({ date, dayIndex, top: Math.min(...hidden.map((s) => s.top)), bottom: Math.max(...hidden.map((s) => s.bottom)), column: keep, columns: maxColumns, events: hidden.map((s) => s.event) })
    }
    cluster = []; ends = []; clusterEnd = -1
  }
  for (const segment of sorted) {
    if (segment.top >= clusterEnd) flush()
    let column = ends.findIndex((end) => end <= segment.top)
    if (column < 0) { column = ends.length; ends.push(segment.bottom) } else ends[column] = segment.bottom
    cluster.push({ ...segment, column })
    clusterEnd = Math.max(clusterEnd, segment.bottom)
  }
  flush()
  visible.sort((a, b) => a.top - b.top || a.column - b.column)
  return { visible, collapsed }
}

export function layoutWeekEvents(events: readonly CalendarEvent[], range: DateRange, zone: string, options: WeekLayoutOptions = {}): WeekLayout {
  const { tasks = [], maxColumns = 3, maxLaneRows = 3, minMinutes = 20 } = options
  const frames = dayFrames(range, zone)
  const perDay: TimedSegment[][] = frames.map(() => [])
  const entries: LaneDraft[] = []
  for (const event of events) {
    if (event.all_day) {
      const dates = allDayDates(event)
      const startIndex = Math.max(0, daysBetween(range.start, dates.start)), endIndex = Math.min(frames.length, daysBetween(range.start, dates.end))
      if (endIndex > startIndex) entries.push({ kind: 'event', event, key: `event:${event.id}`, startIndex, span: endIndex - startIndex, startsBefore: dates.start < range.start, endsAfter: dates.end > range.end })
      continue
    }
    const { start, end } = instants(event)
    frames.forEach((frame, dayIndex) => {
      if (!overlaps(start, end, frame)) return
      const from = Math.max(start, frame.start), to = Math.min(end, frame.end)
      const top = start < frame.start ? 0 : wallMinutes(from, zone)
      let bottom = end >= frame.end ? DAY_MINUTES : wallMinutes(to, zone)
      if (bottom <= top) bottom = top + Math.round((to - from) / 60000)
      bottom = Math.min(DAY_MINUTES, Math.max(bottom, top + minMinutes))
      perDay[dayIndex]!.push({ event, date: frame.date, dayIndex, start: from, end: to, top, bottom, startsBefore: start < frame.start, endsAfter: end > frame.end, column: 0, columns: 1 })
    })
  }
  for (const task of tasks) {
    if (!task.due_at) continue
    const index = daysBetween(range.start, dateInZone(task.due_at, zone))
    if (index >= 0 && index < frames.length) entries.push({ kind: 'task', task, key: `task:${task.id}`, startIndex: index, span: 1, startsBefore: false, endsAfter: false })
  }
  // 全天区域：先放全天日程（早开始、跨度长的在上），再放任务截止（按截止时间）
  const order = (item: LaneDraft) => (item.kind === 'event' ? 0 : 1)
  entries.sort((a, b) => order(a) - order(b) || a.startIndex - b.startIndex || b.span - a.span
    || (a.kind === 'task' && b.kind === 'task' ? Date.parse(a.task.due_at!) - Date.parse(b.task.due_at!) : 0) || a.key.localeCompare(b.key))
  const rows: boolean[][] = [], lane: LaneItem[] = [], laneHidden = frames.map(() => 0)
  for (const entry of entries) {
    let row = rows.findIndex((cells) => cells.slice(entry.startIndex, entry.startIndex + entry.span).every((used) => !used))
    if (row < 0) { row = rows.length; rows.push(frames.map(() => false)) }
    for (let index = entry.startIndex; index < entry.startIndex + entry.span; index++) rows[row]![index] = true
    if (row < maxLaneRows) lane.push({ ...entry, row })
    else for (let index = entry.startIndex; index < entry.startIndex + entry.span; index++) laneHidden[index]!++
  }
  const days = frames.map((frame, index) => {
    const { visible, collapsed } = placeColumns(perDay[index]!, maxColumns, frame.date, index)
    return { ...frame, segments: visible, collapsed }
  })
  return { days, lane, laneRows: Math.min(rows.length, maxLaneRows), laneHidden }
}

// “显示内容”筛选：日程（定时）、任务截止、全天事件
export interface CalendarFilters { timed: boolean; due: boolean; allDay: boolean }
export function filterEvents(events: readonly CalendarEvent[], filters: CalendarFilters): CalendarEvent[] {
  return events.filter((event) => (event.all_day ? filters.allDay : filters.timed))
}

// 日程列表与“选中日期”的每天条目：全天在前，其余按时间；任务截止与日程按时间穿插
export type DayEntry =
  | { kind: 'event'; key: string; event: CalendarEvent; allDay: boolean; start: number; startsBefore: boolean; endsAfter: boolean }
  | { kind: 'task'; key: string; task: DueTask; start: number }

export function entriesByDay(events: readonly CalendarEvent[], tasks: readonly DueTask[], range: DateRange, zone: string): Map<string, DayEntry[]> {
  const frames = dayFrames(range, zone), result = new Map<string, DayEntry[]>(frames.map((frame) => [frame.date, []]))
  for (const event of events) {
    if (event.all_day) {
      const dates = allDayDates(event)
      for (let day = dates.start < range.start ? range.start : dates.start; day < dates.end && day < range.end; day = addDays(day, 1)) {
        result.get(day)?.push({ kind: 'event', key: `event:${event.id}`, event, allDay: true, start: -Infinity, startsBefore: day > dates.start, endsAfter: addDays(day, 1) < dates.end })
      }
      continue
    }
    const { start, end } = instants(event)
    for (const frame of frames) {
      if (overlaps(start, end, frame)) result.get(frame.date)!.push({ kind: 'event', key: `event:${event.id}`, event, allDay: false, start: Math.max(start, frame.start), startsBefore: start < frame.start, endsAfter: end > frame.end })
    }
  }
  for (const task of tasks) {
    if (!task.due_at) continue
    result.get(dateInZone(task.due_at, zone))?.push({ kind: 'task', key: `task:${task.id}`, task, start: Date.parse(task.due_at) })
  }
  const title = (entry: DayEntry) => (entry.kind === 'event' ? entry.event.title : entry.task.title)
  for (const list of result.values()) list.sort((a, b) => (a.start === b.start ? 0 : a.start - b.start) || (a.kind === b.kind ? 0 : a.kind === 'event' ? -1 : 1) || title(a).localeCompare(title(b), 'zh-CN'))
  return result
}
