import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// AI 审批中心改版（T-0191，效果图 01；计划 A2）：汇总卡来自服务端汇总、筛选与到期排序交给服务端、
// 倒计时按 as_of 校时、固定参数只读、批准后如实显示执行结果、响应丢失先核对、外部文字只当文本。
type Exec = Schema['AiToolExecutionOut']
type Summary = Schema['AiExecutionSummaryOut']
const path = (r: Request) => new URL(r.url).pathname
const params = (r: Request) => new URL(r.url).searchParams
const LIST = '/api/v1/ai/tool-executions', SUMMARY = '/api/v1/ai/tool-executions/summary'
const iso = (ms: number) => new Date(ms).toISOString()
const hash = 'ab12cd34'.repeat(8)

const execution = (over: Partial<Exec> = {}): Exec => ({
  id: 'exec-1', instance_id: 'inst-a', instance_name: '运维助手', acting_user_id: 'user-a', tool_name: 'update_task', risk_level: 'write', status: 'pending_approval',
  summary: '修改任务「接口联调」：标题', arguments: { task_id: 'task-7', title: '接口联调（二期）' }, arguments_hash: hash, request_id: 'req-1',
  source: { conversation_id: 'si_alpha', message_server_id: 'srv-9', triggered_by_im_id: 'ws_owner', triggered_by_name: 'Owner', inferred: false },
  result_summary: null, error_code: null, decision_reason: null, decided_by: null, decided_at: null, executed_at: null,
  expires_at: iso(Date.now() + 10 * 60_000), created_at: iso(Date.now() - 5 * 60_000), version: 3, ...over,
})
const summaryOf = (over: Partial<Summary> = {}): Summary => ({
  pending_count: 1, expiring_count: 0, critical_pending_count: 0, exception_count: 0, as_of: iso(Date.now()), period: '24h', expiring_window_seconds: 180,
  instances: [{ id: 'inst-a', name: '运维助手' }], tools: ['update_task'], ...over,
})

