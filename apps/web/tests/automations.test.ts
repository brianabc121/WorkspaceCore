import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import { automation, automations, connection, run, path } from './sprint5-fixtures'
import { useAuthStore } from '../src/stores/auth'

function setup(handler?: (r: Request) => Response | Promise<Response | undefined> | undefined) {
  return mockApi(async r => await handler?.(r) ?? (
    path(r) === '/api/v1/automations' ? Response.json(automations) :
    path(r) === '/api/v1/automations/connection' ? Response.json(connection) :
    path(r).endsWith('/runs') ? Response.json({ items: [run], next_cursor: null, total_estimate: 1 }) : undefined
  ))
}
async function openTrigger() { await fireEvent.click(await screen.findByRole('button', { name: '触发：生成日报' })); return screen.findByRole('dialog', { name: '触发工作流：生成日报' }) }
async function fillTrigger() {
  await fireEvent.update(screen.getByLabelText('收件人 *'), 'sample@example.test')
  await fireEvent.update(screen.getByLabelText('份数 *'), '2')
  await fireEvent.update(screen.getByLabelText('格式'), '摘要')
  await fireEvent.update(screen.getByLabelText('发送 *'), 'false')
  await fireEvent.click(screen.getByRole('button', { name: '检查并确认' }))
}
afterEach(() => vi.restoreAllMocks())

