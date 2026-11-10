import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { fail, mockApi, openApp } from './helpers'
import type { Schema } from '../src/services/api'

// 密钥库改版（T-0196，效果图 07；规范第 10 节、契约第 4 节）：名称 / 说明与类型在服务端筛选、换条件从第一页开始，
// 标题里的数是服务端 total；?secret= 打开详情（不在当前页也按 GET 单项读），详情只有元数据、不回显、无复制；
// 轮换带新值与记录 version、提交 / 换对象都清空输入，409 不报成功；删除先提示受影响的智能体，删后清面板；
// 详情里的取用记录按 secret_id 读；不存在或失权时不留旧内容。
// T-0206：密钥库并入中转站管理的“迁移与兼容凭据”页签（标题“兼容凭据”），/agents/secrets 重定向过去并保留 ?secret=；
// 内层“取用记录”标签改用 ?view=accesses（外层页签占用 ?tab=）。
const path = (r: Request) => new URL(r.url).pathname
const at = '2026-11-08T10:30:00Z'
const A_ID = '11111111-1111-4111-8111-111111111111', B_ID = '22222222-2222-4222-8222-222222222222'
const C_ID = '33333333-3333-4333-8333-333333333333', D_ID = '44444444-4444-4444-8444-444444444444'
const secret = (over: Partial<Schema['AgentSecretOut']> = {}): Schema['AgentSecretOut'] => ({
  id: A_ID, name: 'openai_main', kind: 'model_api_key', description: '主模型服务连接', value_version: 3, last_rotated_at: at,
  grants: [{ instance_id: 'inst-a', instance_name: '开发助手', ref_id: 'openai_api_key' }, { instance_id: 'inst-b', instance_name: '知识助手', ref_id: 'openai_api_key' }],
  version: 7, created_at: at, updated_at: at, ...over,
})
const A = secret()
const B = secret({ id: B_ID, name: 'github_readonly', kind: 'api_token', description: '', value_version: 2, grants: [{ instance_id: 'inst-a', instance_name: '开发助手', ref_id: 'github_token' }], version: 4 })
const C = secret({ id: C_ID, name: 'sandbox_demo', kind: 'generic', description: '', value_version: 1, grants: [], version: 1 })
const D = secret({ id: D_ID, name: 'deploy_token', kind: 'api_token', description: '部署用', value_version: 1, grants: [], version: 2 })
const byId: Record<string, Schema['AgentSecretOut']> = { [A_ID]: A, [B_ID]: B, [C_ID]: C, [D_ID]: D }
const access: Schema['SecretAccessOut'] = { id: 'acc-1', at, instance_id: 'inst-a', instance_name: '开发助手', ref_id: 'openai_api_key', result: 'ok', secret_id: A_ID, source_ip: '192.0.2.41', value_version: 3 }
const page = (items: Schema['AgentSecretOut'][], total = items.length, next_cursor: string | null = null) => Response.json({ items, next_cursor, total, total_estimate: total })

