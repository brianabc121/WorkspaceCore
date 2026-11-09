// 首页组件共用的时间文字（T-0164），都按浏览器时区
const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const DAY = 86400000
const dayStart = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
export function dayOffset(value: Date, now = new Date()) { return Math.round((dayStart(value) - dayStart(now)) / DAY) }
export function clockText(value: Date) { return clock.format(value) }

// 今天 / 明天 / 昨天 + 时刻；其余日子写月日
export function dayClock(value: string, now = new Date()) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间不可用'
  const offset = dayOffset(date, now)
  const day = offset === 0 ? '今天' : offset === 1 ? '明天' : offset === -1 ? '昨天' : `${date.getMonth() + 1}月${date.getDate()}日`
  return `${day} ${clockText(date)}`
}

// 最近更新：刚刚 / N 分钟前 / N 小时前（当天）/ 昨天 HH:MM / M月D日
export function relativeTime(value: string, now = new Date()) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间不可用'
  const minutes = Math.floor((now.getTime() - date.getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  if (dayOffset(date, now) === 0) return `${Math.floor(minutes / 60)}小时前`
  if (dayOffset(date, now) === -1) return `昨天 ${clockText(date)}`
  return date.getFullYear() === now.getFullYear() ? `${date.getMonth() + 1}月${date.getDate()}日` : `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`
}

// 我的待办的标签：逾期 / 紧急 / 今天 / 明天 / 月日（没有截止时间又不紧急时没有标签）
export type TodoTag = { text: string; tone: 'danger' | 'accent' | 'neutral' }
export function todoTag(task: { due_at: string | null; priority: number }, now = new Date()): TodoTag | null {
  const due = task.due_at ? new Date(task.due_at) : null
  if (due && !Number.isNaN(due.getTime()) && due.getTime() < now.getTime()) return { text: '已逾期', tone: 'danger' }
  if (task.priority >= 4) return { text: '紧急', tone: 'danger' }
  if (!due || Number.isNaN(due.getTime())) return null
  const offset = dayOffset(due, now)
  if (offset === 0) return { text: '今天', tone: 'accent' }
  if (offset === 1) return { text: '明天', tone: 'neutral' }
  return { text: `${due.getMonth() + 1}月${due.getDate()}日`, tone: 'neutral' }
}

// “近期提醒”的区间：从现在到第 days 天结束（1 = 今天，2 = 今天和明天）
export function reminderRange(days: number, now = new Date()) {
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days)
  return { start: now.toISOString(), end: end.toISOString() }
}
