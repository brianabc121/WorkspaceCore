import { describe, expect, it } from 'vitest'
import { dashboardFileSchema, exportDashboard, layoutSchema, parseWidget, registry } from '../src/widgets/registry'
import type { Schema } from '../src/services/api'

export const layout: Schema['WidgetLayout'] = { desktop: { x: 0, y: 0, w: 6, h: 3 }, mobile: { y: 0, h: 3 } }
export const widget: Schema['WidgetOut'] = { id: '11111111-1111-4111-8111-111111111111', dashboard_id: '22222222-2222-4222-8222-222222222222', widget_type: 'note', config: { text: '欢迎' }, layout, version: 1, created_at: '2026-10-30T00:00:00Z', updated_at: '2026-10-30T00:00:00Z' }
describe('Widget Registry 与导入契约', () => {
  it('registers the supported builtin cards with their contract defaults', () => {
    expect(Object.keys(registry).sort()).toEqual(['calendar_agenda', 'github_activity', 'note', 'quick_links', 'recent_docs', 'today_tasks', 'todo_summary'])
    expect(parseWidget({ ...widget, widget_type: 'today_tasks', config: {} })?.config).toEqual({ limit: 5 })
    expect(parseWidget({ ...widget, widget_type: 'quick_links', config: {} })?.config).toEqual({ links: [] })
  })
  it.each(['unknown', '__proto__', 'constructor'])('rejects unsupported type %s', (widget_type) => {
    expect(parseWidget({ ...widget, widget_type })).toBeNull()
  })
  it.each([{ text: 'x'.repeat(2001) }, { text: 'hi', surprise: true }])('rejects invalid note configuration', (config) => {
    expect(parseWidget({ ...widget, config })).toBeNull()
  })
  it.each(['javascript:alert(1)', 'data:text/html,hello', '//example.com', 'https://user:pass@example.com'])('rejects unsafe or credential-bearing link %s', (url) => {
    expect(parseWidget({ ...widget, widget_type: 'quick_links', config: { links: [{ title: 'link', url }] } })).toBeNull()
  })
  it('allows http(s) links and plain note text without interpreting markup', () => {
    expect(parseWidget({ ...widget, config: { text: '<script>alert(1)</script>' } })?.config).toEqual({ text: '<script>alert(1)</script>' })
    expect(parseWidget({ ...widget, widget_type: 'quick_links', config: { links: [{ title: '文档', url: 'https://example.com/docs' }] } })).not.toBeNull()
  })
  it('checks integer coordinates, the right boundary, and mobile ranges', () => {
    expect(layoutSchema.safeParse({ ...layout, desktop: { x: 10, w: 4, y: 0, h: 3 } }).success).toBe(false)
    expect(layoutSchema.safeParse({ ...layout, desktop: { x: 0, w: 6, y: 0.5, h: 3 } }).success).toBe(false)
    expect(layoutSchema.safeParse({ ...layout, mobile: { y: -1, h: 3 } }).success).toBe(false)
    expect(layoutSchema.safeParse(layout).success).toBe(true)
  })
  it('exports a portable document without IDs, versions, or timestamps and validates it for import', () => {
    const exported = exportDashboard('首页', [widget])
    expect(JSON.parse(exported)).toEqual({ format: 'my-workspace/dashboard', format_version: 1, title: '首页', widgets: [{ widget_type: 'note', config: { text: '欢迎' }, layout }] })
    expect(dashboardFileSchema.parse(JSON.parse(exported)).widgets).toHaveLength(1)
  })
  it('rejects unsupported format versions, unknown fields and more than 50 widgets', () => {
    const file = JSON.parse(exportDashboard('首页', [widget]))
    expect(dashboardFileSchema.safeParse({ ...file, format_version: 2 }).success).toBe(false)
    expect(dashboardFileSchema.safeParse({ ...file, secret: 'not accepted' }).success).toBe(false)
    expect(dashboardFileSchema.safeParse({ ...file, widgets: Array(51).fill(file.widgets[0]) }).success).toBe(false)
    expect(() => exportDashboard('首页', [{ ...widget, widget_type: 'unknown' }])).toThrow()
  })
})
