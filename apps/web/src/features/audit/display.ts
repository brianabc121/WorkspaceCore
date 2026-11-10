import type { Component } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import { BookOpen, Bot, Box, Building2, CalendarDays, FileDown, FolderKanban, KeyRound, ListTodo, Mail, MessagesSquare, Newspaper, Pencil, Plug, Rocket, ScrollText, Server, Settings2, User } from '@lucide/vue'
import { isUuid, type AuditEntry } from './queries'

// 审计记录的显示（T-0198）：只用记录本身与服务端给的显示名，不推断结果、不补前后值、不额外读别的数据。

export const SYSTEM_ACTOR = '系统'
/** 长 ID 的简写：8a92…e1c4（完整值放在 title 与详情里） */
export const shortId = (value: string) => (value.length > 12 ? `${value.slice(0, 4)}…${value.slice(-4)}` : value)

/** 标题：内置事件的中文名；未知事件按原 action 显示 */
export const eventTitle = (entry: AuditEntry) => entry.action_label || entry.action
export const isKnownEvent = (entry: AuditEntry) => !!entry.action_label

/** 操作者：服务端解析的显示名；actor_id 为空是系统；已不是成员（解析不到）就显示 ID */
export function actorText(entry: AuditEntry): { name: string; id: string | null; resolved: boolean } {
  if (entry.actor_id === null) return { name: entry.actor_label || SYSTEM_ACTOR, id: null, resolved: true }
  return entry.actor_label ? { name: entry.actor_label, id: entry.actor_id, resolved: true } : { name: shortId(entry.actor_id), id: entry.actor_id, resolved: false }
}

