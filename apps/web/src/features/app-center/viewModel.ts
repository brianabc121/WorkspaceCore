import type { Component } from 'vue'
import { Blocks } from '@lucide/vue'
import type { ModuleDefinition, MinRole } from '../../modules/types'
import { registry as widgetRegistry, type WidgetType } from '../../widgets/registry'
import type { HealthState, Management, SyncReason, SyncState } from './api'

// 应用中心的视图模型（T-0211，方案第 3、5 节）：把后端的管理投影与前端模块注册表按 key 合并。
// - 入口（打开应用、应用设置）只来自模块自己声明的 management，且必须是本模块注册过的路由；对不上就不显示按钮、不猜路径；
// - 前端没有这个模块（前后端版本不一致）时只显示后端信息并提示；
// - 能力数量是清单里的声明，不是“已授权给所有实例”的数量；
// - 启用、部署、健康、同步四类状态分开显示。宿主不写任何具体模块的 key。

export type Tone = 'green' | 'amber' | 'red' | 'blue' | 'muted'
export interface StatusLabel { text: string; tone: Tone }
export interface ModuleEntry { path: string; title: string }
export interface ModuleNavInfo { path: string; title: string; minRole?: MinRole }

export interface ModuleView {
  key: string
  item: Management
  name: string
  description: string
  version: string
  category: string | null
  icon: Component
  /** 前端注册表里没有这个模块（前后端版本不一致）：不显示入口 */
  frontendMissing: boolean
  primary: ModuleEntry | null
  settings: ModuleEntry | null
  nav: ModuleNavInfo[]
  widgetTitles: string[]
  counts: { pages: number; widgets: number; tools: number; readOnlyTools: boolean; crons: number }
  enabled: StatusLabel
  deploy: StatusLabel
  health: StatusLabel
  sync: StatusLabel
  attention: boolean
  /** 卡片上的一句提示：没部署、同步失败等 */
  notice: string | null
}

export const HEALTH_LABELS: Record<HealthState, StatusLabel> = {
  ok: { text: '连通正常', tone: 'green' },
  degraded: { text: '部分异常', tone: 'amber' },
  down: { text: '连接失败', tone: 'red' },
  unknown: { text: '未检测', tone: 'muted' },
  not_configured: { text: '尚未配置', tone: 'amber' },
  not_applicable: { text: '不需要外部服务', tone: 'muted' },
}
export const SYNC_LABELS: Record<SyncState, StatusLabel> = {
  not_required: { text: '无需同步', tone: 'muted' },
  unrecorded: { text: '暂无记录', tone: 'muted' },
  pending: { text: '等待执行', tone: 'blue' },
  running: { text: '正在同步', tone: 'blue' },
  succeeded: { text: '已同步', tone: 'green' },
  failed: { text: '同步失败', tone: 'red' },
  unknown: { text: '结果未知', tone: 'amber' },
  superseded: { text: '已被新操作取代', tone: 'muted' },
}
export const SYNC_REASONS: Record<SyncReason, string> = {
  hook_failed: '同步过程出错',
  config_render_failed: '外部配置文件没有写成',
  timeout: '超时，外部配置可能已部分写入',
  interrupted: '执行被中断（服务重启等）',
  stale_revision: '执行期间目标已变化',
  busy: '外部配置正被其他操作写入',
  result_unverified: '没有拿到明确的执行结果',
  not_deployed: '外部服务没有部署',
  application_not_active: '这个工作空间没有打开相关功能，不需要写外部配置',
}
export const SERVICE_STATES: Record<string, StatusLabel> = {
  ok: HEALTH_LABELS.ok, degraded: HEALTH_LABELS.degraded, down: HEALTH_LABELS.down, unknown: HEALTH_LABELS.unknown, not_configured: HEALTH_LABELS.not_configured,
}
export const ROLE_LABELS: Record<MinRole | 'viewer', string> = { viewer: '所有成员', admin: 'owner 与 admin', owner: '只有 owner' }

function routePaths(definition: ModuleDefinition | undefined) {
  return new Set((definition?.routes ?? []).map(route => route.path))
}