describe('automations', () => {
  it('blocks policy-denied triggering across close/reopen until a post-failure read succeeds, ignoring an older read', async () => {
    let rejected = false, refreshFails = false, hold = false, finish!: (r: Response) => void
    const requests = setup(r => {
      if (path(r).endsWith('/trigger')) { rejected = true; refreshFails = true; return Response.json({ error: { code: 'AUTOMATION_UNAVAILABLE', details: { reason: 'connection_unavailable' } } }, { status: 409 }) }
      if (path(r) === '/api/v1/automations') { if (hold) return new Promise<Response>(resolve => { finish = resolve }); if (refreshFails) return fail(503, 'HTTP_ERROR') }
    })
    const { queryClient } = await openApp('/automations'); await openTrigger(); await fillTrigger()
    hold = true; const late = queryClient.invalidateQueries({ queryKey: ['automations'] }); await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.click(screen.getByRole('button', { name: '确认触发' })); await vi.waitFor(() => expect(rejected).toBe(true)); await screen.findByRole('alert')
    expect(screen.queryByRole('button', { name: '检查并确认' })).not.toBeInTheDocument()
    hold = false; finish(Response.json(automations)); await late
    await fireEvent.click(screen.getByRole('button', { name: '关闭对话框' })); expect(screen.getByRole('button', { name: '触发：生成日报' })).toBeDisabled()
    await fireEvent.click(screen.getByRole('button', { name: '刷新自动化' })); await screen.findAllByRole('alert'); expect(screen.getByRole('button', { name: '触发：生成日报' })).toBeDisabled()
    refreshFails = false; await fireEvent.click(screen.getByRole('button', { name: '刷新自动化' })); await vi.waitFor(() => expect(screen.getByRole('button', { name: '触发：生成日报' })).toBeEnabled())
    expect(requests.filter(r => path(r).endsWith('/trigger'))).toHaveLength(1)
  })
  it('latches an upstream credential rejection and does not offer same-key trigger replay', async () => {
    setup(r => path(r).endsWith('/trigger') ? fail(502, 'INTEGRATION_UNAUTHORIZED') : undefined)
    await openApp('/automations'); await openTrigger(); await fillTrigger(); await fireEvent.click(screen.getByRole('button', { name: '确认触发' })); expect(await screen.findByRole('alert')).toHaveTextContent('n8n 凭据被拒绝')
    expect(screen.queryByRole('button', { name: '用原请求核对' })).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: '检查并确认' })).not.toBeInTheDocument()
  })
  it('shows usable run records inside unknown recovery and requires a fresh successful read before new-submission acknowledgement', async () => {
    let finish!: (r: Response) => void, failed = false
    setup(r => {
      if (path(r).endsWith('/trigger')) return Response.json(run, { status: 202 })
      if (path(r).endsWith('/runs')) return failed ? fail(503, 'HTTP_ERROR') : new Promise<Response>(resolve => { finish = resolve })
    })
    await openApp('/automations'); await openTrigger(); await fillTrigger(); await fireEvent.click(screen.getByRole('button', { name: '确认触发' })); const dialog = screen.getByRole('dialog'); await within(dialog).findByText('结果未知，正在确认')
    await fireEvent.click(within(dialog).getByRole('button', { name: '核对运行记录' })); await vi.waitFor(() => expect(finish).toBeDefined())
    expect(within(dialog).getByRole('button', { name: '已核对，开始新提交' })).toBeDisabled()
    finish(Response.json({ items: [run], next_cursor: null })); expect(await within(dialog).findByRole('table', { name: '生成日报运行记录' })).toBeVisible()
    await vi.waitFor(() => expect(within(dialog).getByRole('button', { name: '已核对，开始新提交' })).toBeEnabled())
    failed = true; await fireEvent.click(within(dialog).getByRole('button', { name: '刷新运行记录' })); await within(dialog).findByRole('alert')
    expect(within(dialog).getByRole('button', { name: '已核对，开始新提交' })).toBeDisabled()
  })
  it('blocks triggering if browser session recovery storage is unavailable', async () => {
    const requests = setup(); await openApp('/automations')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('blocked') })
    await openTrigger(); expect(await screen.findByRole('alert')).toHaveTextContent('无法保存触发恢复标识')
    expect(screen.getByRole('button', { name: '检查并确认' })).toBeDisabled(); expect(requests.some(r => path(r).endsWith('/trigger'))).toBe(false)
    vi.restoreAllMocks()
  })
  it.each([['not_configured', '尚未配置 n8n'], ['unavailable', 'n8n 暂不可用'], ['unauthorized', 'n8n 授权已失效'], ['stale', '自动化数据可能过期']])('shows connection %s explicitly and prevents triggering', async (status, text) => {
    setup(r => path(r) === '/api/v1/automations' ? Response.json({ ...automations, connection: { status, as_of: status === 'stale' ? '2026-10-31T01:00:00Z' : null } }) : undefined)
    await openApp('/automations'); expect(await screen.findByText(new RegExp(text))).toBeVisible()
    expect(screen.getByRole('button', { name: '触发：生成日报' })).toBeDisabled()
  })
  it('registers and edits local whitelist parameters with version, then confirms removal', async () => {
    let items = [automation]
    const requests = setup(async r => {
      if (path(r) === '/api/v1/automations') {
        if (r.method === 'POST') { const body = await r.clone().json(); const item = { ...automation, ...body, id: 'flow-new' }; items = [...items, item]; return Response.json(item, { status: 201 }) }
        return Response.json({ ...automations, items })
      }
      if (path(r) === '/api/v1/automations/flow-new') {
        if (r.method === 'PATCH') { const body = await r.clone().json(); items = items.map(i => i.id === 'flow-new' ? { ...i, ...body, version: 2 } : i); return Response.json(items[1]) }
        if (r.method === 'DELETE') { items = items.filter(i => i.id !== 'flow-new'); return new Response(null, { status: 204 }) }
      }
    })
    await openApp('/automations'); await fireEvent.click(await screen.findByRole('button', { name: '登记工作流' }))
    await fireEvent.update(screen.getByLabelText('名称'), '新工作流'); await fireEvent.update(screen.getByLabelText('工作流 ID'), 'New1'); await fireEvent.update(screen.getByLabelText('触发路径'), 'new-hook')
    await fireEvent.click(screen.getByRole('button', { name: '添加参数' })); await fireEvent.update(screen.getByLabelText('参数标识'), 'target'); await fireEvent.update(screen.getByLabelText('显示名称'), '目标'); await fireEvent.click(screen.getByLabelText('必填')); await fireEvent.update(screen.getByLabelText('最大长度'), '12')
    await fireEvent.click(screen.getByRole('button', { name: '保存工作流' })); const entry = await screen.findByRole('region', { name: '新工作流' }); await vi.waitFor(() => expect(within(entry).getByRole('button', { name: '编辑工作流' })).toBeEnabled())
    const created = await requests.find(r => r.method === 'POST' && path(r) === '/api/v1/automations')!.clone().json(); expect(created.params).toEqual([{ name: 'target', label: '目标', type: 'string', required: true, sensitive: false, max_length: 12, choices: null }])
    await fireEvent.click(within(entry).getByRole('button', { name: '编辑工作流' })); await fireEvent.update(screen.getByLabelText('名称'), '已更新工作流'); await fireEvent.click(screen.getByRole('button', { name: '保存工作流' }))
    const updated = await screen.findByRole('region', { name: '已更新工作流' }); await vi.waitFor(() => expect(within(updated).getByRole('button', { name: '移出白名单' })).toBeEnabled())
    expect((await requests.find(r => r.method === 'PATCH')!.clone().json()).version).toBe(1)
    vi.spyOn(window, 'confirm').mockReturnValue(false); await fireEvent.click(within(updated).getByRole('button', { name: '移出白名单' })); expect(requests.some(r => r.method === 'DELETE')).toBe(false)
    vi.spyOn(window, 'confirm').mockReturnValue(true); await fireEvent.click(within(updated).getByRole('button', { name: '移出白名单' })); await vi.waitFor(() => expect(screen.queryByRole('region', { name: '已更新工作流' })).not.toBeInTheDocument())
    expect(new URL(requests.find(r => r.method === 'DELETE')!.url).searchParams.get('version')).toBe('2')
  })
  it('shows whitelist, unknown last run, safe editor link and paged run table', async () => {
    const requests = setup(r => path(r).endsWith('/runs') ? Response.json({ items: [{ ...run, status: 'error', error_summary: '<b>节点失败</b>' }], next_cursor: new URL(r.url).searchParams.has('cursor') ? null : 'next-run' }) : undefined)
    await openApp('/automations'); const flow = await screen.findByRole('region', { name: '生成日报' })
    expect(flow).toHaveTextContent('结果未知，正在确认')
    await fireEvent.click(within(flow).getByRole('button', { name: '查看运行记录' }))
    const table = await screen.findByRole('table', { name: '生成日报运行记录' }); expect(table).toHaveTextContent('<b>节点失败</b>'); expect(table.querySelector('b')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '更多运行记录' })); await vi.waitFor(() => expect(requests.some(r => new URL(r.url).searchParams.get('cursor') === 'next-run')).toBe(true))
    expect(screen.getByRole('link', { name: /打开 n8n 编辑器/ })).toHaveAttribute('rel', 'noopener noreferrer')
  })
  it('allows viewer reads only and never requests connection secrets/settings', async () => {
    const requests = setup(r => path(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'viewer' }] }) : undefined)
    await openApp('/automations'); await screen.findByRole('region', { name: '生成日报' })
    expect(screen.queryByRole('button', { name: /触发：|配置 n8n|登记工作流|移出白名单/ })).not.toBeInTheDocument()
    expect(requests.some(r => path(r).includes('/connection') || r.method !== 'GET')).toBe(false)
  })
  it('requires typed params and confirmation, preserves false/number, and gives a fresh key only for a new submission', async () => {
    const requests = setup(r => path(r).endsWith('/trigger') ? Response.json({ ...run, status: 'queued' }, { status: 202 }) : undefined)
    document.cookie = 'ws_csrf=trigger-csrf'; await openApp('/automations'); await openTrigger()
    await fireEvent.click(screen.getByRole('button', { name: '检查并确认' })); expect(await screen.findByRole('alert')).toHaveTextContent(/收件人|必填/)
    expect(requests.some(r => r.method === 'POST')).toBe(false)
    await fillTrigger(); expect(screen.getByText(/确认触发.*生成日报/)).toBeVisible(); expect(requests.some(r => r.method === 'POST')).toBe(false)
    await fireEvent.click(screen.getByRole('button', { name: '确认触发' })); await screen.findByText('已受理，等待执行。')
    const first = requests.find(r => path(r).endsWith('/trigger'))!
    expect(await first.clone().json()).toEqual({ params: { recipient: 'sample@example.test', count: 2, mode: '摘要', send: false } })
    expect(first.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/); expect(first.headers.get('X-Workspace-Id')).toBe('workspace-a'); expect(first.headers.get('X-CSRF-Token')).toBe('trigger-csrf')
    await fireEvent.click(screen.getByRole('button', { name: '关闭对话框' })); await openTrigger(); await fillTrigger(); await fireEvent.click(screen.getByRole('button', { name: '确认触发' })); await screen.findByText('已受理，等待执行。')
    expect(requests.filter(r => path(r).endsWith('/trigger'))[1]!.headers.get('Idempotency-Key')).not.toBe(first.headers.get('Idempotency-Key'))
  })
  it('validates string length/choices/finite number without sending a trigger', async () => {
    const requests = setup(); await openApp('/automations'); await openTrigger()
    await fireEvent.update(screen.getByLabelText('收件人 *'), 'x'.repeat(25)); await fireEvent.update(screen.getByLabelText('份数 *'), 'abc'); await fireEvent.update(screen.getByLabelText('发送 *'), 'true')
    await fireEvent.click(screen.getByRole('button', { name: '检查并确认' })); expect(await screen.findByRole('alert')).toHaveTextContent(/收件人|24/)
    expect(requests.some(r => path(r).endsWith('/trigger'))).toBe(false)
  })
  it('does not repeat unknown accepted runs and offers read-only reconciliation', async () => {
    const requests = setup(r => path(r).endsWith('/trigger') ? Response.json(run, { status: 202 }) : undefined)
    await openApp('/automations'); await openTrigger(); await fillTrigger(); await fireEvent.click(screen.getByRole('button', { name: '确认触发' }))
    const dialog = screen.getByRole('dialog'); expect(await within(dialog).findByText('结果未知，正在确认')).toBeVisible()
    await fireEvent.click(within(dialog).getByRole('button', { name: '核对运行记录' })); await screen.findByRole('table', { name: '生成日报运行记录' })
    expect(requests.filter(r => path(r).endsWith('/trigger'))).toHaveLength(1)
    expect(within(dialog).queryByRole('button', { name: '确认触发' })).not.toBeInTheDocument()
  })
  it('uses exactly the same key/payload for explicit network recovery and guards a remounted unknown request', async () => {
    let attempt = 0
    const requests = setup(r => { if (path(r).endsWith('/trigger')) { if (++attempt === 1) throw new Error('lost reply'); return Response.json(run, { status: 202 }) } })
    await openApp('/automations'); await openTrigger(); await fillTrigger(); await fireEvent.click(screen.getByRole('button', { name: '确认触发' }))
    await fireEvent.click(await screen.findByRole('button', { name: '用原请求核对' })); await within(screen.getByRole('dialog')).findByText('结果未知，正在确认')
    const writes = requests.filter(r => path(r).endsWith('/trigger')); expect(writes).toHaveLength(2); expect(writes[1]!.headers.get('Idempotency-Key')).toBe(writes[0]!.headers.get('Idempotency-Key')); expect(await writes[1]!.clone().json()).toEqual(await writes[0]!.clone().json())
    expect(JSON.stringify(sessionStorage)).not.toContain('sample@example.test')
    await fireEvent.click(screen.getByRole('button', { name: '关闭对话框' })); await openTrigger()
    expect(screen.queryByRole('button', { name: '检查并确认' })).not.toBeInTheDocument(); expect(screen.getByText(/上次触发尚未确认/)).toBeVisible()
  })
  it.each([['disabled', '工作流已停用'], ['workflow_inactive', '工作流尚未激活'], ['webhook_not_found', '触发入口不存在或尚未激活']])('explains policy rejection %s without retrying', async (reason, text) => {
    const requests = setup(r => path(r).endsWith('/trigger') ? Response.json({ error: { code: 'AUTOMATION_UNAVAILABLE', message: 'not echoed', details: { reason } } }, { status: 409 }) : undefined)
    await openApp('/automations'); await openTrigger(); await fillTrigger(); await fireEvent.click(screen.getByRole('button', { name: '确认触发' })); expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(requests.filter(r => path(r).endsWith('/trigger'))).toHaveLength(1)
  })
  it('shows stale original time/connection state and hides cache after access loss', async () => {
    let status = 200
    setup(r => path(r) === '/api/v1/automations' && status !== 200 ? fail(status, 'HTTP_ERROR') : undefined)
    const { queryClient } = await openApp('/automations'); await screen.findByRole('region', { name: '生成日报' })
    status = 503; await queryClient.invalidateQueries({ queryKey: ['automations'] }); expect(await screen.findByText(/自动化数据可能过期.*截至/)).toBeVisible()
    status = 403; await queryClient.invalidateQueries({ queryKey: ['automations'] }); await vi.waitFor(() => expect(screen.queryByRole('region', { name: '生成日报' })).not.toBeInTheDocument())
  })
  it('clears write-only connection secrets after submit and locks a version conflict until reload', async () => {
    const requests = setup(r => path(r) === '/api/v1/automations/connection' && r.method === 'PUT' ? fail(409, 'VERSION_CONFLICT') : undefined)
    await openApp('/automations'); await fireEvent.click(await screen.findByRole('button', { name: '配置 n8n 连接' }))
    expect(screen.getByLabelText('API Key')).toHaveValue(''); expect(screen.getByLabelText('触发共享密钥')).toHaveValue('')
    await fireEvent.update(screen.getByLabelText('API Key'), 'a'.repeat(20)); await fireEvent.update(screen.getByLabelText('触发共享密钥'), 'b'.repeat(16)); await fireEvent.click(screen.getByRole('button', { name: '保存连接' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/重新加载最新版本/); expect(screen.getByLabelText('API Key')).toHaveValue(''); expect(screen.getByLabelText('触发共享密钥')).toHaveValue('')
    expect(screen.getByRole('button', { name: '保存连接' })).toBeDisabled()
    expect((await requests.find(r => r.method === 'PUT')!.clone().json()).version).toBe(2)
  })
  it('does not restore old automation data or form after a workspace switch', async () => {
    let finish!: (r: Response) => void
    setup(r => {
      if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B', role: 'viewer' }] })
      if (path(r) === '/api/v1/automations') return r.headers.get('X-Workspace-Id') === 'workspace-b' ? Response.json({ ...automations, items: [] }) : new Promise<Response>(resolve => { finish = resolve })
    })
    const { pinia } = await openApp('/automations'); await vi.waitFor(() => expect(finish).toBeDefined()); useAuthStore(pinia).selectWorkspace('workspace-b'); await screen.findByText('暂无白名单工作流。'); finish(Response.json(automations))
    await vi.waitFor(() => expect(screen.queryByRole('region', { name: automation.name })).not.toBeInTheDocument())
  })
})
