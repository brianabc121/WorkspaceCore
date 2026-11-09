import type { MailAccount, MailAccountState, MailItem } from './api'

// 邮件页的显示文字（T-0171）。发件人、主题等邮件头是外部输入：这里只拆字符串，页面按纯文字显示。

// “名字 <a@b>” → 名字与地址；没有名字时名字就是地址
export function parseSender(value: string): { name: string; address: string } {
  const match = /^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/.exec(value)
  if (match) {
    const address = match[2]!.trim()
    return { name: match[1]!.trim() || address, address }
  }
  const address = value.trim()
  return { name: address || '（未知发件人）', address }
}

const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const DAY = 86400000
const dayStart = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()

// 列表里的时间：今天 14:32 / 昨天 18:20 / 11月6日 / 2025/11/6；时间未知时写“时间未知”
export function shortTime(value: string | null, now = new Date()): string {
  if (!value) return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  const offset = Math.round((dayStart(date) - dayStart(now)) / DAY)
  if (offset === 0) return `今天 ${clock.format(date)}`
  if (offset === -1) return `昨天 ${clock.format(date)}`
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日`
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`
}

type AccountLike = Pick<MailAccount, 'display_name' | 'email'>
// 邮箱的名字：设置了显示名就用显示名，否则用地址
export const accountName = (account: AccountLike) => (account.display_name && account.display_name !== account.email ? account.display_name : account.email)
// 列表行上的邮箱标签：短一些（没有显示名时取地址 @ 前面的部分）
export const accountTag = (account: AccountLike) => (account.display_name && account.display_name !== account.email ? account.display_name : account.email.split('@')[0] || account.email)

// 每个邮箱一个颜色（只是辅助，旁边总有邮箱名字）；按邮箱在清单里的顺序取
const TONES = ['var(--chart-1)', 'var(--chart-3)', 'var(--chart-7)', 'var(--chart-2)', 'var(--chart-5)', 'var(--chart-4)']
export const toneOf = (index: number) => TONES[index % TONES.length]!

export const ACCOUNT_STATUS: Record<MailAccountState['status'], string> = {
  ok: '正常',
  needs_reconnect: '需要重新连接',
  rate_limited: 'Gmail 限流，稍后再试',
  unavailable: '暂时读不到',
  pending: '还没读完，加载更多时继续',
}

// 一个邮箱为什么读不到：只用工作台自己的说明，不显示 Gmail 返回的原文
export function accountProblem(state: MailAccountState): string {
  if (state.status === 'needs_reconnect') {
    return state.error_code === 'MAIL_SCOPE_MISSING' ? '没有授权读取邮件，需要重新连接并勾选读取权限' : 'Google 授权已失效，需要在 设置 → 邮件 里重新连接'
  }
  if (state.status === 'rate_limited') {
    const at = state.retry_at ? new Date(state.retry_at) : null
    return at && !Number.isNaN(at.getTime()) ? `Gmail 限流，${clock.format(at)} 以后再试` : 'Gmail 限流，稍后再试'
  }
  if (state.status === 'unavailable') return state.error_code === 'NOT_FOUND' ? '这个邮箱已断开或不存在' : 'Gmail 暂时读不到，稍后再试'
  if (state.status === 'pending') return '邮箱响应较慢，这一批没读完；点“加载更多”继续'
  return ACCOUNT_STATUS.ok
}

// 已加载的几批合在一起：按 (internalDate 倒序, account_id, id) 排；时间未知的放最后；同一邮箱同一封只留一条
export function mergeItems(pages: { items: MailItem[] }[]): MailItem[] {
  const seen = new Map<string, MailItem>()
  for (const page of pages) for (const item of page.items) seen.set(`${item.account_id}:${item.id}`, item)
  return [...seen.values()].sort(compareItems)
}

export function compareItems(a: MailItem, b: MailItem): number {
  const ta = a.date ? Date.parse(a.date) : Number.NaN, tb = b.date ? Date.parse(b.date) : Number.NaN
  const na = Number.isNaN(ta), nb = Number.isNaN(tb)
  if (na !== nb) return na ? 1 : -1
  if (!na && ta !== tb) return tb - ta
  if (a.account_id !== b.account_id) return a.account_id < b.account_id ? -1 : 1
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
}

// 阅读器里显示的系统标签（自定义标签只有 ID，不显示）
export const LABEL_NAMES: Record<string, string> = { INBOX: '收件箱', SENT: '已发送', STARRED: '星标', IMPORTANT: '重要', UNREAD: '未读' }
