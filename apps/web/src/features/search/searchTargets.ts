import type { Schema } from '../../services/api'
import { safeUrl } from '../../widgets/registry'
export const searchTypes = { task: '任务', project: '项目', bookmark: '收藏文档', event: '日程', repo: '仓库', issue: 'Issues', pull: 'Pull requests' } as const
export type SearchType = keyof typeof searchTypes
export function searchTarget(item: Schema['SearchResultOut']) {
  if (item.source_url && safeUrl(item.source_url)) return { href: item.source_url, external: true }
  const paths: Record<SearchType, string> = { task: '/tasks', project: '/projects', bookmark: '/knowledge', event: '/calendar', repo: '/developer', issue: '/developer', pull: '/developer' }
  const date = item.context ?? ''
  const validDate = /^\d{4}-\d{2}-\d{2}$/.test(date) && Number.isFinite(Date.parse(date)) && new Date(date).toISOString().slice(0, 10) === date
  // 工作台自己的任务、项目（T-0082）：打开任务页并定位到这个任务 / 这个项目的任务
  if (item.resource_type === 'task') return { href: '/tasks?task=' + encodeURIComponent(item.id), external: false }
  if (item.resource_type === 'project') return { href: '/tasks?project_id=' + encodeURIComponent(item.id), external: false }
  return { href: paths[item.resource_type] + (item.resource_type === 'event' && validDate ? '?date=' + date : ''), external: false }
}
