import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { emptyNavigation, me, mockApi, openApp, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// 中转站管理（T-0206 / MC-6，web 计划 MC-6；规范第 4 节、契约第 1、2 节）：
// 列表的搜索 / 页签 / 状态筛选在服务端、total 与汇总来自服务端；官方预设、第三方中转与多模型表单；Key 不回显、空值不提交；
// 限流 / 凭据 / 不支持 / 过期等检测状态不被当成空成功；迁移草稿与非模型凭据；切换工作空间与迟到的响应；OpenClaw 连接分配。
const path = (r: Request) => new URL(r.url).pathname
const params = (r: Request) => new URL(r.url).searchParams
const at = '2026-11-09T04:00:00Z'
const ID = {
  a: '10000000-0000-4000-8000-00000000000a', b: '10000000-0000-4000-8000-00000000000b', c: '10000000-0000-4000-8000-00000000000c',
  d: '10000000-0000-4000-8000-00000000000d', e: '10000000-0000-4000-8000-00000000000e', n: '10000000-0000-4000-8000-0000000000ff',
}
const recent = () => new Date(Date.now() - 60_000).toISOString()
const check = (over: Partial<Schema['ConnectionCheckOut']> = {}): Schema['ConnectionCheckOut'] => ({
  id: `chk-${Math.random().toString(16).slice(2)}`, connection_id: ID.a, config_revision: 1, mode: 'metadata', model_id: null, status: 'passed', reason: null, checked_at: recent(), latency_ms: 120, retry_at: null, ...over,
})
const conn = (over: Partial<Schema['ModelConnectionOut']> = {}): Schema['ModelConnectionOut'] => ({
  id: ID.a, workspace_id: 'workspace-a', name: '团队多模型中转', description: '', connection_type: 'relay', official_provider: null, protocol: 'openai_compatible',
  base_url: 'https://gateway.example.com/v1', credential_configured: true, credential_version: 1, model_scope: 'selected', configuration_state: 'ready', enabled: true,
  migration_state: 'native', legacy_source_id: null, model_count: 2, model_preview: [], grant_count: 1, binding_count: 0, config_revision: 1, version: 3, last_check: null,
  models_synced_at: null, sync_state: null, created_at: at, updated_at: at, ...over,
})
const modelPage = (ids: string[], over: Partial<Schema['ConnectionModelPage']> = {}): Schema['ConnectionModelPage'] => ({
  items: ids.map((m, i) => ({ id: `m-${i}-${m}`, model_id: m, display_name: '', source: 'manual', availability: 'listed', last_seen_at: null, version: 1 })),
  next_cursor: null, total: ids.length, discovered: [], sync_state: null, synced_at: null, ...over,
})
const page = <T>(items: T[], total = items.length, next_cursor: string | null = null) => Response.json({ items, next_cursor, total })
const conflict = (code: string, details: Record<string, unknown>) => Response.json({ error: { code, message: '冲突', request_id: 'req-test', details } }, { status: 409 })
const binding = (over: Partial<Schema['AgentModelBindingOut']> = {}): Schema['AgentModelBindingOut'] => ({
  instance_id: 'inst-a', connection_id: ID.a, connection_name: 'OpenAI 官方', model_id: 'gpt-4.1', desired_revision: 1, applied_connection_id: ID.a, applied_connection_name: 'OpenAI 官方',
  applied_model_id: 'gpt-4.1', applied_revision: 1, applied_at: at, revoked: false, apply_state: 'applied', last_error_code: null, job_id: null, version: 1, ...over,
})

type Handler = (r: Request) => Response | Promise<Response | undefined> | undefined
/** 页面要的接口都有默认值；连接单项、模型、授权、活动按 byId 给出 */
function setup(handler?: Handler, byId: Record<string, Schema['ModelConnectionOut']> = {}, models: Record<string, string[]> = {}) {
  return mockApi(async r => {
    const result = await handler?.(r)
    if (result) return result
    const p = path(r)
    if (p === '/api/v1/agents/instances' && r.method === 'GET') return page([])
    const single = /^\/api\/v1\/model-connections\/([0-9a-f-]{36})$/.exec(p)
    if (single && r.method === 'GET') return byId[single[1]!] ? Response.json(byId[single[1]!]) : Response.json({ error: { code: 'NOT_FOUND', message: '', request_id: null, details: {} } }, { status: 404 })
    const sub = /^\/api\/v1\/model-connections\/([0-9a-f-]{36})\/(models|grants|activity)$/.exec(p)
    if (sub && r.method === 'GET') {
      if (sub[2] === 'models') return Response.json(modelPage(models[sub[1]!] ?? []))
      if (sub[2] === 'grants') return page([])
      return Response.json({ items: [], next_cursor: null })
    }
    return undefined
  })
}
const lists = (requests: Request[]) => requests.filter(r => r.method === 'GET' && path(r) === '/api/v1/model-connections').map(params)
async function drawer(name: '编辑连接' | '添加连接') { return screen.findByRole('region', { name }) }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('中转站管理：列表与汇总', () => {
  it('test_connections_list_filters_total_and_summary: searches, tabs and filters on the server, counts from the workspace summary, and tells an empty list from a failed load', async () => {
    const A = conn({ id: ID.a, name: 'OpenAI 官方', connection_type: 'official', official_provider: 'openai', base_url: 'https://api.openai.com/v1', model_preview: ['gpt-4.1', 'gpt-4.1-mini'], grant_count: 2, last_check: check({ connection_id: ID.a }) })
    const B = conn({ id: ID.b, name: '团队多模型中转', model_scope: 'all_available', model_count: 5, model_preview: ['GPT-5', 'claude-sonnet-4', 'gemini-2.5-pro'], grant_count: 3 })
    const C = conn({ id: ID.c, name: '备用模型连接', model_count: 1 })
    const requests = setup(r => {
      const p = path(r), q = params(r)
      if (p === '/api/v1/model-connections/summary') return Response.json({ total: 42, check_passed: 7, check_passed_inference: 2, needs_attention: 5, non_model: 1, unique_granted_instances: 9 })
      if (p === '/api/v1/model-connections/migration/entries') return Response.json({ items: [], next_cursor: null, total: 2, counts: { needs_configuration: 1, needs_classification: 0, managed: 1 } })
      if (p === '/api/v1/model-connections' && r.method === 'GET') {
        if (q.get('q') === 'boom') return Response.json({ error: { code: 'HTTP_ERROR', message: '', request_id: null, details: {} } }, { status: 503 })
        if (q.get('q') === 'nothing') return page([], 0)
        if (q.get('cursor') === 'p2') return page([C], 42)
        return page([A, B], 42, 'p2')
      }
    }, {}, { [ID.a]: ['gpt-4.1', 'gpt-4.1-mini'], [ID.b]: ['GPT-5', 'claude-sonnet-4', 'gemini-2.5-pro'], [ID.c]: ['gpt-4.1-mini'] })
    const { router } = await openApp('/settings/model-connections')
    expect(await screen.findByRole('heading', { level: 1, name: '中转站管理' })).toBeVisible()
    // 汇总卡来自 /summary（不是当前页的 2 行）；检测通过分出推理与元数据
    const tiles = screen.getByRole('region', { name: '连接统计' })
    await vi.waitFor(() => expect(within(tiles).getByText('连接总数').closest('div')).toHaveTextContent('连接总数42'))
    expect(within(tiles).getByText('检测通过').closest('div')).toHaveTextContent('检测通过7推理 2 · 元数据 5')
    expect(within(tiles).getByText('待完善').closest('div')).toHaveTextContent('待完善5')
    expect(within(tiles).getByText('关联智能体').closest('div')).toHaveTextContent('关联智能体9')
    expect(tiles).toHaveTextContent('元数据检测不代表模型能推理')
    expect(await screen.findByText(/旧密钥已导入 · 1 项待补全连接信息/)).toBeVisible()

    const list = screen.getByRole('region', { name: '连接列表' })
    const official = await within(list).findByRole('row', { name: /OpenAI 官方/ })
    expect(official).toHaveTextContent('官方直连')
    expect(official).toHaveTextContent('https://api.openai.com/v1')
    expect(official).toHaveTextContent('元数据检测通过')
    expect(official).toHaveTextContent('2 个')
    expect(await within(official).findByText('gpt-4.1')).toBeVisible()
    const relay = within(list).getByRole('row', { name: /团队多模型中转/ })
    expect(relay).toHaveTextContent('第三方中转')
    expect(relay).toHaveTextContent('服务商全部可用模型')
    expect(relay).toHaveTextContent('未检测')
    // 预览用列表接口带回的前 3 个模型 ID，剩下的显示为 +N，不再逐行取模型列表
    expect(relay).toHaveTextContent('gemini-2.5-pro')
    expect(relay).toHaveTextContent('+2')
    expect(requests.some(r => /\/model-connections\/[^/]+\/models$/.test(path(r)))).toBe(false)
    expect(list).toHaveTextContent('共 42 条连接')
    await fireEvent.click(within(list).getByRole('button', { name: '加载更多连接' }))
    expect(await within(list).findByRole('row', { name: /备用模型连接/ })).toBeVisible()
    expect(lists(requests).some(q => q.get('cursor') === 'p2')).toBe(true)

    // 搜索名称 / URL / 模型：服务端 q，从第一页开始；汇总不跟着筛选
    await fireEvent.update(within(list).getByRole('searchbox', { name: '搜索名称、URL 或模型' }), 'gpt')
    await vi.waitFor(() => expect(lists(requests).some(q => q.get('q') === 'gpt')).toBe(true))
    expect(lists(requests).filter(q => q.get('q') === 'gpt').every(q => !q.has('cursor'))).toBe(true)
    await fireEvent.click(screen.getByRole('tab', { name: '官方直连' }))
    await vi.waitFor(() => expect(lists(requests).some(q => q.get('q') === 'gpt' && q.get('type') === 'official')).toBe(true))
    expect(router.currentRoute.value.query.tab).toBe('official')
    await fireEvent.update(within(list).getByRole('combobox', { name: '按状态筛选' }), 'disabled')
    await fireEvent.update(within(list).getByRole('combobox', { name: '按检测结果筛选' }), 'failed')
    await vi.waitFor(() => expect(lists(requests).some(q => q.get('enabled') === 'false' && q.get('check_status') === 'failed' && q.get('type') === 'official')).toBe(true))
    expect(requests.filter(r => path(r) === '/api/v1/model-connections/summary').every(r => new URL(r.url).search === '')).toBe(true)

    // 空列表与加载失败分开说
    await fireEvent.update(within(list).getByRole('searchbox', { name: '搜索名称、URL 或模型' }), 'nothing')
    expect(await within(list).findByText('没有符合条件的连接。')).toBeVisible()
    await fireEvent.update(within(list).getByRole('searchbox', { name: '搜索名称、URL 或模型' }), 'boom')
    expect(await within(list).findByRole('alert')).toHaveTextContent('服务暂时不可用')
    expect(within(list).queryByText('没有符合条件的连接。')).toBeNull()
    expect(within(list).getByRole('button', { name: '重试' })).toBeVisible()
    // 汇总仍是全工作空间的数
    expect(within(tiles).getByText('连接总数').closest('div')).toHaveTextContent('连接总数42')

    await fireEvent.click(screen.getByRole('button', { name: /查看迁移记录/ }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.tab).toBe('compat'))
  })

  it('confirms the impact before disabling and explains why a connection in use cannot be deleted', async () => {
    let A = conn({ id: ID.a, name: 'OpenAI 官方', version: 3, grant_count: 1, binding_count: 1 })
    const impact = { instances: [{ instance_id: 'inst-a', instance_name: '开发助手', instance_status: 'online', bound: true, created_at: at }], bound_instances: 1, pending_jobs: 0 }
    const requests = setup(async r => {
      if (path(r) === '/api/v1/model-connections' && r.method === 'GET') return page([A])
      if (r.method === 'PATCH') {
        const body = await r.clone().json() as Schema['ModelConnectionUpdate']
        if (!body.confirm_impact) return conflict('CONNECTION_IN_USE', { impact })
        A = { ...A, enabled: false, version: 4 }
        return Response.json(A)
      }
      if (r.method === 'DELETE') return conflict('CONNECTION_IN_USE', { legacy_grants: 2 })
    }, { [ID.a]: A }, { [ID.a]: ['gpt-4.1'] })
    await openApp('/settings/model-connections')
    const list = screen.getByRole('region', { name: '连接列表' })
    await within(list).findByRole('row', { name: /OpenAI 官方/ })
    await fireEvent.keyDown(within(list).getByRole('button', { name: '更多操作：OpenAI 官方' }), { key: 'Enter' })
    await fireEvent.click(await screen.findByRole('menuitem', { name: '停用连接' }))
    const dialog = await screen.findByRole('dialog', { name: '确认停用连接' })
    expect(dialog).toHaveTextContent('开发助手（以它为主模型）')
    expect(dialog).toHaveTextContent('已在进程里的 Key 不保证立即失效')
    await fireEvent.click(within(dialog).getByRole('button', { name: '确认停用' }))
    await vi.waitFor(() => expect(requests.filter(r => r.method === 'PATCH')).toHaveLength(2))
    const bodies = await Promise.all(requests.filter(r => r.method === 'PATCH').map(r => r.clone().json()))
    expect(bodies).toEqual([{ version: 3, enabled: false, confirm_impact: false }, { version: 3, enabled: false, confirm_impact: true }])
    expect(await screen.findByText(/「OpenAI 官方」已停用/)).toBeVisible()
    expect(screen.queryByRole('dialog')).toBeNull()
    // 停用与检测结果分开显示
    await vi.waitFor(() => expect(within(list).getByRole('row', { name: /OpenAI 官方/ })).toHaveTextContent('已停用'))

    await fireEvent.keyDown(within(list).getByRole('button', { name: '更多操作：OpenAI 官方' }), { key: 'Enter' })
    await fireEvent.click(await screen.findByRole('menuitem', { name: '删除连接' }))
    const confirm = await screen.findByRole('dialog', { name: '删除连接' })
    await fireEvent.click(within(confirm).getByRole('button', { name: '删除' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'DELETE')).toBe(true))
    expect(params(requests.find(r => r.method === 'DELETE')!).get('version')).toBe('4')
    expect(await within(confirm).findByRole('alert')).toHaveTextContent('仍被旧配置或运行环境引用（2 处旧授权）')
    expect(screen.getByRole('dialog', { name: '删除连接' })).toBeVisible()
    expect(screen.queryByText(/已删除/)).toBeNull()
  })
})

describe('中转站管理：连接表单', () => {
  it('test_official_relay_and_multi_model_form: fills the official preset, adds several model IDs to a relay, and saves all-available models as a draft', async () => {
    const created = conn({ id: ID.n, name: 'OpenAI 官方', connection_type: 'official', official_provider: 'openai', base_url: 'https://api.openai.com/v1', version: 1 })
    const posts: Schema['ModelConnectionCreate'][] = []
    const requests = setup(async r => {
      if (path(r) === '/api/v1/model-connections' && r.method === 'POST') { posts.push(await r.clone().json()); return Response.json(created, { status: 201 }) }
    }, { [ID.n]: created }, { [ID.n]: ['gpt-4.1', 'gpt-4.1-mini'] })
    const { router } = await openApp('/settings/model-connections')
    await fireEvent.click(await screen.findByRole('button', { name: '添加连接' }))
    let panel = await drawer('添加连接')
    let form = within(panel).getByRole('form', { name: '添加连接' })
    // 协议只列 capabilities 里开放的
    const protocol = within(form).getByLabelText('接口协议') as HTMLSelectElement
    await vi.waitFor(() => expect([...protocol.options].filter(o => !o.disabled).map(o => o.textContent)).toEqual(['OpenAI 兼容']))
    await fireEvent.click(within(form).getByRole('radio', { name: '官方直连' }))
    const url = within(form).getByLabelText('连接 URL') as HTMLInputElement
    expect(url).toHaveValue('https://api.openai.com/v1')
    expect(url).toHaveAttribute('readonly')
    expect(protocol).toHaveValue('openai_compatible')
    expect(protocol).toBeDisabled()
    expect(form).toHaveTextContent('要用别的地址请切换为“第三方中转”')
    await fireEvent.update(within(form).getByLabelText('连接名称'), 'OpenAI 官方')
    const key = within(form).getByLabelText('API Key')
    expect(key).toHaveAttribute('type', 'password')
    expect(key).toHaveAttribute('autocomplete', 'new-password')
    await fireEvent.update(key, 'sk-official-test')
    await fireEvent.update(within(form).getByLabelText('添加模型 ID'), 'gpt-4.1 gpt-4.1-mini')
    await fireEvent.click(within(form).getByRole('button', { name: '添加' }))
    expect(within(within(form).getByRole('list', { name: '已选模型' })).getAllByRole('listitem').map(li => li.textContent?.trim())).toEqual(['gpt-4.1', 'gpt-4.1-mini'])
    await fireEvent.click(within(form).getByRole('button', { name: '保存配置' }))
    await vi.waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toEqual({ name: 'OpenAI 官方', description: '', connection_type: 'official', official_provider: 'openai', protocol: 'openai_compatible', base_url: 'https://api.openai.com/v1', api_key: 'sk-official-test', model_scope: 'selected', models: [{ model_id: 'gpt-4.1' }, { model_id: 'gpt-4.1-mini' }], save_as_draft: false })
    expect(requests.find(r => r.method === 'POST')!.headers.get('Idempotency-Key')).toBeTruthy()
    // 保存后切到这条连接的详情；Key 不再出现
    await vi.waitFor(() => expect(router.currentRoute.value.query.connection).toBe(ID.n))
    panel = await drawer('编辑连接')
    expect(await within(panel).findByRole('heading', { name: 'OpenAI 官方' })).toBeVisible()
    expect(within(panel).getByText('已加密保存')).toBeVisible()
    expect(screen.queryByDisplayValue('sk-official-test')).toBeNull()

    // 第三方中转：URL 不能带查询参数；“服务商全部可用模型”新建时只能先存草稿
    await fireEvent.click(screen.getByRole('button', { name: '添加连接' }))
    panel = await drawer('添加连接')
    form = within(panel).getByRole('form', { name: '添加连接' })
    expect(within(form).getByRole('radio', { name: '第三方中转' })).toBeChecked()
    expect(within(form).getByLabelText('接口协议')).toHaveValue('openai_compatible')
    await fireEvent.update(within(form).getByLabelText('连接名称'), '团队多模型中转')
    await fireEvent.update(within(form).getByLabelText('API Key'), 'sk-relay-test')
    await fireEvent.update(within(form).getByLabelText('连接 URL'), 'https://gateway.example.com/v1?key=abc')
    expect(within(form).getByRole('alert')).toHaveTextContent('不能带查询参数')
    await fireEvent.update(within(form).getByLabelText('连接 URL'), 'https://gateway.example.com/v1')
    await fireEvent.click(within(form).getByRole('radio', { name: '服务商全部可用模型' }))
    expect(form).toHaveTextContent('新建时先保存为草稿，再点“同步模型”')
    await fireEvent.click(within(form).getByRole('button', { name: '保存为草稿' }))
    await vi.waitFor(() => expect(posts).toHaveLength(2))
    expect(posts[1]).toMatchObject({ connection_type: 'relay', official_provider: null, protocol: 'openai_compatible', base_url: 'https://gateway.example.com/v1', model_scope: 'all_available', models: [], save_as_draft: true })
  })

  it('test_saved_key_is_never_revealed_or_submitted_blank: shows only “saved”, edits without the key, rotates in its own form and clears every typed key', async () => {
    let A = conn({ id: ID.a, name: '团队多模型中转', version: 7 })
    const requests = setup(async r => {
      if (path(r) === `/api/v1/model-connections/${ID.a}` && r.method === 'GET') return Response.json({ ...A, api_key: 'sk-should-never-render', credential_ref: 'secret-ref-x' })
      if (r.method === 'PATCH') { A = { ...A, ...(await r.clone().json()), version: A.version + 1 }; return Response.json(A) }
      if (path(r).endsWith('/rotate-key')) { A = { ...A, version: A.version + 1, credential_version: 2 }; return Response.json(A) }
    }, {}, { [ID.a]: ['gpt-4.1'] })
    const { router, queryClient } = await openApp(`/settings/model-connections?connection=${ID.a}`)
    const panel = await drawer('编辑连接')
    expect(await within(panel).findByRole('heading', { name: '团队多模型中转' })).toBeVisible()
    expect(panel).toHaveTextContent('已加密保存，原值不回显')
    expect(panel).not.toHaveTextContent('sk-should-never-render')
    expect(panel).not.toHaveTextContent('secret-ref-x')
    expect(screen.queryByDisplayValue('sk-should-never-render')).toBeNull()
    expect(within(panel).queryByRole('button', { name: /显示|查看 Key|导出|复制 Key/ })).toBeNull()

    // 编辑名称：PATCH 只有改过的字段，没有 Key
    await fireEvent.update(within(panel).getByLabelText('连接名称'), '团队中转')
    await fireEvent.click(within(panel).getByRole('button', { name: '保存配置' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'PATCH')).toBe(true))
    expect(await requests.find(r => r.method === 'PATCH')!.clone().json()).toEqual({ version: 7, confirm_impact: false, name: '团队中转' })
    expect(await within(panel).findByText('配置已保存。')).toBeVisible()
    expect(await within(panel).findByRole('heading', { name: '团队中转' })).toBeVisible()
    // 保存后没有未保存的修改
    await vi.waitFor(() => expect(within(panel).getByRole('button', { name: '保存配置' })).toBeDisabled())

    // 更换 Key：独立表单，空值不能提交；取消后清空
    await fireEvent.click(within(panel).getByRole('button', { name: '更换' }))
    let replace = within(panel).getByRole('group', { name: '更换 API Key' })
    expect(within(replace).getByRole('button', { name: '确认更换' })).toBeDisabled()
    await fireEvent.update(within(replace).getByLabelText('新的 API Key'), 'sk-typed-then-cancelled')
    await fireEvent.click(within(replace).getByRole('button', { name: '取消' }))
    expect(screen.queryByDisplayValue('sk-typed-then-cancelled')).toBeNull()
    await fireEvent.click(within(panel).getByRole('button', { name: '更换' }))
    replace = within(panel).getByRole('group', { name: '更换 API Key' })
    expect(within(replace).getByLabelText('新的 API Key')).toHaveValue('')
    await fireEvent.update(within(replace).getByLabelText('新的 API Key'), 'sk-rotated')
    await fireEvent.click(within(replace).getByRole('button', { name: '确认更换' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/rotate-key'))).toBe(true))
    const rotations = requests.filter(r => path(r).endsWith('/rotate-key'))
    expect(rotations).toHaveLength(1)
    expect(await rotations[0]!.clone().json()).toEqual({ version: 8, api_key: 'sk-rotated' })
    expect(await within(panel).findByText(/Key 已更换/)).toBeVisible()
    expect(within(panel).queryByRole('group', { name: '更换 API Key' })).toBeNull()
    expect(screen.queryByDisplayValue('sk-rotated')).toBeNull()
    expect(router.currentRoute.value.fullPath).not.toContain('sk-')
    const cached = JSON.stringify(queryClient.getQueryCache().getAll().map(q => [q.queryKey, q.state.data]))
    expect(cached).not.toContain('sk-rotated')
    expect(JSON.stringify({ ...localStorage })).not.toContain('sk-')
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('sk-')

    // 新建表单里的 Key：取消后再打开是空的
    await fireEvent.click(screen.getByRole('button', { name: '添加连接' }))
    let create = await drawer('添加连接')
    await fireEvent.update(within(create).getByLabelText('API Key'), 'sk-draft-key')
    await fireEvent.click(within(create).getByRole('button', { name: '取消' }))
    await vi.waitFor(() => expect(screen.queryByRole('region', { name: '添加连接' })).toBeNull())
    await fireEvent.click(screen.getByRole('button', { name: '添加连接' }))
    create = await drawer('添加连接')
    expect(within(create).getByLabelText('API Key')).toHaveValue('')
    expect(screen.queryByDisplayValue('sk-draft-key')).toBeNull()
  })

  it('offers “copy as new connection” when the endpoint of a connection in use cannot change, with the key left empty', async () => {
    const A = conn({ id: ID.a, name: '团队多模型中转', grant_count: 2, version: 5 })
    const requests = setup(r => (r.method === 'PATCH' ? conflict('CONNECTION_ENDPOINT_IN_USE', { grant_count: 2, bound_instances: 1 }) : undefined), { [ID.a]: A }, { [ID.a]: ['gpt-4.1', 'claude-sonnet-4'] })
    await openApp(`/settings/model-connections?connection=${ID.a}`)
    const panel = await drawer('编辑连接')
    await within(panel).findByRole('heading', { name: '团队多模型中转' })
    await fireEvent.update(within(panel).getByLabelText('连接 URL'), 'https://new-gateway.example.com/v1')
    await fireEvent.click(within(panel).getByRole('button', { name: '保存配置' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'PATCH')).toBe(true))
    expect(await requests.find(r => r.method === 'PATCH')!.clone().json()).toEqual({ version: 5, confirm_impact: false, base_url: 'https://new-gateway.example.com/v1' })
    expect(await within(panel).findByText('不能直接修改地址或协议')).toBeVisible()
    await fireEvent.click(within(panel).getByRole('button', { name: '复制为新连接' }))
    const create = await drawer('添加连接')
    expect(within(create).getByLabelText('连接名称')).toHaveValue('团队多模型中转 副本')
    expect(within(create).getByLabelText('连接 URL')).toHaveValue('https://new-gateway.example.com/v1')
    expect(within(create).getByLabelText('API Key')).toHaveValue('')
    expect(within(within(create).getByRole('list', { name: '已选模型' })).getAllByRole('listitem').map(li => li.textContent?.trim())).toEqual(['gpt-4.1', 'claude-sonnet-4'])
  })
})

describe('中转站管理：检测与同步状态', () => {
  it('test_statuses_do_not_collapse_to_empty: rate limit, credentials, unsupported model lists and stale results each say what they are', async () => {
    const old = new Date(Date.now() - 60 * 60_000).toISOString()
    const L = conn({ id: ID.a, name: '限流连接', last_check: check({ connection_id: ID.a, status: 'rate_limited', retry_at: '2026-11-10T12:30:00Z' }) })
    const U = conn({ id: ID.b, name: 'Key 失效', last_check: check({ connection_id: ID.b, status: 'failed', reason: 'unauthorized' }) })
    const N = conn({ id: ID.c, name: '不能列模型', last_check: check({ connection_id: ID.c, status: 'unsupported', reason: 'models_endpoint_unsupported' }) })
    const S = conn({ id: ID.d, name: '旧检测', last_check: check({ connection_id: ID.d, checked_at: old }) })
    const I = conn({ id: ID.e, name: '推理通过', last_check: check({ connection_id: ID.e, mode: 'inference', model_id: 'gpt-4.1' }) })
    const byId = { [ID.a]: L, [ID.b]: U, [ID.c]: N, [ID.d]: S, [ID.e]: I }
    const requests = setup(r => {
      if (path(r) === '/api/v1/model-connections' && r.method === 'GET') return page([L, U, N, S, I])
      if (path(r).endsWith('/checks')) return Response.json(check({ connection_id: ID.b, mode: 'inference', model_id: 'gpt-4.1', status: 'rate_limited', retry_at: null }))
      if (path(r).endsWith('/models/sync')) return Response.json({ sync_state: 'unsupported', total: 0, added: 0, removed: 0, reason: 'models_endpoint_unsupported', retry_at: null, kept_previous: true, kept_in_use: [], discovered: [], connection: U })
    }, byId, { [ID.a]: ['gpt-4.1'], [ID.b]: ['gpt-4.1', 'gpt-4.1-mini'], [ID.c]: ['gpt-4.1'], [ID.d]: ['gpt-4.1'], [ID.e]: ['gpt-4.1'] })
    await openApp('/settings/model-connections')
    const list = screen.getByRole('region', { name: '连接列表' })
    const row = (name: RegExp) => within(list).getByRole('row', { name })
    await within(list).findByRole('row', { name: /限流连接/ })
    expect(row(/限流连接/)).toHaveTextContent('服务商限流中')
    expect(row(/Key 失效/)).toHaveTextContent('凭据失效或权限不足')
    expect(row(/不能列模型/)).toHaveTextContent('需手动配置模型')
    expect(row(/旧检测/)).toHaveTextContent('元数据检测通过')
    expect(row(/旧检测/)).toHaveTextContent('结果可能已过期')
    expect(row(/推理通过/)).toHaveTextContent('推理检测通过')
    for (const name of [/限流连接/, /Key 失效/, /不能列模型/]) expect(row(name)).not.toHaveTextContent('检测通过')

    await fireEvent.click(within(list).getByRole('button', { name: '管理：限流连接' }))
    let panel = await drawer('编辑连接')
    let result = await within(panel).findByRole('region', { name: '检测结果' })
    expect(result).toHaveTextContent('服务商限流中')
    expect(result).toHaveTextContent('可在')
    expect(result).toHaveTextContent('之后重试')
    await fireEvent.click(within(list).getByRole('button', { name: '管理：旧检测' }))
    await screen.findByRole('heading', { name: '旧检测' })
    panel = await drawer('编辑连接')
    result = within(panel).getByRole('region', { name: '检测结果' })
    expect(result).toHaveTextContent('结果可能已过期')
    expect(result).toHaveTextContent('检测于')
    expect(result).toHaveTextContent('不代表任何模型能推理')

    await fireEvent.click(within(list).getByRole('button', { name: '管理：Key 失效' }))
    await screen.findByRole('heading', { name: 'Key 失效' })
    panel = await drawer('编辑连接')
    expect(within(panel).getByRole('region', { name: '检测结果' })).toHaveTextContent('请用“更换”填写新的 API Key')
    // 推理检测：先提示费用，必须选模型
    await fireEvent.click(within(panel).getByRole('button', { name: '检测连接' }))
    const checking = within(panel).getByRole('group', { name: '检测连接' })
    await fireEvent.click(within(checking).getByRole('radio', { name: /推理检测/ }))
    expect(checking).toHaveTextContent('可能产生少量服务商费用')
    expect(within(checking).getByRole('button', { name: '开始检测' })).toBeDisabled()
    await vi.waitFor(() => expect(within(checking).getByRole('option', { name: 'gpt-4.1' })).toBeInTheDocument())
    await fireEvent.update(within(checking).getByLabelText('检测的模型'), 'gpt-4.1')
    await fireEvent.click(within(checking).getByRole('button', { name: '开始检测' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/checks'))).toBe(true))
    expect(await requests.find(r => path(r).endsWith('/checks'))!.clone().json()).toEqual({ version: 3, mode: 'inference', model_id: 'gpt-4.1' })
    await vi.waitFor(() => expect(within(panel).getByRole('region', { name: '检测结果' })).toHaveTextContent('服务商没有给出可重试时间'))
    expect(within(panel).getByRole('region', { name: '检测结果' })).toHaveTextContent('推理检测')

    // 同步：服务商不支持列模型时说明要手工填写，不显示“已同步 0 个”
    await fireEvent.click(within(panel).getByRole('button', { name: '同步模型' }))
    expect(await within(panel).findByText(/服务商不支持列出模型：请用“指定模型”手工填写/)).toBeVisible()
    expect(panel).not.toHaveTextContent('已同步 0 个')
  })
})

describe('中转站管理：迁移与兼容凭据', () => {
  it('test_migration_drafts_and_non_model_credentials: completes an imported draft without creating bindings, classifies an entry and keeps managing non-model credentials', async () => {
    const SEC = '20000000-0000-4000-8000-000000000001'
    let draft = conn({ id: ID.a, name: 'news_model', connection_type: null, protocol: null, base_url: null, configuration_state: 'draft', migration_state: 'needs_configuration', legacy_source_id: SEC, model_count: 0, grant_count: 1, version: 4 })
    const token = conn({ id: ID.b, name: 'github_token', connection_type: null, protocol: null, base_url: null, configuration_state: 'draft', migration_state: 'needs_classification', model_count: 0, grant_count: 0, version: 2 })
    const nonModel = conn({ id: ID.c, name: 'deploy_token', connection_type: null, protocol: null, base_url: null, configuration_state: 'draft', migration_state: 'non_model', model_count: 0, version: 1 })
    const entry = (c: Schema['ModelConnectionOut'], legacy_kind: Schema['MigrationEntryOut']['legacy_kind'], refs: string[]): Schema['MigrationEntryOut'] => ({ entry_id: c.id, connection: c, legacy_name: c.name, legacy_kind, legacy_grant_refs: refs })
    const secret: Schema['AgentSecretOut'] = { id: SEC, name: 'deploy_token', kind: 'api_token', description: '部署用', value_version: 1, last_rotated_at: at, grants: [], version: 2, created_at: at, updated_at: at }
    const requests = setup(async r => {
      const p = path(r)
      if (p === '/api/v1/model-connections/migration/entries') return Response.json({ items: [entry(draft, 'model_api_key', ['openai_api_key']), entry(token, 'api_token', ['github_token']), entry(nonModel, 'api_token', [])], next_cursor: null, total: 3, counts: { needs_configuration: 1, needs_classification: 1, non_model: 1, managed: 0 } })
      if (p.endsWith('/classify')) return Response.json(entry({ ...token, migration_state: 'non_model', version: 3 }, 'api_token', ['github_token']))
      if (p === `/api/v1/model-connections/${ID.a}` && r.method === 'GET') return Response.json(draft)
      if (p === `/api/v1/model-connections/${ID.a}` && r.method === 'PATCH') { draft = { ...draft, ...(await r.clone().json()), version: 5 }; return Response.json(draft) }
      if (p === `/api/v1/model-connections/${ID.a}/models` && r.method === 'PUT') { draft = { ...draft, configuration_state: 'ready', migration_state: 'managed', model_count: 1, version: 6 }; return Response.json(draft) }
      if (p === '/api/v1/agents/secrets' && r.method === 'GET') return page([secret])
      if (p === `/api/v1/agents/secrets/${SEC}`) return Response.json(secret)
      if (p === '/api/v1/agents/secrets/accesses') return Response.json({ items: [], next_cursor: null })
    })
    // 旧的密钥库深链接：跳到“迁移与兼容凭据”，保留 ?secret=
    const { router } = await openApp(`/agents/secrets?secret=${SEC}`)
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/settings/model-connections'))
    expect(router.currentRoute.value.query).toEqual({ tab: 'compat', secret: SEC })
    expect(screen.getByRole('tab', { name: '迁移与兼容凭据' })).toHaveAttribute('aria-selected', 'true')
    // 非模型凭据仍能管理：原密钥库的详情、轮换都还在
    const compat = await screen.findByRole('region', { name: '兼容凭据' })
    expect(within(compat).getByRole('heading', { level: 2, name: '兼容凭据' })).toBeVisible()
    const secretDetail = await screen.findByRole('region', { name: '密钥详情' })
    expect(await within(secretDetail).findByRole('heading', { name: 'deploy_token' })).toBeVisible()
    expect(within(secretDetail).getByRole('button', { name: '轮换密钥' })).toBeVisible()

    const migration = screen.getByRole('region', { name: '迁移记录' })
    const draftRow = await within(migration).findByRole('row', { name: /news_model/ })
    expect(draftRow).toHaveTextContent('待完善')
    expect(draftRow).toHaveTextContent('openai_api_key')
    expect(within(migration).getByRole('row', { name: /github_token/ })).toHaveTextContent('待归类')
    expect(within(migration).getByRole('row', { name: /deploy_token/ })).toHaveTextContent('非模型凭据')
    expect(within(migration).getByRole('button', { name: /待完善\s*1/ })).toBeVisible()
    await fireEvent.click(within(migration).getByRole('button', { name: '归为非模型凭据：github_token' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/classify'))).toBe(true))
    const classify = requests.find(r => path(r).endsWith('/classify'))!
    expect(path(classify)).toBe(`/api/v1/model-connections/migration/entries/${ID.b}/classify`)
    expect(await classify.clone().json()).toEqual({ version: 2, purpose: 'non_model' })
    expect(await within(migration).findByText(/已归为非模型凭据：仍可在下方“兼容凭据”里管理/)).toBeVisible()

    // 补全待完善的导入项：只改连接元数据与模型，不建绑定
    await fireEvent.click(within(migration).getByRole('button', { name: '补全配置：news_model' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.connection).toBe(ID.a))
    expect(router.currentRoute.value.query.secret).toBeUndefined()
    const panel = await drawer('编辑连接')
    expect(await within(panel).findByRole('heading', { name: 'news_model' })).toBeVisible()
    expect(panel).toHaveTextContent('待完善')
    await fireEvent.update(within(panel).getByLabelText('连接 URL'), 'https://news-gateway.example.com/v1')
    await fireEvent.update(within(panel).getByLabelText('添加模型 ID'), 'news-model-1')
    await fireEvent.click(within(panel).getByRole('button', { name: '添加' }))
    await fireEvent.click(within(panel).getByRole('button', { name: '保存配置' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'PUT' && path(r).endsWith('/models'))).toBe(true))
    expect(await requests.find(r => r.method === 'PATCH')!.clone().json()).toEqual({ version: 4, confirm_impact: false, connection_type: 'relay', protocol: 'openai_compatible', base_url: 'https://news-gateway.example.com/v1' })
    expect(await requests.find(r => r.method === 'PUT' && path(r).endsWith('/models'))!.clone().json()).toEqual({ version: 5, model_scope: 'selected', models: [{ model_id: 'news-model-1', display_name: null }] })
    expect(await within(panel).findByText('配置已保存。')).toBeVisible()
    expect(requests.some(r => path(r).includes('/model-binding'))).toBe(false)

    // 兼容凭据里新建只给非模型类型
    await fireEvent.click(within(compat).getByRole('button', { name: '添加密钥' }))
    const dialog = await screen.findByRole('dialog', { name: '添加密钥' })
    expect(within(dialog).queryByRole('option', { name: '模型服务密钥' })).toBeNull()
    expect(dialog).toHaveTextContent('模型服务的 API Key 请在中转站管理里“添加连接”保存')
  })
})

describe('中转站管理：身份与工作空间', () => {
  it('test_workspace_switch_and_stale_responses: drops typed keys and never shows a late response from the previous workspace', async () => {
    let finishA: ((response: Response) => void) | undefined
    const requests = setup(r => {
      const p = path(r), scope = r.headers.get('X-Workspace-Id')
      if (p === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: '空间 B' }] })
      if (p === '/api/v1/model-connections/summary') return Response.json({ total: scope === 'workspace-a' ? 11 : 22, check_passed: 0, check_passed_inference: 0, needs_attention: 0, non_model: 0, unique_granted_instances: 0 })
      if (p === '/api/v1/model-connections' && r.method === 'GET') {
        if (scope === 'workspace-a') return new Promise<Response>(resolve => { finishA = resolve })
        return page([conn({ id: ID.b, name: 'B 空间的连接', workspace_id: 'workspace-b' })])
      }
    })
    await openApp('/settings/model-connections')
    const tiles = screen.getByRole('region', { name: '连接统计' })
    await vi.waitFor(() => expect(within(tiles).getByText('连接总数').closest('div')).toHaveTextContent('连接总数11'))
    await vi.waitFor(() => expect(finishA).toBeDefined())
    await fireEvent.click(screen.getByRole('button', { name: '添加连接' }))
    await fireEvent.update(within(await drawer('添加连接')).getByLabelText('API Key'), 'sk-typed-in-a')

    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b')
    const list = await screen.findByRole('region', { name: '连接列表' })
    expect(await within(list).findByRole('row', { name: /B 空间的连接/ })).toBeVisible()
    await vi.waitFor(() => expect(within(screen.getByRole('region', { name: '连接统计' })).getByText('连接总数').closest('div')).toHaveTextContent('连接总数22'))
    expect(screen.queryByDisplayValue('sk-typed-in-a')).toBeNull()
    expect(screen.queryByRole('region', { name: '添加连接' })).toBeNull()

    // 上一个工作空间迟到的响应：不显示
    finishA!(page([conn({ id: ID.a, name: 'A 空间的连接' })]))
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(screen.queryByText('A 空间的连接')).toBeNull()
    expect(within(list).getByRole('row', { name: /B 空间的连接/ })).toBeVisible()
    expect(requests.filter(r => r.method === 'GET' && path(r) === '/api/v1/model-connections').map(r => r.headers.get('X-Workspace-Id'))).toEqual(['workspace-a', 'workspace-b'])
  })

  it('is only for the owner and replaces the secret vault in the sidebar, keeping a saved placement of the old path', async () => {
    setup(r => {
      if (path(r) === '/api/v1/navigation' && r.method === 'GET') return Response.json({ ...emptyNavigation, version: 2, placements: { '/agents/secrets': 'tools' } })
    })
    await openApp('/settings/model-connections')
    const nav = screen.getByRole('navigation', { name: '主导航' })
    const tools = document.getElementById(within(nav).getByRole('button', { name: '连接与工具' }).getAttribute('aria-controls')!)!
    await vi.waitFor(() => expect(within(tools).getByRole('link', { name: '中转站管理' })).toHaveAttribute('href', '/settings/model-connections'))
    expect(within(nav).getAllByRole('link', { name: '中转站管理' })).toHaveLength(1)
    expect(within(nav).queryByRole('link', { name: '密钥库' })).toBeNull()
  })

  it('sends admins to the forbidden page', async () => {
    mockApi(r => (path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'admin' }] }) : undefined))
    const { router } = await openApp('/settings/model-connections')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
    expect(screen.queryByRole('link', { name: '中转站管理' })).toBeNull()
  })
})

describe('中转站管理：OpenClaw 连接分配', () => {
  it('shows target and applied connection separately, legacy agents as old configuration, and changes the connection with the picker', async () => {
    const rows: Schema['ModelAssignmentRow'][] = [
      { instance_id: 'inst-a', instance_name: '开发助手', instance_status: 'online', legacy: null, binding: binding({ connection_id: ID.b, connection_name: '团队多模型中转', model_id: 'claude-sonnet-4', desired_revision: 2, applied_revision: 1, apply_state: 'pending', version: 3 }) },
      { instance_id: 'inst-b', instance_name: '新闻助手', instance_status: 'online', binding: null, legacy: { legacy_refs: ['openai_api_key'] } },
      { instance_id: 'inst-c', instance_name: '知识助手', instance_status: 'offline', legacy: null, binding: binding({ instance_id: 'inst-c', apply_state: 'failed', last_error_code: 'credential_unavailable', desired_revision: 2, applied_revision: 2 }) },
    ]
    const options: Schema['PickerConnectionPage'] = { items: [
      { id: ID.a, name: 'OpenAI 官方', connection_type: 'official', base_url: 'https://api.openai.com/v1', protocol: 'openai_compatible', model_count: 2, granted: true },
      { id: ID.c, name: '未授权的中转', connection_type: 'relay', base_url: 'https://other.example.com/v1', protocol: 'openai_compatible', model_count: 1, granted: false },
    ], next_cursor: null, total: 2 }
    const requests = setup(r => {
      const p = path(r)
      if (p === '/api/v1/agents/model-assignments') return Response.json({ items: rows, next_cursor: null, total: 3 })
      if (p === '/api/v1/agents/model-connection-options') return Response.json(options)
      if (p === '/api/v1/agents/instances/inst-a/model-binding' && r.method === 'PUT') return Response.json(binding({ model_id: 'gpt-4.1', desired_revision: 3, apply_state: 'pending', version: 4 }))
    }, {}, { [ID.a]: ['gpt-4.1', 'gpt-4.1-mini'] })
    await openApp('/settings/model-connections')
    const card = await screen.findByRole('region', { name: 'OpenClaw 连接分配' })
    const dev = await within(card).findByRole('row', { name: /开发助手/ })
    expect(dev).toHaveTextContent('团队多模型中转')
    expect(dev).toHaveTextContent('已应用：OpenAI 官方')
    expect(dev).toHaveTextContent('claude-sonnet-4')
    expect(dev).toHaveTextContent('已应用：gpt-4.1')
    expect(dev).toHaveTextContent('待应用')
    expect(within(card).getByRole('row', { name: /新闻助手/ })).toHaveTextContent('旧配置')
    expect(within(card).getByRole('row', { name: /新闻助手/ })).toHaveTextContent('openai_api_key')
    expect(within(card).getByRole('row', { name: /知识助手/ })).toHaveTextContent('应用失败')
    expect(within(card).getByRole('row', { name: /知识助手/ })).toHaveTextContent('OpenClaw 取不到这条连接的 Key')

    await fireEvent.click(within(dev).getByRole('button', { name: '更换连接：开发助手' }))
    const dialog = await screen.findByRole('dialog', { name: '更换连接：开发助手' })
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/agents/model-connection-options' && params(r).get('instance_id') === 'inst-a')).toBe(true))
    const select = within(dialog).getByLabelText('模型连接 *') as HTMLSelectElement
    await vi.waitFor(() => expect(within(select).getByRole('option', { name: /OpenAI 官方/ })).toBeInTheDocument())
    expect(within(select).getByRole('option', { name: /未授权的中转/ })).toBeDisabled()
    await fireEvent.update(select, ID.a)
    const model = await within(dialog).findByLabelText('主模型 *')
    await vi.waitFor(() => expect(within(model).getByRole('option', { name: 'gpt-4.1' })).toBeInTheDocument())
    await fireEvent.update(model, 'gpt-4.1')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'PUT' && path(r).endsWith('/model-binding'))).toBe(true))
    expect(await requests.find(r => r.method === 'PUT' && path(r).endsWith('/model-binding'))!.clone().json()).toEqual({ version: 3, connection_id: ID.a, model_id: 'gpt-4.1' })
    expect(await screen.findByText(/已保存「开发助手」的主模型/)).toBeVisible()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
