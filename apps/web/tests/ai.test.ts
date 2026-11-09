import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import { aiError } from '../src/features/ai/queries'
import { ApiError, type Schema } from '../src/services/api'

const at = '2026-11-01T04:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const execution = (over: Partial<Schema['AiToolExecutionOut']> = {}): Schema['AiToolExecutionOut'] => ({
  id: 'exec-1', instance_id: 'inst-a', instance_name: 'alpha', acting_user_id: 'user-a', tool_name: 'workspace.task.create', risk_level: 'write', status: 'pending_approval',
  summary: '在项目「运维」创建任务「重启 nginx」', arguments: { project_id: 'p1', title: '重启 nginx', token: '***' }, arguments_hash: 'h', request_id: 'req-1',
  source: { conversation_id: 'si_alpha', message_server_id: 'srv-9', triggered_by_im_id: 'ws_owner', triggered_by_name: 'Owner', inferred: true },
  result_summary: null, error_code: null, decision_reason: null, decided_by: null, decided_at: null, executed_at: null, expires_at: '2026-11-01T04:15:00Z', created_at: at, version: 3, ...over })
const page = (items: Schema['AiToolExecutionOut'][], pending = items.filter(e => e.status === 'pending_approval').length) => Response.json({ items, next_cursor: null, total_estimate: items.length, pending_count: pending })
// 审批中心改版（T-0191）：汇总来自 /summary；as_of 是这些记录发起后 5 分钟的服务端时间，倒计时按它校正
const summary = (items: Schema['AiToolExecutionOut'][]) => Response.json({ pending_count: items.filter(e => e.status === 'pending_approval').length, expiring_count: 0, critical_pending_count: items.filter(e => e.status === 'pending_approval' && e.risk_level === 'critical').length, exception_count: 0, as_of: '2026-11-01T04:05:00Z', period: '24h', expiring_window_seconds: 180, instances: [{ id: 'inst-a', name: 'alpha' }], tools: ['workspace.task.create'] })

function setup(handler?: (r: Request) => Response | Promise<Response | undefined> | undefined, items = [execution()], role: Schema['WorkspaceOut']['role'] = 'owner') {
  return mockApi(async r => await handler?.(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) :
    path(r) === '/api/v1/ai/tool-executions/summary' ? summary(items) :
    path(r) === '/api/v1/ai/tool-executions' ? page(items) :
    path(r).startsWith('/api/v1/ai/tool-executions/') && r.method === 'GET' ? Response.json(items.find(e => path(r).endsWith(e.id)) ?? execution()) : undefined
  ))
}
const decisions = (requests: Request[], verb: 'approve' | 'reject') => requests.filter(r => r.method === 'POST' && path(r).endsWith(`/${verb}`))
const lists = (requests: Request[]) => requests.filter(r => path(r) === '/api/v1/ai/tool-executions')
// 列表是表格，一行一条；点“查看”在右侧打开详情（宽屏），批准与参数都在详情里
const row = (summary: string) => screen.findByRole('row', { name: `申请：${summary}` })
async function openRecord(summary: string) {
  await fireEvent.click(within(await row(summary)).getByRole('button', { name: /查看/ }))
  const record = await screen.findByRole('region', { name: '审批详情' })
  await within(record).findByRole('heading', { level: 2, name: summary })
  return record
}
afterEach(() => vi.restoreAllMocks())

