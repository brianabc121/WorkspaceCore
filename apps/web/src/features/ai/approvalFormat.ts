import type { RouteLocationRaw } from 'vue-router'
import { Bell, Bookmark, CalendarDays, CircleHelp, MessageSquare, SquareCheck, Users, Wrench } from '@lucide/vue'
import type { Execution, ExecutionStatus, RiskLevel } from './queries'

// 审批中心的展示规则（T-0191，规范 docs/v2/ai-admin-redesign §4）：只整理服务端给的数据，不补理由、前后值或聊天原文。
// 外部文字（summary、参数、来源、结果）一律由模板按文本渲染；这里只返回字符串。

export type Tone = 'blue' | 'red' | 'amber' | 'green' | 'muted' | 'violet'
export const RISK_TONE: Record<RiskLevel, Tone> = { read: 'muted', write: 'blue', critical: 'red', knowledge: 'green', direct: 'violet' }
export const STATUS_TONE: Record<ExecutionStatus, Tone> = {
  pending_approval: 'blue', rejected: 'muted', expired: 'muted', executing: 'blue', succeeded: 'green', failed: 'red', unknown: 'amber', denied: 'muted',
}
/** 已经执行（或正在执行）的状态；其余（待审批、拒绝、过期、权限不够）都没有执行 */
export const EXECUTED: ExecutionStatus[] = ['executing', 'succeeded', 'failed', 'unknown']

export const shortId = (id: string, head = 6, tail = 4) => (id.length > head + tail + 1 ? `${id.slice(0, head)}…${id.slice(-tail)}` : id)

// ---------------------------------------------------------------- 时间

/** 剩余秒数：按服务端时钟（本机时间 + offset）算，向上取整；没到期时间返回 null，已到期返回 0 */
export function remainingSeconds(expiresAt: string | null, serverNow: number): number | null {
  if (!expiresAt) return null
  const at = Date.parse(expiresAt)
  if (Number.isNaN(at)) return null
  return Math.max(0, Math.ceil((at - serverNow) / 1000))
}
export function formatRemaining(seconds: number) {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60
  const two = (n: number) => String(n).padStart(2, '0')
  return h ? `${h}:${two(m)}:${two(s)}` : `${two(m)}:${two(s)}`
}
const clockFormatter = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
const dayFormatter = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
const valid = (value: string | null) => (value && !Number.isNaN(Date.parse(value)) ? new Date(value) : null)
/** 14:32:14 */
export const clockTime = (value: string | null) => { const d = valid(value); return d ? clockFormatter.format(d) : '—' }
/** 11月9日 14:32:14 */
export const dayTime = (value: string | null) => { const d = valid(value); return d ? dayFormatter.format(d) : '—' }

// ---------------------------------------------------------------- 来源

export type SourceKind = 'direct' | 'group' | 'unknown'
/** IM 会话（单聊 si_ 与其他会话）/ IM 群聊（sg_）/ 来源未知（没有会话）；inferred 另外标“来源推断” */
export function sourceOf(source: Execution['source']) {
  const conversation = source.conversation_id
  const kind: SourceKind = !conversation ? 'unknown' : conversation.startsWith('sg_') ? 'group' : 'direct'
  const label = kind === 'group' ? 'IM 群聊' : kind === 'direct' ? 'IM 会话' : '来源未知'
  return { kind, label, conversation, inferred: source.inferred }
}
export const SOURCE_ICONS = { direct: MessageSquare, group: Users, unknown: CircleHelp } as const
/** 触发者：记下的显示名，否则 IM 账号；都没有就写未记录 */
export const triggeredBy = (source: Execution['source']) => source.triggered_by_name || source.triggered_by_im_id || null

// ---------------------------------------------------------------- 工具与参数

/** 已知工具（services/api/app/schemas/ai_tools.py 的写操作与高危工具）：读得懂的字段顺序与工具做什么 */
const TOOLS: Record<string, { effect: string; fields: string[]; kind: 'task' | 'calendar' | 'bookmark' | 'notification' }> = {
  create_task: { effect: '在项目里新建一个任务', fields: ['title', 'project_id', 'description', 'priority', 'due_at', 'labels', 'assignee_user_id'], kind: 'task' },
  update_task: { effect: '修改一个任务的字段', fields: ['task_id', 'title', 'description', 'priority', 'due_at', 'project_id'], kind: 'task' },
  delete_task: { effect: '删除一个任务', fields: ['task_id'], kind: 'task' },
  create_calendar_event: { effect: '在日历里新建一条日程', fields: ['title', 'all_day', 'start_at', 'end_at', 'start_date', 'end_date', 'timezone', 'location', 'description'], kind: 'calendar' },
  delete_calendar_event: { effect: '删除日历中的一条日程', fields: ['event_id'], kind: 'calendar' },
  create_bookmark: { effect: '新建一个外部文档书签', fields: ['title', 'url', 'note', 'visibility', 'linked_resource_ids'], kind: 'bookmark' },
  mark_notification_read: { effect: '把通知标为已读', fields: ['ids'], kind: 'notification' },
}
const FIELD_LABELS: Record<string, string> = {
  title: '标题', description: '描述', priority: '优先级', due_at: '截止时间', project_id: '项目 ID', labels: '标签', assignee_user_id: '执行者 ID',
  task_id: '任务 ID', event_id: '日程 ID', location: '地点', timezone: '时区', all_day: '全天', start_at: '开始', end_at: '结束',
  start_date: '开始日期', end_date: '结束日期（不含）', url: '网址', note: '备注', visibility: '可见范围', linked_resource_ids: '关联资源 ID', ids: '通知 ID',
}
export const toolKind = (tool: string) => TOOLS[tool]?.kind ?? null
const TOOL_ICONS = { task: SquareCheck, calendar: CalendarDays, bookmark: Bookmark, notification: Bell } as const
export const toolIcon = (tool: string) => { const kind = toolKind(tool); return kind ? TOOL_ICONS[kind] : Wrench }
export const toolEffect = (tool: string) => TOOLS[tool]?.effect ?? null

