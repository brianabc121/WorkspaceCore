// 容器服务器页（原“运行服务器”，T-0140；改版 T-0186）：环境模板、执行记录、服务器设置；实例卡片上的运行环境权限。
// 容器总览、智能体用量与详情在 container-server-pages.test.ts / container-server-detail.test.ts。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace, runnerOff } from './helpers'
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
  backend: 'docker', node_name: null, runtime_metadata: {},
  id: 'host-1', name: '运行服务器 A', status: 'online', ingress: 'intranet', token_prefix: 'mwr_abcdefgh', cpus: 16, mem_bytes: 64 * GB, disk_bytes: 500 * GB, disk_free_bytes: 50 * GB,
  kvm: true, runtimes: ['runc'], docker_version: '29.9.1', agent_version: '0.1.0', last_seen_at: at,
  report: { mem_bytes: 64 * GB, mem_free_bytes: 40 * GB, disk_bytes: 500 * GB, disk_free_bytes: 50 * GB, images_bytes: 30 * GB, containers_bytes: 5 * GB, volumes_bytes: 200 * GB, unlabeled_containers: 1, load: 1.5 },
  policy: { session_idle_minutes: 15, mem_high: 0.85, disk_high: 0.8, disk_emergency: 0.95, snapshot_max_gb: 5 }, version: 4, created_at: at, ...over,
})
const env = (over: Partial<Schema['RunnerEnvAdminOut']> = {}): Schema['RunnerEnvAdminOut'] => ({
  runtime_backend: 'docker', host_id: 'host-1', placement: {},
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

describe('容器服务器页：模板、执行记录与设置', () => {
  it('lists every host and manages the selected host without using the singular endpoint', async () => {
    const second = host({ id: 'host-2', name: '运行服务器 B', version: 8 })
    const requests = setup(r => {
      if (path(r) === '/api/v1/runner/hosts/host-2/policy') return Response.json({ ...second, version: 9 })
      if (path(r) === '/api/v1/runner/hosts/host-2/ssh') return Response.json({ target: null, job: null, warning: null })
      return undefined
    }, { data: { ...overview({ host: null }), hosts: [host(), second] } as Schema['RunnerOverview'] })
    await openApp('/runner?tab=settings&host_id=host-2')
    const hosts = await screen.findByRole('region', { name: '容器服务器池' })
    expect(within(hosts).getByRole('button', { name: /运行服务器 A/ })).toBeVisible()
    expect(within(hosts).getByRole('button', { name: /运行服务器 B/ })).toHaveAttribute('aria-pressed', 'true')
    const form = await screen.findByRole('form', { name: '回收策略' })
    await fireEvent.update(within(form).getByLabelText('磁盘高水位（0 ~ 1）'), '0.75')
    await fireEvent.click(within(form).getByRole('button', { name: '保存回收策略' }))
    await screen.findByText(/已保存回收策略/)
    const write = requests.find(r => r.method === 'PUT' && path(r) === '/api/v1/runner/hosts/host-2/policy')!
    expect(await write.clone().json()).toMatchObject({ version: 8 })
    expect(requests.some(r => /^\/api\/v1\/runner\/host(?:\/|$)/.test(path(r)))).toBe(false)
    expect(screen.getByRole('form', { name: '添加容器服务器' })).toBeVisible()
  })
  it('redirects members to forbidden', async () => {
    setup(undefined, { role: 'member' })
    const { router } = await openApp('/runner')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
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
    const requests = setup(r => (path(r) === '/api/v1/runner/hosts' && r.method === 'POST' ? Response.json(host({ status: 'pending', last_seen_at: null }), { status: 201 }) : undefined),
      { data: overview({ host: null, environments: {}, instances: [], recent: [] }) })
    await openApp('/runner')
    await fireEvent.click(await screen.findByRole('button', { name: '去添加容器服务器' }))
    const form = await screen.findByRole('form', { name: '添加容器服务器' })
    await fireEvent.update(within(form).getByLabelText('连工作台的入口'), 'public')
    await fireEvent.click(within(form).getByRole('button', { name: '添加' }))
    await screen.findByText(/已添加/)
    const post = requests.find(r => r.method === 'POST' && path(r) === '/api/v1/runner/hosts')!
    expect(await post.clone().json()).toEqual({ name: '容器服务器', ingress: 'public' })
  })

  it('saves the reclaim policy with the version and removes the host after confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup(r => {
      if (path(r) === '/api/v1/runner/hosts/host-1/policy') return Response.json(host({ version: 5 }))
      if (path(r) === '/api/v1/runner/hosts/host-1' && r.method === 'DELETE') return new Response(null, { status: 204 })
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
    const put = requests.find(r => path(r) === '/api/v1/runner/hosts/host-1/policy')!
    expect(await put.clone().json()).toEqual({ version: 4, policy: { session_idle_minutes: 15, mem_high: 0.85, disk_high: 0.75, disk_emergency: 0.95, snapshot_max_gb: 5 } })
    await fireEvent.click(screen.getByRole('button', { name: '移除容器服务器' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('凭证立即失效'))
    await screen.findByText('已移除容器服务器')
    const del = requests.find(r => r.method === 'DELETE' && path(r) === '/api/v1/runner/hosts/host-1')!
    expect(new URL(del.url).searchParams.get('version')).toBe('4')
  })
})

describe('instance runner permission', () => {
  const instance = (over: Partial<Schema['AgentInstanceOut']> = {}): Schema['AgentInstanceOut'] => ({ id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read_write', max_role: 'member', status: 'online', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: at, reported_version: '2026.9.36', plugin_version: '0.3.3+myworkspace.1', bundle_issued_at: at, im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, stop_others: { enabled: false, granted_by: null, granted_at: null }, mail: { accounts: [], read_enabled: true, send_enabled: false, send_scope: null, recipients: [], send_by: null, send_at: null, version: 3 }, bundle_outdated: false, server_id: null, server_manual: false, limits: { mem_limit_mb: null, cpus: null }, version: 3, created_at: at, updated_at: at, credentials: [], secret_grants: [], ...over })
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

  it('shows held quota and separate capability grants, requests approval and revokes with an owner credential', async () => {
    const grant = { id: 'grant-1', instance_id: 'inst-a', approval_id: 'approval-1', capability: 'runner.cross_agent_communication', reason: '连接数据库', requested_by: 'user-a', approved_by: 'owner', approved_at: at, state: 'active', expires_at: null, revoked_by: null, revoked_at: null, revoke_reason: null, desired_revision: 2, applied_revision: 2, policy_version: 1 }
    const requests = agents(instance(), r => {
      if (path(r).endsWith('/runner')) return Response.json({ ...runnerOff, enabled: true })
      if (path(r).endsWith('/runner/capabilities')) return Response.json({ instance_id: 'inst-a', held_count: 6, default_limit: 5, unlimited_active: false, grants: [grant], pending_approval_ids: [] })
      if (path(r).endsWith('/runner/capabilities/request')) return Response.json({ status: 'pending_approval', action_id: 'approval-2', summary: '免除数量上限', expires_at: at, deduplicated: false })
      if (path(r).endsWith('/runner/capabilities/grant-1/revoke')) return Response.json({ ...grant, state: 'revoked' })
      return undefined
    })
    await openApp('/agents?instance=inst-a')
    const panel = await screen.findByRole('region', { name: '容器数量与高级权限' })
    expect(await within(panel).findByText(/保有环境：6 \/ 5/)).toBeVisible()
    expect(within(panel).getByText(/已超额.*阻止新增/)).toBeVisible()
    expect(within(panel).getByText(/仍需目标允许/)).toBeVisible()
    await fireEvent.update(within(panel).getByLabelText('申请能力'), 'runner.unlimited_container_count')
    await fireEvent.update(within(panel).getByLabelText('申请原因'), '需要独立环境')
    await fireEvent.click(within(panel).getByRole('button', { name: '申请权限' }))
    expect(await within(panel).findByRole('link', { name: '查看本次审批' })).toHaveAttribute('href', '/approvals?id=approval-2')
    const request = requests.find(r => path(r).endsWith('/runner/capabilities/request'))!
    expect(await request.clone().json()).toEqual({ capability: 'runner.unlimited_container_count', reason: '需要独立环境', expires_at: null })
    await fireEvent.click(within(panel).getByRole('button', { name: '撤销跨智能体通信权限' }))
    await fireEvent.update(within(panel).getByLabelText('撤销原因'), '不再需要')
    await fireEvent.update(within(panel).getByLabelText('确认登录密码'), 'owner-password')
    await fireEvent.click(within(panel).getByRole('button', { name: '确认撤销' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/runner/capabilities/grant-1/revoke'))).toBe(true))
    expect(within(panel).queryByLabelText('确认登录密码')).not.toBeInTheDocument()
  })

  it('is off by default and opens with limits after confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = agents(instance(), r => (path(r) === '/api/v1/agents/instances/inst-a/runner' && r.method === 'PUT'
      ? Response.json({ ...runnerOff, enabled: true, limits: { ...runnerOff.limits, envs: 5 }, enabled_at: at, version: 2 })
      : undefined))
    await openApp('/agents?instance=inst-a')
    const card = await screen.findByRole('article', { name: '实例：alpha' })
    const section = within(card).getByRole('region', { name: '运行环境权限' })
    expect(await within(section).findByText('运行环境权限：关闭')).toBeVisible()
    expect(within(section).getByText(/在下面的“高级凭据授权”里授权给它的密钥才能注入/)).toBeVisible()
    await fireEvent.update(within(section).getByLabelText('保有环境总数（含停止、未知、待删除）'), '5')
    await fireEvent.click(within(section).getByRole('button', { name: '允许它在容器服务器上建环境、执行命令' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('执行任意命令'))
    await within(section).findByText('已打开运行环境权限')
    const put = requests.find(r => r.method === 'PUT' && path(r).endsWith('/runner'))!
    expect(await put.clone().json()).toEqual({ enabled: true, version: 1, limits: { envs: 5, persistent: 2, memory_mb: 16384, cpus: 8, disk_gb: 100, execs_per_hour: 120, run_hours_per_day: 8 } })
  })

  it('cannot be opened for a read-only instance', async () => {
    agents(instance({ scope: 'read' }))
    await openApp('/agents?instance=inst-a')
    const section = within(await screen.findByRole('article', { name: '实例：alpha' })).getByRole('region', { name: '运行环境权限' })
    expect(await within(section).findByRole('button', { name: '允许它在容器服务器上建环境、执行命令' })).toBeDisabled()
    expect(within(section).getByText(/工具权限是只读，打不开/)).toBeVisible()
  })
})

describe('通过 SSH 部署容器服务器', () => {
  const target = (over: Partial<Schema['AgentSshTargetOut']> = {}): Schema['AgentSshTargetOut'] => ({
    host: '203.0.113.20', address: '203.0.113.20', port: 22, username: 'deploy', host_key_algorithm: 'ssh-ed25519', host_key_fingerprint: 'SHA256:abc123',
    host_key_confirmed: false, host_key_confirmed_at: null, client_key_installed: false, client_key_fingerprint: null, install_dir: null, installed_at: null,
    version: 1, created_at: at, updated_at: at, ...over,
  })
  const job = (over: Partial<Schema['AgentSshJobOut']> = {}): Schema['AgentSshJobOut'] => ({
    id: 'job-1', kind: 'install', status: 'running', log: '', error_code: null, error_message: null, started_at: at, finished_at: null, updated_at: at,
    steps: [{ key: 'connect', status: 'done', detail: null }, { key: 'install', status: 'running', detail: '安装 Docker 与 gVisor' }, { key: 'online', status: 'pending', detail: null }], ...over,
  })

  it('probes the host key, confirms the fingerprint and deploys with a one-time password', async () => {
    let state: Schema['AgentSshState'] = { target: null, job: null, warning: null }
    const requests = setup(r => {
      const p = path(r)
      if (p === '/api/v1/runner/hosts/host-1/ssh' && r.method === 'GET') return Response.json(state)
      if (p === '/api/v1/runner/hosts/host-1/ssh' && r.method === 'PUT') { state = { target: target(), job: null, warning: null }; return Response.json(state) }
      if (p === '/api/v1/runner/hosts/host-1/ssh/confirm') { state = { target: target({ host_key_confirmed: true, version: 2 }), job: null, warning: null }; return Response.json(state) }
      if (p === '/api/v1/runner/hosts/host-1/ssh/install') { state = { target: target({ host_key_confirmed: true, version: 2 }), job: job(), warning: null }; return Response.json(state, { status: 202 }) }
      return undefined
    }, { role: 'owner' })
    await openApp('/runner?tab=settings')
    const panel = await screen.findByRole('region', { name: '通过 SSH 部署' })
    const form = await within(panel).findByRole('form', { name: '容器服务器的 SSH 地址' })
    await fireEvent.update(within(form).getByLabelText('服务器 IP 或域名 *'), '203.0.113.20')
    await fireEvent.update(within(form).getByLabelText('SSH 账号 *'), 'deploy')
    await fireEvent.click(within(form).getByRole('button', { name: '读取主机公钥' }))
    expect(await within(panel).findByTestId('runner-ssh-fingerprint')).toHaveTextContent('SHA256:abc123')
    await within(panel).findByText('已读取主机公钥：请在服务器上核对指纹')
    expect(within(panel).getByText('ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub')).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: '确认指纹' })).toBeDisabled()
    await fireEvent.click(within(panel).getByLabelText(/我已在服务器上核对/))
    await vi.waitFor(() => expect(within(panel).getByRole('button', { name: '确认指纹' })).toBeEnabled())
    await fireEvent.click(within(panel).getByRole('button', { name: '确认指纹' }))
    const deploy = await within(panel).findByRole('form', { name: '部署容器服务器' })
    await fireEvent.update(within(deploy).getByLabelText('登录密码 *'), 'server pw')
    await vi.waitFor(() => expect(within(deploy).getByRole('button', { name: '开始部署' })).toBeEnabled())
    await fireEvent.click(within(deploy).getByRole('button', { name: '开始部署' }))
    expect(await within(panel).findByRole('region', { name: '部署进度' })).toHaveTextContent('运行安装脚本（Docker、gVisor、runner-agent）')
    const install = requests.find(r => path(r) === '/api/v1/runner/hosts/host-1/ssh/install')!
    expect(await install.clone().json()).toEqual({ version: 2, password: 'server pw' })
    const put = requests.find(r => path(r) === '/api/v1/runner/hosts/host-1/ssh' && r.method === 'PUT')!
    expect(await put.clone().json()).toEqual({ host: '203.0.113.20', port: 22, username: 'deploy' })
  })

  it('is owner only', async () => {
    setup()
    await openApp('/runner?tab=settings')
    const panel = await screen.findByRole('region', { name: '通过 SSH 部署' })
    expect(within(panel).getByText(/只有 owner 能通过 SSH 部署/)).toBeVisible()
  })
})
