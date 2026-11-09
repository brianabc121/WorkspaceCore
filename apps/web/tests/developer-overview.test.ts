import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, workspace, me } from './helpers'
import { repo, pull, run, developerPage, developerRoutes, evidenceOf, overviewOf, overviewRow } from './github-fixtures'
import type { Schema } from '../src/services/api'

// 开发者总览（T-0176，二批四页改造计划 Task 5）：统计四卡、仓库表、开放 PR、失败运行、权限快照与同步状态。
const path = (r: Request) => new URL(r.url).pathname
const isOverview = (r: Request) => path(r) === '/api/v1/developer/overview'
const isPermissions = (r: Request) => /\/developer\/repos\/[^/]+\/permissions$/.test(path(r))
const rateLimited: Schema['SyncState'] = { status: 'stale', as_of: '2026-11-01T10:00:00Z', reason: 'rate_limited', retry_at: '2026-11-01T12:34:00Z' }
const docs: Schema['RepoOut'] = { ...repo, id: '20000000-0000-4000-8000-000000000032', external_id: 'github:repo:32', full_name: 'team/docs', description: '文档', source_url: 'https://github.com/team/docs' }
function developer(override?: (r: Request) => Response | Promise<Response> | undefined, role: Schema['WorkspaceOut']['role'] = 'owner') {
  return mockApi(async (r) => {
    if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [{ ...workspace, role }] })
    return (await override?.(r)) ?? developerRoutes(path(r))
  })
}
const panel = async (name: RegExp) => (await screen.findByRole('heading', { name })).closest('section') as HTMLElement

