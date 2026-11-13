// 容器详情（T-0187）：按 environment_id 直接打开（不在当前筛选 / 当前页也行）、404 / 失权不留旧内容、
// 资源负载的口径与上报时间、执行记录的真实列表、生命周期动作（version、确认、409 / 结果未知不重发）、
// 用途与命令只当文字、打开与刷新详情不发 POST。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { fail, openApp } from './helpers'
import { env, metrics, path, run, setup } from './container-fixtures'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
const ID = env().id
const posts = (requests: Request[]) => requests.filter(r => r.method === 'POST')

describe('容器详情', () => {
  it('opens a container by id even when it is not in the current list, without sending any write', async () => {
    const requests = setup({
      list: () => ({ total: 0, next_cursor: null, items: [] }),
      detail: id => Response.json(env({ id, untrusted: { purpose: '<img src=x onerror=alert(1)>筛选外的容器' } })),
      handler: r => (path(r) === '/api/v1/runner/runs' ? Response.json({ items: [run(), run({ run_id: 'run-2', command: 'npm test', status: 'queued' }), run({ run_id: 'run-3', command: 'ls', status: 'succeeded', exit_code: 0 })] }) : undefined),
    })
    await openApp(`/runner?environment_id=${ID}`)
    const detail = await screen.findByRole('tablist', { name: '容器详情' })
    const panel = detail.closest('.cs-detail') as HTMLElement
    expect(within(panel).getByText('<img src=x onerror=alert(1)>筛选外的容器')).toBeVisible()  // 用途只当文字
    expect(panel.querySelector('img')).toBeNull()
    expect(requests.some(r => path(r) === `/api/v1/runner/environments/${ID}`)).toBe(true)
    const load = within(panel).getByRole('region', { name: 'CPU' })
    expect(load).toHaveTextContent('CPU 已用 0.42 核 / 上限 2 核')
    expect(load).toHaveTextContent('最近 60 秒的平均')
    expect(within(panel).getByRole('region', { name: '内存' })).toHaveTextContent('640 MiB / 上限 2048 MiB')
    expect(within(panel).getByRole('region', { name: '数据卷' })).toHaveTextContent('1.2 GiB')
    // 当前执行：排队与执行中的都列出来
    const current = within(panel).getByRole('region', { name: '当前执行' })
    expect(current).toHaveTextContent('npm run build')
    expect(current).toHaveTextContent('npm test')
    expect(current).not.toHaveTextContent('ls')
    await fireEvent.click(within(panel).getByRole('tab', { name: /执行记录/ }))
    const runs = within(panel).getByRole('region', { name: '这个容器的执行记录' })
    expect(runs).toHaveTextContent('当前已加载 3 条')
    expect(runs).toHaveTextContent('结果未知是命令的状态，不代表容器停止')
    expect(posts(requests)).toHaveLength(0)
  })

  it('clears the detail when the container is gone or no longer accessible', async () => {
    setup({ detail: () => fail(404, 'NOT_FOUND') })
    const { router } = await openApp(`/runner?environment_id=${ID}`)
    expect(await screen.findByText('这个容器不存在，或你已无权访问。')).toBeVisible()
    expect(screen.queryByText('前端构建', { selector: '.cs-detail *' })).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.environment_id).toBeUndefined())
  })

  it('marks stale readings with their report time and says why CPU is unknown', async () => {
    setup({ detail: id => Response.json(env({ id, metrics: metrics({ cpu_status: 'unavailable', cpu_used_cores: null, cpu_percent_of_limit: null, cpu_reported_at: null, memory_status: 'stale', memory_reported_at: null }) })) })
    await openApp(`/runner?environment_id=${ID}`)
    const cpu = await screen.findByRole('region', { name: 'CPU' })
    expect(cpu).toHaveTextContent('CPU 已用 未知')
    expect(cpu).toHaveTextContent('要等第二次上报')
    expect(within(cpu).queryByRole('meter')).toBeNull()
    expect(screen.getByRole('region', { name: '内存' })).toHaveTextContent('已过期 · 上报时间未知')
  })

  it('runs lifecycle actions with the latest version, confirms data loss by lifetime and does not resend on conflicts', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup({
      detail: id => Response.json(env({ id, version: 9 })),
      handler: r => (path(r).endsWith('/actions') ? Response.json(env({ version: 10 })) : undefined),
    })
    await openApp(`/runner?environment_id=${ID}`)
    const tabs = await screen.findByRole('tablist', { name: '容器详情' })
    await fireEvent.click(within(tabs).getByRole('tab', { name: '配置与回收' }))
    const actions = await screen.findByRole('region', { name: '生命周期操作' })
    expect(within(actions).queryByRole('button', { name: '启动' })).toBeNull()
    // 会话环境归档：提示数据卷会删掉
    confirm.mockReturnValueOnce(false)
    await fireEvent.click(within(actions).getByRole('button', { name: '归档' }))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('会话环境的数据不保留'))
    expect(posts(requests)).toHaveLength(0)
    // 停止：用详情里的最新 version，提示“已发出”而不是“已停止”
    await fireEvent.click(within(actions).getByRole('button', { name: '停止' }))
    expect(await within(actions).findByRole('status')).toHaveTextContent('已发出“停止”：前端构建（以刷新后的容器状态为准）')
    const stop = posts(requests).find(r => path(r) === `/api/v1/runner/environments/${ID}/actions`)!
    expect(await stop.clone().json()).toEqual({ action: 'stop', version: 9 })
  })

  it('refreshes instead of resending after a version conflict or an unknown result', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let answer = fail(409, 'VERSION_CONFLICT')
    let detailCalls = 0
    const requests = setup({
      detail: id => { detailCalls += 1; return Response.json(env({ id })) },
      handler: r => (path(r).endsWith('/actions') ? answer : undefined),
    })
    await openApp(`/runner?environment_id=${ID}`)
    await fireEvent.click(within(await screen.findByRole('tablist', { name: '容器详情' })).getByRole('tab', { name: '配置与回收' }))
    const actions = await screen.findByRole('region', { name: '生命周期操作' })
    const before = detailCalls
    await fireEvent.click(within(actions).getByRole('button', { name: '删除' }))
    expect(await within(actions).findByRole('alert')).toHaveTextContent('已被其他人修改，请刷新后再操作。')
    await vi.waitFor(() => expect(detailCalls).toBeGreaterThan(before))
    answer = fail(503, 'WRITE_UNKNOWN')
    await fireEvent.click(within(actions).getByRole('button', { name: '删除' }))
    expect(await within(actions).findByRole('alert')).toHaveTextContent('结果未知')
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(posts(requests).filter(r => path(r).endsWith('/actions'))).toHaveLength(2)  // 每次点击只发一次，不自动重发
  })

  it('has one protection control that explains what it does and does not cover', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup({ detail: id => Response.json(env({ id, pinned: true })), handler: r => (path(r).endsWith('/actions') ? Response.json(env({ pinned: false, version: 8 })) : undefined) })
    await openApp(`/runner?environment_id=${ID}`)
    await fireEvent.click(within(await screen.findByRole('tablist', { name: '容器详情' })).getByRole('tab', { name: '配置与回收' }))
    const rules = await screen.findByRole('region', { name: '回收规则' })
    expect(rules).toHaveTextContent('删除条件到点仍会进入待删除')
    expect(within(rules).getAllByRole('button')).toHaveLength(1)
    expect(within(screen.getByRole('region', { name: '生命周期操作' })).queryByRole('button', { name: '归档' })).toBeNull()  // 保护期间不能归档
    await fireEvent.click(within(rules).getByRole('button', { name: '取消保护' }))
    await vi.waitFor(() => expect(posts(requests)).toHaveLength(1))
    expect(await posts(requests)[0].clone().json()).toEqual({ action: 'unpin', version: 7 })
  })

  it('closes and returns to the list without changing the container', async () => {
    const requests = setup()
    const { router } = await openApp('/runner')
    const row = (await screen.findAllByRole('row', { name: /前端构建/ }))[0]
    await fireEvent.click(row)
    await vi.waitFor(() => expect(router.currentRoute.value.query.environment_id).toBe(ID))
    await screen.findByRole('tablist', { name: '容器详情' })
    router.back()
    await vi.waitFor(() => expect(router.currentRoute.value.query.environment_id).toBeUndefined())
    expect(posts(requests)).toHaveLength(0)
  })
})

it('shows network enforcement readiness separately without publishing on open', async () => {
  const requests = setup({ handler: r => path(r) === `/api/v1/runner/environments/${ID}/network`
    ? Response.json({ environment_id: ID, workspace_id: 'ws-1', owner_instance_id: env().owner_instance_id, host_id: null,
      runtime_status: 'unsupported', runtime_error: 'MANAGED_NETWORK_REQUIRES_KUBERNETES', services: [], ingress_rules: [], published_endpoints: [] })
    : undefined })
  await openApp(`/runner?environment_id=${ID}`)
  await fireEvent.click(await screen.findByRole('tab', { name: '网络与服务' }))
  expect(await screen.findByText('当前容器使用兼容运行后端，受管跨节点网络尚不可用。')).toBeVisible()
  expect(posts(requests)).toHaveLength(0)
})