describe('ai approvals', () => {
  it('explains approval errors', () => {
    expect(aiError(new ApiError(403, 'STEP_UP_FAILED', ''))).toBe('密码不正确。')
    expect(aiError(new ApiError(409, 'EXECUTION_NOT_PENDING', ''))).toContain('已经处理过')
    expect(aiError(new ApiError(503, 'SOMETHING', ''))).toContain('暂时不可用')
    expect(aiError(new Error('x'))).toContain('请求失败')
  })

  it('lists pending requests and approves one with its version', async () => {
    const requests = setup(r => r.method === 'POST' && path(r).endsWith('/approve') ? Response.json(execution({ status: 'succeeded', version: 4 })) : undefined)
    await openApp('/ai')
    expect(screen.getByRole('link', { name: 'AI 审批' })).toBeVisible()
    const line = await row('在项目「运维」创建任务「重启 nginx」')
    expect(within(line).getByText('在项目「运维」创建任务「重启 nginx」')).toBeVisible()
    expect(within(line).getByText('写操作')).toBeVisible()
    expect(within(line).getByText('IM 会话')).toBeVisible()
    expect(within(line).getByText('来源推断')).toBeVisible()
    const list = lists(requests)[0]
    expect(new URL(list.url).searchParams.getAll('status')).toEqual(['pending_approval'])
    expect(list.headers.get('X-Workspace-Id')).toBe('workspace-a')
    const record = await openRecord('在项目「运维」创建任务「重启 nginx」')
    // Who triggered it and where (inferred from the instance's latest message).
    expect(within(record).getByText('Owner')).toBeVisible()
    expect(within(record).getByText('私聊它')).toBeVisible()
    expect(within(record).getByRole('link', { name: 'IM 会话' })).toHaveAttribute('href', '/im?c=si_alpha')
    expect(within(record).getByText(/根据实例最近收到的消息推断/)).toBeVisible()
    const before = lists(requests).length
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(decisions(requests, 'approve')).toHaveLength(1))
    const approve = decisions(requests, 'approve')[0]
    expect(path(approve)).toBe('/api/v1/ai/tool-executions/exec-1/approve')
    expect(approve.headers.get('If-Match')).toBe('"3"')
    expect(approve.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(await approve.clone().json()).toEqual({})
    // The list is refreshed after the decision.
    await vi.waitFor(() => expect(lists(requests).length).toBeGreaterThan(before))
  })

  it('asks for the password before approving a critical action', async () => {
    let attempts = 0
    const requests = setup(r => {
      if (r.method === 'POST' && path(r).endsWith('/approve')) { attempts++; return attempts === 1 ? fail(403, 'STEP_UP_FAILED') : Response.json(execution({ risk_level: 'critical', status: 'succeeded', version: 4 })) }
    }, [execution({ risk_level: 'critical', tool_name: 'workspace.task.delete', summary: '删除任务「旧任务」' })])
    await openApp('/ai')
    expect(within(await row('删除任务「旧任务」')).getByText('高危')).toBeVisible()
    const record = await openRecord('删除任务「旧任务」')
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    expect(await within(record).findByRole('alert')).toHaveTextContent('需要输入你的登录密码')
    expect(decisions(requests, 'approve')).toHaveLength(0)
    await fireEvent.update(within(record).getByLabelText('你的登录密码'), 'wrong')
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(within(record).getByRole('alert')).toHaveTextContent('密码不正确'))
    expect(await decisions(requests, 'approve')[0].clone().json()).toEqual({ password: 'wrong' })
    await fireEvent.update(within(record).getByLabelText('你的登录密码'), 'right')
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(decisions(requests, 'approve')).toHaveLength(2))
    expect(await decisions(requests, 'approve')[1].clone().json()).toEqual({ password: 'right' })
  })

  it('rejects with a reason', async () => {
    const requests = setup(r => r.method === 'POST' && path(r).endsWith('/reject') ? Response.json(execution({ status: 'rejected', decision_reason: '不需要', version: 4 })) : undefined)
    await openApp('/ai')
    const record = await openRecord('在项目「运维」创建任务「重启 nginx」')
    await fireEvent.click(within(record).getByRole('button', { name: '拒绝时填写原因' }))
    await fireEvent.update(within(record).getByLabelText('拒绝原因（可选）'), ' 不需要 ')
    await fireEvent.click(within(record).getByRole('button', { name: '拒绝申请' }))
    await vi.waitFor(() => expect(decisions(requests, 'reject')).toHaveLength(1))
    expect(decisions(requests, 'reject')[0].headers.get('If-Match')).toBe('"3"')
    expect(await decisions(requests, 'reject')[0].clone().json()).toEqual({ reason: '不需要' })
  })

  it('refreshes when the record changed in the meantime', async () => {
    let listed = 0, conflicted = false
    const requests = setup(r => {
      if (r.method === 'POST' && path(r).endsWith('/approve')) { conflicted = true; return fail(409, 'VERSION_CONFLICT') }
      if (path(r) === '/api/v1/ai/tool-executions') { listed++; return page([execution(listed > 1 ? { version: 5 } : {})]) }
      if (r.method === 'GET' && path(r) === '/api/v1/ai/tool-executions/exec-1') return Response.json(execution(conflicted ? { version: 5 } : {}))
    })
    await openApp('/ai')
    const record = await openRecord('在项目「运维」创建任务「重启 nginx」')
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(listed).toBeGreaterThan(1))
    expect(decisions(requests, 'approve')).toHaveLength(1)
    // The refreshed record (new version) replaces the old one; approving again uses the new version.
    await vi.waitFor(() => expect(screen.getAllByRole('row', { name: /^申请：/ })).toHaveLength(1))
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(decisions(requests, 'approve')).toHaveLength(2))
    expect(decisions(requests, 'approve')[1].headers.get('If-Match')).toBe('"5"')
  })

  it('filters the activity log and shows masked arguments on request', async () => {
    const done = execution({ id: 'exec-2', status: 'succeeded', result_summary: '已创建任务 #42', source: { conversation_id: null, message_server_id: null, triggered_by_im_id: null, inferred: false } })
    const requests = setup(undefined, [done])
    await openApp('/ai?view=activity')
    expect(within(await row(done.summary)).getByText('执行成功')).toBeVisible()
    const record = await openRecord(done.summary)
    expect(within(record).getByText('已创建任务 #42')).toBeVisible()
    expect(within(record).queryByRole('button', { name: '批准并执行' })).not.toBeInTheDocument()
    expect(within(record).queryByRole('link', { name: 'IM 会话' })).not.toBeInTheDocument()
    // 这个工具没有专门的展示方式：脱敏后的参数按原样（JSON 文本）显示
    expect(within(record).getByText(/"token": "\*\*\*"/)).toBeVisible()
    const filters = screen.getByRole('region', { name: '筛选' })
    await fireEvent.update(within(filters).getByLabelText('状态'), 'failed')
    await fireEvent.update(within(filters).getByLabelText('类型'), 'critical')
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/ai/tool-executions' && new URL(r.url).searchParams.get('risk_level') === 'critical')).toBe(true))
    const last = new URL(lists(requests).at(-1)!.url)
    expect(last.searchParams.getAll('status')).toEqual(['failed'])
    expect(last.searchParams.getAll('risk_level')).toEqual(['critical'])
  })

  it('opens the record from a notification link next to the list', async () => {
    const other = execution({ id: 'exec-2', summary: '另一条请求' }), linked = execution({ id: 'exec-9', summary: '通知里的请求', status: 'expired' })
    const requests = setup(r => r.method === 'GET' && path(r) === '/api/v1/ai/tool-executions/exec-9' ? Response.json(linked) : undefined, [other, linked])
    const { router } = await openApp('/approvals?id=exec-9')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/ai?id=exec-9'))
    const record = await screen.findByRole('region', { name: '审批详情' })
    expect(await within(record).findByRole('heading', { level: 2, name: '通知里的请求' })).toBeVisible()
    // The list row of the linked record is marked as the one shown; the detail is shown once.
    await vi.waitFor(() => expect(screen.getByRole('row', { name: '申请：通知里的请求' })).toHaveAttribute('aria-selected', 'true'))
    expect(screen.getByRole('row', { name: '申请：另一条请求' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getAllByRole('heading', { name: '通知里的请求' })).toHaveLength(1)
    // Arguments are shown for the focused record.
    expect(within(record).getByText(/"title": "重启 nginx"/)).toBeVisible()
    expect(requests.some(r => path(r) === '/api/v1/ai/tool-executions/exec-9')).toBe(true)
  })

  it('lets members see their own records but not decide', async () => {
    const requests = setup(undefined, [execution()], 'member')
    await openApp('/ai')
    const record = await openRecord('在项目「运维」创建任务「重启 nginx」')
    expect(within(record).getByText('等待 owner 或 admin 审批。')).toBeVisible()
    expect(within(record).queryByRole('button', { name: '批准并执行' })).not.toBeInTheDocument()
    // Members cannot open the IM page, so the source is shown without a link.
    expect(within(record).queryByRole('link', { name: 'IM 会话' })).not.toBeInTheDocument()
    expect(within(record).getByText('IM 会话')).toBeVisible()
    expect(screen.getByText(/你只能看到以自己身份执行的记录/)).toBeVisible()
    expect(decisions(requests, 'approve')).toHaveLength(0)
  })
})

