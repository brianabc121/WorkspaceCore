import { fireEvent, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import { mockApi, openApp } from './helpers'
import { overview, widget } from './dashboard-fixtures'
import { developerPage, run } from './github-fixtures'
import { registry, parseWidget } from '../src/widgets/registry'
function card(state = 'ok', reason?: string, title = () => run.display_title) {
  return mockApi((r) => new URL(r.url).pathname === '/api/v1/dashboard/overview' ? Response.json({ ...overview, widgets: [{ ...widget, widget_type: 'github_activity', config: {} }], modules: { github: { status: state, reason, retry_at: '2026-11-01T12:34:00Z', retryable: false } } }) : new URL(r.url).pathname.startsWith('/api/v1/developer/') ? Response.json(developerPage([{ ...run, display_title: title() }])) : undefined)
}
describe('GitHubActivity 首页组件', () => {
  it.each(['overview', 'list'])('preserves rate-limit recovery from the %s when independent states differ', async (limited) => {
    const rate = { status: 'stale', as_of: run.synced_at, reason: 'rate_limited', retry_at: '2026-11-01T12:34:00Z' }
    const outage = { status: 'stale', as_of: run.synced_at, reason: 'source_unavailable' }
    mockApi((r) => new URL(r.url).pathname === '/api/v1/dashboard/overview' ? Response.json({ ...overview, widgets: [{ ...widget, widget_type: 'github_activity', config: {} }], modules: { github: { ...(limited === 'overview' ? rate : outage), retryable: false } } }) : new URL(r.url).pathname.startsWith('/api/v1/developer/') ? Response.json(developerPage([run], (limited === 'list' ? rate : outage) as Parameters<typeof developerPage>[1])) : undefined)
    await openApp('/'); await screen.findByText(run.display_title)
    expect(screen.getByText(/GitHub 限流中/)).toHaveTextContent(/预计.*恢复/)
    expect(screen.queryByText('暂无 GitHub 动态。')).not.toBeInTheDocument()
  })
  it('validates configuration defaults and rejects invalid kinds and limits', () => {
    expect(registry.github_activity?.configSchema.parse({})).toEqual({ kind: 'runs', limit: 5 })
    expect(parseWidget({ ...widget, widget_type: 'github_activity', config: { kind: 'repos' } })).toBeNull()
    expect(parseWidget({ ...widget, widget_type: 'github_activity', config: { limit: 21 } })).toBeNull()
  })
  it('shows runs and failure summaries and refreshes from the dashboard', async () => {
    let changed = false
    card('ok', undefined, () => changed ? '更新后的运行' : run.display_title); await openApp('/'); expect(await screen.findByText(run.display_title)).toBeVisible()
    expect(screen.getByText(/Typecheck/)).toBeVisible()
    changed = true; await fireEvent.click(screen.getByRole('button', { name: '刷新首页' }))
    expect(await screen.findByText('更新后的运行')).toBeVisible()
  })
  it('does not expose cached rows when the module is unauthorized', async () => {
    card('unauthorized'); await openApp('/')
    expect(await screen.findByText(/GitHub 授权已失效.*重新连接/)).toBeVisible()
    expect(screen.queryByText(run.display_title)).not.toBeInTheDocument()
  })
  it('shows retry_at for an unavailable rate-limited module instead of empty', async () => {
    card('unavailable', 'rate_limited'); await openApp('/')
    expect(await screen.findByText(/GitHub 限流中/)).toHaveTextContent(/预计.*恢复/)
    expect(screen.queryByText('暂无 GitHub 动态。')).not.toBeInTheDocument()
  })
})
