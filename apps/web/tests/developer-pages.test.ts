import { fireEvent, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import { repo, issue, pull, run, developerPage } from './github-fixtures'
const path = (r: Request) => new URL(r.url).pathname
function developer(override?: (r: Request) => Response | Promise<Response> | undefined) {
  return mockApi(async (r) => await override?.(r) ?? (path(r).startsWith('/api/v1/developer/') ? Response.json(developerPage(path(r).endsWith('repos') ? [repo] : path(r).endsWith('issues') ? [issue] : path(r).endsWith('pulls') ? [pull] : [run])) : undefined))
}
describe('GitHub 开发者页', () => {
  it('shows four read-only projections and failed jobs/steps with native links', async () => {
    developer(); await openApp('/developer')
    expect(await screen.findByRole('heading', { name: repo.full_name })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: 'Issues' })); expect(await screen.findByRole('heading', { name: issue.title })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: 'Pull requests' })); expect(await screen.findByRole('heading', { name: pull.title })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: 'Actions' })); expect(await screen.findByRole('heading', { name: run.display_title })).toBeVisible()
    expect(screen.getByText(/Typecheck/)).toBeVisible()
    expect(screen.getByRole('link', { name: /在 GitHub 中打开/ })).toHaveAttribute('href', run.source_url)
    expect(screen.queryByRole('button', { name: '修改连接' })).not.toBeInTheDocument()
  })
  it.each(['stale', 'unavailable'] as const)('shows rate limits instead of a normal empty list (%s)', async (status) => {
    developer((r) => path(r).startsWith('/api/v1/developer/') ? Response.json(developerPage([], { status, as_of: null, reason: 'rate_limited', retry_at: '2026-11-01T12:34:00Z' })) : undefined)
    await openApp('/developer'); expect(await screen.findByText(/GitHub 限流中/)).toHaveTextContent(/预计.*恢复/)
    expect(screen.queryByText('没有符合条件的 GitHub 数据。')).not.toBeInTheDocument()
  })
  it('keeps cached rows marked stale on rate limiting and hides them on token loss', async () => {
    let state = 'ok'
    developer((r) => path(r).startsWith('/api/v1/developer/') ? Response.json(developerPage([repo], state === 'ok' ? { status: 'ok', as_of: repo.synced_at } : state === 'rate' ? { status: 'stale', as_of: repo.synced_at, reason: 'rate_limited', retry_at: '2026-11-01T12:34:00Z' } : { status: 'unauthorized', as_of: repo.synced_at, reason: 'token_rejected' })) : undefined)
    await openApp('/developer'); await screen.findByRole('heading', { name: repo.full_name })
    state = 'rate'; await fireEvent.click(screen.getByRole('button', { name: '刷新 GitHub 数据' }))
    expect(await screen.findByText(/GitHub 限流中/)).toBeVisible(); expect(screen.getByRole('heading', { name: repo.full_name })).toBeVisible()
    state = 'unauthorized'; await fireEvent.click(screen.getByRole('button', { name: '刷新 GitHub 数据' }))
    expect(await screen.findByText(/GitHub 授权已失效.*重新连接/)).toBeVisible()
    expect(screen.queryByRole('heading', { name: repo.full_name })).not.toBeInTheDocument()
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
    await openApp('/developer'); await screen.findByRole('heading', { name: repo.full_name })
    revoked = true; await fireEvent.click(screen.getByRole('button', { name: '刷新 GitHub 数据' }))
    await vi.waitFor(() => expect(screen.queryByRole('heading', { name: repo.full_name })).not.toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('没有查看')
  })
  it('does not show late data from the previous workspace', async () => {
    let finish: ((r: Response) => void) | undefined
    const requests = developer((r) => {
      if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B空间', role: 'viewer' }] })
      if (path(r).startsWith('/api/v1/developer/')) return r.headers.get('X-Workspace-Id') === 'workspace-a' ? new Promise<Response>((resolve) => { finish = resolve }) : Response.json(developerPage([{ ...repo, full_name: 'team/space-b' }]))
    })
    await openApp('/developer'); await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    expect(await screen.findByRole('heading', { name: 'team/space-b' })).toBeVisible()
    finish!(Response.json(developerPage([repo]))); await Promise.resolve()
    expect(screen.queryByRole('heading', { name: repo.full_name })).not.toBeInTheDocument()
    expect(requests.filter((r) => path(r).includes('/developer/')).at(-1)!.headers.get('X-Workspace-Id')).toBe('workspace-b')
  })
})
