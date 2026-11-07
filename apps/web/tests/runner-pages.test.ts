// 运行服务器页（T-0140，docs/v2/RUNNER_PLAN.md 第 11 节）：总览、环境、模板、运行记录、设置；实例卡片上的运行环境权限。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace, fail, runnerOff } from './helpers'
import type { Schema } from '../src/services/api'

const at = '2026-11-06T08:00:00Z'
const GB = 1024 ** 3
const path = (r: Request) => new URL(r.url).pathname
const as = (role: string) => Response.json({ ...me, workspaces: [{ ...workspace, role }] })
const spec = (over: Partial<Schema['RunnerSpecOut']> = {}): Schema['RunnerSpecOut'] => ({
  image: 'python:3.12-slim', steps: [], start_command: null, runtime: 'gvisor', user: 'user', network: 'none', domains: [], memory_mb: 2048, cpus: 2, pids: 1024, disk_gb: 20,
  env: {}, secrets: [], persist: [], lifetime: 'session', ...over,
})
const host = (over: Partial<Schema['RunnerHostOut']> = {}): Schema['RunnerHostOut'] => ({
  id: 'host-1', name: '运行服务器 A', status: 'online', ingress: 'intranet', token_prefix: 'mwr_abcdefgh', cpus: 16, mem_bytes: 64 * GB, disk_bytes: 500 * GB, disk_free_bytes: 50 * GB,
  kvm: true, runtimes: ['runc'], docker_version: '29.9.1', agent_version: '0.1.0', last_seen_at: at,
  report: { mem_bytes: 64 * GB, mem_free_bytes: 40 * GB, disk_bytes: 500 * GB, disk_free_bytes: 50 * GB, images_bytes: 30 * GB, containers_bytes: 5 * GB, volumes_bytes: 200 * GB, unlabeled_containers: 1, load: 1.5 },
  policy: { session_idle_minutes: 15, mem_high: 0.85, disk_high: 0.8, disk_emergency: 0.95, snapshot_max_gb: 5 }, version: 4, created_at: at, ...over,
})
const env = (over: Partial<Schema['RunnerEnvAdminOut']> = {}): Schema['RunnerEnvAdminOut'] => ({
  id: 'env-1', untrusted: { purpose: '跑回归测试' }, lifetime: 'session', state: 'running', state_reason: null, template: 'py', reuse_key: null, owner: 'alpha', mine: false,
  runtime: 'gvisor', user: 'user', network: 'none', memory_mb: 2048, cpus: 2, delete_when: [], share: 'none', project_id: null, pinned: false, use_count: 3, created_at: at, last_used_at: at,
  next_step: '空闲 15 分钟后停止', wake_seconds: 2.5, owner_instance_id: 'inst-a', template_id: 'tpl-1', spec: spec(), mem_bytes: 512 * 1024 * 1024, disk_bytes: GB, egress: {},
  retention_score: 12.5, retention_infinite: false, snapshot_at: null, snapshot_bytes: null, started_at: at, stopped_at: null, archived_at: null, deleted_at: null, delete_after: null, version: 7, ...over,
})
const template = (over: Partial<Schema['RunnerTemplateAdminOut']> = {}): Schema['RunnerTemplateAdminOut'] => ({
  id: 'tpl-1', name: 'py', untrusted: { description: '<b>Python</b> 测试环境' }, owner: 'alpha', mine: false, status: 'active', revision: 2, spec: spec({ steps: ['RUN pip install pytest'] }),
  build_status: 'ready', build_error: null, image_digest: 'sha256:abc', build_seconds: 42, image_mb: 180, use_count: 5, last_used_at: at, updated_at: at, version: 9, build_log: 'Step 1/2 : FROM python\nSuccessfully built', ...over,
})
const run = (over: Partial<Schema['RunnerRunAdminOut']> = {}): Schema['RunnerRunAdminOut'] => ({
  run_id: 'run-1', environment_id: 'env-1', status: 'succeeded', exit_code: 0, untrusted: { output: 'tail of output' }, output_truncated: false, seconds: 3, started_at: at, finished_at: at,
  instance: 'alpha', command: 'pytest -q', timeout_seconds: 600, ...over,
})
const overview = (over: Partial<Schema['RunnerOverview']> = {}): Schema['RunnerOverview'] => ({
  host: host(), environments: { running: 2, archived: 1 },
  instances: [{ instance_id: 'inst-a', name: 'alpha', enabled: true, running: 2, persistent: 1, memory_mb: 4096, cpus: 4, limits: { envs: 3, persistent: 2, memory_mb: 16384, cpus: 8 } }],
  recent: [{ environment_id: 'env-9', purpose: '抓取', state: 'stopped', reason: '空闲 30 分钟，自动停止', at }], ...over,
})