type Handler = (r: Request) => Response | Promise<Response | undefined> | undefined
function setup(options: { list?: (url: URL) => Response; detail?: (id: string) => Response; handler?: Handler } = {}) {
  return mockApi(async (r) => {
    const extra = await options.handler?.(r)
    if (extra) return extra
    const url = new URL(r.url), p = url.pathname
    if (p === '/api/v1/agents/secrets' && r.method === 'GET') return options.list ? options.list(url) : page([A, B])
    if (p === '/api/v1/agents/secrets/accesses') return Response.json({ items: [access], next_cursor: null, total_estimate: 1 })
    const single = /^\/api\/v1\/agents\/secrets\/([^/]+)$/.exec(p)
    if (single && r.method === 'GET') return options.detail ? options.detail(single[1]) : byId[single[1]] ? Response.json(byId[single[1]]) : fail(404, 'NOT_FOUND')
    if (p === '/api/v1/agents/instances' && r.method === 'GET') return Response.json({ items: [], next_cursor: null, total_estimate: 0 })
    return undefined
  })
}
const lists = (requests: Request[]) => requests.filter(r => r.method === 'GET' && path(r) === '/api/v1/agents/secrets').map(r => new URL(r.url).searchParams)
const detailReads = (requests: Request[], id: string) => requests.filter(r => r.method === 'GET' && path(r) === `/api/v1/agents/secrets/${id}`).length
async function openDetail(url: string, name: string) {
  const opened = await openApp(url)
  const detail = await screen.findByRole('region', { name: '密钥详情' })
  expect(await within(detail).findByRole('heading', { name })).toBeVisible()
  return { ...opened, detail }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('密钥库改版', () => {
  it('searches name / description and kind on the server, starts again from the first page, and counts with the server total', async () => {
    const requests = setup({
      list: (url) => {
        const q = url.searchParams.get('q'), kind = url.searchParams.get('kind'), cursor = url.searchParams.get('cursor')
        if (!q && !kind) return cursor === 'page-2' ? page([C], 120) : page([A, B], 120, 'page-2')
        return page([D], 1)
      },
    })
    const { router } = await openApp('/agents/secrets')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/settings/model-connections?tab=compat'))
    expect(await screen.findByRole('heading', { level: 2, name: '兼容凭据' })).toBeVisible()
    const list = await screen.findByRole('region', { name: '密钥列表' })
    // 标题里的数是服务端 total（120），不是第一页的 2 行
    expect(await within(list).findByRole('heading', { name: '密钥列表 · 120' })).toBeVisible()
    expect(within(list).getByRole('row', { name: /openai_main/ })).toHaveTextContent('模型服务密钥')
    expect(within(list).getByRole('row', { name: /openai_main/ })).toHaveTextContent('v3')
    expect(within(list).getByRole('row', { name: /openai_main/ })).toHaveTextContent('开发助手、知识助手')
    await fireEvent.click(within(list).getByRole('button', { name: '加载更多密钥' }))
    expect(await within(list).findByRole('row', { name: /sandbox_demo/ })).toHaveTextContent('未授权')
    expect(list).toHaveTextContent('已显示 3 个，共 120 个')
    expect(lists(requests).some(p => p.get('cursor') === 'page-2')).toBe(true)

    await fireEvent.update(within(list).getByRole('searchbox', { name: '按名称或说明搜索' }), 'deploy')
    await vi.waitFor(() => expect(lists(requests).some(p => p.get('q') === 'deploy')).toBe(true))
    expect(lists(requests).filter(p => p.get('q') === 'deploy').every(p => !p.has('cursor'))).toBe(true)
    expect(await within(list).findByRole('heading', { name: '密钥列表 · 1' })).toBeVisible()
    // 换了条件不拼旧页
    expect(within(list).getByRole('row', { name: /deploy_token/ })).toBeVisible()
    expect(within(list).queryByRole('row', { name: /openai_main|sandbox_demo/ })).toBeNull()

    await fireEvent.update(within(list).getByRole('combobox', { name: '按类型筛选' }), 'api_token')
    await vi.waitFor(() => expect(lists(requests).some(p => p.get('q') === 'deploy' && p.get('kind') === 'api_token')).toBe(true))
    expect(lists(requests).filter(p => p.get('kind') === 'api_token').every(p => !p.has('cursor'))).toBe(true)
  })

  it('opens a deep-linked secret that is not on the loaded page through the detail endpoint, showing metadata only', async () => {
    // 万一响应里多了值字段也不显示：页面只渲染元数据字段
    const requests = setup({ list: () => page([B]), detail: id => id === A_ID ? Response.json({ ...A, value: 'sk-should-never-render' }) : fail(404, 'NOT_FOUND') })
    const { detail, router } = await openDetail(`/agents/secrets?secret=${A_ID}`, 'openai_main')
    // 旧的深链接重定向到中转站管理的兼容凭据，保留 ?secret=
    expect(router.currentRoute.value.path).toBe('/settings/model-connections')
    expect(router.currentRoute.value.query).toEqual({ tab: 'compat', secret: A_ID })
    expect(requests.some(r => r.method === 'GET' && path(r) === `/api/v1/agents/secrets/${A_ID}`)).toBe(true)
    expect(detail).toHaveTextContent('模型服务密钥 · v3')
    expect(detail).toHaveTextContent('主模型服务连接')
    expect(detail).toHaveTextContent('不支持回显')
    expect(detail).not.toHaveTextContent('sk-should-never-render')
    expect(detail).not.toHaveTextContent(/有效期|到期/)
    expect(within(detail).queryByRole('button', { name: /复制|显示|明文|眼/ })).toBeNull()
    expect(detail.querySelector('input')).toBeNull()
    // 授权对象：智能体名与引用名，管理授权链到对应智能体
    const grants = within(detail).getByRole('region', { name: '授权对象' })
    expect(grants).toHaveTextContent('开发助手')
    expect(grants).toHaveTextContent('引用名：openai_api_key')
    expect(within(grants).getByRole('link', { name: '开发助手' })).toHaveAttribute('href', '/agents?instance=inst-a')
    expect(within(grants).getByRole('link', { name: /前往智能体管理授权/ })).toHaveAttribute('href', '/agents')
    // 取用记录：按 secret_id 读，说明是已加载的记录
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/agents/secrets/accesses' && new URL(r.url).searchParams.get('secret_id') === A_ID)).toBe(true))
    const log = within(detail).getByRole('region', { name: '当前已加载的取用记录' })
    expect(await within(log).findByText('192.0.2.41')).toBeVisible()
    expect(log).toHaveTextContent('成功')
    // 查看全部：切到“取用记录”标签，按这个密钥筛选
    await fireEvent.click(within(log).getByRole('button', { name: '查看全部取用记录' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.view).toBe('accesses'))
    expect(router.currentRoute.value.query.tab).toBe('compat')
    const tab = await screen.findByRole('region', { name: '取用记录' })
    expect(within(tab).getByText('密钥：openai_main')).toBeVisible()
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/agents/secrets/accesses' && new URL(r.url).searchParams.get('secret_id') === A_ID && new URL(r.url).searchParams.get('limit') === '50')).toBe(true))
  })

  it('rotates with the new value and the record version, then clears the input and keeps the value out of the URL and cache', async () => {
    const requests = setup({ handler: r => (path(r).endsWith('/rotate') ? Response.json({ ...A, value_version: 4, version: 8 }) : undefined) })
    const { detail, router, queryClient } = await openDetail(`/agents/secrets?secret=${A_ID}`, 'openai_main')
    await fireEvent.click(within(detail).getByRole('button', { name: '轮换密钥' }))
    const form = within(detail).getByRole('form', { name: '轮换密钥' })
    const input = within(form).getByLabelText('新值')
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveAttribute('autocomplete', 'new-password')
    await fireEvent.update(input, 'sk-new-value')
    await fireEvent.click(within(form).getByRole('button', { name: '确认轮换' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/rotate'))).toBe(true))
    const post = requests.find(r => path(r).endsWith('/rotate'))!
    expect(path(post)).toBe(`/api/v1/agents/secrets/${A_ID}/rotate`)
    // version 是记录的乐观版本 7，不是值版本 3
    expect(await post.clone().json()).toEqual({ value: 'sk-new-value', version: 7 })
    expect(await within(detail).findByText(/密钥已轮换/)).toBeVisible()
    expect(within(detail).queryByRole('form', { name: '轮换密钥' })).toBeNull()
    expect(screen.queryByDisplayValue('sk-new-value')).toBeNull()
    expect(router.currentRoute.value.fullPath).not.toContain('sk-new-value')
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map(q => [q.queryKey, q.state.data]))).not.toContain('sk-new-value')
    await fireEvent.click(within(detail).getByRole('button', { name: '轮换密钥' }))
    expect(within(detail).getByLabelText('新值')).toHaveValue('')
  })

  it('reports a rotate conflict instead of success and re-reads the secret', async () => {
    const requests = setup({ handler: r => (path(r).endsWith('/rotate') ? fail(409, 'VERSION_CONFLICT') : undefined) })
    const { detail } = await openDetail(`/agents/secrets?secret=${A_ID}`, 'openai_main')
    await fireEvent.click(within(detail).getByRole('button', { name: '轮换密钥' }))
    await fireEvent.update(within(detail).getByLabelText('新值'), 'sk-conflict')
    const before = detailReads(requests, A_ID)
    await fireEvent.click(within(detail).getByRole('button', { name: '确认轮换' }))
    expect(await within(detail).findByRole('alert')).toHaveTextContent('这次没有轮换')
    expect(within(detail).queryByText(/密钥已轮换/)).toBeNull()
    expect(screen.queryByDisplayValue('sk-conflict')).toBeNull()
    await vi.waitFor(() => expect(detailReads(requests, A_ID)).toBeGreaterThan(before))
  })

  it('clears the rotate input when another secret is selected', async () => {
    setup()
    const { detail, router } = await openDetail(`/agents/secrets?secret=${A_ID}`, 'openai_main')
    await fireEvent.click(within(detail).getByRole('button', { name: '轮换密钥' }))
    await fireEvent.update(within(detail).getByLabelText('新值'), 'sk-typed')
    const list = screen.getByRole('region', { name: '密钥列表' })
    await fireEvent.click(await within(list).findByRole('button', { name: '管理：github_readonly' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.secret).toBe(B_ID))
    const next = await screen.findByRole('region', { name: '密钥详情' })
    expect(await within(next).findByRole('heading', { name: 'github_readonly' })).toBeVisible()
    expect(screen.queryByDisplayValue('sk-typed')).toBeNull()
    await fireEvent.click(within(next).getByRole('button', { name: '轮换密钥' }))
    expect(within(next).getByLabelText('新值')).toHaveValue('')
  })

  it('warns about the granted agents before deleting with the version, then clears the panel', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup({ handler: r => (r.method === 'DELETE' ? new Response(null, { status: 204 }) : undefined) })
    const { detail, router } = await openDetail(`/agents/secrets?secret=${A_ID}`, 'openai_main')
    await fireEvent.keyDown(within(detail).getByRole('button', { name: '更多操作' }), { key: 'Enter' })
    await fireEvent.click(await screen.findByRole('menuitem', { name: '删除密钥' }))
    expect(confirm).toHaveBeenCalledOnce()
    expect(confirm.mock.calls[0][0]).toContain('开发助手')
    expect(confirm.mock.calls[0][0]).toContain('知识助手')
    await vi.waitFor(() => expect(requests.some(r => r.method === 'DELETE')).toBe(true))
    const del = requests.find(r => r.method === 'DELETE')!
    expect(path(del)).toBe(`/api/v1/agents/secrets/${A_ID}`)
    expect(new URL(del.url).searchParams.get('version')).toBe('7')
    await vi.waitFor(() => expect(router.currentRoute.value.query.secret).toBeUndefined())
    expect(screen.queryByRole('region', { name: '密钥详情' })).toBeNull()
    expect(await screen.findByText('密钥「openai_main」已删除')).toBeVisible()
  })

  it('does not delete when the confirmation is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const requests = setup()
    const { detail } = await openDetail(`/agents/secrets?secret=${A_ID}`, 'openai_main')
    await fireEvent.keyDown(within(detail).getByRole('button', { name: '更多操作' }), { key: 'Enter' })
    await fireEvent.click(await screen.findByRole('menuitem', { name: '删除密钥' }))
    expect(requests.some(r => r.method === 'DELETE')).toBe(false)
    // 菜单关上后详情原样还在
    expect(await within(detail).findByRole('heading', { name: 'openai_main' })).toBeVisible()
  })

  it('clears stale detail content when the secret is gone or no longer accessible', async () => {
    let gone = false
    setup({ detail: id => (gone ? fail(404, 'NOT_FOUND') : byId[id] ? Response.json(byId[id]) : fail(403, 'FORBIDDEN')) })
    const { detail, router } = await openDetail(`/agents/secrets?secret=${A_ID}`, 'openai_main')
    gone = true
    // 页头与“兼容凭据”各有一个刷新：点兼容凭据自己的
    await fireEvent.click(within(screen.getByRole('region', { name: '兼容凭据' })).getAllByRole('button', { name: '刷新' })[0]!)
    expect(await within(detail).findByText('这个密钥不存在，或你已无权访问。')).toBeVisible()
    expect(within(detail).queryByRole('heading', { name: 'openai_main' })).toBeNull()
    expect(within(detail).queryByRole('button', { name: '轮换密钥' })).toBeNull()
    expect(within(detail).queryByRole('region', { name: '授权对象' })).toBeNull()
    expect(detail).not.toHaveTextContent('主模型服务连接')
    // 深链接到失权 / 不存在的密钥：直接说明，不显示别的密钥
    gone = false
    await router.push('/agents/secrets?secret=55555555-5555-4555-8555-555555555555')
    const other = await screen.findByRole('region', { name: '密钥详情' })
    expect(await within(other).findByText('这个密钥不存在，或你已无权访问。')).toBeVisible()
    await fireEvent.click(within(other).getByRole('button', { name: '关闭' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.secret).toBeUndefined())
  })
})
