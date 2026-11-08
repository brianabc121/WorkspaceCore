import type { Component } from 'vue'
import { AppWindow, LayoutGrid, Link, NotebookText, FileText } from '@lucide/vue'
import type { Schema } from '../services/api'
import { safeUrl } from '../widgets/registry'
import { navIcon } from './navIcons'

// 侧栏的分组与条目（T-0161）：内置分组 + owner 自定义的分组，按布局里的 section_order 排序；
// 已有页面（核心功能与模块）默认在自己的分组，placements 可以把它挪到别的分组；零代码页面放在它选的分组里。
// 布局读不到（还没保存过、请求失败）时就是原来的默认侧栏。
export type NavigationLayout = Schema['NavigationOut']
export type CustomPage = Schema['NavPage']
export type Role = Schema['Role']

export const BUILTIN_SECTIONS: readonly { key: string; title: string }[] = [
  { key: 'workspace', title: '工作空间' },
  { key: 'apps', title: '应用' },
  { key: 'tools', title: '连接与工具' },
  { key: 'admin', title: '管理' },
]
const BUILTIN_KEY = new Map(BUILTIN_SECTIONS.map((section) => [section.title, section.key]))
/** 侧栏项默认所在分组的 key（navigation.ts 的 section 是中文名） */
export const defaultSectionKey = (section: string) => BUILTIN_KEY.get(section) ?? 'apps'
export const customKey = (id: string) => `custom-${id}`

const RANK: Record<Role, number> = { viewer: 0, member: 1, admin: 2, owner: 3 }
export const hasRole = (role: Role | undefined, minRole: Role) => !!role && RANK[role] >= RANK[minRole]

export const KIND_LABELS: Record<CustomPage['kind'], string> = { link: '外部链接', page: '站内页面', note: '知识库笔记', dashboard: '仪表盘', embed: '嵌入页' }
const KIND_ICONS: Record<CustomPage['kind'], Component> = { link: Link, page: FileText, note: NotebookText, dashboard: LayoutGrid, embed: AppWindow }

/** 所有分组（内置 + 自定义）：{ key, title } */
export function allSections(layout: Pick<NavigationLayout, 'sections'> | null | undefined) {
  return [...BUILTIN_SECTIONS, ...(layout?.sections ?? []).map((section) => ({ key: customKey(section.id), title: section.title }))]
}

/** 分组顺序：section_order 里列出的在前；没列出的按默认顺序（工作空间、应用、连接与工具、自定义分组、管理）排在后面 */
export function sectionOrder(layout: Pick<NavigationLayout, 'sections' | 'section_order'> | null | undefined): string[] {
  const custom = (layout?.sections ?? []).map((section) => customKey(section.id))
  const defaults = ['workspace', 'apps', 'tools', ...custom, 'admin']
  const listed = [...new Set((layout?.section_order ?? []).filter((key) => defaults.includes(key)))]
  return [...listed, ...defaults.filter((key) => !listed.includes(key))]
}

/** 零代码页面在侧栏里指向哪里：外部链接用 href（新窗口），其余是站内地址 */
export function pageTarget(page: CustomPage): { to: string | null; href: string | null } {
  if (page.kind === 'link') return { to: null, href: page.url && safeUrl(page.url) ? page.url : null }
  if (page.kind === 'page') return { to: page.path && /^\/(?!\/)/.test(page.path) ? page.path : null, href: null }
  if (page.kind === 'note') return { to: page.target_id ? `/knowledge/notes/${page.target_id}` : null, href: null }
  return { to: `/p/${page.id}`, href: null }
}

export interface SidebarItem { key: string; title: string; to: string | null; href: string | null; icon: Component; custom: boolean }
export interface SidebarSection { key: string; title: string; items: SidebarItem[] }

/** 拼出侧栏：entries 是当前用户能看到的已有页面（已按角色、模块启用过滤），空分组不显示 */
export function buildSidebar(entries: readonly { path: string; title: string; section: string }[], layout: NavigationLayout | null | undefined, role: Role | undefined): SidebarSection[] {
  const sections = new Map(allSections(layout).map((section) => [section.key, { ...section, items: [] as SidebarItem[] }]))
  const placements = layout?.placements ?? {}
  for (const entry of entries) {
    const moved = placements[entry.path]
    const key = moved && sections.has(moved) ? moved : defaultSectionKey(entry.section)
    sections.get(key)?.items.push({ key: entry.path, title: entry.title, to: entry.path, href: null, icon: navIcon(entry.path), custom: false })
  }
  for (const page of layout?.pages ?? []) {
    if (!hasRole(role, page.min_role) || !sections.has(page.section)) continue
    const target = pageTarget(page)
    if (!target.to && !target.href) continue
    sections.get(page.section)!.items.push({ key: `page:${page.id}`, title: page.title, ...target, icon: KIND_ICONS[page.kind], custom: true })
  }
  return sectionOrder(layout).map((key) => sections.get(key)!).filter((section) => section?.items.length)
}
