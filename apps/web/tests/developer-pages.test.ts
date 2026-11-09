import { fireEvent, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import { repo, issue, run, developerPage, developerRoutes, evidenceOf, overviewOf, overviewRow } from './github-fixtures'
import type { Schema } from '../src/services/api'
const path = (r: Request) => new URL(r.url).pathname
const isOverview = (r: Request) => path(r) === '/api/v1/developer/overview'
function developer(override?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => await override?.(r) ?? developerRoutes(path(r)))
}
// 同一个同步状态下的总览与列表（列表与总览各自带 sync）
const stateful = (state: Schema['SyncState'], rows: Schema['RepoOverviewRow'][] = [], items: unknown[] = []) => (r: Request) =>
  isOverview(r) ? Response.json(overviewOf(rows, state, rows.length ? {} : { attention_pulls: [], failed_runs: [], recent_updates: [] })) : path(r).startsWith('/api/v1/developer/') && !path(r).endsWith('/permissions') ? Response.json(developerPage(items, state)) : undefined
describe('GitHub 开发者页', () => {
  it('shows four read-only projections and failed jobs/steps with native links', async () => {
    developer(); await openApp('/developer')
    expect(await screen.findByRole('button', { name: repo.full_name })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: 'Issues' })); expect(await screen.findByRole('heading', { name: issue.title })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: 'Pull requests' })); expect(await screen.findByRole('heading', { name: '增加搜索页面' })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: 'Actions' })); expect(await screen.findByRole('heading', { name: run.display_title })).toBeVisible()
    expect(screen.getByText(/Typecheck/)).toBeVisible()
    expect(screen.getByRole('link', { name: /在 GitHub 中打开/ })).toHaveAttribute('href', run.source_url)
    expect(screen.queryByRole('button', { name: '修改连接' })).not.toBeInTheDocument()
  })
  it.each(['stale', 'unavailable'] as const)('shows rate limits instead of a normal empty list (%s)', async (status) => {
    developer(stateful({ status, as_of: null, reason: 'rate_limited', retry_at: '2026-11-01T12:34:00Z' }))
    await openApp('/developer'); expect(await screen.findByText(/GitHub 限流中/)).toHaveTextContent(/预计.*恢复/)
    expect(screen.queryByText('没有符合条件的 GitHub 数据。')).not.toBeInTheDocument()
    expect(screen.queryByText('所选范围没有已同步的仓库。')).not.toBeInTheDocument()
  })
  it('keeps cached rows marked stale on rate limiting and hides them on token loss', async () => {
    let state = 'ok'
    developer((r) => {
      const current: Schema['SyncState'] = state === 'ok' ? { status: 'ok', as_of: repo.synced_at } : state === 'rate' ? { status: 'stale', as_of: repo.synced_at, reason: 'rate_limited', retry_at: '2026-11-01T12:34:00Z' } : { status: 'unauthorized', as_of: repo.synced_at, reason: 'token_rejected' }
      return stateful(current, state === 'unauthorized' ? [] : [overviewRow()], [repo])(r)
    })
    await openApp('/developer'); await screen.findByRole('button', { name: repo.full_name })
    state = 'rate'; await fireEvent.click(screen.getByRole('button', { name: '刷新 GitHub 数据' }))
    expect(await screen.findByText(/GitHub 限流中/)).toBeVisible(); expect(screen.getByRole('button', { name: repo.full_name })).toBeVisible()
    state = 'unauthorized'; await fireEvent.click(screen.getByRole('button', { name: '刷新 GitHub 数据' }))
    expect(await screen.findByText(/GitHub 授权已失效.*重新连接/)).toBeVisible()
    expect(screen.queryByRole('button', { name: repo.full_name })).not.toBeInTheDocument()
  })
  it('paginates opaque cursors and resets pagination when filters change', async () => {
    const requests = developer((r) => path(r).endsWith('/issues') ? Response.json({ ...developerPage([issue]), next_cursor: new URL(r.url).searchParams.has('cursor') ? null : 'cursor+/=' }) : undefined)
    await openApp('/developer'); await fireEvent.click(await screen.findByRole('button', { name: 'Issues' }))
    await screen.findByRole('heading', { name: issue.title }); await fireEvent.click(screen.getByRole('button', { name: '加载更多 GitHub 数据' }))
    await vi.waitFor(() => expect(requests.some((r) => new URL(r.url).searchParams.get('cursor') === 'cursor+/=')).toBe(true))
    await fireEvent.update(screen.getByLabelText('GitHub 状态'), 'closed'); await fireEvent.click(screen.getByRole('button', { name: '应用 GitHub 筛选' }))
    await vi.waitFor(() => expect(new URL(requests.filter((r) => path(r).endsWith('/issues')).at(-1)!.url).searchParams.get('state')).toBe('closed'))
    expect(new URL(requests.filter((r) => path(r).endsWith('/issues')).at(-1)!.url).searchParams.has('cursor')).toBe(false)
  })
  it('hides inaccessible cached rows on a forbidden refresh', async () => {
    let revoked = false
    developer((r) => revoked && path(r).startsWith('/api/v1/developer/') ? fail(403, 'FORBIDDEN') : undefined)
    await openApp('/developer'); await screen.findByRole('button', { name: repo.full_name })
    revoked = true; await fireEvent.click(screen.getByRole('button', { name: '刷新 GitHub 数据' }))
    await vi.waitFor(() => expect(screen.queryByRole('button', { name: repo.full_name })).not.toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('没有查看')
    expect(screen.queryByText(/所选仓库：/)).not.toBeInTheDocument()
  })
  it('does not show late data from the previous workspace', async () => {
    let finish: ((r: Response) => void) | undefined
    const spaceB = { ...repo, full_name: 'team/space-b' }
    const requests = developer((r) => {
      if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B空间', role: 'viewer' }] })
      if (!path(r).startsWith('/api/v1/developer/')) return undefined
      if (r.headers.get('X-Workspace-Id') === 'workspace-a') return new Promise<Response>((resolve) => { finish = resolve })
      if (isOverview(r)) return Response.json(overviewOf([overviewRow({ repo: spaceB })]))
      if (path(r).endsWith('/permissions')) return Response.json(evidenceOf({ full_name: spaceB.full_name }))
      return Response.json(developerPage([spaceB]))
    })
    await openApp('/developer'); await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    expect(await screen.findByRole('button', { name: 'team/space-b' })).toBeVisible()
    finish!(Response.json(overviewOf())); await Promise.resolve()
    expect(screen.queryByRole('button', { name: repo.full_name })).not.toBeInTheDocument()
    expect(requests.filter((r) => path(r).includes('/developer/')).at(-1)!.headers.get('X-Workspace-Id')).toBe('workspace-b')
  })
})
