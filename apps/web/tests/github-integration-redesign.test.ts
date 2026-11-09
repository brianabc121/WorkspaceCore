import { fireEvent, screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, emptyPage, fail } from './helpers'
import { github, repo, checked, developerPage, evidenceOf, allChecks } from './github-fixtures'
import { formatTime } from '../src/features/time'
import type { Schema } from '../src/services/api'

// 集成设置页改版（T-0192，效果图 03）：逐仓库四项权限只来自实际核验结果，未核验就写“未核验”；
// 刷新只读、测试连接与手动同步分开；限流显示恢复时间；失权不显示旧结果；Token 只写不回显。
type Integration = Schema['IntegrationOut']
type Evidence = Schema['RepositoryPermissionEvidenceOut']
type Check = Schema['RepositoryPermissionCheck']
const path = (r: Request) => new URL(r.url).pathname
const check = (name: Check['name'], ok: boolean, code: string | null = null): Check => ({ name, ok, code })
const repoOf = (n: number, full_name: string): Schema['RepoOut'] => ({ ...repo, id: `20000000-0000-4000-8000-0000000000${n}`, external_id: `github:repo:${n}`, full_name, source_url: `https://github.com/${full_name}` })
const workbench = repo, api = repoOf(41, 'team/api')
const missingActions = [check('metadata', true), check('issues', true), check('pull_requests', true), check('actions', false, 'missing_permission')]
const LABELS = ['Metadata', 'Issues', 'Pull requests', 'Actions'] as const
const CHECKED_AT = '2026-11-09T02:00:00Z'

type State = { integration: Integration; repos: Schema['RepoOut'][]; evidence: Record<string, Partial<Evidence>> }
function setup(initial: Partial<Omit<State, 'integration'>> & { integration?: Partial<Integration> } = {}, override?: (r: Request, state: State) => Response | undefined | Promise<Response | undefined>) {
  const state: State = { integration: { ...github, ...initial.integration }, repos: initial.repos ?? [workbench], evidence: initial.evidence ?? {} }
  const requests = mockApi(async (r) => {
    const p = path(r), custom = await override?.(r, state)
    if (custom) return custom
    if (p === '/api/v1/integrations' && r.method === 'GET') return Response.json({ ...emptyPage, items: [state.integration] })
    if (p === '/api/v1/developer/repos') return Response.json(developerPage(state.repos, state.integration.sync))
    const match = /^\/api\/v1\/developer\/repos\/([^/]+)\/permissions$/.exec(p)
    if (match) {
      const found = state.repos.find((item) => item.id === match[1])
      if (!found) return fail(404, 'NOT_FOUND')
      return Response.json(evidenceOf({ repo_id: found.id, full_name: found.full_name, checked_at: null, checks: [], complete: false, sync: state.integration.sync, ...state.evidence[found.full_name] }))
    }
    return undefined
  })
  return { requests, state }
}
const row = (name: string) => screen.getByRole('row', { name: `仓库：${name}` })
const cell = (name: string, label: string) => row(name).querySelector<HTMLElement>(`[data-label="${label}"]`)!
const table = () => screen.findByRole('table', { name: '仓库权限核验' })
const cellTexts = () => [...document.querySelectorAll('table td[data-label]')].map((td) => td.textContent ?? '')

