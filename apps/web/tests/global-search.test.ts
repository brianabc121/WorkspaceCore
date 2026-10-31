import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import type { Schema } from '../src/services/api'
import { mockApi, openApp, emptyPage, fail, me, workspace } from './helpers'
const path = (r: Request) => new URL(r.url).pathname
const result: Schema['SearchResultOut'] = { id: '60000000-0000-4000-8000-000000000031', resource_type: 'task', provider: 'vikunja', title: 'needle task', context: '发布项目', source_url: 'https://tasks.example.com/tasks/31', updated_at: '2026-10-31T00:00:00Z', match: 'title' }
async function openSearch(q = 'needle') {
  await fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  await fireEvent.update(screen.getByLabelText('搜索页面与组件'), q)
}
describe('跨资源全局搜索', () => {
  it.each(['filter', 'query', 'reopen'])('retains denial until a fresh successful read after %s', async (transition) => {
    let revoked = true, finish: ((r: Response) => void) | undefined
    mockApi((r) => path(r) === '/api/v1/search' ? revoked ? fail(403, 'FORBIDDEN') : new Promise<Response>((resolve) => { finish = resolve }) : undefined)
    await openApp('/'); await screen.findByText('欢迎便签'); await openSearch('欢迎')
    await screen.findByRole('alert'); revoked = false
    if (transition === 'filter') await fireEvent.update(screen.getByLabelText('搜索资源类型'), 'task')
    else if (transition === 'query') await fireEvent.update(screen.getByLabelText('搜索页面与组件'), '欢')
    else { await fireEvent.click(screen.getByRole('button', { name: '关闭对话框' })); await fireEvent.keyDown(window, { key: 'k', ctrlKey: true }) }
    expect(screen.queryByRole('button', { name: /文本便签/ })).not.toBeInTheDocument()
    if (transition === 'reopen') await fireEvent.update(screen.getByLabelText('搜索页面与组件'), '欢迎')
    await vi.waitFor(() => expect(finish).toBeDefined())
    expect(screen.queryByRole('button', { name: /文本便签/ })).not.toBeInTheDocument()
    finish!(Response.json({ ...emptyPage, items: [{ ...result, title: '授权恢复后的资源' }] }))
    expect(await screen.findByRole('link', { name: /授权恢复后的资源/ })).toBeVisible()
  })
  it('updates an already-mounted calendar for successive search result dates', async () => {
    let date = '2026-11-08'
    mockApi((r) => path(r) === '/api/v1/search' ? Response.json({ ...emptyPage, items: [{ ...result, resource_type: 'event', provider: 'local', title: 'needle event', context: date, source_url: null }] }) : undefined)
    await openApp('/calendar?date=2026-10-30'); await screen.findByLabelText('查看日期')
    await openSearch(); await fireEvent.click(await screen.findByRole('link', { name: /needle event/ }))
    await vi.waitFor(() => expect(screen.getByLabelText('查看日期')).toHaveValue('2026-11-08'))
    expect(screen.getByText(/2026-11-02 — 2026-11-08/)).toBeVisible()
    date = '2026-12-04'; await openSearch(); await fireEvent.click(await screen.findByRole('link', { name: /needle event/ }))
    await vi.waitFor(() => expect(screen.getByLabelText('查看日期')).toHaveValue('2026-12-04'))
    expect(screen.getByText(/2026-11-30 — 2026-12-06/)).toBeVisible()
  })
  it('hides cached local widget content when BFF search denies workspace access', async () => {
    mockApi((r) => path(r) === '/api/v1/search' ? fail(403, 'FORBIDDEN') : undefined)
    await openApp('/'); await screen.findByText('欢迎便签'); await openSearch('欢迎')
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.queryByRole('button', { name: /文本便签/ })).not.toBeInTheDocument()
  })
  it('queries BFF with workspace and groups types with source metadata and native links', async () => {
    const requests = mockApi((r) => path(r) === '/api/v1/search' ? Response.json({ ...emptyPage, items: [{ ...result, resource_type: 'issue', provider: 'github', title: 'needle issue' }, result] }) : undefined)
    await openApp('/'); await openSearch(' needle ')
    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByRole('link', { name: /needle task/ })).toHaveAttribute('href', result.source_url)
    expect(within(dialog).getAllByRole('heading', { level: 3 }).map((el) => el.textContent)).toEqual(['任务', 'Issues'])
    expect(within(dialog).getByText(/Vikunja.*更新于/)).toBeVisible()
    const select = screen.getByLabelText('搜索资源类型'); select.focus()
    await fireEvent.keyDown(select, { key: 'ArrowDown' }); expect(select).toHaveFocus()
    screen.getByLabelText('搜索页面与组件').focus()
    await fireEvent.keyDown(document.activeElement!, { key: 'ArrowUp' })
    expect(within(dialog).getByRole('link', { name: /needle issue/ })).toHaveFocus()
    await fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' })
    expect(within(dialog).getByRole('link', { name: /needle task/ })).toHaveFocus()
    const read = requests.find((r) => path(r) === '/api/v1/search')!
    expect(new URL(read.url).searchParams.get('q')).toBe('needle')
    expect(read.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })
  it('paginates search with opaque cursor without repeating earlier results', async () => {
    const requests = mockApi((r) => path(r) === '/api/v1/search' ? Response.json({ ...emptyPage, items: [new URL(r.url).searchParams.has('cursor') ? { ...result, id: 'second', title: 'needle second' } : result], next_cursor: new URL(r.url).searchParams.has('cursor') ? null : 'opaque+/=' }) : undefined)
    await openApp('/'); await openSearch(); await screen.findByRole('link', { name: /needle task/ })
    await fireEvent.click(screen.getByRole('button', { name: '加载更多搜索结果' }))
    expect(await screen.findByRole('link', { name: /needle second/ })).toBeVisible()
    expect(screen.getAllByRole('link', { name: /needle task/ })).toHaveLength(1)
    expect(new URL(requests.filter((r) => path(r) === '/api/v1/search')[1]!.url).searchParams.get('cursor')).toBe('opaque+/=')
  })
  it('navigates grouped results with keyboard and opens a local event on its date', async () => {
    mockApi((r) => path(r) === '/api/v1/search' ? Response.json({ ...emptyPage, items: [{ ...result, resource_type: 'event', provider: 'local', title: 'needle event', context: '2026-11-08', source_url: null }] }) : undefined)
    const { router } = await openApp('/'); await openSearch(); await screen.findByRole('link', { name: /needle event/ })
    await fireEvent.keyDown(screen.getByLabelText('搜索页面与组件'), { key: 'ArrowDown' })
    expect(screen.getByRole('link', { name: /needle event/ })).toHaveFocus()
    await fireEvent.keyDown(document.activeElement!, { key: 'Enter' })
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/calendar'))
    expect(router.currentRoute.value.query.date).toBe('2026-11-08')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByLabelText('查看日期')).toHaveValue('2026-11-08')
  })
  it('hides previous-query results immediately while the next request is pending', async () => {
    let finish: ((r: Response) => void) | undefined
    mockApi((r) => path(r) === '/api/v1/search' ? new URL(r.url).searchParams.get('q') === 'next' ? new Promise<Response>((resolve) => { finish = resolve }) : Response.json({ ...emptyPage, items: [result] }) : undefined)
    await openApp('/'); await openSearch(); await screen.findByRole('link', { name: /needle task/ })
    await fireEvent.update(screen.getByLabelText('搜索页面与组件'), 'next')
    expect(screen.queryByRole('link', { name: /needle task/ })).not.toBeInTheDocument()
    await vi.waitFor(() => expect(finish).toBeDefined()); finish!(Response.json(emptyPage))
    expect(await screen.findByText('没有匹配的资源。')).toBeVisible()
  })
  it('closes on scope changes and never renders a late old-workspace result', async () => {
    let finish: ((r: Response) => void) | undefined
    const requests = mockApi((r) => path(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B空间' }] }) : path(r) === '/api/v1/search' ? r.headers.get('X-Workspace-Id') === 'workspace-a' ? new Promise<Response>((resolve) => { finish = resolve }) : Response.json({ ...emptyPage, items: [{ ...result, title: 'B空间文档' }] }) : undefined)
    await openApp('/'); await openSearch(); await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    finish!(Response.json({ ...emptyPage, items: [result] })); await openSearch()
    expect(await screen.findByRole('link', { name: /B空间文档/ })).toBeVisible()
    expect(screen.queryByRole('link', { name: /needle task/ })).not.toBeInTheDocument()
    expect(requests.filter((r) => path(r) === '/api/v1/search').at(-1)!.headers.get('X-Workspace-Id')).toBe('workspace-b')
  })
  it('shows fetch errors instead of empty results, and retries safely', async () => {
    let broken = true
    mockApi((r) => path(r) === '/api/v1/search' ? broken ? fail(503, 'HTTP_ERROR') : Response.json(emptyPage) : undefined)
    await openApp('/'); await openSearch(); expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.queryByText('没有匹配的资源。')).not.toBeInTheDocument()
    broken = false; await fireEvent.click(screen.getByRole('button', { name: '重试搜索' }))
    expect(await screen.findByText('没有匹配的资源。')).toBeVisible()
  })
  it('never turns an unsafe native URL into an executable link', async () => {
    mockApi((r) => path(r) === '/api/v1/search' ? Response.json({ ...emptyPage, items: [{ ...result, source_url: 'javascript:alert(1)' }] }) : undefined)
    await openApp('/'); await openSearch(); const link = await screen.findByRole('link', { name: /needle task/ })
    expect(link).toHaveAttribute('href', '/tasks')
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull()
  })
})