describe('开发者总览', () => {
  it('shows_rate_limit_retry_and_cached_records', async () => {
    developer((r) => (isOverview(r) ? Response.json(overviewOf([overviewRow()], rateLimited)) : undefined))
    await openApp('/developer')
    const banner = await screen.findByText(/GitHub 限流中/)
    expect(banner).toHaveTextContent(/预计 2026\/11\/01 \d\d:34 恢复/)
    expect(banner).toHaveTextContent(/当前显示 2026\/11\/01 \d\d:00 同步的缓存/)
    // 限流时照常显示缓存，不当成空列表
    expect(screen.getByRole('button', { name: repo.full_name })).toBeVisible()
    expect(screen.queryByText('所选范围没有已同步的仓库。')).not.toBeInTheDocument()
    const sync = await panel(/同步状态/)
    expect(within(sync).getByText('限流，等待恢复')).toBeVisible()
    expect(within(sync).getByText('预计恢复')).toBeVisible()
  })

  it('unauthorized_clears_overview_and_permission_cache', async () => {
    let state: 'ok' | 'unauthorized' = 'ok'
    const requests = developer((r) => {
      if (state === 'ok') return undefined
      const lost: Schema['SyncState'] = { status: 'unauthorized', as_of: '2026-11-01T10:00:00Z', reason: 'token_rejected' }
      if (isOverview(r)) return Response.json(overviewOf([], lost, { attention_pulls: [], failed_runs: [], recent_updates: [], partial: true, summary: { repo_count: null, open_issue_count: null, open_pull_count: null, failed_run_count: null, window_start: '2026-10-23T00:00:00Z', window_end: '2026-10-30T00:00:00Z' } }))
      if (isPermissions(r)) return Response.json(evidenceOf({ full_name: null, checked_at: null, checks: [], complete: false, sync: lost }))
      return Response.json(developerPage([], lost))
    })
    const { queryClient } = await openApp('/developer')
    const permissions = await panel(/所选仓库：team\/workbench 的读取权限/)
    await vi.waitFor(() => expect(within(permissions).getAllByText('通过')).toHaveLength(4))
    expect(within(permissions).getByText(/上次核验/)).toBeVisible()
    state = 'unauthorized'
    await fireEvent.click(screen.getByRole('button', { name: '刷新 GitHub 数据' }))
    expect(await screen.findByText(/GitHub 授权已失效.*重新连接/)).toBeVisible()
    expect(screen.queryByRole('button', { name: repo.full_name })).not.toBeInTheDocument()
    expect(screen.queryByText(/所选仓库：/)).not.toBeInTheDocument()
    expect(screen.queryByText('通过')).not.toBeInTheDocument()
    expect(screen.queryByText(pull.title)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '重新连接 GitHub' })).toHaveAttribute('href', '/settings/integrations')
    // 统计变成未知，权限快照的缓存被清掉
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(4)
    expect(queryClient.getQueryCache().findAll({ queryKey: ['developer', me.user.id, workspace.id, 'permissions'] }).filter((q) => q.state.data !== undefined)).toHaveLength(0)
    expect(requests.every((r) => r.method === 'GET')).toBe(true)
  })

  it('unknown_checks_never_turn_green_from_repo_access', async () => {
    let evidence = evidenceOf({ checked_at: null, checks: [], complete: false })
    developer((r) => (isPermissions(r) ? Response.json(evidence) : undefined))
    await openApp('/developer')
    expect(repo.access).toBe('ok')
    const permissions = await panel(/所选仓库：team\/workbench 的读取权限/)
    await vi.waitFor(() => expect(within(permissions).getAllByText('未核验')).toHaveLength(4))
    expect(within(permissions).queryByText('通过')).not.toBeInTheDocument()
    expect(within(permissions).getByText('尚未核验')).toBeVisible()
    // metadata 未通过时其余三项没有执行：显示未核验，不补成通过或未通过
    evidence = evidenceOf({ checks: [{ name: 'metadata', ok: false, code: 'not_found' }], complete: false })
    await fireEvent.click(screen.getByRole('button', { name: '刷新 GitHub 数据' }))
    await vi.waitFor(() => expect(within(permissions).getByText('未通过')).toBeVisible())
    expect(within(permissions).getAllByText('未核验')).toHaveLength(3)
    expect(within(permissions).getByText('仓库不存在或 Token 未获授权')).toBeVisible()
    expect(within(permissions).getByText(/有的项目这次没有执行/)).toBeVisible()
    // 管理员可以去连接设置，页面上不发起测试连接
    expect(within(permissions).getByRole('link', { name: /前往连接设置/ })).toHaveAttribute('href', '/settings/integrations')
    expect(screen.queryByRole('button', { name: /测试连接|请求同步|合并|重新运行/ })).not.toBeInTheDocument()
  })

  it('does not attach CI results to pull requests and only labels real review requests', async () => {
    // 两个仓库同一个分支、不同提交；只有运行带 head_sha，PR 没有：不能给 PR 贴“CI 通过”
    const passing: Schema['RunOut'] = { ...run, id: '50000000-0000-4000-8000-000000000041', repo_id: docs.id, repo_full_name: docs.full_name, conclusion: 'success', head_branch: 'feature/search', head_sha: 'def456', failure_summary: null }
    const failing: Schema['RunOut'] = { ...run, failure_summary: null }
    const open: Schema['PullOut'] = { ...pull, id: '40000000-0000-4000-8000-000000000042', title: '没有审查请求', requested_reviewers: [], number: 14 }
    const requested: Schema['PullOut'] = { ...pull, id: '40000000-0000-4000-8000-000000000043', title: '已有人被请求审查', requested_reviewers: ['alice'], number: 15 }
    const rows = [overviewRow({ latest_run: failing }), overviewRow({ repo: docs, latest_run: passing, failed_run_count: 0 })]
    developer((r) => (isOverview(r) ? Response.json(overviewOf(rows, undefined, { attention_pulls: [requested, open], failed_runs: [failing] })) : undefined))
    await openApp('/developer')
    const pulls = await panel(/开放的 Pull requests/)
    const requestedItem = within(pulls).getByText('已有人被请求审查').closest('li') as HTMLElement
    const openItem = within(pulls).getByText('没有审查请求').closest('li') as HTMLElement
    expect(within(requestedItem).getByText('已请求审查')).toBeVisible()
    expect(within(openItem).getByText('开放')).toBeVisible()
    expect(within(openItem).queryByText('已请求审查')).not.toBeInTheDocument()
    for (const item of [requestedItem, openItem]) expect(within(item).queryByText(/CI|通过|运行中|失败/)).not.toBeInTheDocument()
    expect(screen.queryByText(/待审/)).not.toBeInTheDocument()
    // 失败摘要缺失时如实说明，不填示例里的步骤名
    const failed = await panel(/Actions 失败摘要/)
    expect(within(failed).getByText('失败步骤未同步，前往 GitHub 查看')).toBeVisible()
    expect(screen.queryByText(/Typecheck|typecheck/)).not.toBeInTheDocument()
    expect(within(failed).getByRole('link', { name: /查看 GitHub 运行/ })).toHaveAttribute('href', run.source_url)
    // 仓库表的“最近运行”只是这个仓库自己的运行状态
    const table = screen.getByRole('table', { name: '仓库概览' })
    expect(within(table).getByText('失败')).toBeVisible()
    expect(within(table).getByText('通过')).toBeVisible()
  })

  it('marks unknown statistics instead of zero and labels the synced scope', async () => {
    const summary = { repo_count: 2, open_issue_count: null, open_pull_count: 3, failed_run_count: null, window_start: '2026-10-23T00:00:00Z', window_end: '2026-10-30T00:00:00Z' }
    const rows = [overviewRow({ repo: { ...repo, access: 'missing_permission' }, open_issue_count: null, failed_run_count: null, partial: true }), overviewRow({ repo: docs })]
    developer((r) => (isOverview(r) ? Response.json(overviewOf(rows, undefined, { summary, partial: true, failed_runs: [] })) : undefined))
    await openApp('/developer')
    const stats = screen.getByRole('region', { name: '已同步范围的统计' })
    await vi.waitFor(() => expect(within(stats).getByText('3')).toBeVisible())
    expect(within(stats).getByText('开放 PR')).toBeVisible()
    expect(within(stats).getAllByText('—')).toHaveLength(2)
    expect(within(stats).getAllByText(/已同步范围/).length).toBe(4)
    expect(within(stats).getByText('近 7 天更新 · 已同步范围')).toBeVisible()
    expect(screen.getByText(/标“—”的数字未知，不按 0 计/)).toBeVisible()
    const table = screen.getByRole('table', { name: '仓库概览' })
    expect(within(table).getByText('部分内容缺少读取权限')).toBeVisible()
    expect(within(table).getByText('失败数未知')).toBeVisible()
    expect(within(await panel(/Actions 失败摘要/)).getByText(/失败运行数未知/)).toBeVisible()
  })

  it('selecting a row only changes the permission request; filters change the overview', async () => {
    const requests = developer((r) => {
      if (isOverview(r)) return Response.json(overviewOf([overviewRow(), overviewRow({ repo: docs })]))
      if (isPermissions(r)) return Response.json(evidenceOf({ repo_id: path(r).split('/')[5], full_name: path(r).includes(docs.id) ? docs.full_name : repo.full_name }))
      if (path(r) === '/api/v1/developer/repos') return Response.json(developerPage([repo, docs]))
      return undefined
    })
    await openApp('/developer')
    await screen.findByRole('heading', { name: /所选仓库：team\/workbench/ })
    const overviews = () => requests.filter(isOverview)
    const before = overviews().length
    await fireEvent.click(screen.getByRole('button', { name: docs.full_name }))
    expect(await screen.findByRole('heading', { name: /所选仓库：team\/docs 的读取权限/ })).toBeVisible()
    await vi.waitFor(() => expect(requests.some((r) => isPermissions(r) && path(r).includes(docs.id))).toBe(true))
    expect(overviews().length).toBe(before)
    // 时间窗与搜索才改变总览（统计）
    await fireEvent.update(screen.getByLabelText('失败运行时间窗'), '24h')
    await vi.waitFor(() => expect(new URL(overviews().at(-1)!.url).searchParams.get('range')).toBe('24h'))
    expect(screen.getByText('近 24 小时更新 · 已同步范围')).toBeVisible()
    await fireEvent.update(screen.getByLabelText('搜索仓库'), 'docs')
    await vi.waitFor(() => expect(new URL(overviews().at(-1)!.url).searchParams.get('q')).toBe('docs'))
    expect(new URL(overviews().at(-1)!.url).searchParams.has('cursor')).toBe(false)
    await fireEvent.update(screen.getByLabelText('仓库范围'), docs.id)
    await vi.waitFor(() => expect(new URL(overviews().at(-1)!.url).searchParams.get('repo_id')).toBe(docs.id))
    expect(requests.every((r) => r.method === 'GET' && new URL(r.url).origin === location.origin)).toBe(true)
  })

  it('late permission results of the previous repository never show on the newly selected one', async () => {
    const pending = new Map<string, (r: Response) => void>()
    developer((r) => {
      if (isOverview(r)) return Response.json(overviewOf([overviewRow(), overviewRow({ repo: docs })]))
      if (isPermissions(r)) return new Promise<Response>((resolve) => pending.set(path(r).split('/')[5], resolve))
      return undefined
    })
    await openApp('/developer')
    await vi.waitFor(() => expect(pending.has(repo.id)).toBe(true))
    await fireEvent.click(screen.getByRole('button', { name: docs.full_name }))
    await vi.waitFor(() => expect(pending.has(docs.id)).toBe(true))
    // 先选中的仓库的结果迟到：不能显示在 team/docs 下
    pending.get(repo.id)!(Response.json(evidenceOf()))
    await new Promise((resolve) => setTimeout(resolve, 20))
    const permissions = await panel(/所选仓库：team\/docs 的读取权限/)
    expect(within(permissions).queryByText('通过')).not.toBeInTheDocument()
    pending.get(docs.id)!(Response.json(evidenceOf({ repo_id: docs.id, full_name: docs.full_name, checks: [{ name: 'metadata', ok: true }], complete: false })))
    await vi.waitFor(() => expect(within(permissions).getAllByText('通过')).toHaveLength(1))
    expect(within(permissions).getAllByText('未核验')).toHaveLength(3)
  })

  it('handles not configured and gives the settings link only to managers', async () => {
    const none: Schema['SyncState'] = { status: 'not_configured', as_of: null, reason: 'not_configured' }
    const empty = (r: Request) => (isOverview(r) ? Response.json(overviewOf([], none, { attention_pulls: [], failed_runs: [], recent_updates: [] })) : path(r).startsWith('/api/v1/developer/') ? Response.json(developerPage([], none)) : undefined)
    developer(empty, 'viewer')
    await openApp('/developer')
    expect(await screen.findByText('尚未配置可用的 GitHub 连接')).toBeVisible()
    expect(screen.getByText(/还没有可用的 GitHub 连接/)).toBeVisible()
    expect(screen.queryByRole('link', { name: /重新连接 GitHub|GitHub 连接设置|前往/ })).not.toBeInTheDocument()
    expect(screen.getByText('请联系管理员在连接设置中处理。')).toBeVisible()
  })

  it('refetches overview and permissions when the workspace event stream says developer changed', async () => {
    class Stream extends EventTarget {
      static last: Stream | null = null
      readyState = 1
      constructor(public url: string) { super(); Stream.last = this }
      close() { this.readyState = 2 }
    }
    vi.stubGlobal('EventSource', Stream)
    const requests = developer()
    await openApp('/developer')
    // 等权限快照读完再推送事件（还在请求中的查询失效时会沿用这次请求的结果）
    const permissions = await panel(/所选仓库：team\/workbench 的读取权限/)
    await vi.waitFor(() => expect(within(permissions).getAllByText('通过')).toHaveLength(4))
    const count = (match: (r: Request) => boolean) => requests.filter(match).length
    const before = { overview: count(isOverview), permissions: count(isPermissions) }
    Stream.last!.dispatchEvent(new MessageEvent('invalidate', { data: JSON.stringify({ workspace_id: 'workspace-a', topics: ['developer'], at: '2026-11-01T10:00:00Z' }) }))
    await vi.waitFor(() => expect(count(isOverview)).toBe(before.overview + 1))
    await vi.waitFor(() => expect(count(isPermissions)).toBe(before.permissions + 1))
  })
})