describe('集成设置改版：逐仓库权限核验', () => {
  it('shows 未核验 for repositories never checked or checked only for metadata, never deriving results from access=ok', async () => {
    setup({
      integration: { repositories: ['team/workbench', 'team/api', 'team/new'] },
      repos: [workbench, api], // team/new 还没同步到本地；两个仓库的 access 都是 ok
      evidence: { 'team/api': { checked_at: CHECKED_AT, checks: [check('metadata', false, 'not_found')] } },
    })
    await openApp('/settings/integrations')
    await table()
    await vi.waitFor(() => {
      expect(cell('team/api', 'Metadata')).toHaveTextContent('无法访问')
      for (const label of ['Issues', 'Pull requests', 'Actions']) expect(cell('team/api', label)).toHaveTextContent('未核验')
      for (const name of ['team/workbench', 'team/new']) for (const label of LABELS) expect(cell(name, label)).toHaveTextContent('未核验')
    })
    expect(cell('team/workbench', '结果')).toHaveTextContent('未核验')
    expect(cell('team/new', '结果')).toHaveTextContent('未核验')
    expect(cell('team/api', '结果')).toHaveTextContent('无法访问')
    expect(within(row('team/api')).getByText(new RegExp(`上次核验.*${formatTime(CHECKED_AT)}`))).toBeVisible()
    expect(within(row('team/workbench')).getByText('尚未核验')).toBeVisible()
    expect(cellTexts().some((text) => text.includes('通过'))).toBe(false)
    // 看不到仓库（metadata 未通过）是真实的权限问题；其余两个仓库只是未核验
    expect(screen.getByText('部分权限不足')).toBeVisible()
    expect(within(screen.getByRole('group', { name: '同步状态概览' })).getByText(/1 个仓库缺少权限/)).toBeVisible()
  })

  it('keeps the other three results when a read-only repository is missing only Actions', async () => {
    setup({
      integration: { repositories: ['team/workbench', 'team/api'] },
      repos: [workbench, api],
      evidence: { 'team/workbench': { checked_at: CHECKED_AT, checks: allChecks, complete: true }, 'team/api': { checked_at: CHECKED_AT, checks: missingActions, complete: true } },
    })
    await openApp('/settings/integrations')
    await table()
    await vi.waitFor(() => expect(cell('team/api', 'Actions')).toHaveTextContent('缺少权限'))
    for (const label of ['Metadata', 'Issues', 'Pull requests']) expect(cell('team/api', label)).toHaveTextContent('通过')
    expect(cell('team/api', '结果')).toHaveTextContent('需调整 Token')
    for (const label of LABELS) expect(cell('team/workbench', label)).toHaveTextContent('通过')
    expect(cell('team/workbench', '结果')).toHaveTextContent('可读取')
    expect(screen.getByText('部分权限不足')).toBeVisible()
    const tiles = screen.getByRole('group', { name: '同步状态概览' })
    expect(within(tiles).getByText(/1 个仓库缺少权限/)).toBeVisible()
    expect(within(tiles).getByText(/Actions/)).toBeVisible()
    expect(within(tiles).getByText('2')).toBeVisible() // 已选择仓库数来自连接配置
  })

  it('shows an explicit test result per repository and drops it once the repository list changes', async () => {
    let tests = 0
    const { requests } = setup({ repos: [] }, async (r, state) => {
      if (path(r).endsWith('/test')) {
        tests += 1
        state.integration = { ...state.integration, version: state.integration.version + 1, last_tested_at: CHECKED_AT }
        return Response.json(tests === 1
          ? { ...checked, ok: true, status: 'active', warnings: [], repositories: [{ full_name: 'team/workbench', ok: true, checks: allChecks }] }
          : { ...checked, ok: false, status: 'unavailable', warnings: [], checks: [{ name: 'reachable', ok: true }, { name: 'version', ok: true }, { name: 'token', ok: false, code: 'rate_limited' }], repositories: [] })
      }
      if (r.method === 'PATCH') {
        const body = await r.clone().json() as Schema['IntegrationUpdate']
        state.integration = { ...state.integration, version: state.integration.version + 1, repositories: body.repositories ?? state.integration.repositories }
        return Response.json(state.integration)
      }
      return undefined
    })
    await openApp('/settings/integrations')
    await table()
    await vi.waitFor(() => expect(cell('team/workbench', 'Metadata')).toHaveTextContent('未核验'))
    await fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    await vi.waitFor(() => { for (const label of LABELS) expect(cell('team/workbench', label)).toHaveTextContent('通过') })
    expect(within(row('team/workbench')).getByText(/本次测试/)).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '修改连接' }))
    await fireEvent.update(screen.getByLabelText('GitHub 仓库列表'), 'team/workbench\nteam/api')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(tests).toBe(2))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 2, repositories: ['team/workbench', 'team/api'] })
    await vi.waitFor(() => { for (const name of ['team/workbench', 'team/api']) for (const label of LABELS) expect(cell(name, label)).toHaveTextContent('未核验') })
  })

  it('refetches evidence after a token change, keeps the token out of the page and clears the field', async () => {
    const { requests } = setup({ evidence: { 'team/workbench': { checked_at: CHECKED_AT, checks: allChecks, complete: true } } }, (r, state) => {
      if (r.method === 'PATCH') {
        state.evidence = {} // 后端在 Token 更换时清掉旧快照
        state.integration = { ...state.integration, version: 2, status: 'pending' }
        return Response.json(state.integration)
      }
      if (path(r).endsWith('/test')) {
        state.integration = { ...state.integration, version: 3 }
        return Response.json({ ...checked, ok: false, status: 'unavailable', warnings: [], checks: [{ name: 'reachable', ok: true }, { name: 'version', ok: true }, { name: 'token', ok: false, code: 'rate_limited' }], repositories: [] })
      }
      return undefined
    })
    await openApp('/settings/integrations')
    await table()
    await vi.waitFor(() => expect(cell('team/workbench', 'Actions')).toHaveTextContent('通过'))
    await fireEvent.click(screen.getByRole('button', { name: '修改连接' }))
    const token = screen.getByLabelText('更换细粒度 Token')
    expect(token).toHaveValue('')
    expect(token).toHaveAttribute('type', 'password')
    await fireEvent.update(token, 'github_pat_rotated-token')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(requests.some((r) => path(r).endsWith('/test'))).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 1, api_token: 'github_pat_rotated-token' })
    await vi.waitFor(() => { for (const label of LABELS) expect(cell('team/workbench', label)).toHaveTextContent('未核验') })
    expect(screen.queryByLabelText('更换细粒度 Token')).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('github_pat_rotated-token')
    expect(requests.every((r) => !r.url.includes('github_pat_'))).toBe(true)
  })

  it('hides stale permission results and prompts to reconnect when GitHub rejects the token', async () => {
    const { state } = setup({ evidence: { 'team/workbench': { checked_at: CHECKED_AT, checks: allChecks, complete: true } } })
    await openApp('/settings/integrations')
    await table()
    await vi.waitFor(() => expect(cell('team/workbench', 'Metadata')).toHaveTextContent('通过'))
    state.integration = { ...state.integration, status: 'unauthorized', sync: { status: 'unauthorized', as_of: state.integration.sync.as_of, reason: 'token_rejected' } }
    await fireEvent.click(screen.getByRole('button', { name: '刷新状态' }))
    expect(await screen.findByText(/授权已失效.*更换 Token/)).toBeVisible()
    expect(screen.queryByRole('table', { name: '仓库权限核验' })).not.toBeInTheDocument()
    expect(cellTexts()).toEqual([])
    expect(screen.getByText('授权失效', { selector: '.asv-state' })).toBeVisible()
    expect(screen.getByRole('button', { name: '手动同步' })).toBeDisabled()
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '重新连接' })).toBeEnabled())
    await fireEvent.click(screen.getByRole('button', { name: '重新连接' }))
    await vi.waitFor(() => expect(screen.getByLabelText('更换细粒度 Token')).toHaveFocus())
  })

  it('shows the rate-limit recovery time and keeps the cached results', async () => {
    const retry = '2026-11-09T03:30:00Z'
    setup({ integration: { sync: { status: 'stale', as_of: '2026-11-09T01:00:00Z', reason: 'rate_limited', retry_at: retry } }, evidence: { 'team/workbench': { checked_at: CHECKED_AT, checks: allChecks, complete: true } } })
    await openApp('/settings/integrations')
    expect(await screen.findByText(new RegExp(`GitHub 限流中，预计 ${formatTime(retry)} 恢复`))).toBeVisible()
    expect(screen.getByRole('button', { name: '手动同步' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '测试连接' })).toBeEnabled()
    await vi.waitFor(() => expect(cell('team/workbench', 'Issues')).toHaveTextContent('通过'))
  })

  it('refreshes with reads only; test connection and manual sync are separate writes', async () => {
    const { requests } = setup({}, (r) => {
      if (path(r).endsWith('/sync')) return Response.json({ queued_at: '2026-11-09T02:00:00Z', mode: 'full' }, { status: 202 })
      if (path(r).endsWith('/test')) return Response.json({ ...checked, repositories: [] })
      return undefined
    })
    await openApp('/settings/integrations')
    await table()
    // 没有核验结果：只说未核验，不说“部分权限不足”
    await vi.waitFor(() => expect(cell('team/workbench', '结果')).toHaveTextContent('未核验'))
    expect(screen.queryByText('部分权限不足')).not.toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: '同步状态概览' })).getByText(/1 个仓库未核验/)).toBeVisible()
    const reads = () => requests.filter((r) => path(r) === '/api/v1/integrations').length
    const before = reads()
    await fireEvent.click(screen.getByRole('button', { name: '刷新状态' }))
    await vi.waitFor(() => expect(reads()).toBeGreaterThan(before))
    expect(requests.filter((r) => r.method !== 'GET')).toEqual([])
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '手动同步' })).toBeEnabled())
    await fireEvent.click(screen.getByRole('button', { name: '手动同步' }))
    await vi.waitFor(() => expect(requests.some((r) => path(r).endsWith('/sync'))).toBe(true))
    const writes = requests.filter((r) => r.method !== 'GET')
    expect(writes.map((r) => path(r))).toEqual([`/api/v1/integrations/${github.id}/sync`])
    expect(writes[0]!.headers.get('Idempotency-Key')).toBeTruthy()
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '测试连接' })).toBeEnabled())
    await fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    await vi.waitFor(() => expect(requests.some((r) => path(r).endsWith('/test'))).toBe(true))
    expect(requests.filter((r) => path(r).endsWith('/sync'))).toHaveLength(1)
  })

  it('edits in the side panel: token state, classic token rejected, 20-repository limit and case-insensitive dedupe', async () => {
    const names = Array.from({ length: 20 }, (_, i) => `team/r${String(i + 1).padStart(2, '0')}`)
    const listing = { repositories: [...names.map((name) => name.replace('team/r', 'Team/R')), 'team/extra'], complete: true }
    const { requests } = setup({ integration: { repositories: names, token_expires_at: '2027-02-01T00:00:00Z' } }, (r) => {
      if (path(r) === '/api/v1/integrations/github/repositories') return Response.json(listing)
      if (r.method === 'PATCH') return Response.json(github)
      return undefined
    })
    await openApp('/settings/integrations')
    const aside = await screen.findByRole('complementary', { name: 'GitHub 连接配置' })
    expect(await within(aside).findByText('已设置 · 不回显')).toBeVisible()
    expect(within(aside).getByText(formatTime('2027-02-01T00:00:00Z'))).toBeVisible()
    expect(within(aside).getByRole('link', { name: /获取细粒度 Token/ }).getAttribute('href')).toMatch(/^https:\/\/github\.com\/settings\/personal-access-tokens\/new\?/)
    // 刷新状态不是测试：Webhook 不支持只是说明
    const note = screen.getByText(/GitHub V1 使用周期同步/)
    expect(note.closest('[role="alert"]')).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: '修改连接' }))
    await vi.waitFor(() => expect(screen.getByLabelText('连接名称')).toHaveFocus())
    const token = screen.getByLabelText('更换细粒度 Token')
    expect(token).toHaveAttribute('placeholder', '留空保持现有 Token')
    await fireEvent.update(token, 'ghp_classic-token')
    await fireEvent.click(screen.getByRole('button', { name: '读取可访问的仓库' }))
    expect(await screen.findByText(/细粒度.*github_pat_/, { selector: '[role="alert"]' })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(screen.getAllByText(/细粒度.*github_pat_/).length).toBeGreaterThan(0))
    expect(requests.filter((r) => r.method !== 'GET')).toEqual([])

    // 取消：Token 输入清空；再打开仍是空的
    await fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByLabelText('更换细粒度 Token')).not.toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '修改连接' }))
    expect(screen.getByLabelText('更换细粒度 Token')).toHaveValue('')

    // 用已保存的 Token 读取：大小写不同的同名仓库算已选，满 20 个后不能再勾
    await fireEvent.click(screen.getByRole('button', { name: '读取可访问的仓库' }))
    const picker = await screen.findByRole('group', { name: /Token 能访问的仓库（已选 20 \/ 20）/ })
    expect(within(picker).getAllByRole('checkbox')).toHaveLength(21)
    expect(within(picker).getByRole('checkbox', { name: 'Team/R01' })).toBeChecked()
    expect(within(picker).getByRole('checkbox', { name: 'team/extra' })).toBeDisabled()
    expect(await requests.find((r) => path(r) === '/api/v1/integrations/github/repositories')!.clone().json()).toEqual({})

    await fireEvent.update(screen.getByLabelText('GitHub 仓库列表'), [...names, 'team/r21'].join('\n'))
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    expect(await screen.findByText(/请填写 1–20 个仓库/)).toBeVisible()
    expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(0)

    await fireEvent.update(screen.getByLabelText('GitHub 仓库列表'), 'Team/A\nteam/a\nteam/b')
    await fireEvent.click(screen.getByRole('button', { name: '保存 GitHub 连接' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 1, repositories: ['team/a', 'team/b'] })
  })
})
