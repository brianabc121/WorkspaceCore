import { render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import WidgetFrame from '../src/widgets/WidgetFrame.vue'
import { widget, overview } from './dashboard-fixtures'
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
    render(WidgetFrame, { props: { widget: { ...widget, widget_type: 'today_tasks', config: { limit: 1 } }, overview: { ...overview, modules: { tasks: { status: 'ok', as_of: overview.generated_at, retryable: false } }, today_tasks: [{ id: 'a', title: '第一项', due_at: null, project_title: null, source_url: null }, { id: 'b', title: '第二项', due_at: null, project_title: null, source_url: null }] } } })
    expect(screen.getByText('第一项')).toBeVisible()
    expect(screen.queryByText('第二项')).not.toBeInTheDocument()
  })
})
