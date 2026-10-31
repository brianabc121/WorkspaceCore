import { markRaw, type Component } from 'vue'
import { z } from 'zod'
import type { Schema } from '../services/api'
import BuiltinCard from './BuiltinCard.vue'
import ResourceCard from './ResourceCard.vue'
import GitHubActivity from './GitHubActivity.vue'

export const safeUrl = (value: string) => {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password } catch { return false }
}
const name = z.string().min(1).max(64)
const link = z.object({ title: name, url: z.string().min(1).max(2048).refine(safeUrl) }).strict()
const note = z.object({ text: z.string().max(2000).default('') }).strict()
const links = z.object({ links: z.array(link).max(20).default([]) }).strict()
const summary = z.object({}).strict()
const tasks = z.object({ limit: z.number().int().min(1).max(20).default(5) }).strict()
const agenda = z.object({ days: z.number().int().min(1).max(14).default(7), limit: z.number().int().min(1).max(20).default(5) }).strict()
export const layoutSchema = z.object({
  desktop: z.object({ x: z.number().int().min(0).max(11), y: z.number().int().min(0).max(999), w: z.number().int().min(1).max(12), h: z.number().int().min(1).max(24) }).strict().refine((p) => p.x + p.w <= 12),
  mobile: z.object({ y: z.number().int().min(0).max(999), h: z.number().int().min(1).max(24) }).strict(),
}).strict() satisfies z.ZodType<Schema['WidgetLayout']>
const activity = z.object({ kind: z.enum(['runs', 'pulls', 'issues']).default('runs'), limit: z.number().int().min(1).max(20).default(5) }).strict()
const shape = { layout: layoutSchema }
export const widgetSchema = z.discriminatedUnion('widget_type', [
  z.object({ ...shape, widget_type: z.literal('note'), config: note.default({}) }).strict(),
  z.object({ ...shape, widget_type: z.literal('quick_links'), config: links.default({}) }).strict(),
  z.object({ ...shape, widget_type: z.literal('todo_summary'), config: summary.default({}) }).strict(),
  z.object({ ...shape, widget_type: z.literal('today_tasks'), config: tasks.default({}) }).strict(),
  z.object({ ...shape, widget_type: z.literal('recent_docs'), config: tasks.default({}) }).strict(),
  z.object({ ...shape, widget_type: z.literal('calendar_agenda'), config: agenda.default({}) }).strict(),
  z.object({ ...shape, widget_type: z.literal('github_activity'), config: activity.default({}) }).strict(),
])
export type WidgetValue = z.output<typeof widgetSchema>
export type WidgetType = WidgetValue['widget_type']
type WidgetDefinition = { type: WidgetType; title: string; defaultSize: { w: number; h: number }; configSchema: z.ZodTypeAny; component: Component; refreshPolicy: 'manual'; requiredCapability?: string }
export const registry: Record<WidgetType, WidgetDefinition> = {
  github_activity: { type: 'github_activity', title: 'GitHub 动态', defaultSize: { w: 6, h: 5 }, configSchema: activity, component: markRaw(GitHubActivity), refreshPolicy: 'manual', requiredCapability: 'github' },
  note: { type: 'note', title: '文本便签', defaultSize: { w: 6, h: 3 }, configSchema: note, component: markRaw(BuiltinCard), refreshPolicy: 'manual' },
  quick_links: { type: 'quick_links', title: '常用链接', defaultSize: { w: 6, h: 3 }, configSchema: links, component: markRaw(BuiltinCard), refreshPolicy: 'manual' },
  todo_summary: { type: 'todo_summary', title: '待办汇总', defaultSize: { w: 4, h: 3 }, configSchema: summary, component: markRaw(BuiltinCard), refreshPolicy: 'manual', requiredCapability: 'tasks' },
  today_tasks: { type: 'today_tasks', title: '今日任务', defaultSize: { w: 8, h: 4 }, configSchema: tasks, component: markRaw(BuiltinCard), refreshPolicy: 'manual', requiredCapability: 'tasks' },
  recent_docs: { type: 'recent_docs', title: '最近文档', defaultSize: { w: 6, h: 4 }, configSchema: tasks, component: markRaw(ResourceCard), refreshPolicy: 'manual', requiredCapability: 'knowledge' },
  calendar_agenda: { type: 'calendar_agenda', title: '近期日程', defaultSize: { w: 6, h: 4 }, configSchema: agenda, component: markRaw(ResourceCard), refreshPolicy: 'manual', requiredCapability: 'calendar' },
}
export function parseWidget(widget: Schema['WidgetOut']): WidgetValue | null {
  const parsed = widgetSchema.safeParse({ widget_type: widget.widget_type, config: widget.config, layout: widget.layout })
  return parsed.success ? parsed.data : null
}
export const dashboardFileSchema = z.object({ format: z.literal('my-workspace/dashboard'), format_version: z.literal(1), title: name, widgets: z.array(widgetSchema).max(50) }).strict()
export function exportDashboard(title: string, widgets: Schema['WidgetOut'][]) {
  const values = widgets.map((widget) => {
    const parsed = parseWidget(widget)
    if (!parsed) throw new Error('无法导出包含未知类型或无效配置的仪表盘。')
    return parsed
  })
  return JSON.stringify(dashboardFileSchema.parse({ format: 'my-workspace/dashboard', format_version: 1, title, widgets: values }), null, 2)
}
