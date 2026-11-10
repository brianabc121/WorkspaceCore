import { ApiError, dataOf, type Schema } from '../../services/api'
import type { CustomPage, NavigationLayout } from '../../app/sidebar'
import type { useAuthStore } from '../../stores/auth'

// 单个零代码页面的修改按整份布局保存（T-0211，方案第 3.3 节；与“侧栏与页面”共用 PUT /api/v1/navigation）。
// 在读到的完整快照里只改这一项（新增 / 替换 / 移除），其余分组、页面、位置、分组顺序原样带回，带上读到的 version；
// 别人先保存过时后端返回 409：调用方保留草稿，读取最新布局后由用户确认，再把同一个修改套到最新快照上重新保存。
// 移除入口只改导航，不会删除它指向的笔记、仪表盘或外部服务。

export type PageChange = { kind: 'upsert'; page: CustomPage } | { kind: 'remove'; id: string }
type Api = ReturnType<typeof useAuthStore>['api']

/** 把一个修改套到完整快照上，得到 PUT 的请求体（不改动无关的部分） */
export function applyPageChange(layout: NavigationLayout, change: PageChange): Schema['NavigationIn'] {
  const pages = [...(layout.pages ?? [])]
  if (change.kind === 'remove') {
    const index = pages.findIndex(page => page.id === change.id)
    if (index >= 0) pages.splice(index, 1)
  } else {
    const index = pages.findIndex(page => page.id === change.page.id)
    if (index >= 0) pages.splice(index, 1, change.page)
    else pages.push(change.page)
  }
  return {
    version: layout.version,
    sections: (layout.sections ?? []).map(section => ({ ...section })),
    pages,
    placements: { ...(layout.placements ?? {}) },
    section_order: [...(layout.section_order ?? [])],
  }
}

export type SaveResult = { ok: true; layout: NavigationLayout } | { ok: false; conflict: true } | { ok: false; conflict: false; error: unknown }

/** 按读到的快照保存一个修改；409 时返回 conflict（不重试、不覆盖） */
export async function savePageChange(api: Api, scope: string, base: NavigationLayout, change: PageChange): Promise<SaveResult> {
  try {
    const saved = dataOf(await api.PUT('/api/v1/navigation', { params: { header: { 'X-Workspace-Id': scope } }, body: applyPageChange(base, change) }))
    return { ok: true, layout: saved }
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) return { ok: false, conflict: true }
    return { ok: false, conflict: false, error: cause }
  }
}

const FIELDS: Record<string, string> = { title: '名称', url: '地址', path: '站内路径', target_id: '目标', section: '分组', min_role: '谁能看到', kind: '类型' }

/** 422 里与这个页面有关的字段错误（中文） */
export function pageProblems(cause: unknown, body: Schema['NavigationIn'], id: string): string[] {
  if (!(cause instanceof ApiError) || cause.status !== 422) return []
  const index = (body.pages ?? []).findIndex(page => page.id === id)
  const list = ((cause.details as { errors?: { loc?: (string | number)[]; msg?: string }[] } | undefined)?.errors ?? [])
  return list.map(item => {
    const [, area, position, field] = item.loc ?? []
    const message = String(item.msg ?? '').replace(/^Value error, /, '')
    if (area === 'pages' && position === index && typeof field === 'string') return `${FIELDS[field] ?? field}：${message}`
    if (area === 'pages' && typeof position !== 'number') return `页面数量：${message}`
    return `侧栏布局：${message}`
  })
}
