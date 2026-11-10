import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import { at, fail, instance, overviewRow, overviewSummary, path } from './agents-fixtures'
import type { Schema } from '../src/services/api'

// T-0207（web 计划 MC-7）：智能体详情的“主模型”——保存只改目标，不冒充已应用；已有实例只能选已授权的连接；
// 失败 / 状态待确认 / 待手动分别说明并能重新应用；旧配置的实例不猜连接；解除要确认。
type View = Schema['ModelBindingView']
type Binding = Schema['AgentModelBindingOut']
const a = instance({ id: 'inst-a', name: '开发助手', status: 'online' })
const OPTIONS: Schema['PickerConnectionPage'] = {
  items: [
    { id: 'conn-1', name: '团队多模型中转', connection_type: 'relay', protocol: 'openai_compatible', base_url: 'https://gateway.example.com/v1', model_count: 2, granted: true },
    { id: 'conn-2', name: 'OpenAI 官方', connection_type: 'official', protocol: 'openai_compatible', base_url: 'https://api.openai.com/v1', model_count: 1, granted: false },
  ],
  next_cursor: null, total: 2,
}
const MODELS: Schema['ConnectionModelPage'] = {
  items: [
    { id: 'm-1', model_id: 'claude-sonnet-4', display_name: 'Claude Sonnet', source: 'manual', availability: 'verified', last_seen_at: null, version: 1 },
    { id: 'm-2', model_id: 'gemini-2.5-pro', display_name: 'gemini-2.5-pro', source: 'manual', availability: 'listed', last_seen_at: null, version: 1 },
  ],
  next_cursor: null, total: 2, synced_at: null, sync_state: null, discovered: [],
}
const binding = (over: Partial<Binding> = {}): Binding => ({
  instance_id: 'inst-a', connection_id: 'conn-1', model_id: 'claude-sonnet-4', desired_revision: 1,
  applied_connection_id: null, applied_model_id: null, applied_revision: null, applied_at: null,
  revoked: false, apply_state: 'pending', last_error_code: null, job_id: 'job-1', version: 1,
  connection_name: '团队多模型中转', applied_connection_name: null, ...over,
})
const view = (b: Binding | null, legacy: View['legacy'] = null): View => ({ binding: b, apply_state: b?.apply_state ?? (legacy ? 'legacy_active' : null), legacy })

function setup(current: () => View, handler?: (r: Request) => Response | Promise<Response | undefined> | undefined) {
  return mockApi(async r => await handler?.(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/servers' && r.method === 'GET' ? Response.json({ items: [], next_cursor: null, total: 0, summary: null }) :
    path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
    path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) :
    path(r) === '/api/v1/agents/overview' ? Response.json({ items: [overviewRow(a)], next_cursor: null, generated_at: at, summary: overviewSummary({ total: 1, online: 1 }) }) :
    path(r) === '/api/v1/agents/instances' && r.method === 'GET' ? Response.json({ items: [a], next_cursor: null, total_estimate: 1 }) :
    path(r).endsWith('/ops') && r.method === 'GET' ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) :
    path(r) === '/api/v1/agents/model-connection-options' ? Response.json(OPTIONS) :
    path(r) === '/api/v1/model-connections/conn-1/models' ? Response.json(MODELS) :
    path(r) === '/api/v1/agents/instances/inst-a/model-binding' && r.method === 'GET' ? Response.json(current()) : undefined
  ))
}
async function panel() {
  await openApp('/agents?instance=inst-a')
  const card = await screen.findByRole('article', { name: '实例：开发助手' })
  return within(card).getByRole('region', { name: '主模型' })
}
const sent = (requests: Request[], method: string, suffix: string) => requests.filter(r => r.method === method && path(r) === `/api/v1/agents/instances/inst-a/model-binding${suffix}`)
afterEach(() => { vi.restoreAllMocks() })