function setup(handler?: (r: Request) => Response | undefined, options: { role?: string; data?: Schema['RunnerOverview'] } = {}) {
  return mockApi(async r => {
    const result = handler?.(r)
    if (result) return result
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return as(options.role ?? 'admin')
    if (p === '/api/v1/runner/overview') return Response.json(options.data ?? overview())
    if (p === '/api/v1/runner/environments' && r.method === 'GET') return Response.json({ items: [env(), env({ id: 'env-2', untrusted: { purpose: '安卓模拟器' }, lifetime: 'persistent', runtime: 'kvm', user: 'root', network: 'all', state: 'stopped', retention_score: null, retention_infinite: true })] })
    if (p === '/api/v1/runner/templates' && r.method === 'GET') return Response.json({ items: [template()] })
    if (p === '/api/v1/runner/runs') return Response.json({ items: [run()] })
    if (p === '/api/v1/runner/runs/run-1') return Response.json({ ...run(), untrusted: { output: 'full output line 1\nfull output line 2' } })
    return undefined
  })
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('运行服务器页', () => {
  it('shows the host, alerts, usage per agent and recent reclaims; members cannot open it', async () => {
    setup()
    await openApp('/runner')
    const card = await screen.findByRole('region', { name: '运行服务器' })
    expect(within(card).getByText('运行服务器 A')).toBeVisible()
    expect(within(card).getByText(/gVisor 没装/)).toBeVisible()
    const alerts = within(card).getByRole('list', { name: '告警' })
    expect(alerts).toHaveTextContent('磁盘超过高水位（已用 90%）')
    expect(alerts).toHaveTextContent('1 个不是工作台建的容器')
    expect(alerts).toHaveTextContent('没装 gVisor')
    expect(within(card).getByRole('meter', { name: '磁盘用量' })).toHaveAttribute('aria-valuenow', '90')
    const usage = screen.getByRole('region', { name: '智能体的用量' })
    expect(within(usage).getByRole('row', { name: /alpha/ })).toHaveTextContent('2 / 3')
    expect(screen.getByRole('region', { name: '最近的回收' })).toHaveTextContent('空闲 30 分钟，自动停止')
    // 点环境数直接筛到这个状态
    const requests = setup()
    await fireEvent.click(screen.getByRole('button', { name: '运行中 2' }))
    await screen.findByRole('list', { name: '环境列表' })
    expect(requests.some(r => path(r) === '/api/v1/runner/environments' && new URL(r.url).searchParams.get('state') === 'running')).toBe(true)
  })

  it('redirects members to forbidden', async () => {
    setup(undefined, { role: 'member' })
    const { router } = await openApp('/runner')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
  })

  it('lists environments with non-default choices marked and runs actions with the version', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup(r => (path(r).endsWith('/actions') ? Response.json(env({ state: 'stopped', version: 8 })) : undefined))
    await openApp('/runner?tab=environments')
    const first = await screen.findByRole('article', { name: '环境：跑回归测试' })
    expect(within(first).getByText('空闲 15 分钟后停止')).toBeVisible()
    expect(within(first).getByText('12.50')).toBeVisible()
    expect(within(first).queryByRole('note')).toBeNull()
    const risky = screen.getByRole('article', { name: '环境：安卓模拟器' })
    expect(within(risky).getByRole('note')).toHaveTextContent('运行时 runc + KVM、root 用户、全部外网')
    expect(within(risky).getByText(/无穷大/)).toBeVisible()
    await fireEvent.click(within(first).getByRole('button', { name: '停止' }))
    await screen.findByText('已停止：跑回归测试')
    const stop = requests.find(r => r.method === 'POST' && path(r) === '/api/v1/runner/environments/env-1/actions')!
    expect(await stop.clone().json()).toEqual({ action: 'stop', version: 7 })
    expect(stop.headers.get('X-Workspace-Id')).toBe('workspace-a')
    // 常驻环境删除前提示会先做快照
    await fireEvent.click(within(risky).getByRole('button', { name: '删除' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('先做加密快照（保留 30 天'))
    // 看这个环境的运行记录
    await fireEvent.click(within(first).getByRole('button', { name: '运行记录' }))
    await screen.findByRole('list', { name: '运行记录' })
    expect(requests.some(r => path(r) === '/api/v1/runner/runs' && new URL(r.url).searchParams.get('environment_id') === 'env-1')).toBe(true)
  })

  it('offers keep for pending deletes and restore for archived or deleted persistent environments', async () => {
    setup(r => (path(r) === '/api/v1/runner/environments' && r.method === 'GET'
      ? Response.json({ items: [
        env({ id: 'a', untrusted: { purpose: '待删除的' }, state: 'pending_delete', delete_after: at }),
        env({ id: 'b', untrusted: { purpose: '归档的' }, state: 'archived', archived_at: at }),
        env({ id: 'c', untrusted: { purpose: '删掉的常驻' }, lifetime: 'persistent', state: 'deleted', snapshot_at: at, snapshot_bytes: 3 * GB }),
      ] })
      : path(r).endsWith('/actions') ? fail(409, 'VERSION_CONFLICT') : undefined))
    await openApp('/runner?tab=environments')
    const pending = await screen.findByRole('article', { name: '环境：待删除的' })
    expect(within(pending).getByRole('button', { name: '撤销删除' })).toBeVisible()
    expect(within(pending).queryByRole('button', { name: '删除' })).toBeNull()
    expect(within(screen.getByRole('article', { name: '环境：归档的' })).getByRole('button', { name: '恢复' })).toBeVisible()
    const deleted = screen.getByRole('article', { name: '环境：删掉的常驻' })
    expect(within(deleted).getByText(/3\.0 GB/)).toBeVisible()
    await fireEvent.click(within(deleted).getByRole('button', { name: '恢复' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('已被其他人修改，请刷新后再操作。')
  })

  it('shows templates with the build log, saves a new one and disables with the version', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup(r => {
      if (path(r) === '/api/v1/runner/templates' && r.method === 'PUT') return Response.json(template({ id: 'tpl-2', name: 'node' }))
      if (path(r).endsWith('/status')) return Response.json(template({ status: 'disabled', version: 10 }))
      return undefined
    })
    await openApp('/runner?tab=templates')
    const card = await screen.findByRole('article', { name: '模板：py' })
    expect(within(card).getByText('<b>Python</b> 测试环境')).toBeVisible()  // 智能体写的说明按纯文字显示
    expect(within(card).getByText(/Successfully built/)).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: '新建模板' }))
    const form = screen.getByRole('form', { name: '新建模板' })
    await fireEvent.update(within(form).getByLabelText(/^名称/), 'Node')
    await fireEvent.update(within(form).getByLabelText('基础镜像'), 'node:22-slim')
    await fireEvent.click(within(form).getByRole('button', { name: '保存并构建' }))
    expect(screen.getByRole('alert')).toHaveTextContent('名称只能是小写字母')
    await fireEvent.update(within(form).getByLabelText(/^名称/), 'node')
    await fireEvent.update(within(form).getByLabelText(/安装步骤/), 'RUN npm i -g pnpm\n')
    await fireEvent.update(within(form).getByLabelText('网络'), 'allowlist')
    await fireEvent.update(within(form).getByLabelText(/放行的域名/), 'registry.npmjs.org')
    await fireEvent.update(within(form).getByLabelText(/环境变量/), 'CI=1')
    await fireEvent.click(within(form).getByRole('button', { name: '保存并构建' }))
    await screen.findByText(/已保存模板 node/)
    const put = requests.find(r => r.method === 'PUT' && path(r) === '/api/v1/runner/templates')!
    expect(await put.clone().json()).toMatchObject({ name: 'node', version: null, spec: { image: 'node:22-slim', steps: ['RUN npm i -g pnpm'], network: 'allowlist', domains: ['registry.npmjs.org'], env: { CI: '1' }, runtime: 'gvisor', user: 'user' } })
    await fireEvent.click(within(screen.getByRole('article', { name: '模板：py' })).getByRole('button', { name: '停用' }))
    await screen.findByText('已停用 py')
    const status = requests.find(r => path(r) === '/api/v1/runner/templates/tpl-1/status')!
    expect(await status.clone().json()).toEqual({ status: 'disabled', version: 9 })
  })

  it('loads the full output of a run on demand', async () => {
    setup()
    await openApp('/runner?tab=runs')
    const item = await screen.findByRole('article', { name: '运行：pytest -q' })
    expect(within(item).getByLabelText('输出的结尾')).toHaveTextContent('tail of output')
    await fireEvent.click(within(item).getByRole('button', { name: '完整输出' }))
    expect(await within(item).findByLabelText('完整输出')).toHaveTextContent('full output line 2')
  })

  it('adds a runner host when there is none', async () => {
    const requests = setup(r => (path(r) === '/api/v1/runner/host' && r.method === 'POST' ? Response.json(host({ status: 'pending', last_seen_at: null }), { status: 201 }) : undefined),
      { data: overview({ host: null, environments: {}, instances: [], recent: [] }) })
    await openApp('/runner')
    await fireEvent.click(await screen.findByRole('button', { name: '去添加运行服务器' }))
    const form = await screen.findByRole('form', { name: '添加运行服务器' })
    await fireEvent.update(within(form).getByLabelText('连工作台的入口'), 'public')
    await fireEvent.click(within(form).getByRole('button', { name: '添加' }))
    await screen.findByText(/已添加/)
    const post = requests.find(r => r.method === 'POST' && path(r) === '/api/v1/runner/host')!
    expect(await post.clone().json()).toEqual({ name: '运行服务器', ingress: 'public' })
  })

  it('saves the reclaim policy with the version and removes the host after confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup(r => {
      if (path(r) === '/api/v1/runner/host/policy') return Response.json(host({ version: 5 }))
      if (path(r) === '/api/v1/runner/host' && r.method === 'DELETE') return new Response(null, { status: 204 })
      return undefined
    })
    await openApp('/runner?tab=settings')
    const form = await screen.findByRole('form', { name: '回收策略' })
    expect(within(form).getByLabelText('会话：空闲多少分钟停止')).toHaveValue(15)
    await fireEvent.update(within(form).getByLabelText('磁盘高水位（0 ~ 1）'), '1.5')
    await fireEvent.click(within(form).getByRole('button', { name: '保存回收策略' }))
    expect(screen.getByRole('alert')).toHaveTextContent('要在 0 到 1 之间')
    await fireEvent.update(within(form).getByLabelText('磁盘高水位（0 ~ 1）'), '0.75')
    await fireEvent.click(within(form).getByRole('button', { name: '保存回收策略' }))
    await screen.findByText(/已保存回收策略/)
    const put = requests.find(r => path(r) === '/api/v1/runner/host/policy')!
    expect(await put.clone().json()).toEqual({ version: 4, policy: { session_idle_minutes: 15, mem_high: 0.85, disk_high: 0.75, disk_emergency: 0.95, snapshot_max_gb: 5 } })
    await fireEvent.click(screen.getByRole('button', { name: '移除运行服务器' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('凭证立即失效'))
    await screen.findByText('已移除运行服务器')
    const del = requests.find(r => r.method === 'DELETE' && path(r) === '/api/v1/runner/host')!
    expect(new URL(del.url).searchParams.get('version')).toBe('4')
  })
})