/** 模块声明的入口：必须是本模块注册过的路由，否则不显示 */
function entry(definition: ModuleDefinition | undefined, path: string | undefined): ModuleEntry | null {
  if (!definition || !path || !routePaths(definition).has(path)) return null
  const nav = definition.nav?.find(item => item.path === path)
  return { path, title: nav?.title ?? path }
}

export function syncNotice(item: Management): string | null {
  const sync = item.sync
  if (sync.state === 'failed') return `开关已保存，外部配置同步失败：${sync.reason ? SYNC_REASONS[sync.reason] : '原因未知'}。`
  if (sync.state === 'unknown') return `开关已保存，外部配置同步结果未知：${sync.reason ? SYNC_REASONS[sync.reason] : '原因未知'}。`
  return null
}

export function buildModuleView(item: Management, definition: ModuleDefinition | undefined): ModuleView {
  const module = item.module, capabilities = item.capabilities
  const primary = entry(definition, definition?.management?.primaryPath)
  const settings = entry(definition, definition?.management?.settingsPath)
  const nav = (definition?.nav ?? []).map(navItem => ({ path: navItem.path, title: navItem.title, minRole: navItem.minRole }))
  const icon = (primary && definition?.nav?.find(navItem => navItem.path === primary.path)?.icon) ?? definition?.nav?.[0]?.icon ?? Blocks
  const tools = capabilities.tools
  const deploy: StatusLabel = module.ready ? { text: '已完成', tone: 'green' } : { text: '待完成', tone: 'amber' }
  let notice = syncNotice(item)
  if (!notice && module.enabled && !module.ready) notice = '已启用此应用，完成部署配置后即可使用完整功能。'
  return {
    key: module.key,
    item,
    name: module.name,
    description: module.description,
    version: module.version,
    category: definition?.management?.category ?? null,
    icon,
    frontendMissing: !definition,
    primary,
    settings,
    nav,
    widgetTitles: capabilities.widgets.map(type => widgetRegistry[type as WidgetType]?.title ?? type),
    counts: {
      pages: new Set(nav.map(navItem => navItem.path)).size,
      widgets: capabilities.widgets.length,
      tools: tools.length,
      readOnlyTools: tools.length > 0 && tools.every(tool => tool.risk === 'read'),
      crons: capabilities.crons.length,
    },
    enabled: module.enabled ? { text: '已启用', tone: 'blue' } : { text: '已停用', tone: 'muted' },
    deploy,
    health: HEALTH_LABELS[item.health.state],
    sync: SYNC_LABELS[item.sync.state],
    attention: item.attention,
    notice,
  }
}

export type StatusFilter = 'all' | 'enabled' | 'disabled' | 'attention'
export const STATUS_FILTERS: Record<StatusFilter, string> = { all: '全部状态', enabled: '已启用', disabled: '已停用', attention: '需处理' }

export interface AppCenterViewModel {
  modules: ModuleView[]
  /** 前端注册了、后端没有的模块 key（前后端版本不一致） */
  frontendOnly: string[]
}

export function buildAppCenterViewModel(items: readonly Management[], definitions: readonly ModuleDefinition[]): AppCenterViewModel {
  const byKey = new Map(definitions.map(definition => [definition.key, definition]))
  const keys = new Set(items.map(item => item.module.key))
  return {
    modules: items.map(item => buildModuleView(item, byKey.get(item.module.key))),
    frontendOnly: definitions.map(definition => definition.key).filter(key => !keys.has(key)),
  }
}

/** 搜索（名称、说明、key、工具名）与状态筛选：只影响列表，汇总始终基于完整集合 */
export function filterModules(modules: readonly ModuleView[], search: string, status: StatusFilter): ModuleView[] {
  const q = search.trim().toLowerCase()
  return modules.filter(view => {
    if (status === 'enabled' && !view.item.module.enabled) return false
    if (status === 'disabled' && view.item.module.enabled) return false
    if (status === 'attention' && !view.attention) return false
    if (!q) return true
    const haystack = [view.name, view.description, view.key, view.category ?? '', ...view.item.capabilities.tools.map(tool => tool.name)].join('\n').toLowerCase()
    return haystack.includes(q)
  })
}
