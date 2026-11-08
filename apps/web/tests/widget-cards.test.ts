import { render as baseRender, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia } from 'pinia'
import WidgetFrame from '../src/widgets/WidgetFrame.vue'
import { widget, overview } from './dashboard-fixtures'
// 任务组件里有指向任务页的链接（T-0085），需要一个路由；组件框按模块启用状态显示（T-0159），需要 Pinia
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] })
const render: typeof baseRender = (component, options) => baseRender(component, { ...options, global: { ...options?.global, plugins: [router, createPinia(), ...(options?.global?.plugins ?? [])] } })
describe('独立组件状态', () => {
  it('renders note markup as inert text', () => {
    render(WidgetFrame, { props: { widget: { ...widget, config: { text: '<img src=x onerror=alert(1)>' } } } })
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeVisible()
    expect(document.querySelector('.widget-body img')).toBeNull()
  })
  it('shows empty quick links independently', () => {
    render(WidgetFrame, { props: { widget: { ...widget, widget_type: 'quick_links', config: { links: [] } } } })
    expect(screen.getByText('暂无常用链接')).toBeVisible()
  })
  it('shows per-module loading', () => {
    render(WidgetFrame, { props: { widget: overview.widgets[1]!, loading: true } })
    expect(screen.getByRole('status')).toHaveTextContent('正在加载组件')
  })
  it('shows missing authorization without exposing old counts', () => {
    render(WidgetFrame, { props: { widget: overview.widgets[1]!, overview: { ...overview, summary: { ...overview.summary, pending_tasks: 99 }, modules: { tasks: { status: 'unauthorized', as_of: null, retryable: false } } } } })
    expect(screen.getByText(/授权已失效/)).toBeVisible()
    expect(screen.queryByText('99')).not.toBeInTheDocument()
  })
  it('does not present unavailable as an empty task list', () => {
    render(WidgetFrame, { props: { widget: { ...widget, widget_type: 'today_tasks', config: { limit: 5 } }, overview } })
    expect(screen.getByText(/暂不可用/)).toBeVisible()
    expect(screen.queryByText('今天暂无任务')).not.toBeInTheDocument()
  })
  it('retains unavailable when a refresh fails and no module data was ever available', () => {
    render(WidgetFrame, { props: { widget: overview.widgets[1]!, overview, failed: true } })
    expect(screen.getByText(/暂不可用/)).toBeVisible()
    expect(screen.queryByText(/数据可能已过期/)).not.toBeInTheDocument()
  })
  it('limits tasks to the configured count when module data is fresh', () => {
    render(WidgetFrame, { props: { widget: { ...widget, widget_type: 'today_tasks', config: { limit: 1 } }, overview: { ...overview, modules: { tasks: { status: 'ok', as_of: overview.generated_at, retryable: false } }, today_tasks: [{ id: 'a', title: '第一项', due_at: null, project_title: null }, { id: 'b', title: '第二项', due_at: null, project_title: null }] } } })
    expect(screen.getByText('第一项')).toBeVisible()
    expect(screen.queryByText('第二项')).not.toBeInTheDocument()
  })
  it('links today tasks to the task drawer and hides retry while the module is healthy', () => {
    render(WidgetFrame, { props: { widget: { ...widget, widget_type: 'today_tasks', config: { limit: 5 } }, overview: { ...overview, modules: { tasks: { status: 'ok', as_of: overview.generated_at, retryable: true } }, today_tasks: [{ id: 'task-1', title: '写周报', due_at: '2020-01-01T08:00:00Z', project_title: '日常' }] } } })
    expect(screen.getByRole('link', { name: '写周报' })).toHaveAttribute('href', '/tasks?task=task-1')
    expect(screen.getByText(/^逾期 · /)).toHaveClass('chip-due-overdue')
    expect(screen.getByRole('link', { name: '查看任务 →' })).toHaveAttribute('href', '/tasks')
    expect(screen.queryByRole('button', { name: '重试组件' })).not.toBeInTheDocument()
  })
})
