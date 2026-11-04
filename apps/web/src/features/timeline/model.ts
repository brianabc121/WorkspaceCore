// 任务时间轴的纯函数（T-0105，docs/v2/TIMELINE_PLAN.md 第 2 节）：时间范围、片段的位置与状态、token 的显示、项目颜色。
// 不依赖 Vue 与接口，单独测试（tests/timeline-model.test.ts）。
import type { Schema } from '../../services/api'

export type Scale = 'hour' | 'day' | 'week'
export type Timeline = Schema['TimelineOut']
export type Segment = Schema['TimelineSegment']
export type Run = Schema['TaskRunOut']
export type TimelineTask = Schema['TimelineTask']
export type Tokens = Schema['TokenCount']

export const SCALES: { id: Scale; label: string; hint: string }[] = [
  { id: 'hour', label: '小时', hint: '一格 15 分钟，看正在跑的任务' },
  { id: 'day', label: '天', hint: '一格 1 小时' },
  { id: 'week', label: '周', hint: '一格 1 天，看跨天的任务' },
]
const MINUTE = 60_000, HOUR = 60 * MINUTE

// ---------------------------------------------------------------- 日期与范围（浏览器所在时区）

export const dayKey = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
export function parseDay(value: unknown): Date | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const [y, m, d] = value.split('-').map(Number)
  const date = new Date(y, m - 1, d)
  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? date : null
}
export const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate())
export function addDays(date: Date, days: number) { const next = new Date(date); next.setDate(next.getDate() + days); return next }
// 周一开始的一周
export function startOfWeek(date: Date) { const day = startOfDay(date); return addDays(day, -((day.getDay() + 6) % 7)) }

// 时间范围：天 = 当天 0 点到次日 0 点；周 = 周一到下周一；小时 = 所选那天、以当前时间（不是今天时为中午）为中心的 6 小时
export function rangeOf(scale: Scale, anchor: Date, now = new Date()): { start: Date; end: Date } {
  if (scale === 'week') { const start = startOfWeek(anchor); return { start, end: addDays(start, 7) } }
  const day = startOfDay(anchor)
  if (scale === 'day') return { start: day, end: addDays(day, 1) }
  const focus = startOfDay(now).getTime() === day.getTime() ? now : new Date(day.getTime() + 12 * HOUR)
  const start = new Date(Math.floor((focus.getTime() - 3 * HOUR) / (15 * MINUTE)) * 15 * MINUTE)
  return { start, end: new Date(start.getTime() + 6 * HOUR) }
}
// 前一段 / 后一段
export function shift(scale: Scale, anchor: Date, step: 1 | -1) { return addDays(anchor, scale === 'week' ? 7 * step : step) }

// 刻度：小时刻度每 15 分钟一格（整点有标签），天每小时一格（每 2 小时一个标签），周每天一格
export interface Tick { at: number; left: number; major: boolean; label: string }
const hm = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const md = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', weekday: 'short' })
export function ticks(scale: Scale, start: Date, end: Date): Tick[] {
  const span = end.getTime() - start.getTime(), out: Tick[] = []
  if (scale === 'week') {
    for (let day = startOfDay(start); day < end; day = addDays(day, 1)) {
      const at = day.getTime()
      if (at >= start.getTime()) out.push({ at, left: ((at - start.getTime()) / span) * 100, major: true, label: md.format(day) })
    }
    return out
  }
  const step = scale === 'hour' ? 15 * MINUTE : HOUR
  for (let at = Math.ceil(start.getTime() / step) * step; at < end.getTime(); at += step) {
    const date = new Date(at)
    const major = scale === 'hour' ? date.getMinutes() === 0 : date.getHours() % 2 === 0
    out.push({ at, left: ((at - start.getTime()) / span) * 100, major, label: major ? hm.format(date) : '' })
  }
  return out
}

// ---------------------------------------------------------------- 片段

export type ClipState = 'planned' | 'deadline' | 'running' | 'done' | 'paused' | 'interrupted' | 'cancelled'
export interface Clip {
  key: string
  segment: Segment
  task: TimelineTask | undefined
  state: ClipState
  start: number
  end: number
  // 百分比位置（相对时间范围）
  left: number
  width: number
  overdue: { left: number } | null // 超过截止时间的部分，相对片段的百分比
  waits: { left: number; width: number }[]
  ghost: { left: number; width: number } | null // 执行中、截止时间还没到：从现在到截止
}

const clamp = (value: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, value))
const pct = (at: number, start: number, span: number) => ((at - start) / span) * 100
const time = (value: string | null | undefined) => (value ? new Date(value).getTime() : NaN)

