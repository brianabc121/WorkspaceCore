import type { AsyncComponentLoader, Component } from 'vue'
import type { RouteRecordRaw } from 'vue-router'
import type { z } from 'zod'
import type { Schema } from '../services/api'

// 插拔式模块的前端定义（T-0159；写法见 src/modules/README.md 与 docs/modules/README.md 第 4 节）。
// 一个模块 = src/modules/<key>/index.ts 里的一次 defineModule()，再在 registry.ts 登记一行。
// 侧栏、路由、首页小组件、实时事件都从注册表读；模块停用时宿主自动隐藏入口、页面显示“功能未启用”。

/** 侧栏分组：工作台原有的三个，加上新模块默认放的“应用” */
export type NavSection = '工作空间' | '应用' | '连接与工具' | '管理'
/** 能看到入口、打开页面的最低角色：admin = owner 与 admin，owner = 只有 owner；不写 = 所有成员（含只读） */
export type MinRole = 'admin' | 'owner'
/** 实时事件主题（后端 InvalidateEvent.topics） */
export type EventTopic = Schema['InvalidateEvent']['topics'][number]

export interface NavEntry {
  path: string
  title: string
  section: NavSection
  description: string
  minRole?: MinRole
  /** 侧栏图标（@lucide/vue）；不写用总览的图标 */
  icon?: Component
}

export interface ModuleNavEntry extends NavEntry {
  /** 排在哪个侧栏项后面（同一分组里）；不写排在分组最后 */
  after?: string
}

export interface ModuleWidget<T extends string = string, C extends z.ZodType = z.ZodType> {
  /** 小组件类型：必须已在后端 dashboards 的组件联合类型里 */
  type: T
  title: string
  defaultSize: { w: number; h: number }
  /** 配置的 zod 模式：每个字段都要有默认值（“添加组件”按 {} 解析出默认配置） */
  configSchema: C
  /** 卡片组件，必须按需加载：() => import('./XxxCard.vue')；收到的 props 是 value（含 config） */
  component: AsyncComponentLoader
  refreshPolicy: 'manual'
  /** “添加组件”里的填写说明，例如“今日早报填写 lines（2–20）” */
  help?: string
}

export interface ModuleDefinition<W extends ModuleWidget = ModuleWidget, E extends ModuleEvents = ModuleEvents> {
  /** 与后端清单的 key 相同 */
  key: string
  nav?: readonly ModuleNavEntry[]
  /** 路由组件必须写成 () => import('./XxxPage.vue')，模块不启用就不下载它的代码 */
  routes?: readonly RouteRecordRaw[]
  widgets?: readonly W[]
  /** 实时事件主题 → 要刷新的查询键前缀，例如 { news: ['news'] } */
  events?: E
}
export type ModuleEvents = Partial<Record<EventTopic, readonly string[]>>

/** 声明一个首页小组件（保留类型名与配置模式的精确类型，宿主据此拼出组件联合类型） */
export function defineWidget<T extends string, C extends z.ZodType>(widget: ModuleWidget<T, C>): ModuleWidget<T, C> {
  return widget
}

/** 声明一个模块；在 src/modules/registry.ts 登记后生效 */
export function defineModule<W extends ModuleWidget = never, E extends ModuleEvents = Record<never, never>>(module: ModuleDefinition<W, E>): ModuleDefinition<W, E> {
  return module
}

declare module 'vue-router' {
  interface RouteMeta {
    /** 路由属于哪个模块（宿主按注册表自动填写）；模块停用时页面显示“功能未启用” */
    module?: string
    /** 宽版内容区（四页改造：任务、项目、日历、邮件） */
    wide?: boolean
  }
}
