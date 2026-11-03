import { useInfiniteQuery } from '@tanstack/vue-query'
import { useAuthStore } from '../../stores/auth'
import { dataOf } from '../../services/api'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'
export const priorities = ['未设置', '低', '中', '高', '紧急', '立即']
// 任务标签（T-0082）：用逗号或顿号分隔，最多 20 个、每个不超过 50 字；去掉空白，重复的只保留一个
export const LABELS_MAX = 20, LABEL_MAX = 50
export function parseLabels(text: string) { return [...new Set(text.split(/[,，、]/).map((v) => v.trim()).filter(Boolean))] }
export function toLocalInput(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
// 工作台自己的项目（T-0082）；默认不含已归档的，项目页可以选择显示
export function useProjects(scope: string, includeArchived: MaybeRefOrGetter<boolean> = false) {
  const auth = useAuthStore()
  return useInfiniteQuery({ queryKey: computed(() => ['projects', auth.user?.id, scope, toValue(includeArchived)]), initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => dataOf(await auth.api.GET('/api/v1/projects', { params: { header: { 'X-Workspace-Id': scope }, query: { limit: 100, include_archived: toValue(includeArchived), ...(pageParam ? { cursor: pageParam } : {}) } }, signal })),
    getNextPageParam: (page) => page.next_cursor ?? undefined, enabled: computed(() => !!auth.user),
  })
}

// 任务页的智能视图（T-0084）：转成列表接口已有的参数（status、due_after 含、due_before 不含），按浏览器时区算“今天”
export type TaskView = 'all' | 'open' | 'today' | 'overdue' | 'upcoming' | 'done'
export const taskViews: { id: TaskView; label: string }[] = [
  { id: 'all', label: '全部' }, { id: 'open', label: '未完成' }, { id: 'today', label: '今天' },
  { id: 'overdue', label: '已逾期' }, { id: 'upcoming', label: '未来 7 天' }, { id: 'done', label: '已完成' },
]
const DAY = 24 * 60 * 60 * 1000
export function startOfDay(date: Date) { return new Date(date.getFullYear(), date.getMonth(), date.getDate()) }
function addDays(date: Date, days: number) { const next = new Date(date); next.setDate(next.getDate() + days); return next }
export function viewQuery(view: TaskView, now = new Date()): { status: 'all' | 'open' | 'done'; due_after?: string; due_before?: string } {
  const today = startOfDay(now)
  if (view === 'today') return { status: 'open', due_after: today.toISOString(), due_before: addDays(today, 1).toISOString() }
  if (view === 'overdue') return { status: 'open', due_before: now.toISOString() }
  if (view === 'upcoming') return { status: 'open', due_after: now.toISOString(), due_before: new Date(now.getTime() + 7 * DAY).toISOString() }
  if (view === 'open' || view === 'done') return { status: view }
  return { status: 'all' }
}
// “今天”视图里快速添加的任务截止到今天 23:59（本地时间），添加后仍在这个视图里
export function endOfToday(now = new Date()) { return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59).toISOString() }

// 截止时间的简短说明：今天 / 明天 / 昨天 + 时刻，同一年省略年份；未完成且已过期的标为逾期
const clock = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
export type DueTone = 'none' | 'overdue' | 'today' | 'normal'
export function dueInfo(value: string | null, done: boolean, now = new Date()): { text: string; tone: DueTone } {
  if (!value) return { text: '无截止时间', tone: 'none' }
  const due = new Date(value)
  if (Number.isNaN(due.getTime())) return { text: '时间不可用', tone: 'none' }
  const days = Math.round((startOfDay(due).getTime() - startOfDay(now).getTime()) / DAY)
  const time = clock.format(due)
  const day = days === 0 ? '今天' : days === 1 ? '明天' : days === -1 ? '昨天'
    : due.getFullYear() === now.getFullYear() ? `${due.getMonth() + 1}月${due.getDate()}日` : `${due.getFullYear()}年${due.getMonth() + 1}月${due.getDate()}日`
  const text = `${day} ${time}`
  if (done) return { text, tone: 'normal' }
  if (due.getTime() < now.getTime()) return { text: `逾期 · ${text}`, tone: 'overdue' }
  return { text, tone: days === 0 ? 'today' : 'normal' }
}