describe('agent primary model', () => {
  it('saves a target without claiming it is applied, and only offers connections granted to this agent', async () => {
    let current = view(null)
    const requests = setup(() => current, async r => {
      if (r.method === 'PUT' && path(r) === '/api/v1/agents/instances/inst-a/model-binding') {
        const body = await r.clone().json() as Schema['ModelBindingPut']
        current = view(binding({ connection_id: body.connection_id, model_id: body.model_id }))
        return Response.json(current.binding)
      }
    })
    const section = await panel()
    expect(await within(section).findByText('未设置')).toBeVisible()
    await fireEvent.click(await within(section).findByRole('button', { name: '选择主模型' }))
    const connection = await within(section).findByLabelText('模型连接 *') as HTMLSelectElement
    await vi.waitFor(() => expect(connection.options.length).toBe(3))
    // 已有实例：未授权的连接不能选，并提示先授权
    const official = [...connection.options].find(o => o.value === 'conn-2')!
    expect(official.disabled).toBe(true)
    expect(official.textContent).toContain('未授权给这个智能体')
    expect(within(section).getByText(/要先在中转站管理的“智能体授权”里授权/)).toBeVisible()
    await fireEvent.update(connection, 'conn-1')
    const model = await within(section).findByLabelText('主模型 *') as HTMLSelectElement
    await vi.waitFor(() => expect(model.options.length).toBe(3))
    expect([...model.options].map(o => o.textContent)).toContain('claude-sonnet-4（Claude Sonnet）')
    await fireEvent.update(model, 'claude-sonnet-4')
    await fireEvent.click(within(section).getByRole('button', { name: '保存' }))
    await vi.waitFor(() => expect(sent(requests, 'PUT', '')).toHaveLength(1))
    expect(await sent(requests, 'PUT', '')[0]!.clone().json()).toEqual({ version: null, connection_id: 'conn-1', model_id: 'claude-sonnet-4' })
    // 保存后是“待应用”：已应用一栏写“还没有确认过”，不显示“已应用”
    expect(await within(section).findByText('待应用')).toBeVisible()
    expect(within(section).getByText('目标').closest('div')).toHaveTextContent('团队多模型中转 · claude-sonnet-4')
    expect(within(section).getByText('已应用').closest('div')).toHaveTextContent('还没有确认过')
    expect(within(section).getByRole('status')).toHaveTextContent('实例取走新配置并确认后才显示“已应用”')
  })

  it('keeps the last applied model on failure and re-applies; unknown and manual states explain themselves', async () => {
    let current = view(binding({
      connection_id: 'conn-1', model_id: 'gemini-2.5-pro', desired_revision: 2, apply_state: 'failed', last_error_code: 'credential_unavailable',
      applied_connection_id: 'conn-1', applied_model_id: 'claude-sonnet-4', applied_revision: 1, applied_at: at, applied_connection_name: '团队多模型中转', version: 4,
    }))
    const requests = setup(() => current, r => {
      if (r.method === 'POST' && path(r) === '/api/v1/agents/instances/inst-a/model-binding/apply') {
        current = view({ ...current.binding!, apply_state: 'pending', last_error_code: null, version: 5 })
        return Response.json({ id: 'job-1', instance_id: 'inst-a', state: 'queued', desired_revision: 2, attempt: 2, last_error_code: null, created_at: at, started_at: null, finished_at: null }, { status: 202 })
      }
    })
    const section = await panel()
    expect(await within(section).findByText('应用失败')).toBeVisible()
    expect(within(section).getByText('已应用').closest('div')).toHaveTextContent('团队多模型中转 · claude-sonnet-4')
    expect(within(section).getByText(/原因：OpenClaw 取不到这条连接的 Key/)).toBeVisible()
    expect(within(section).getByText(/切换确认前，旧连接仍可取 Key/)).toBeVisible()
    // 可以改回实例确认在用的那组（只改目标）
    expect(within(section).getByRole('button', { name: '改回已应用的配置' })).toBeVisible()
    await fireEvent.click(within(section).getByRole('button', { name: '重新应用' }))
    await vi.waitFor(() => expect(sent(requests, 'POST', '/apply')).toHaveLength(1))
    expect(await sent(requests, 'POST', '/apply')[0]!.clone().json()).toEqual({ version: 4 })
    expect(await within(section).findByText('待应用')).toBeVisible()
    // 待应用时不提供“重新应用”与“改回”
    expect(within(section).queryByRole('button', { name: '重新应用' })).not.toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: '改回已应用的配置' })).not.toBeInTheDocument()
  })

  it.each([
    ['unknown', '状态待确认', /10 分钟内没有收到实例的回报/],
    ['manual_required', '待手动应用', /按接入包 CHECKLIST\.md 手动应用/],
  ] as const)('explains %s without calling it applied', async (state, label, hint) => {
    setup(() => view(binding({ apply_state: state, last_error_code: state === 'unknown' ? 'no_report' : 'manual_required' })))
    const section = await panel()
    expect(await within(section).findByText(label)).toBeVisible()
    expect(within(section).getByText(hint)).toBeVisible()
    expect(within(section).getByRole('button', { name: '重新应用' })).toBeVisible()
    expect(within(section).queryByText('已应用', { selector: '.asv-state' })).not.toBeInTheDocument()
  })

  it('shows legacy instances as old configuration without guessing a connection', async () => {
    setup(() => view(null, { legacy_refs: ['openai_api_key'] }))
    const section = await panel()
    expect(await within(section).findByText('旧配置')).toBeVisible()
    expect(within(section).getByText(/接入包里的模型引用 openai_api_key/)).toBeVisible()
    expect(within(section).queryByText('目标')).not.toBeInTheDocument()
    expect(within(section).getByRole('button', { name: '选择主模型' })).toBeVisible()
  })

  it('asks before removing the binding and explains a not-granted error', async () => {
    let current = view(binding({ apply_state: 'applied', applied_connection_id: 'conn-1', applied_model_id: 'claude-sonnet-4', applied_revision: 1, applied_at: at, applied_connection_name: '团队多模型中转', version: 3 }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    const requests = setup(() => current, r => {
      if (r.method === 'DELETE' && path(r) === '/api/v1/agents/instances/inst-a/model-binding') {
        current = view(binding({ connection_id: null, model_id: null, revoked: true, desired_revision: 2, applied_connection_id: 'conn-1', applied_model_id: 'claude-sonnet-4', applied_revision: 1, applied_at: at, applied_connection_name: '团队多模型中转', connection_name: null, version: 4 }))
        return Response.json(current.binding)
      }
      if (r.method === 'PUT' && path(r) === '/api/v1/agents/instances/inst-a/model-binding') return fail(409, 'MODEL_NOT_ALLOWED')
    })
    const section = await panel()
    expect(await within(section).findByText('已应用', { selector: '.asv-state' })).toBeVisible()
    await fireEvent.click(within(section).getByRole('button', { name: '解除' }))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(sent(requests, 'DELETE', '')).toHaveLength(0)
    await fireEvent.click(within(section).getByRole('button', { name: '解除' }))
    await vi.waitFor(() => expect(sent(requests, 'DELETE', '')).toHaveLength(1))
    expect(new URL(sent(requests, 'DELETE', '')[0]!.url).searchParams.get('version')).toBe('3')
    expect(await within(section).findByText('已解除（不使用模型）', { exact: false })).toBeVisible()

    // 选择被后端拒绝（未授权）：显示可操作的说明
    await fireEvent.click(within(section).getByRole('button', { name: '选择主模型' }))
    const connection = await within(section).findByLabelText('模型连接 *') as HTMLSelectElement
    await vi.waitFor(() => expect(connection.options.length).toBe(3))
    await fireEvent.update(connection, 'conn-1')
    const model = await within(section).findByLabelText('主模型 *') as HTMLSelectElement
    await vi.waitFor(() => expect(model.options.length).toBe(3))
    await fireEvent.update(model, 'gemini-2.5-pro')
    await fireEvent.click(within(section).getByRole('button', { name: '保存' }))
    expect(await within(section).findByText(/还没有授权给这个智能体/)).toBeVisible()
  })
})

