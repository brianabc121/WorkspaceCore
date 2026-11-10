// 审计日志页改版（T-0198，方案 docs/v2/ai-admin-redesign 计划 C2，效果图 08）：服务端筛选的事件表、
// 按 ?id= 独立读取的事件详情、导出当前服务端筛选。只显示“当前已加载 N 条”，不编造总数；
// 未知事件原样显示；metadata 只当文本；导出只说“文件已生成，开始下载”，ObjectURL 用完就释放。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace, fail } from './helpers'
import type { Schema } from '../src/services/api'
import { CLOCK_ALLOWANCE_MS } from '../src/features/audit/queries'

type Entry = Schema['AuditLogView']
const path = (r: Request) => new URL(r.url).pathname
const params = (r: Request) => new URL(r.url).searchParams
const HOUR = 3600_000, DAY = 24 * HOUR

const OWNER = '1f2e0000-0000-4000-8000-0000000b7a1a'
const ALEX = '2b7e0000-0000-4000-8000-0000000a1e70'
const GONE = '8a920000-0000-4000-8000-00000000e1c4' // 已被移出工作空间的成员：服务端解析不到显示名
const SECRET = '7c300000-0000-4000-8000-000000009a42'
const SECRET_2 = '7c300000-0000-4000-8000-00000000b0b2'
const INSTANCE = '5d100000-0000-4000-8000-0000000d3b10'
const MISSING = 'e0000000-0000-4000-8000-0000000fffff'
const id = (n: number) => `e0000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const at = (minutes: number) => new Date(Date.UTC(2026, 10, 9, 6, 28, 16) - minutes * 60_000).toISOString()
const entry = (n: number, over: Partial<Entry>): Entry => ({
  id: id(n), action: 'workspace.updated', action_label: '修改工作空间设置', actor_id: OWNER, actor_label: 'brian',
  resource_type: null, resource_id: null, resource_current_name: null, metadata: {}, created_at: at(n), ...over,
})
const rows: Entry[] = [
  entry(1, { action: 'agents.secret.rotated', action_label: '轮换密钥', resource_type: 'agent_secret', resource_id: SECRET, resource_current_name: 'openai_main', metadata: { value_version: 3 } }),
  entry(2, { resource_type: 'workspace', resource_id: workspace.id, resource_current_name: null, metadata: { fields: ['name'] } }),
  entry(3, { action: 'custom.plugin_event', action_label: null, actor_id: null, actor_label: '系统', metadata: { title: '<img src=x onerror="window.__auditPwned=1">' } }),
  entry(4, { action: 'agents.ssh.install_started', action_label: '开始部署智能体（已发起）', actor_id: GONE, actor_label: null, resource_type: 'agent_instance', resource_id: INSTANCE, metadata: { name: '开发助手' } }),
]
// 不在已加载列表里的一条（例如通知或分享来的链接）
const extra = entry(9, { action: 'agents.secret.rotated', action_label: '轮换密钥', actor_id: GONE, actor_label: null, resource_type: 'agent_secret', resource_id: SECRET_2, resource_current_name: 'stripe_live', metadata: { value_version: 3 } })
const MEMBERS: Schema['MemberOut'][] = [
  { user_id: OWNER, email: 'brian@example.com', display_name: 'brian', role: 'owner', joined_at: at(9000), you: true },
  { user_id: ALEX, email: 'alex@example.com', display_name: 'Alex', role: 'admin', joined_at: at(9000), you: false },
]
const CSV = '\uFEFFid,created_at_utc,action\r\n' + `${rows[0]!.id},2026-11-09T06:27:16.000000Z,agents.secret.rotated\r\n`
const csv = () => new Response(CSV, { headers: { 'Content-Type': 'text/csv; charset=utf-8', 'Content-Disposition': 'attachment; filename="audit-logs-20261109.csv"', 'Cache-Control': 'no-store' } })

// 假后端：第一页两条、带 next_cursor；第二页两条、到底。详情按 ID 读，不在这里的记录 404
function setup(options: { role?: Schema['WorkspaceOut']['role']; workspaces?: Schema['WorkspaceOut'][]; handler?: (r: Request) => Response | Promise<Response> | undefined } = {}) {
  return mockApi(async (r) => {
    const custom = await options.handler?.(r)
    if (custom) return custom
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: options.workspaces ?? [{ ...workspace, role: options.role ?? 'owner' }] })
    if (p === `/api/v1/workspaces/${workspace.id}/members`) return Response.json({ items: MEMBERS, next_cursor: null, total_estimate: null })
    if (p === '/api/v1/audit-logs') {
      const second = params(r).get('cursor') === 'page-2'
      return Response.json(second ? { items: [rows[2], rows[3]], next_cursor: null, total_estimate: null } : { items: [rows[0], rows[1]], next_cursor: 'page-2', total_estimate: null })
    }
    if (p === '/api/v1/audit-logs/export') return csv()
    const one = /^\/api\/v1\/audit-logs\/([^/]+)$/.exec(p)
    if (one) {
      const found = [...rows, extra].find((e) => e.id === one[1])
      return found ? Response.json(found) : fail(404, 'NOT_FOUND')
    }
    return undefined
  })
}
const lists = (requests: Request[]) => requests.filter((r) => path(r) === '/api/v1/audit-logs')
const exports = (requests: Request[]) => requests.filter((r) => path(r) === '/api/v1/audit-logs/export')
const span = (r: Request) => Date.parse(params(r).get('before')!) - Date.parse(params(r).get('after')!)
async function table() {
  const region = await screen.findByRole('region', { name: '事件记录' })
  await within(region).findByText('轮换密钥')
  return region
}
async function loadMore(region: HTMLElement) {
  await fireEvent.click(within(region).getByRole('button', { name: '加载更多' }))
  await within(region).findByText('custom.plugin_event')
}
const detail = () => screen.findByRole('complementary', { name: '事件详情' })
// jsdom 的 Blob 没有 arrayBuffer()：用 FileReader 读出字节
const bytesOf = (blob: Blob) => new Promise<Uint8Array>((resolve, reject) => {
  const reader = new FileReader()
  reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer))
  reader.onerror = () => reject(reader.error)
  reader.readAsArrayBuffer(blob)
})
function stubDownloads() {
  const blobs: Blob[] = [], downloads: { href: string; download: string }[] = []
  const createObjectURL = vi.fn((blob: Blob) => { blobs.push(blob); return `blob:audit-${blobs.length}` })
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloads.push({ href: this.getAttribute('href') ?? '', download: this.download }) })
  return { blobs, downloads, createObjectURL, revokeObjectURL }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); delete (window as { __auditPwned?: unknown }).__auditPwned })

describe('audit center redesign', () => {
  it('loads the last 7 days from the server, shows only the loaded count and pages with next_cursor', async () => {
    const requests = setup()
    await openApp('/settings/audit')
    expect(await screen.findByRole('heading', { level: 1, name: '审计日志' })).toBeVisible()
    const region = await table()
    expect(within(region).getByText('当前已加载 2 条')).toBeVisible()
    expect(screen.getByRole('main')).not.toHaveTextContent(/共\s*\d+\s*条/)
    const first = lists(requests)[0]!
    expect(params(first).get('after')).toMatch(/Z$/)
    expect(span(first)).toBe(7 * DAY + CLOCK_ALLOWANCE_MS)
    expect(Math.abs(Date.parse(params(first).get('before')!) - CLOCK_ALLOWANCE_MS - Date.now())).toBeLessThan(10_000)
    expect(params(first).has('cursor')).toBe(false)
    expect(first.headers.get('X-Workspace-Id')).toBe('workspace-a')

    await loadMore(region)
    expect(within(region).getByText('当前已加载 4 条')).toBeVisible()
    const second = lists(requests)[1]!
    expect(params(second).get('cursor')).toBe('page-2')
    // 同一组条件：时间上下界不变，第二页接在第一页后面
    expect(params(second).get('after')).toBe(params(first).get('after'))
    expect(params(second).get('before')).toBe(params(first).get('before'))
    expect(within(region).queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument()
    expect(lists(requests)).toHaveLength(2)

    // 刷新记录：按“现在”重新算时间范围，从第一页重新读，不保留旧的第二页
    await fireEvent.click(screen.getByRole('button', { name: '刷新记录' }))
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(3))
    const again = lists(requests)[2]!
    expect(params(again).has('cursor')).toBe(false)
    expect(span(again)).toBe(7 * DAY + CLOCK_ALLOWANCE_MS)
    expect(Date.parse(params(again).get('before')!)).toBeGreaterThanOrEqual(Date.parse(params(first).get('before')!))
    await vi.waitFor(() => expect(within(region).getByText('当前已加载 2 条')).toBeVisible())
    expect(within(region).queryByText('custom.plugin_event')).not.toBeInTheDocument()
  })

  it('sends every filter to the server and starts again from the first page', async () => {
    const requests = setup()
    await openApp('/settings/audit')
    const region = await table()
    await loadMore(region)

    await fireEvent.update(screen.getByLabelText('时间范围'), '24h')
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(3))
    expect(params(lists(requests)[2]!).has('cursor')).toBe(false)
    expect(span(lists(requests)[2]!)).toBe(DAY + CLOCK_ALLOWANCE_MS)
    // 换了条件只显示新条件的第一页，不拼上旧条件已加载的第二页
    await vi.waitFor(() => expect(within(region).getByText('当前已加载 2 条')).toBeVisible())
    expect(within(region).queryByText('custom.plugin_event')).not.toBeInTheDocument()

    const actor = screen.getByLabelText('操作者')
    expect(within(actor).getByRole('option', { name: '全部操作者' })).toBeInTheDocument()
    expect(within(actor).getByRole('option', { name: 'Alex' })).toBeInTheDocument()
    await fireEvent.update(actor, 'system')
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(4))
    expect(params(lists(requests)[3]!).get('system_only')).toBe('true')
    expect(params(lists(requests)[3]!).has('actor_id')).toBe(false)

    await fireEvent.update(actor, ALEX)
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(5))
    expect(params(lists(requests)[4]!).get('actor_id')).toBe(ALEX)
    expect(params(lists(requests)[4]!).has('system_only')).toBe(false)

    await fireEvent.update(screen.getByLabelText('事件类型'), 'agents.secret.')
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(6))
    expect(params(lists(requests)[5]!).get('action_prefix')).toBe('agents.secret.')

    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索审计记录' }), 'openai')
    await vi.waitFor(() => expect(params(lists(requests).at(-1)!).get('q')).toBe('openai'))
    const last = params(lists(requests).at(-1)!)
    expect(last.get('actor_id')).toBe(ALEX)
    expect(last.get('action_prefix')).toBe('agents.secret.')
    expect(span(lists(requests).at(-1)!)).toBe(DAY + CLOCK_ALLOWANCE_MS)
    for (const r of lists(requests).slice(2)) expect(params(r).has('cursor')).toBe(false)
  })

  it('restores filters from the URL on back and forward', async () => {
    const requests = setup()
    const { router } = await openApp('/settings/audit?range=30d&actor=system&type=auth.&q=login')
    await screen.findByRole('region', { name: '事件记录' })
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(1))
    const first = params(lists(requests)[0]!)
    expect(first.get('system_only')).toBe('true')
    expect(first.get('action_prefix')).toBe('auth.')
    expect(first.get('q')).toBe('login')
    expect(span(lists(requests)[0]!)).toBe(30 * DAY + CLOCK_ALLOWANCE_MS)
    expect(screen.getByLabelText('时间范围')).toHaveValue('30d')
    expect(screen.getByLabelText('操作者')).toHaveValue('system')
    expect(screen.getByLabelText('事件类型')).toHaveValue('auth.')
    expect(screen.getByRole('searchbox', { name: '搜索审计记录' })).toHaveValue('login')

    await fireEvent.update(screen.getByLabelText('时间范围'), 'all')
    await vi.waitFor(() => expect(router.currentRoute.value.query.range).toBe('all'))
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(2))
    expect(params(lists(requests)[1]!).has('after')).toBe(false)
    expect(params(lists(requests)[1]!).has('before')).toBe(false)

    router.back()
    await vi.waitFor(() => expect(screen.getByLabelText('时间范围')).toHaveValue('30d'))
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(3))
    expect(params(lists(requests)[2]!).get('system_only')).toBe('true')
    expect(span(lists(requests)[2]!)).toBe(30 * DAY + CLOCK_ALLOWANCE_MS)
    router.forward()
    await vi.waitFor(() => expect(screen.getByLabelText('时间范围')).toHaveValue('all'))
    expect(screen.getByRole('searchbox', { name: '搜索审计记录' })).toHaveValue('login')
  })

  it('ignores filter values in a link that the page does not offer', async () => {
    const requests = setup()
    await openApp(`/settings/audit?range=90d&actor=${encodeURIComponent('<b>x</b>')}&type=evil.&q=${'x'.repeat(150)}&id=not-a-uuid`)
    await table()
    const first = params(lists(requests)[0]!)
    expect(span(lists(requests)[0]!)).toBe(7 * DAY + CLOCK_ALLOWANCE_MS)
    expect(first.has('actor_id')).toBe(false)
    expect(first.has('system_only')).toBe(false)
    expect(first.has('action_prefix')).toBe(false)
    expect(first.get('q')).toBe('x'.repeat(100))
    expect(await within(await detail()).findByRole('alert')).toHaveTextContent('不存在')
    expect(requests.some((r) => path(r).startsWith('/api/v1/audit-logs/not-a-uuid'))).toBe(false)
  })

  it('opens ?id= with a GET by id when the record is not loaded, marks the current name and links owners to the secret', async () => {
    const requests = setup()
    await openApp(`/settings/audit?id=${extra.id}`)
    const aside = await detail()
    expect(await within(aside).findByRole('heading', { name: '轮换密钥' })).toBeVisible()
    const read = requests.find((r) => path(r) === `/api/v1/audit-logs/${extra.id}`)!
    expect(read.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(within(aside).getByText('agents.secret.rotated')).toBeVisible()
    expect(within(aside).getByText('stripe_live')).toBeVisible()
    expect(within(aside).getByText('当前名称')).toBeVisible()
    // 操作者已不是成员：退回显示 ID
    expect(within(aside).getByText(GONE)).toBeVisible()
    expect(within(aside).getByText('agent_secret')).toBeVisible()
    expect(within(aside).getByText(SECRET_2)).toBeVisible()
    // 只有新版本号：照记录显示，不推导旧版本或“轮换成功”
    expect(within(aside).getByText('新密钥版本').closest('div')).toHaveTextContent('新密钥版本3')
    expect(aside).not.toHaveTextContent(/旧版本|原版本|版本 2|成功|失败/)
    expect(within(aside).getByText('记录不包含密钥原文。')).toBeVisible()
    expect(within(aside).getByText(/"value_version": 3/)).toBeInTheDocument()
    expect(within(aside).getByRole('link', { name: /查看兼容凭据/ })).toHaveAttribute('href', `/settings/model-connections?tab=compat&secret=${SECRET_2}`)
    expect(aside).not.toHaveTextContent(/IP|ip_hash|不可篡改/)
    expect(screen.queryByRole('button', { name: /删除|编辑/ })).not.toBeInTheDocument()
  })

  it('does not offer owner-only links to admins', async () => {
    setup({ role: 'admin' })
    await openApp(`/settings/audit?id=${rows[0]!.id}`)
    const aside = await detail()
    expect(await within(aside).findByRole('heading', { name: '轮换密钥' })).toBeVisible()
    expect(within(aside).queryByRole('link', { name: /兼容凭据/ })).not.toBeInTheDocument()
  })

  it('clears stale details when the linked record does not exist or is no longer readable', async () => {
    const requests = setup({ handler: (r) => (path(r) === `/api/v1/audit-logs/${rows[1]!.id}` ? fail(404, 'NOT_FOUND') : undefined) })
    const { router } = await openApp(`/settings/audit?id=${rows[0]!.id}`)
    let aside = await detail()
    expect(await within(aside).findByRole('heading', { name: '轮换密钥' })).toBeVisible()

    // 已加载的一行，但按 ID 读到 404（已无权或已不在这个工作空间）：不留列表里的旧内容
    await router.push(`/settings/audit?id=${rows[1]!.id}`)
    aside = await detail()
    expect(await within(aside).findByRole('alert')).toHaveTextContent('不存在')
    expect(aside).not.toHaveTextContent('修改工作空间设置')
    expect(aside).not.toHaveTextContent('openai_main')

    await router.push(`/settings/audit?id=${MISSING}`)
    aside = await detail()
    await vi.waitFor(() => expect(requests.some((r) => path(r) === `/api/v1/audit-logs/${MISSING}`)).toBe(true))
    expect(await within(aside).findByRole('alert')).toHaveTextContent('不存在')
    expect(aside).not.toHaveTextContent('轮换密钥')
  })

  it('shows unknown actions raw, metadata only as text, the system actor and removed members by ID', async () => {
    setup()
    const { router } = await openApp('/settings/audit')
    const region = await table()
    await loadMore(region)
    const secret = within(region).getByText('轮换密钥').closest('tr')!
    expect(within(secret).getByText('agents.secret.rotated')).toBeVisible()
    expect(within(secret).getByText('openai_main')).toBeVisible()
    expect(within(secret).getByText('当前名称')).toBeVisible()
    const unknown = within(region).getByText('custom.plugin_event').closest('tr')!
    expect(within(unknown).getByText('系统')).toBeVisible()
    const deploy = within(region).getByText('开始部署智能体（已发起）').closest('tr')!
    expect(within(deploy).getByTitle(GONE)).toBeVisible()
    expect(deploy).not.toHaveTextContent(/成功|完成/)
    expect(within(region).queryByRole('columnheader', { name: /结果|IP/ })).not.toBeInTheDocument()

    await fireEvent.click(within(unknown).getByRole('button', { name: '详情' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.id).toBe(rows[2]!.id))
    const aside = await detail()
    expect(await within(aside).findByRole('heading', { name: 'custom.plugin_event' })).toBeVisible()
    expect(within(aside).getByText('系统')).toBeVisible()
    expect(within(aside).getAllByText(/<img src=x onerror=/).length).toBeGreaterThan(0)
    expect(document.querySelector('img[src="x"]')).toBeNull()
    expect((window as { __auditPwned?: unknown }).__auditPwned).toBeUndefined()
    expect(screen.getByRole('main')).not.toHaveTextContent(/不可篡改|ip_hash/)
  })

  it('exports the current server filters instead of the loaded page and only says the file was generated', async () => {
    const files = stubDownloads()
    const requests = setup()
    await openApp('/settings/audit?type=agents.secret.&actor=system&q=openai')
    const region = await table()
    await loadMore(region)
    await fireEvent.click(screen.getByRole('button', { name: '导出当前筛选' }))
    expect(await screen.findByText(/文件已生成，开始下载/)).toBeVisible()
    expect(exports(requests)).toHaveLength(1)
    const list = params(lists(requests)[0]!), sent = params(exports(requests)[0]!)
    for (const key of ['after', 'before', 'system_only', 'action_prefix', 'q']) expect(sent.get(key)).toBe(list.get(key))
    expect(sent.has('cursor')).toBe(false)
    expect(sent.has('limit')).toBe(false)
    expect(exports(requests)[0]!.headers.get('X-Workspace-Id')).toBe('workspace-a')
    // 文件就是服务端生成的 CSV（带 UTF-8 BOM），不是浏览器里已加载的四行
    expect(files.createObjectURL).toHaveBeenCalledTimes(1)
    const bytes = await bytesOf(files.blobs[0]!)
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf])
    expect(new TextDecoder().decode(bytes)).toContain('id,created_at_utc,action')
    expect(files.downloads).toEqual([{ href: 'blob:audit-1', download: 'audit-logs-20261109.csv' }])
    await vi.waitFor(() => expect(files.revokeObjectURL).toHaveBeenCalledWith('blob:audit-1'), { timeout: 3000 })
    expect(screen.getByRole('main')).not.toHaveTextContent(/下载完成|已下载/)
  })

  it('explains export failures and creates no file', async () => {
    const files = stubDownloads()
    let reply: () => Response = () => Response.json({ error: { code: 'AUDIT_EXPORT_TOO_LARGE', message: '符合条件的记录超过 10000 条', request_id: 'req-test', details: { limit: 10000 } } }, { status: 422 })
    const requests = setup({ handler: (r) => (path(r) === '/api/v1/audit-logs/export' ? reply() : undefined) })
    await openApp('/settings/audit')
    await table()
    const button = screen.getByRole('button', { name: '导出当前筛选' })
    await fireEvent.click(button)
    const tooLarge = await screen.findByText(/超过 10000 条/)
    expect(tooLarge).toHaveAttribute('role', 'alert')
    expect(tooLarge).toHaveTextContent('缩小')

    reply = () => fail(403, 'FORBIDDEN')
    await vi.waitFor(() => expect(button).toBeEnabled())
    await fireEvent.click(button)
    expect(await screen.findByText(/没有导出审计日志的权限/)).toHaveAttribute('role', 'alert')

    reply = () => { throw new TypeError('network down') }
    await vi.waitFor(() => expect(button).toBeEnabled())
    await fireEvent.click(button)
    await vi.waitFor(() => expect(exports(requests)).toHaveLength(3))
    expect(await screen.findByText(/网络连接失败/)).toHaveAttribute('role', 'alert')
    expect(files.createObjectURL).not.toHaveBeenCalled()
    expect(files.downloads).toHaveLength(0)
    expect(screen.queryByText(/文件已生成/)).not.toBeInTheDocument()
  })

  it('asks to narrow "全部时间" before exporting instead of calling the server', async () => {
    const files = stubDownloads()
    const requests = setup()
    await openApp('/settings/audit?range=all')
    await table()
    expect(params(lists(requests)[0]!).has('after')).toBe(false)
    await fireEvent.click(screen.getByRole('button', { name: '导出当前筛选' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('31 天')
    expect(exports(requests)).toHaveLength(0)
    expect(files.createObjectURL).not.toHaveBeenCalled()
  })

  it('drops a late detail response after switching workspace', async () => {
    let finish: ((response: Response) => void) | undefined
    const second = { ...workspace, id: 'workspace-b', name: '空间 B' }
    setup({
      workspaces: [workspace, second],
      handler: (r) => {
        if (path(r) !== `/api/v1/audit-logs/${extra.id}`) return undefined
        if (r.headers.get('X-Workspace-Id') === 'workspace-a') return new Promise<Response>((resolve) => { finish = resolve })
        return fail(404, 'NOT_FOUND')
      },
    })
    await openApp(`/settings/audit?id=${extra.id}`)
    await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    const aside = await detail()
    expect(await within(aside).findByRole('alert')).toHaveTextContent('不存在')
    finish!(Response.json(extra))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(aside).not.toHaveTextContent('stripe_live')
    expect(screen.getByRole('main')).toHaveTextContent('空间 B')
  })
})