describe('approval cards in im', () => {
  const card: Schema['ImMessageOut'] = { server_msg_id: 'srv-c', client_msg_id: 'cli-c', conversation_id: 'sn_x', seq: 1, sender_im_id: 'wn_x', sender_instance_id: null, kind: 'approval_card', text: '「alpha」请求：在项目「运维」创建任务「重启 nginx」', image: null, at_im_ids: [], approval_execution_id: 'exec-1', status: 'sent', sent_at: at, pinned: false }
  const conversation: Schema['ImConversationOut'] = { conversation_id: 'sn_x', kind: 'notice', title: '工作空间通知', peer_instance_id: null, group_id: null, pinned: false, pinned_at: null, muted: false, unread_count: 0, read_seq: 1, max_seq: 1, last_message: { kind: 'approval_card', sender_im_id: 'wn_x', sent_at: at, text_preview: '' }, last_message_at: at }
  function setupIm(status: Schema['AiToolExecutionOut']['status'] = 'pending_approval') {
    return mockApi(r =>
      path(r) === '/api/v1/im/status' ? Response.json({ available: true, account_ready: true, im_user_id: 'ws_owner' }) :
      path(r) === '/api/v1/im/contacts' ? Response.json({ items: [] }) :
      path(r) === '/api/v1/im/conversations' ? Response.json({ items: [conversation], next_cursor: null, total_estimate: 1, unread_total: 0 }) :
      path(r).endsWith('/messages') ? Response.json({ items: [card], next_cursor: null, total_estimate: 1 }) :
      path(r).endsWith('/pins') ? Response.json({ items: [] }) :
      path(r) === '/api/v1/im/contact-groups' ? Response.json({ items: [] }) :
      path(r) === '/api/v1/ai/tool-executions/exec-1' ? Response.json(execution({ status })) :
      r.method === 'POST' && path(r).endsWith('/approve') ? Response.json(execution({ status: 'succeeded', version: 4 })) :
      path(r) === '/api/v1/ai/tool-executions' ? page([]) : undefined)
  }

  it('shows the current state from the workspace and approves from the card', async () => {
    const requests = setupIm()
    await openApp('/im?c=sn_x')
    const message = await screen.findByText('在项目「运维」创建任务「重启 nginx」')
    const bubble = message.closest('.ai-card') as HTMLElement
    expect(within(bubble).getByText('待审批')).toBeVisible()
    expect(within(bubble).getByText(/由 Owner 触发/)).toBeVisible()
    expect(within(bubble).getByRole('link', { name: '在审批中心查看参数' })).toHaveAttribute('href', '/ai?id=exec-1')
    await fireEvent.click(within(bubble).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(decisions(requests, 'approve')).toHaveLength(1))
    expect(decisions(requests, 'approve')[0].headers.get('If-Match')).toBe('"3"')
  })

  it('shows decided cards without actions', async () => {
    setupIm('rejected')
    await openApp('/im?c=sn_x')
    const message = await screen.findByText('在项目「运维」创建任务「重启 nginx」')
    const bubble = message.closest('.ai-card') as HTMLElement
    expect(within(bubble).getByText('已拒绝')).toBeVisible()
    expect(within(bubble).queryByRole('button', { name: '批准并执行' })).not.toBeInTheDocument()
  })
})