describe('agent primary model revert', () => {
  it('puts the target back to the last applied pair after a failed switch', async () => {
    let current = view(binding({
      connection_id: 'conn-2', model_id: 'gpt-4.1', desired_revision: 3, apply_state: 'failed', last_error_code: 'invalid_config',
      applied_connection_id: 'conn-1', applied_model_id: 'claude-sonnet-4', applied_revision: 2, applied_at: at, applied_connection_name: '团队多模型中转', connection_name: 'OpenAI 官方', version: 6,
    }))
    const requests = setup(() => current, async r => {
      if (r.method === 'PUT' && path(r) === '/api/v1/agents/instances/inst-a/model-binding') {
        const body = await r.clone().json() as Schema['ModelBindingPut']
        current = view({ ...current.binding!, connection_id: body.connection_id, model_id: body.model_id, connection_name: '团队多模型中转', desired_revision: 4, apply_state: 'pending', last_error_code: null, version: 7 })
        return Response.json(current.binding)
      }
    })
    const section = await panel()
    await fireEvent.click(await within(section).findByRole('button', { name: '改回已应用的配置' }))
    await vi.waitFor(() => expect(sent(requests, 'PUT', '')).toHaveLength(1))
    expect(await sent(requests, 'PUT', '')[0]!.clone().json()).toEqual({ version: 6, connection_id: 'conn-1', model_id: 'claude-sonnet-4' })
    expect(await within(section).findByText('待应用')).toBeVisible()
  })
})
