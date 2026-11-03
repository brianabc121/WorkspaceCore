// 消息页（T-0095）的显示规则：时间按浏览器所在时区，会话列表里越近越短；头像和群里发言人的颜色按 ID 固定。

const pad = (n: number) => String(n).padStart(2, '0')
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const DAY = 86400000

function parse(value: string | null | undefined): Date | null {
  if (!value) return null
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? null : time
}
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
// 相差几个自然日（今天 0，昨天 1）
const daysAgo = (d: Date, now: Date) => Math.round((startOfDay(now) - startOfDay(d)) / DAY)

/** 气泡里的时间：14:05 */
export function clockTime(value: string | null | undefined): string {
  const d = parse(value)
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : ''
}

/** 会话列表里的时间：今天 14:05 / 昨天 / 一周内周几 / 今年 11/01 / 更早 2025/11/01 */
export function listTime(value: string | null | undefined, now = new Date()): string {
  const d = parse(value)
  if (!d) return ''
  const ago = daysAgo(d, now)
  if (ago <= 0) return clockTime(value)
  if (ago === 1) return '昨天'
  if (ago < 7) return WEEKDAYS[d.getDay()]
  if (d.getFullYear() === now.getFullYear()) return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}`
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`
}

/** 消息之间的日期分隔：今天 / 昨天 / 11 月 1 日 / 2025 年 11 月 1 日 */
export function dayLabel(value: string | null | undefined, now = new Date()): string {
  const d = parse(value)
  if (!d) return ''
  const ago = daysAgo(d, now)
  if (ago === 0) return '今天'
  if (ago === 1) return '昨天'
  const md = `${d.getMonth() + 1} 月 ${d.getDate()} 日`
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()} 年 ${md}`
}

/** 两条消息是否在同一个自然日 */
export function sameDay(a: string | null | undefined, b: string | null | undefined): boolean {
  const x = parse(a), y = parse(b)
  return !!x && !!y && startOfDay(x) === startOfDay(y)
}

/** 两条消息相隔几分钟以内（同一个人连续发的消息合成一组） */
export function within(a: string | null | undefined, b: string | null | undefined, minutes: number): boolean {
  const x = parse(a), y = parse(b)
  return !!x && !!y && Math.abs(y.getTime() - x.getTime()) <= minutes * 60000
}

/** 头像与发言人颜色：1–6，按 ID 固定 */
export function colorIndex(seed: string): number {
  let hash = 2166136261 // FNV-1a：短名字也分布得比较开
  for (const ch of seed) { hash ^= ch.codePointAt(0)!; hash = Math.imul(hash, 16777619) >>> 0 }
  return (hash % 6) + 1
}

/** 头像上的字：中文取第一个字，其他取第一个字母（大写） */
export function initial(name: string): string {
  const first = [...name.trim()][0] ?? '?'
  return first.toUpperCase()
}
