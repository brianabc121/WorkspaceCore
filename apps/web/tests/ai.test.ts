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
  source: { conversation_id: 'si_alpha', message_server_id: 'srv-9', triggered_by_im_id: 'ws_owner', inferred: true },
  result_summary: null, error_code: null, decision_reason: null, decided_by: null, decided_at: null, executed_at: null, expires_at: '2026-11-01T04:15:00Z', created_at: at, version: 3, ...over })
const page = (items: Schema['AiToolExecutionOut'][], pending = items.filter(e => e.status === 'pending_approval').length) => Response.json({ items, next_cursor: null, total_estimate: items.length, pending_count: pending })

function setup(handler?: (r: Request) => Response | Promise<Response | undefined> | undefined, items = [execution()], role: Schema['WorkspaceOut']['role'] = 'owner') {
  return mockApi(async r => await handler?.(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) :
    path(r) === '/api/v1/ai/tool-executions' ? page(items) :
    path(r).startsWith('/api/v1/ai/tool-executions/') && r.method === 'GET' ? Response.json(items.find(e => path(r).endsWith(e.id)) ?? execution()) : undefined
  ))
}
const decisions = (requests: Request[], verb: 'approve' | 'reject') => requests.filter(r => r.method === 'POST' && path(r).endsWith(`/${verb}`))
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
    const record = await screen.findByRole('listitem')
    expect(within(record).getByText('在项目「运维」创建任务「重启 nginx」')).toBeVisible()
    expect(within(record).getByText('写操作')).toBeVisible()
    expect(within(record).getByRole('link', { name: 'IM 会话' })).toHaveAttribute('href', '/im?c=si_alpha')
    expect(within(record).getByText(/根据实例最近收到的消息推断/)).toBeVisible()
    const list = requests.find(r => path(r) === '/api/v1/ai/tool-executions')!
    expect(new URL(list.url).searchParams.getAll('status')).toEqual(['pending_approval'])
    expect(list.headers.get('X-Workspace-Id')).toBe('workspace-a')
    const before = requests.filter(r => path(r) === '/api/v1/ai/tool-executions').length
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(decisions(requests, 'approve')).toHaveLength(1))
    const approve = decisions(requests, 'approve')[0]
    expect(path(approve)).toBe('/api/v1/ai/tool-executions/exec-1/approve')
    expect(approve.headers.get('If-Match')).toBe('"3"')
    expect(approve.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(await approve.clone().json()).toEqual({})
    // The list is refreshed after the decision.
    await vi.waitFor(() => expect(requests.filter(r => path(r) === '/api/v1/ai/tool-executions').length).toBeGreaterThan(before))
  })

  it('asks for the password before approving a critical action', async () => {
    let attempts = 0
    const requests = setup(r => {
      if (r.method === 'POST' && path(r).endsWith('/approve')) { attempts++; return attempts === 1 ? fail(403, 'STEP_UP_FAILED') : Response.json(execution({ risk_level: 'critical', status: 'succeeded', version: 4 })) }
    }, [execution({ risk_level: 'critical', tool_name: 'workspace.task.delete', summary: '删除任务「旧任务」' })])
    await openApp('/ai')
    const record = await screen.findByRole('listitem')
    expect(within(record).getByText('高危')).toBeVisible()
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    expect(await within(record).findByRole('alert')).toHaveTextContent('需要输入你的登录密码')
    expect(decisions(requests, 'approve')).toHaveLength(0)
    await fireEvent.update(within(record).getByLabelText('高危操作，请输入你的登录密码'), 'wrong')
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(within(record).getByRole('alert')).toHaveTextContent('密码不正确'))
    expect(await decisions(requests, 'approve')[0].clone().json()).toEqual({ password: 'wrong' })
    await fireEvent.update(within(record).getByLabelText('高危操作，请输入你的登录密码'), 'right')
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(decisions(requests, 'approve')).toHaveLength(2))
    expect(await decisions(requests, 'approve')[1].clone().json()).toEqual({ password: 'right' })
  })

  it('rejects with a reason', async () => {
    const requests = setup(r => r.method === 'POST' && path(r).endsWith('/reject') ? Response.json(execution({ status: 'rejected', decision_reason: '不需要', version: 4 })) : undefined)
    await openApp('/ai')
    const record = await screen.findByRole('listitem')
    await fireEvent.click(within(record).getByRole('button', { name: '拒绝…' }))
    await fireEvent.update(within(record).getByLabelText('拒绝原因（可选）'), ' 不需要 ')
    await fireEvent.click(within(record).getByRole('button', { name: '确认拒绝' }))
    await vi.waitFor(() => expect(decisions(requests, 'reject')).toHaveLength(1))
    expect(decisions(requests, 'reject')[0].headers.get('If-Match')).toBe('"3"')
    expect(await decisions(requests, 'reject')[0].clone().json()).toEqual({ reason: '不需要' })
  })

  it('refreshes when the record changed in the meantime', async () => {
    let lists = 0
    const requests = setup(r => {
      if (r.method === 'POST' && path(r).endsWith('/approve')) return fail(409, 'VERSION_CONFLICT')
      if (path(r) === '/api/v1/ai/tool-executions') { lists++; return page([execution(lists > 1 ? { version: 5 } : {})]) }
    })
    await openApp('/ai')
    const record = await screen.findByRole('listitem')
    await fireEvent.click(within(record).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(lists).toBeGreaterThan(1))
    expect(decisions(requests, 'approve')).toHaveLength(1)
    // The refreshed record (new version) replaces the old one; approving again uses the new version.
    await vi.waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(1))
    await fireEvent.click(within(screen.getByRole('listitem')).getByRole('button', { name: '批准并执行' }))
    await vi.waitFor(() => expect(decisions(requests, 'approve')).toHaveLength(2))
    expect(decisions(requests, 'approve')[1].headers.get('If-Match')).toBe('"5"')
  })

  it('filters the activity log and shows masked arguments on request', async () => {
    const done = execution({ id: 'exec-2', status: 'succeeded', result_summary: '已创建任务 #42', source: { conversation_id: null, message_server_id: null, triggered_by_im_id: null, inferred: false } })
    const requests = setup(undefined, [done])
    await openApp('/ai?view=activity')
    const record = await screen.findByRole('listitem')
    expect(within(record).getByText('已执行')).toBeVisible()
    expect(within(record).getByText('结果：已创建任务 #42')).toBeVisible()
    expect(within(record).queryByRole('button', { name: '批准并执行' })).not.toBeInTheDocument()
    expect(within(record).queryByRole('link', { name: 'IM 会话' })).not.toBeInTheDocument()
    await fireEvent.click(within(record).getByRole('button', { name: '查看参数' }))
    expect(within(record).getByText(/"token": "\*\*\*"/)).toBeVisible()
    const filters = screen.getByRole('region', { name: '筛选' })
    await fireEvent.update(within(filters).getByLabelText('状态'), 'failed')
    await fireEvent.update(within(filters).getByLabelText('类型'), 'critical')
    await vi.waitFor(() => expect(requests.some(r => new URL(r.url).searchParams.get('risk_level') === 'critical')).toBe(true))
    const last = new URL(requests.filter(r => path(r) === '/api/v1/ai/tool-executions').at(-1)!.url)
    expect(last.searchParams.getAll('status')).toEqual(['failed'])
    expect(last.searchParams.getAll('risk_level')).toEqual(['critical'])
  })

  it('opens the record from a notification link on top of the list', async () => {
    const other = execution({ id: 'exec-2', summary: '另一条请求' }), linked = execution({ id: 'exec-9', summary: '通知里的请求', status: 'expired' })
    const requests = setup(r => r.method === 'GET' && path(r) === '/api/v1/ai/tool-executions/exec-9' ? Response.json(linked) : undefined, [other, linked])
    const { router } = await openApp('/approvals?id=exec-9')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/ai?id=exec-9'))
    const items = await screen.findAllByRole('listitem')
    await vi.waitFor(() => expect(screen.getAllByRole('listitem')[0]).toHaveAttribute('aria-current', 'true'))
    expect(within(screen.getAllByRole('listitem')[0]).getByText('通知里的请求')).toBeVisible()
    expect(items.length).toBeGreaterThan(0)
    // Shown once even though the list also contains it; arguments are open for the focused record.
    expect(screen.getAllByText('通知里的请求')).toHaveLength(1)
    expect(within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: '收起参数' })).toBeVisible()
    expect(requests.some(r => path(r) === '/api/v1/ai/tool-executions/exec-9')).toBe(true)
  })

  it('lets members see their own records but not decide', async () => {
    const requests = setup(undefined, [execution()], 'member')
    await openApp('/ai')
    const record = await screen.findByRole('listitem')
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