function display(key: string, value: unknown): string {
  if (value === null) return '空（清除或不设置）'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'string') return key.endsWith('_at') && valid(value) ? `${dayTime(value)}（${value}）` : value
  if (Array.isArray(value) && value.every(v => typeof v === 'string' || typeof v === 'number')) return value.length ? value.join('、') : '无'
  return safeJson(value)
}
/** 已知工具的可读字段（按工具的字段顺序，其余键排在后面，不藏东西）；未知工具返回 null，页面直接显示 JSON */
export function readableFields(tool: string, args: Record<string, unknown>) {
  const spec = TOOLS[tool]
  if (!spec) return null
  const keys = [...spec.fields.filter(k => k in args), ...Object.keys(args).filter(k => !spec.fields.includes(k))]
  return keys.map(key => ({ key, label: FIELD_LABELS[key] ?? key, value: display(key, args[key]) }))
}
/** 安全的 JSON 文本：只做序列化，不解析成标记 */
export function safeJson(value: unknown) {
  try { return JSON.stringify(value, null, 2) ?? String(value) } catch { return String(value) }
}

/** 到对应页面核对（任务 / 日历 / 外部文档）：只用参数里已有的 ID 或日期，不另外读取对象 */
export function relatedLink(e: Pick<Execution, 'tool_name' | 'arguments'>): { to: RouteLocationRaw; label: string } | null {
  const args = e.arguments as Record<string, unknown>
  const text = (key: string) => (typeof args[key] === 'string' && args[key] ? args[key] as string : null)
  switch (toolKind(e.tool_name)) {
    case 'task': {
      const task = text('task_id'), project = text('project_id')
      if (task) return { to: { path: '/tasks', query: { task } }, label: '前往任务' }
      if (project) return { to: { path: '/tasks', query: { project_id: project } }, label: '前往项目任务' }
      return { to: '/tasks', label: '前往任务页' }
    }
    case 'calendar': {
      const day = text('start_date') ?? text('start_at')?.slice(0, 10) ?? null
      return day && /^\d{4}-\d{2}-\d{2}$/.test(day) ? { to: { path: '/calendar', query: { date: day } }, label: '在日历查看' } : { to: '/calendar', label: '前往日历' }
    }
    case 'bookmark': return { to: '/knowledge/docs', label: '前往外部文档' }
    default: return null
  }
}

// ---------------------------------------------------------------- 审批流程

export type StepState = 'done' | 'current' | 'todo' | 'error' | 'warn' | 'skipped'
/** 提交申请 → 等待审批 → 执行并记录结果，按真实状态；不需要审批的工具写“无需审批” */
export function approvalSteps(e: Execution): { label: string; state: StepState; note: string }[] {
  const s = e.status
  const approved = EXECUTED.includes(s) && !!e.decided_by
  const direct = EXECUTED.includes(s) && !e.decided_by
  const wait: { state: StepState; note: string } =
    s === 'pending_approval' ? { state: 'current', note: '等待中' } :
    s === 'rejected' ? { state: 'error', note: `已拒绝 · ${clockTime(e.decided_at)}` } :
    s === 'expired' ? { state: 'error', note: '已过期，没人处理' } :
    s === 'denied' ? { state: 'error', note: '权限不够，直接拒绝' } :
    approved ? { state: 'done', note: `已批准 · ${clockTime(e.decided_at)}` } :
    direct ? { state: 'skipped', note: '无需审批' } : { state: 'todo', note: '' }
  const run: { state: StepState; note: string } =
    s === 'pending_approval' ? { state: 'todo', note: '批准后执行' } :
    s === 'executing' ? { state: 'current', note: '执行中' } :
    s === 'succeeded' ? { state: 'done', note: `执行成功 · ${clockTime(e.executed_at)}` } :
    s === 'failed' ? { state: 'error', note: '执行失败' } :
    s === 'unknown' ? { state: 'warn', note: '结果未知' } : { state: 'skipped', note: '未执行' }
  return [
    { label: '提交申请', state: 'done', note: clockTime(e.created_at) },
    { label: direct ? '审批' : '等待审批', ...wait },
    { label: '执行并记录结果', ...run },
  ]
}
