import type { Author, GraphNode, Note, NoteSummary } from './api'
import { createRenderer, headingId, normalize, type WikiTarget } from './markdown'

// 知识库页面里的显示格式（T-0099）
export function when(value: string | null | undefined, now = new Date()) {
  if (!value) return ''
  const at = new Date(value)
  const diff = (now.getTime() - at.getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  const hm = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
  const day = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
  if (day(at) === day(now)) return `今天 ${hm}`
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1)
  if (day(at) === day(yesterday)) return `昨天 ${hm}`
  return at.getFullYear() === now.getFullYear() ? `${at.getMonth() + 1} 月 ${at.getDate()} 日` : `${at.getFullYear()} 年 ${at.getMonth() + 1} 月 ${at.getDate()} 日`
}
export const dateLabel = (value: string | null | undefined) => {
  if (!value) return ''
  const [y, m, d] = value.slice(0, 10).split('-').map(Number)
  return `${y} 年 ${m} 月 ${d} 日`
}
export const isToday = (value: string, now = new Date()) => new Date(value).toDateString() === now.toDateString()

// 正文摘要：去掉 Markdown 记号与 [[ ]]，取前若干字
export function excerpt(body: string, length = 120) {
  const text = body
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (_m, target: string, display?: string) => display ?? target.split('/').pop() ?? target)
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+(\[[ xX]\]\s+)?/gm, '')
    .replace(/[*_`~]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  return text.length > length ? text.slice(0, length) + '…' : text
}

export function nodeHref(node: Pick<GraphNode, 'kind' | 'id'>) {
  if (node.kind === 'note') return `/knowledge/notes/${node.id}`
  if (node.kind === 'task') return `/tasks?task=${encodeURIComponent(node.id)}`
  if (node.kind === 'project') return `/tasks?project_id=${encodeURIComponent(node.id)}`
  if (node.kind === 'file') return `/api/v1/knowledge/files/${encodeURIComponent(node.id)}`
  return '/knowledge/docs'
}

export function statusOf(note: Pick<NoteSummary, 'conflict' | 'updated_by'>) {
  if (note.conflict) return { key: 'conflict' as const, label: '有冲突提醒' }
  if (note.updated_by.kind === 'agent') return { key: 'agent' as const, label: '智能体写入' }
  return { key: 'human' as const, label: '人写的' }
}
export const byAgent = (author: Author | null | undefined) => author?.kind === 'agent'

// 笔记正文 → 消毒后的 HTML：[[链接]] 按后端解析的结果找到目标，找不到的点了去新建（带上当前知识库）
export function noteHtml(note: Pick<Note, 'body' | 'links' | 'space_id'>) {
  const links = new Map(note.links.map(l => [normalize(l.text), l]))
  return createRenderer((target: WikiTarget) => {
    const link = links.get(normalize(target.text))
    if (link?.note_id && !link.deleted) return { href: `/knowledge/notes/${link.note_id}${target.section ? '#' + headingId(target.section) : ''}`, title: link.space_name ? `${link.title}（${link.space_name}）` : link.title ?? target.text }
    if (link?.resource_type && link.resource_id) return { href: nodeHref({ kind: link.resource_type, id: link.resource_id }) }
    const plain = target.text.includes('/') ? target.text.split('/').pop()! : target.text
    return { href: `/knowledge/notes/new?space=${note.space_id}&title=${encodeURIComponent(plain)}`, missing: true }
  })(note.body)
}

// 反向链接的上下文：去掉 [[ ]] 与 Markdown 记号，按显示文字给出
export function cleanSnippet(snippet: string) {
  return snippet
    .replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (_m, target: string, display?: string) => display ?? target.split('/').pop() ?? target)
    .replace(/(^|\s)(#{1,6}|>|[-*+]|\d+\.)\s+(\[[ xX]\]\s+)?/g, '$1')
    .replace(/[*_`~]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}