describe('instance runner permission', () => {
  const instance = (over: Partial<Schema['AgentInstanceOut']> = {}): Schema['AgentInstanceOut'] => ({ id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read_write', max_role: 'member', status: 'online', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: at, reported_version: '2026.9.36', plugin_version: '0.3.3+myworkspace.1', bundle_issued_at: at, im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null }, mail: { accounts: [], read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 }, bundle_outdated: false, version: 3, created_at: at, updated_at: at, credentials: [], secret_grants: [], ...over })
  function agents(item: Schema['AgentInstanceOut'], handler?: (r: Request) => Response | undefined) {
    return mockApi(async r => handler?.(r) ?? (
      path(r) === '/api/v1/me' && r.method === 'GET' ? as('owner') :
      path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
      path(r) === '/api/v1/agents/instances' ? Response.json({ items: [item], next_cursor: null, total_estimate: 1 }) :
      path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
      path(r).endsWith('/ssh') ? Response.json({ target: null, job: null, warning: null }) :
      path(r).endsWith('/ops') ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) :
      path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) :
      path(r) === '/api/v1/mail/accounts' ? Response.json({ items: [] }) :
      path(r).endsWith('/stops') ? Response.json({ items: [] }) : undefined))
  }

  it('is off by default and opens with limits after confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = agents(instance(), r => (path(r) === '/api/v1/agents/instances/inst-a/runner' && r.method === 'PUT'
      ? Response.json({ ...runnerOff, enabled: true, limits: { ...runnerOff.limits, envs: 5 }, enabled_at: at, version: 2 })
      : undefined))
    await openApp('/agents')
    const card = await screen.findByRole('article', { name: '实例：alpha' })
    const section = within(card).getByRole('region', { name: '运行环境权限' })
    expect(await within(section).findByText('运行环境权限：关闭')).toBeVisible()
    expect(within(section).getByText(/在下面的“密钥授权”里授权给它的密钥才能注入/)).toBeVisible()
    await fireEvent.update(within(section).getByLabelText('同时运行的环境'), '5')
    await fireEvent.click(within(section).getByRole('button', { name: '允许它在运行服务器上建环境、执行命令' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('执行任意命令'))
    await within(section).findByText('已打开运行环境权限')
    const put = requests.find(r => r.method === 'PUT' && path(r).endsWith('/runner'))!
    expect(await put.clone().json()).toEqual({ enabled: true, version: 1, limits: { envs: 5, persistent: 2, memory_mb: 16384, cpus: 8, disk_gb: 100, execs_per_hour: 120, run_hours_per_day: 8 } })
  })

  it('cannot be opened for a read-only instance', async () => {
    agents(instance({ scope: 'read' }))
    await openApp('/agents')
    const section = within(await screen.findByRole('article', { name: '实例：alpha' })).getByRole('region', { name: '运行环境权限' })
    expect(await within(section).findByRole('button', { name: '允许它在运行服务器上建环境、执行命令' })).toBeDisabled()
    expect(within(section).getByText(/工具权限是只读，打不开/)).toBeVisible()
  })
})