const recordedName = (metadata: AuditEntry['metadata']) => {
  for (const key of ['name', 'title']) {
    const value = metadata[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return null
}
/** 对象：现在的名称（标“当前名称”）；没有就用记录里保存的名称（标“记录时”）；再没有就是类型与 ID */
export function objectText(entry: AuditEntry): { name: string; tag: 'current' | 'recorded' | null; title: string | null } | null {
  if (entry.resource_current_name) return { name: entry.resource_current_name, tag: 'current', title: null }
  const recorded = recordedName(entry.metadata)
  if (recorded) return { name: recorded, tag: 'recorded', title: null }
  if (!entry.resource_type && !entry.resource_id) return null
  const id = entry.resource_id ? shortId(entry.resource_id) : ''
  return { name: [entry.resource_type, id].filter(Boolean).join(' · '), tag: null, title: entry.resource_id }
}

// 事件图标：按前缀，未知的用通用图标
const ICONS: [string, Component][] = [
  ['agents.secret.', KeyRound], ['agents.server.', Server], ['agents.ssh.', Rocket], ['agents.', Bot],
  ['auth.', User], ['user.', User], ['workspace.', Building2], ['module.', Settings2], ['navigation.', Settings2],
  ['integration.', Plug], ['project.', FolderKanban], ['task.', ListTodo], ['issues.', ListTodo],
  ['knowledge_', BookOpen], ['calendar_event.', CalendarDays], ['bookmark.', BookOpen], ['mail.', Mail], ['news.', Newspaper],
  ['runner.', Box], ['im.', MessagesSquare], ['audit.', FileDown], ['dashboard.', Pencil], ['widget.', Pencil], ['setup.', Settings2],
]
export const eventIcon = (action: string): Component => ICONS.find(([prefix]) => action.startsWith(prefix))?.[1] ?? ScrollText

/** 密钥类事件：详情里注明记录不含密钥原文 */
export const isSecretEvent = (entry: AuditEntry) => entry.action.startsWith('agents.secret.') || entry.resource_type === 'agent_secret'

/** 相关页面的链接：只给链接，不额外读数据。只有 owner 能打开的页面只给 owner；删除类事件不给（对象已不在） */
export function relatedLink(entry: AuditEntry, isOwner: boolean): { to: RouteLocationRaw; label: string } | null {
  const type = entry.resource_type, id = entry.resource_id ?? ''
  if (!type || !isUuid(id) || /\.(deleted|removed|purged)$/.test(entry.action)) return null
  switch (type) {
    // 密钥库并入中转站管理的“迁移与兼容凭据”页签（T-0206）
    case 'agent_secret': return isOwner ? { to: { path: '/settings/model-connections', query: { tab: 'compat', secret: id } }, label: '查看兼容凭据' } : null
    case 'model_connection': return isOwner ? { to: { path: '/settings/model-connections', query: { connection: id } }, label: '查看中转站连接' } : null
    case 'agent_instance': return isOwner ? { to: { path: '/agents', query: { instance: id } }, label: '查看智能体' } : null
    case 'agent_server': return isOwner ? { to: { path: '/agent-servers', query: { server: id } }, label: '查看智能体服务器' } : null
    case 'project': return { to: `/projects/${encodeURIComponent(id)}`, label: '查看项目' }
    case 'task': return { to: { path: '/tasks', query: { task: id } }, label: '查看任务' }
    case 'integration': return { to: '/settings/integrations', label: '查看集成设置' }
    case 'workspace': return { to: '/settings', label: '查看工作空间设置' }
    default: return null
  }
}

// 事件信息：metadata 第一层里的简单值，按记录原样显示（文字）。嵌套内容只在“原始元数据”里
const KEY_LABELS: Record<string, string> = {
  name: '名称', title: '标题', display_name: '显示名', email: '邮箱', username: '用户名', role: '角色', reason: '原因',
  status: '状态', kind: '类型', mode: '方式', provider: '提供方', host: '主机', port: '端口', module: '模块', fields: '修改的字段',
  enabled: '开启', instance_id: '智能体实例 ID', task_id: '任务 ID', project_id: '项目 ID', job_id: '作业 ID',
  value_version: '密钥版本', revoked_sessions: '撤销的会话数', error_code: '错误代码', source: '来源', target: '目标', purpose: '用途',
}
const MAX_FACTS = 12
const textOf = (value: unknown): string | null => {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value) && value.length && value.every((v) => typeof v === 'string' || typeof v === 'number')) return value.join('、')
  return null
}
export function eventFacts(entry: AuditEntry): { key: string; label: string; value: string }[] {
  const facts: { key: string; label: string; value: string }[] = []
  for (const [key, raw] of Object.entries(entry.metadata ?? {})) {
    const value = textOf(raw)
    if (value === null || value === '') continue
    // 轮换只记了新的版本号：写明是“新密钥版本”，不推出旧版本
    const label = key === 'value_version' && entry.action === 'agents.secret.rotated' ? '新密钥版本' : KEY_LABELS[key] ?? key
    facts.push({ key, label, value })
    if (facts.length >= MAX_FACTS) break
  }
  return facts
}

/** 原始元数据：已脱敏的 JSON，按文本缩进显示 */
export function rawMetadata(entry: AuditEntry) {
  try { return JSON.stringify(entry.metadata ?? {}, null, 2) } catch { return '{}' }
}

// 时间：浏览器所在时区，精确到秒。表格里写“11月9日 14:28:16”（不是今年的加上年份），详情里写完整日期
const clock = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
const fullFormat = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
export function rowTime(value: string) {
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return '时间不可用'
  const part = Object.fromEntries(clock.formatToParts(time).map((p) => [p.type, p.value]))
  const day = `${part.month}月${part.day}日 ${part.hour}:${part.minute}:${part.second}`
  return time.getFullYear() === new Date().getFullYear() ? day : `${part.year}年${day}`
}
export function fullTime(value: string) {
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? '时间不可用' : fullFormat.format(time)
}

/** 事件名、ID 这类长串：在 . _ - 之后允许换行（模板里每段后面放 <wbr>），不在单词中间断开 */
export const breakable = (value: string) => value.split(/(?<=[._-])/)
