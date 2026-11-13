import type { RouteRecordRaw } from 'vue-router'
import type { EventTopic, ModuleNavEntry } from './types'
import news from './news'
import llmBridge from './llm_bridge'

// 模块注册表（T-0159；docs/modules/README.md）：工作台有哪些插拔式模块。
// 新增模块：上面 import 一行、下面列表里加一行（make module-new 会自动加，所以每个 import 都写在一行里）。
// 宿主（侧栏、路由、首页小组件、实时事件）只从这里读，不再手写模块的条目。
export const modules = [
  news,
  llmBridge,
  // module-new: 新模块加在这一行上面（make module-new 会自动插入）
]

type Module = (typeof modules)[number]
/** 所有模块提供的小组件（类型名与配置模式保留精确类型，widgets/registry.ts 据此拼出组件联合类型） */
export type ModuleWidgetOf<M = Module> = M extends { widgets?: readonly (infer W)[] } ? W : never
/** 模块声明的实时事件主题 */
export type ModuleTopic = Module extends infer M ? (M extends { events?: infer E } ? keyof NonNullable<E> & EventTopic : never) : never

export interface ModuleNavItem extends ModuleNavEntry { module: string }

/** 所有模块的侧栏条目（app/navigation.ts 按 after 合并进侧栏） */
export const moduleNav: ModuleNavItem[] = modules.flatMap((module) => (module.nav ?? []).map((entry) => ({ ...entry, module: module.key })))

/** 所有模块的路由；meta.module 记下所属模块，宿主据此在模块停用时显示“功能未启用” */
export const moduleRoutes: RouteRecordRaw[] = modules.flatMap((module) => (module.routes ?? []).map((route) => ({ ...route, meta: { ...route.meta, module: module.key } }) as RouteRecordRaw))

/** 所有模块的首页小组件，附上所属模块 */
export const moduleWidgets = modules.flatMap((module) => (module.widgets ?? []).map((widget) => ({ ...widget, module: module.key }))) as (ModuleWidgetOf & { module: string })[]

/** 实时事件主题 → 要刷新的查询键前缀（多个模块声明同一主题时合并） */
export const moduleEvents: Partial<Record<EventTopic, string[]>> = {}
for (const module of modules) {
  for (const [topic, prefixes] of Object.entries(module.events ?? {}) as [EventTopic, readonly string[]][]) {
    moduleEvents[topic] = [...new Set([...(moduleEvents[topic] ?? []), ...prefixes])]
  }
}

/** 一个页面地址属于哪个模块；不属于任何模块（核心功能）时为 null */
export function moduleOfPath(path: string): string | null {
  for (const module of modules) {
    for (const route of module.routes ?? []) {
      const base = route.path.split('/:')[0]!.replace(/\/+$/, '')
      if (path === route.path || path === base || (base && path.startsWith(base + '/'))) return module.key
    }
  }
  return null
}