type Handler = (r: Request, store: Map<string, Exec>) => Response | Promise<Response | undefined> | undefined
function setup({ items = [execution()], role = 'owner', summary = {}, handler }: { items?: Exec[]; role?: Schema['WorkspaceOut']['role']; summary?: Partial<Summary> | (() => Partial<Summary>); handler?: Handler } = {}) {
  const store = new Map(items.map(e => [e.id, e]))
  const requests = mockApi(async r => {
    const extra = await handler?.(r, store)
    if (extra) return extra
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: [{ ...workspace, role }] })
    if (p === SUMMARY) return Response.json(summaryOf(typeof summary === 'function' ? summary() : summary))
    if (p === LIST) {
      const statuses = params(r).getAll('status')
      const list = [...store.values()].filter(e => !statuses.length || statuses.includes(e.status))
      return Response.json({ items: list, next_cursor: null, total_estimate: list.length, pending_count: 0 })
    }
    const one = /^\/api\/v1\/ai\/tool-executions\/([^/]+)$/.exec(p)
    if (one && r.method === 'GET') return store.has(one[1]) ? Response.json(store.get(one[1])) : fail(404, 'NOT_FOUND')
  })
  return { requests, store }
}
const lists = (requests: Request[]) => requests.filter(r => path(r) === LIST && r.method === 'GET')
const summaries = (requests: Request[]) => requests.filter(r => path(r) === SUMMARY)
const posts = (requests: Request[], verb?: 'approve' | 'reject') => requests.filter(r => r.method === 'POST' && (!verb || path(r).endsWith(`/${verb}`)))
const gets = (requests: Request[], id: string) => requests.filter(r => r.method === 'GET' && path(r) === `${LIST}/${id}`)
const tile = (name: string) => screen.getByText(name, { selector: '.ac-tile-name' }).closest('.ag-tile') as HTMLElement
const detail = () => screen.findByRole('region', { name: '审批详情' })
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('approval center redesign', () => {
  it('takes the cards and counts from the summary endpoint, not from the loaded page', async () => {
    const { requests } = setup({
      items: [execution(), execution({ id: 'exec-2', summary: '新建任务「容器负载核验」' })],
      summary: { pending_count: 37, expiring_count: 4, critical_pending_count: 2, exception_count: 5 },
    })
    await openApp('/ai')
    expect(await screen.findByRole('heading', { level: 1, name: 'AI 审批' })).toBeVisible()
    await vi.waitFor(() => expect(within(tile('待审批')).getByText('37')).toBeVisible())
    expect(within(tile('即将到期')).getByText('4')).toBeVisible()
    expect(within(tile('即将到期')).getByText(/3 分钟内/)).toBeVisible()
    expect(within(tile('高危待审批')).getByText('2')).toBeVisible()
    expect(within(tile('执行异常')).getByText('5')).toBeVisible()
    expect(within(tile('执行异常')).getByText(/失败 \/ 结果未知/)).toBeVisible()
    // 每张卡写明范围（默认最近 24 小时发起）
    for (const name of ['待审批', '即将到期', '高危待审批', '执行异常']) expect(within(tile(name)).getByText(/最近 24 小时发起/)).toBeVisible()
    expect(screen.getByRole('tab', { name: '待审批 37' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: '执行异常 5' })).toBeVisible()
    expect(await screen.findByRole('heading', { name: /待处理请求 · 37/ })).toBeVisible()
    expect(await screen.findAllByRole('row', { name: /^申请：/ })).toHaveLength(2)
    // 汇总不带状态；列表是待审批 + 按到期时间；两边的时间范围一致
    const sum = summaries(requests)[0]
    expect(params(sum).get('period')).toBe('24h')
    expect(params(sum).getAll('status')).toEqual([])
    const list = lists(requests)[0]
    expect(params(list).getAll('status')).toEqual(['pending_approval'])
    expect(params(list).get('sort')).toBe('expires_asc')
    expect(params(list).get('period')).toBe('24h')
    expect(list.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })

  it('sends every filter to the server for both the summary and the list and starts paging again', async () => {
    const { requests } = setup({
      summary: { instances: [{ id: 'inst-a', name: '运维助手' }, { id: 'inst-b', name: '开发助手' }], tools: ['update_task', 'create_task'] },
      handler: r => path(r) === LIST && !params(r).get('cursor') && params(r).get('tool_name') === null && params(r).get('instance_id') === null
        ? Response.json({ items: [execution()], next_cursor: 'cur-1', total_estimate: null, pending_count: 0 }) : undefined,
    })
    await openApp('/ai')
    await fireEvent.click(await screen.findByRole('button', { name: '加载更多' }))
    await vi.waitFor(() => expect(lists(requests).some(r => params(r).get('cursor') === 'cur-1')).toBe(true))
    const filters = screen.getByRole('region', { name: '筛选' })
    await vi.waitFor(() => expect(within(filters).getByRole('option', { name: '开发助手' })).toBeInTheDocument())
    await fireEvent.update(within(filters).getByLabelText('智能体'), 'inst-b')
    await vi.waitFor(() => expect(params(lists(requests).at(-1)!).get('instance_id')).toBe('inst-b'))
    expect(params(lists(requests).at(-1)!).get('cursor')).toBeNull()
    expect(params(summaries(requests).at(-1)!).get('instance_id')).toBe('inst-b')
    await fireEvent.update(within(filters).getByLabelText('工具'), 'create_task')
    await fireEvent.update(within(filters).getByLabelText('类型'), 'critical')
    await fireEvent.update(within(filters).getByLabelText('排序'), 'created_desc')
    await fireEvent.update(screen.getByLabelText('统计范围'), '7d')
    await vi.waitFor(() => expect(params(lists(requests).at(-1)!).get('period')).toBe('7d'))
    const list = params(lists(requests).at(-1)!), sum = params(summaries(requests).at(-1)!)
    expect([list.get('instance_id'), list.get('tool_name'), list.getAll('risk_level'), list.get('sort'), list.get('cursor')]).toEqual(['inst-b', 'create_task', ['critical'], 'created_desc', null])
    expect([sum.get('instance_id'), sum.get('tool_name'), sum.getAll('risk_level'), sum.get('period')]).toEqual(['inst-b', 'create_task', ['critical'], '7d'])
    // 汇总从不带状态与排序；卡片跟着写新的范围
    expect(summaries(requests).every(r => !params(r).has('status') && !params(r).has('sort'))).toBe(true)
    await vi.waitFor(() => expect(within(tile('待审批')).getByText(/最近 7 天发起/)).toBeVisible())
  })

  it('drops a stale list request when the filters change', async () => {
    let held = false
    const { requests } = setup({
      summary: { instances: [{ id: 'inst-a', name: '运维助手' }, { id: 'inst-b', name: '开发助手' }] },
      handler: r => {
        if (path(r) === LIST && !params(r).get('instance_id') && !held) { held = true; return new Promise<Response>(() => {}) }
      },
    })
    await openApp('/ai')
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(1))
    const filters = screen.getByRole('region', { name: '筛选' })
    await vi.waitFor(() => expect(within(filters).getByRole('option', { name: '开发助手' })).toBeInTheDocument())
    await fireEvent.update(within(filters).getByLabelText('智能体'), 'inst-b')
    await vi.waitFor(() => expect(lists(requests)).toHaveLength(2))
    await vi.waitFor(() => expect(lists(requests)[0].signal.aborted).toBe(true))
    expect(await screen.findByRole('row', { name: '申请：修改任务「接口联调」：标题' })).toBeVisible()
  })

  it('corrects the countdown with the server clock and, at zero, disables approval and reads again', async () => {
    // 服务端比本机快 10 分钟：不校时会显示 11:30
    const skew = 10 * 60_000, server = () => Date.now() + skew
    const soon = execution({ id: 'exec-soon', summary: '新建日程「项目进度同步」', tool_name: 'create_calendar_event', expires_at: iso(server() + 90_000) })
    const due = execution({ id: 'exec-due', summary: '收藏「Docker 运行资料」', tool_name: 'create_bookmark', expires_at: iso(server() + 2_500) })
    const { requests } = setup({ items: [due, soon], summary: () => ({ as_of: iso(server()), pending_count: 2 }) })
    await openApp('/ai?id=exec-due')
    const next = await detail()
    await vi.waitFor(() => expect(within(next).getByRole('heading', { name: '收藏「Docker 运行资料」' })).toBeVisible())
    await vi.waitFor(() => expect(within(next).getByText(/剩余 00:0[0-3]/)).toBeVisible())
    expect(within(next).queryByText(/剩余 1\d:/)).not.toBeInTheDocument()
    const row = screen.getByRole('row', { name: '申请：新建日程「项目进度同步」' })
    // 只看倒计时：行里“截止 HH:MM:SS”的 <time> 是钟点，截止时间恰好是 01:29、01:30（或 11 点多）时也会匹配
    expect(within(row).getByText(/01:(29|30)/, { ignore: 'script, style, time' })).toBeVisible()
    expect(within(row).queryByText(/11:/, { ignore: 'script, style, time' })).not.toBeInTheDocument()
    expect(within(next).getByRole('button', { name: '批准并执行' })).toBeEnabled()
    const before = gets(requests, 'exec-due').length, listsBefore = lists(requests).length
    await vi.waitFor(() => expect(within(next).getByRole('button', { name: '批准并执行' })).toBeDisabled(), { timeout: 5000 })
    expect(within(next).getByText(/已到期，不能再批准/)).toBeVisible()
    // 归零后重新读取这条记录、列表与汇总；不发批准
    await vi.waitFor(() => expect(gets(requests, 'exec-due').length).toBeGreaterThan(before))
    await vi.waitFor(() => expect(lists(requests).length).toBeGreaterThan(listsBefore))
    await fireEvent.click(within(next).getByRole('button', { name: '批准并执行' }))
    expect(posts(requests)).toHaveLength(0)
  })

  it('labels an inferred source and does not make one up', async () => {
    const inferred = execution({ id: 'exec-a', summary: '甲', source: { conversation_id: 'si_alpha', message_server_id: null, triggered_by_im_id: 'ws_owner', triggered_by_name: 'Owner', inferred: true } })
    const group = execution({ id: 'exec-b', summary: '乙', source: { conversation_id: 'sg_team', message_server_id: 'srv-2', triggered_by_im_id: 'ws_bob', triggered_by_name: 'Bob', inferred: false } })
    const none = execution({ id: 'exec-c', summary: '丙', source: { conversation_id: null, message_server_id: null, triggered_by_im_id: null, inferred: true } })
    setup({ items: [inferred, group, none] })
    await openApp('/ai?id=exec-a')
    expect(within(await screen.findByRole('row', { name: '申请：甲' })).getByText('IM 会话')).toBeVisible()
    expect(within(screen.getByRole('row', { name: '申请：甲' })).getByText('来源推断')).toBeVisible()
    expect(within(screen.getByRole('row', { name: '申请：乙' })).getByText('IM 群聊')).toBeVisible()
    expect(within(screen.getByRole('row', { name: '申请：乙' })).queryByText('来源推断')).not.toBeInTheDocument()
    expect(within(screen.getByRole('row', { name: '申请：丙' })).getByText('来源未知')).toBeVisible()
    const panel = await detail()
    await vi.waitFor(() => expect(within(panel).getByText('来源推断')).toBeVisible())
    expect(within(panel).getByText(/根据实例最近收到的消息推断/)).toBeVisible()
    expect(within(panel).getByText('Owner')).toBeVisible()
    expect(within(panel).getByRole('link', { name: 'IM 会话' })).toHaveAttribute('href', '/im?c=si_alpha')
    // 没有 AI 给的申请理由或修改前后的值：不编造
    expect(within(panel).queryByText(/申请理由|修改前|原值/)).not.toBeInTheDocument()
  })

  it('shows a known tool as readable fields with the raw JSON folded, and an unknown tool as safe JSON', async () => {
    const known = execution({ id: 'exec-k', summary: '修改任务「接口联调」：标题' })
    const unknown = execution({ id: 'exec-u', summary: 'acme_widget_sync', tool_name: 'acme_widget_sync', arguments: { nested: { depth: 2 }, list: [1, 2], token: '***' } })
    setup({ items: [known, unknown] })
    await openApp('/ai?id=exec-k')
    let panel = await detail()
    const fields = await within(panel).findByRole('list', { name: '提交参数' })
    expect(within(fields).getByText('标题')).toBeVisible()
    expect(within(fields).getByText('接口联调（二期）')).toBeVisible()
    expect(within(fields).getByText('任务 ID')).toBeVisible()
    expect(within(panel).getByText('固定参数')).toBeVisible()
    const raw = within(panel).getByText('查看原始参数').closest('details')!
    expect(raw).not.toHaveAttribute('open')
    expect(raw.querySelector('pre')!.textContent).toContain('"task_id": "task-7"')
    // 摘要只是服务端算的 SHA-256，不当成页面已校验
    expect(within(panel).getByText(/参数摘要/)).toBeVisible()
    expect(within(panel).getByTitle(hash)).toBeVisible()
    expect(within(panel).getByRole('link', { name: /前往任务/ })).toHaveAttribute('href', '/tasks?task=task-7')
    await fireEvent.click(within(await screen.findByRole('row', { name: '申请：acme_widget_sync' })).getByRole('button', { name: /查看/ }))
    await screen.findByRole('heading', { level: 2, name: 'acme_widget_sync' })
    panel = await detail()
    expect(within(panel).queryByRole('list', { name: '提交参数' })).not.toBeInTheDocument()
    const json = panel.querySelector('pre.ai-arguments')!
    expect(json).toBeVisible()
    expect(json.textContent).toContain('"nested": {')
    expect(json.textContent).toContain('"token": "***"')
  })

  it('keeps the historic name of a deleted instance and does not link it to another one', async () => {
    setup({ items: [execution({ instance_id: null, instance_name: '旧助手' })], summary: { instances: [] } })
    await openApp('/ai?id=exec-1')
    const row = await screen.findByRole('row', { name: '申请：修改任务「接口联调」：标题' })
    expect(within(row).getByText('旧助手')).toBeVisible()
    const panel = await detail()
    await vi.waitFor(() => expect(within(panel).getByText('旧助手')).toBeVisible())
    expect(within(panel).getByText(/已删除/)).toBeVisible()
    expect(panel.querySelector('a[href^="/agents"]')).toBeNull()
  })

  it('asks a critical approval for the password, clears it after each attempt and on switching records, and rejects without it', async () => {
    const first = execution({ id: 'exec-x', risk_level: 'critical', tool_name: 'delete_calendar_event', summary: '删除日程「旧版发布检查」', arguments: { event_id: 'evt-1' } })
    const second = execution({ id: 'exec-y', risk_level: 'critical', tool_name: 'delete_task', summary: '删除任务「旧任务」', arguments: { task_id: 'task-9' } })
    const { requests } = setup({
      items: [first, second],
      handler: (r, store) => {
        if (r.method === 'POST' && path(r).endsWith('/approve')) return fail(403, 'STEP_UP_FAILED')
        if (r.method === 'POST' && path(r).endsWith('/reject')) { const rejected = { ...store.get('exec-y')!, status: 'rejected' as const, version: 4, decided_at: iso(Date.now()) }; store.set('exec-y', rejected); return Response.json(rejected) }
      },
    })
    await openApp('/ai?id=exec-x')
    let panel = await detail()
    expect(await within(panel).findByText('高危操作需验证你的工作台登录密码。')).toBeVisible()
    await fireEvent.click(within(panel).getByRole('button', { name: '批准并执行' }))
    expect(await within(panel).findByRole('alert')).toHaveTextContent('需要输入你的登录密码')
    expect(posts(requests)).toHaveLength(0)
    const field = within(panel).getByLabelText('你的登录密码') as HTMLInputElement
    expect(field).toHaveAttribute('type', 'password')
    expect(field).toHaveAttribute('maxlength', '128')
    await fireEvent.click(within(panel).getByRole('button', { name: '显示密码' }))
    expect(field).toHaveAttribute('type', 'text')
    await fireEvent.update(field, 'wrong')
    await fireEvent.click(within(panel).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(within(panel).getByRole('alert')).toHaveTextContent('密码不正确'))
    expect(await posts(requests, 'approve')[0].clone().json()).toEqual({ password: 'wrong' })
    expect(posts(requests, 'approve')[0].headers.get('If-Match')).toBe('"3"')
    expect(field.value).toBe('')
    expect(field).toHaveAttribute('type', 'password')
    // 换到另一条：输入过的密码不跟过去
    await fireEvent.update(field, 'typed-but-not-sent')
    await fireEvent.click(within(await screen.findByRole('row', { name: '申请：删除任务「旧任务」' })).getByRole('button', { name: /查看/ }))
    await screen.findByRole('heading', { level: 2, name: '删除任务「旧任务」' })
    panel = await detail()
    expect((within(panel).getByLabelText('你的登录密码') as HTMLInputElement).value).toBe('')
    // 拒绝不需要密码
    await fireEvent.click(within(panel).getByRole('button', { name: '拒绝时填写原因' }))
    await fireEvent.update(within(panel).getByLabelText('拒绝原因（可选）'), ' 不需要 ')
    await fireEvent.click(within(panel).getByRole('button', { name: '拒绝申请' }))
    await vi.waitFor(() => expect(posts(requests, 'reject')).toHaveLength(1))
    expect(await posts(requests, 'reject')[0].clone().json()).toEqual({ reason: '不需要' })
    expect(posts(requests, 'reject')[0].headers.get('If-Match')).toBe('"3"')
    expect(posts(requests, 'approve')).toHaveLength(1)
    await vi.waitFor(() => expect(within(panel).getAllByText('已拒绝').length).toBeGreaterThan(0))
  })

  it('uses the current version, refreshes on a conflict without claiming success, and shows success only when it succeeded', async () => {
    let approvals = 0
    const { requests } = setup({
      handler: (r, store) => {
        if (r.method === 'POST' && path(r).endsWith('/approve')) {
          approvals++
          if (approvals === 1) { store.set('exec-1', { ...store.get('exec-1')!, version: 5 }); return fail(409, 'VERSION_CONFLICT') }
          const done = { ...store.get('exec-1')!, status: 'succeeded' as const, version: 7, decided_by: 'user-a', decided_at: iso(Date.now()), executed_at: iso(Date.now()), result_summary: '已修改任务「接口联调（二期）」' }
          store.set('exec-1', done)
          return Response.json(done)
        }
      },
    })
    await openApp('/ai?id=exec-1')
    const panel = await detail()
    await fireEvent.click(await within(panel).findByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(within(panel).getByRole('alert')).toHaveTextContent('刚刚有变化'))
    await vi.waitFor(() => expect(gets(requests, 'exec-1').length).toBeGreaterThan(1))
    expect(within(panel).queryByText(/执行成功/)).not.toBeInTheDocument()
    expect(posts(requests, 'approve')[0].headers.get('If-Match')).toBe('"3"')
    await fireEvent.click(within(panel).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(posts(requests, 'approve')).toHaveLength(2))
    expect(posts(requests, 'approve')[1].headers.get('If-Match')).toBe('"5"')
    await vi.waitFor(() => expect(within(panel).getAllByText(/执行成功/).length).toBeGreaterThan(0))
    expect(within(panel).getByText('已修改任务「接口联调（二期）」')).toBeVisible()
    expect(within(panel).queryByRole('button', { name: '批准并执行' })).not.toBeInTheDocument()
  })

  it('shows an approved call that is still executing as executing, not as a success', async () => {
    setup({
      handler: (r, store) => {
        if (r.method === 'POST' && path(r).endsWith('/approve')) { const running = { ...store.get('exec-1')!, status: 'executing' as const, version: 4, decided_by: 'user-a', decided_at: iso(Date.now()) }; store.set('exec-1', running); return Response.json(running) }
      },
    })
    await openApp('/ai?id=exec-1')
    const panel = await detail()
    await fireEvent.click(await within(panel).findByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(within(panel).getAllByText('执行中').length).toBeGreaterThan(0))
    expect(within(panel).getAllByText(/已批准/).length).toBeGreaterThan(0)
    expect(within(panel).queryByText(/执行成功/)).not.toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: /批准|重试/ })).not.toBeInTheDocument()
  })

  it('after a lost approve response reads the same record first and offers no blind retry', async () => {
    const { requests } = setup({
      handler: (r, store) => {
        if (r.method === 'POST' && path(r).endsWith('/approve')) {
          store.set('exec-1', { ...store.get('exec-1')!, status: 'unknown', version: 5, decided_by: 'user-a', decided_at: iso(Date.now()), executed_at: iso(Date.now()), error_code: 'EXTERNAL_UNAVAILABLE', result_summary: '请求已发出，结果未知；请到对应页面确认，不会自动重试' })
          throw new TypeError('Failed to fetch')
        }
        return undefined
      },
    })
    await openApp('/ai?id=exec-1')
    const panel = await detail()
    await fireEvent.click(await within(panel).findByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(within(panel).getAllByText('结果未知').length).toBeGreaterThan(0))
    const approve = requests.indexOf(posts(requests, 'approve')[0])
    expect(requests.findIndex((r, i) => i > approve && r.method === 'GET' && path(r) === `${LIST}/exec-1`)).toBeGreaterThan(approve)
    expect(posts(requests, 'approve')).toHaveLength(1)
    expect(within(panel).queryByRole('button', { name: /批准|重试/ })).not.toBeInTheDocument()
    expect(within(panel).getByText('请求已发出，结果未知；请到对应页面确认，不会自动重试')).toBeVisible()
    expect(within(panel).getByRole('link', { name: /前往任务/ })).toHaveAttribute('href', '/tasks?task=task-7')
    expect(within(panel).queryByText(/执行成功/)).not.toBeInTheDocument()
  })

  it('never approves when the page opens or is refreshed', async () => {
    const { requests } = setup()
    await openApp('/approvals?id=exec-1')
    const panel = await detail()
    await within(panel).findByRole('button', { name: '批准并执行' })
    const before = summaries(requests).length
    await fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await vi.waitFor(() => expect(summaries(requests).length).toBeGreaterThan(before))
    expect(requests.filter(r => r.method !== 'GET')).toHaveLength(0)
  })

  it('shows untrusted text from the record as text', async () => {
    const evil = execution({
      summary: '<img src=x onerror="window.__pwned=1">删除', tool_name: 'acme_widget_sync',
      arguments: { title: '<script>window.__pwned=2</script>' }, result_summary: null,
      source: { conversation_id: 'si_x', message_server_id: null, triggered_by_im_id: 'ws_x', triggered_by_name: '<b>坏人</b>', inferred: true },
    })
    setup({ items: [evil] })
    await openApp('/ai?id=exec-1')
    const panel = await detail()
    expect(await within(panel).findByRole('heading', { name: '<img src=x onerror="window.__pwned=1">删除' })).toBeVisible()
    expect(within(panel).getByText('<b>坏人</b>')).toBeVisible()
    expect(panel.querySelector('pre.ai-arguments')!.textContent).toContain('<script>window.__pwned=2</script>')
    expect(document.querySelector('img[src="x"]')).toBeNull()
    expect(document.querySelector('main script, [role="region"] script')).toBeNull()
    expect(panel.querySelector('b')).toBeNull()
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined()
  })

  it('lets members see their own records without approve or reject controls', async () => {
    const { requests } = setup({ role: 'member' })
    await openApp('/ai?id=exec-1')
    const panel = await detail()
    expect(await within(panel).findByText('等待 owner 或 admin 审批。')).toBeVisible()
    expect(within(panel).queryByRole('button', { name: '批准并执行' })).not.toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: '拒绝申请' })).not.toBeInTheDocument()
    expect(within(panel).queryByLabelText('你的登录密码')).not.toBeInTheDocument()
    // 成员进不了 IM 页：来源只显示文字
    expect(within(panel).queryByRole('link', { name: 'IM 会话' })).not.toBeInTheDocument()
    expect(screen.getByText(/你只能看到以自己身份执行的记录/)).toBeVisible()
    expect(posts(requests)).toHaveLength(0)
  })

  it.each(['/approvals?id=exec-9', '/ai?id=exec-9'])('opens %s in the detail even when the record is not in the list', async (url) => {
    const linked = execution({ id: 'exec-9', summary: '通知里的请求', status: 'expired' })
    const { requests } = setup({ items: [execution()], handler: r => r.method === 'GET' && path(r) === `${LIST}/exec-9` ? Response.json(linked) : undefined })
    const { router } = await openApp(url)
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/ai?id=exec-9'))
    const panel = await detail()
    expect(await within(panel).findByRole('heading', { name: '通知里的请求' })).toBeVisible()
    expect(within(panel).getAllByText(/已过期/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('row', { name: '申请：通知里的请求' })).not.toBeInTheDocument()
    expect(gets(requests, 'exec-9').length).toBeGreaterThan(0)
    await fireEvent.click(within(panel).getByRole('button', { name: '关闭审批详情' }))
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/ai'))
  })

  it('lists failed and unknown calls under execution exceptions with a way to verify them', async () => {
    const failed = execution({ id: 'exec-f', summary: '修改任务「项目周报」：标题', status: 'failed', error_code: 'VERSION_CONFLICT', result_summary: '目标版本已变化', decided_by: 'user-a', executed_at: iso(Date.now()) })
    const unknown = execution({ id: 'exec-n', summary: '新建任务「上线检查」', tool_name: 'create_task', arguments: { project_id: 'proj-1', title: '上线检查' }, status: 'unknown', result_summary: '请求已发出，结果未知；请到对应页面确认，不会自动重试', decided_by: 'user-a', executed_at: iso(Date.now()) })
    const { requests } = setup({ items: [execution(), failed, unknown], summary: { exception_count: 2 } })
    await openApp('/ai')
    const strip = await screen.findByRole('region', { name: '执行异常 · 2' })
    expect(await within(strip).findByText('修改任务「项目周报」：标题')).toBeVisible()
    expect(within(strip).getByText('执行失败')).toBeVisible()
    expect(within(strip).getByText('结果未知')).toBeVisible()
    expect(within(strip).getByText('目标版本已变化')).toBeVisible()
    expect(within(strip).getByRole('link', { name: /前往项目任务/ })).toHaveAttribute('href', '/tasks?project_id=proj-1')
    expect(within(strip).getByRole('link', { name: /前往任务/ })).toHaveAttribute('href', '/tasks?task=task-7')
    const stripList = lists(requests).find(r => params(r).getAll('status').includes('failed'))!
    expect(params(stripList).getAll('status')).toEqual(['failed', 'unknown'])
    expect(params(stripList).get('sort')).toBe('created_desc')
    await fireEvent.click(screen.getByRole('tab', { name: '执行异常 2' }))
    await vi.waitFor(() => expect(params(lists(requests).at(-1)!).getAll('status')).toEqual(['failed', 'unknown']))
    expect(await screen.findByRole('row', { name: '申请：新建任务「上线检查」' })).toBeVisible()
    expect(screen.queryByRole('row', { name: '申请：修改任务「接口联调」：标题' })).not.toBeInTheDocument()
  })

  it('explains the rules read-only, without batch approval or permanent trust', async () => {
    const { requests } = setup()
    await openApp('/ai')
    await fireEvent.click(await screen.findByRole('button', { name: '规则说明' }))
    const dialog = await screen.findByRole('dialog', { name: '审批规则说明' })
    expect(within(dialog).getByText(/15 分钟/)).toBeVisible()
    expect(within(dialog).getByText(/工作台登录密码/)).toBeVisible()
    expect(within(dialog).getByText(/审批只授权这一次操作/)).toBeVisible()
    expect(within(dialog).getByText(/没有批量批准/)).toBeVisible()
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument()
    await fireEvent.click(within(dialog).getByRole('button', { name: '关闭对话框' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(requests.filter(r => r.method !== 'GET')).toHaveLength(0)
  })
})