export function clipOf(segment: Segment, task: TimelineTask | undefined, start: number, end: number, now: number): Clip | null {
  const span = end - start
  let from: number, to: number, state: ClipState
  if (segment.kind === 'planned') {
    const planned = time(segment.start), due = time(segment.end)
    if (Number.isNaN(planned)) {
      if (Number.isNaN(due)) return null
      from = due - 10 * MINUTE; to = due; state = 'deadline'
    } else {
      from = planned; to = Number.isNaN(due) || due <= planned ? planned + HOUR : due; state = 'planned'
    }
  } else {
    const run = segment.run
    from = time(segment.start); to = segment.end ? time(segment.end) : now
    state = !run || !segment.end ? 'running' : (run.end_reason ?? 'done') as ClipState
  }
  if (to <= start || from >= end) return null
  const a = clamp(from, start, end), b = clamp(Math.max(to, from + MINUTE), start, end)
  const left = pct(a, start, span), width = Math.max(pct(b, start, span) - left, 0.25)
  const due = time(task?.due_at)
  const clipSpan = b - a || 1
  const overdue = segment.kind === 'run' && !Number.isNaN(due) && due < to && due > from && !task?.done
    ? { left: clamp(((due - a) / clipSpan) * 100, 0, 100) } : null
  const ghost = state === 'running' && !Number.isNaN(due) && due > now && now < end
    ? { left: pct(now, start, span), width: pct(Math.min(due, end), start, span) - pct(now, start, span) } : null
  const waits = (segment.run?.waits ?? []).flatMap(wait => {
    const ws = clamp(time(wait.start), a, b), we = clamp(wait.end ? time(wait.end) : now, a, b)
    return we > ws ? [{ left: ((ws - a) / clipSpan) * 100, width: Math.max(((we - ws) / clipSpan) * 100, 1) }] : []
  })
  return { key: segment.run?.id ?? `plan:${segment.task_id}`, segment, task, state, start: from, end: to, left, width, overdue, waits, ghost }
}

export const STATE_LABELS: Record<ClipState, string> = {
  planned: '计划中', deadline: '截止', running: '执行中', done: '已完成', paused: '已暂停', interrupted: '已中断', cancelled: '已取消',
}

// ---------------------------------------------------------------- 文字

export function compact(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 100_000 ? 0 : 1)}k`
  return String(value)
}
// 一段的 token：人的任务不计；实例没上报显示“未上报”；平均分的标“≈”
export function tokenText(run: Run | null | undefined): string {
  if (!run) return ''
  if (run.executor_kind === 'user') return '人，不计 token'
  if (!run.usage_reported || !run.tokens) return 'token 未上报'
  return `${run.tokens_shared ? '≈' : ''}${compact(run.tokens.total)} token`
}
export function duration(ms: number) {
  const minutes = Math.max(0, Math.round(ms / MINUTE))
  if (minutes < 60) return `${minutes} 分`
  const hours = Math.floor(minutes / 60), rest = minutes % 60
  if (hours < 24) return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`
  const days = Math.floor(hours / 24)
  return `${days} 天 ${hours % 24} 小时`
}
export const clock = (value: number | string) => hm.format(new Date(value))

// 超时多久（执行中、已过截止）
export function overdueBy(task: TimelineTask | undefined, now: number): number | null {
  const due = time(task?.due_at)
  return task && !task.done && !Number.isNaN(due) && due < now ? now - due : null
}

// ---------------------------------------------------------------- 项目颜色

// 6 种颜色（与知识图谱同一组，亮暗两套，已过 dataviz 的配色检查）；按项目在工作空间里的顺序固定分配，
// 第 7 个起是灰色（“其他项目”），不循环。片段上总有标题，不只靠颜色区分。
export const PROJECT_COLORS = 6
export function projectColors(projectIds: readonly string[]): Map<string, number> {
  const map = new Map<string, number>()
  projectIds.forEach((id, index) => map.set(id, index < PROJECT_COLORS ? index + 1 : 0))
  return map
}

// ---------------------------------------------------------------- token 用量

export function bars(usage: Schema['TimelineUsage'], start: number, end: number) {
  const span = end - start, width = (usage.bucket_minutes * MINUTE / span) * 100
  const max = Math.max(1, ...usage.buckets.map(b => b.tokens.total))
  return usage.buckets
    .map(b => ({ at: time(b.start), total: b.tokens.total }))
    .filter(b => b.at >= start && b.at < end)
    .map(b => ({ ...b, left: pct(b.at, start, span), width, height: Math.max(4, Math.round((b.total / max) * 100)) }))
}
