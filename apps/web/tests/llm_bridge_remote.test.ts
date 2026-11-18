// 开发环境的“云端远程容器”（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 10 节）：新增开发环境弹窗的运行位置、
// 新建 / 选用远程容器、远程环境的访问权限、修改时位置只读；开发环境页的位置标签、容器信息条、操作与删除确认、
// 在容器里登录；容器服务器页上模块建的容器（申请方、不能归档 / 删除）。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { me, mockApi, newsModule, openApp, workspace } from './helpers'
import { env as runnerEnv, path as runnerPath, setup as runnerSetup } from './container-fixtures'
import type { Schema } from '../src/services/api'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const at = '2026-11-18T08:00:00Z'
const BRIDGE = '11111111-1111-4111-8111-111111111111'
const HOST = '44444444-4444-4444-8444-444444444444'
const OFFLINE = '55555555-5555-4555-8555-555555555555'
const CONTAINER = '66666666-6666-4666-8666-666666666666'
const RUNNER_ENV = '77777777-7777-4777-8777-777777777777'
const base = `/api/v1/llm_bridge/bridges/${BRIDGE}`
const path = (r: Request) => new URL(r.url).pathname
const lbModule: Schema['ModuleOut'] = { ...newsModule, key: 'llm_bridge', name: '大模型客户端', tools: [], widgets: [] }
const bridge: Schema['LlmBridgeOut'] = { id: BRIDGE, name: '主账号', base_url: 'http://10.0.0.5:8787', enabled: true, has_token: true, created_at: at, updated_at: at }
const settings: Schema['LlmBridgeSettings'] = { concurrency: 2, min_start_interval_seconds: 2, turn_timeout_seconds: 1800, send_per_hour: 60, max_text_chars: 20000, retention_days: 0, digest_enabled: true, digest_follow_retention: true, claude_subscription_ack: false, auto_resume: true, auto_resume_max: 3, resume_text: '继续', platforms: ['codex', 'claude', 'workbuddy'] }
const status: Schema['LlmBridgeStatusOut'] = { reachable: true, error: '', version: '0.1.0', platforms: ['codex', 'claude', 'workbuddy'], queued: 0, running: 0, load: {}, quota: {}, settings, accounts: [], channels: [] }
const targets: Schema['LlmBridgeRemoteTargets'] = {
  hosts: [
    { id: HOST, name: '容器服务器 A', status: 'online', available: true, reason: null, cpus: 16, memory_mb: 65536, disk_gb: 400, runtimes: ['runc', 'runsc'] },
    { id: OFFLINE, name: '容器服务器 B', status: 'offline', available: false, reason: '容器服务器不在线', cpus: 8, memory_mb: 32768, disk_gb: 100, runtimes: ['runc'] },
  ],
  presets: { small: { cpus: 1, memory_mb: 2048, disk_gb: 10 }, medium: { cpus: 2, memory_mb: 4096, disk_gb: 20 }, large: { cpus: 4, memory_mb: 8192, disk_gb: 50 } },
  limits: { count: 5, cpus: 16, memory_mb: 32768, disk_gb: 200 }, used: { count: 0, cpus: 0, memory_mb: 0, disk_gb: 0 },
  versions: { codex: '0.169.0', claude: '2.1.323', workbuddy: '2.184.0' }, network_domains: {},
}
const container = (over: Partial<Schema['LlmBridgeRemoteContainerOut']> = {}): Schema['LlmBridgeRemoteContainerOut'] => ({
  id: CONTAINER, bridge_id: BRIDGE, name: '开发机', host_id: HOST, host_name: '容器服务器 A', image_kind: 'builtin', image: null, state: 'running', state_reason: null,
  runner_environment_id: RUNNER_ENV, cpus: 2, memory_mb: 4096, disk_gb: 20, runtime: 'gvisor', network: 'all', domains: [], secrets: [], idle_stop_minutes: 30,
  cli_versions: { codex: '0.169.0', claude: '2.1.323', workbuddy: '2.184.0' }, versions_ok: true, updating_image: false, image_error: null, last_used_at: at, created_at: at, version: 3, ...over,
})
const remoteEnv: Schema['LlmBridgeEnvironmentOut'] = { id: 'be_remote0000000000000001', platform: 'claude', mode: 'dev', name: '远程仓库', enabled: true, repo_url: 'https://github.com/o/r', branch: 'main', sandbox: 'exec', network: false, description: '', location: 'remote', remote_container: CONTAINER, remote_label: '容器服务器 A · 开发机' }
const localEnv: Schema['LlmBridgeEnvironmentOut'] = { id: 'be_local00000000000000001', platform: 'codex', mode: 'dev', name: '本地仓库', enabled: true, repo_url: null, branch: null, sandbox: 'read_only', network: false, description: '', location: 'local' }

function setup(handler?: (r: Request) => Response | undefined | Promise<Response | undefined>, options: { envs?: Schema['LlmBridgeEnvironmentOut'][]; containers?: Schema['LlmBridgeRemoteContainerOut'][] } = {}) {
  return mockApi(async r => {
    const result = await handler?.(r)
    if (result) return result
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] })
    if (p === '/api/v1/modules' && r.method === 'GET') return Response.json({ items: [newsModule, lbModule] })
    if (p === '/api/v1/llm_bridge/bridges' && r.method === 'GET') return Response.json({ bridges: [bridge] })
    if (p === `${base}/status`) return Response.json(status)
    if (p === `${base}/connectivity`) return Response.json({ supported: true, slots: {} })
    if (p === '/api/v1/llm_bridge/grants') return Response.json({ grants: [] })
    if (p === '/api/v1/llm_bridge/local' && r.method === 'GET') return Response.json({ available: false, bridge_id: null, taken: false })
    if (p === '/api/v1/llm_bridge/deployments' && r.method === 'GET') return Response.json({ jobs: [], available: false })
    if (p === `${base}/environments` && r.method === 'GET') return Response.json({ environments: options.envs ?? [] })
    if (p === `${base}/sessions`) return Response.json({ sessions: [], sync_failed: '' })
    if (p === `${base}/containers` && r.method === 'GET') return Response.json({ containers: options.containers ?? [] })
    if (p === '/api/v1/llm_bridge/remote/targets') return Response.json(targets)
    if (/\/environments\/[^/]+\/sessions$/.test(p)) return Response.json({ sessions: [] })
    return undefined
  })
}
const writes = (requests: Request[]) => requests.filter(r => r.method !== 'GET' && path(r).startsWith('/api/v1/llm_bridge'))
async function envTab() {
  await openApp('/llm_bridge?view=manage')
  await fireEvent.click(await screen.findByRole('tab', { name: '开发环境' }))
  return screen.findByRole('tabpanel', { name: '开发环境' })
}

describe('新增开发环境：云端远程容器', () => {
  it('creates an environment with a new remote container on an available server', async () => {
    const requests = setup(async r => (path(r) === `${base}/environments` && r.method === 'POST' ? Response.json({ id: 'be_new', ...(await r.clone().json()), remote_container: CONTAINER, remote_label: '容器服务器 A · 远程仓库 容器' }, { status: 201 }) : undefined))
    const tab = await envTab()
    await fireEvent.click(await within(tab).findByRole('button', { name: '新增环境' }))
    const form = within(tab).getByRole('form', { name: '新增开发环境' })
    await fireEvent.click(within(form).getByRole('radio', { name: 'Claude' }))
    await fireEvent.update(within(form).getByLabelText(/^环境名称/), '远程仓库')
    // 默认在管理器容器里：有“允许联网”，没有远程容器一节
    expect(within(form).getByLabelText(/允许联网/)).toBeInTheDocument()
    expect(within(form).queryByRole('region', { name: '远程容器' })).toBeNull()
    await fireEvent.click(within(form).getByRole('radio', { name: /云端远程容器/ }))
    const remote = await within(form).findByRole('region', { name: '远程容器' })
    // 远程：访问权限默认“可执行命令”，没有“允许联网”，提醒容器里的命令能读到登录凭据
    expect(within(form).getByLabelText('访问权限')).toHaveValue('exec')
    expect(within(form).getByText('可以改文件、执行命令（在远程容器里，容器本身是沙箱）。')).toBeVisible()
    expect(within(form).queryByLabelText(/允许联网/)).toBeNull()
    expect(within(form).getByText(/容器里执行的命令能读到这个平台在容器里的登录凭据/)).toBeVisible()
    // 不在线的服务器写原因、不能选；只有一台能用时自动选上
    const host = await within(remote).findByLabelText('容器服务器')
    await waitFor(() => expect(host).toHaveValue(HOST))
    const offline = within(host).getByRole('option', { name: /容器服务器 B（容器服务器不在线）/ })
    expect(offline).toBeDisabled()
    // 网络默认“全部外网”，说明订阅登录的域名还没核验完
    expect(within(remote).getByLabelText('网络')).toHaveValue('all')
    expect(within(remote).getByText(/订阅账号登录、刷新令牌要连的域名还没核验完/)).toBeVisible()
    await fireEvent.click(within(remote).getByRole('radio', { name: /大/ }))
    expect(within(tab).getByText('会在容器服务器「容器服务器 A」上新建一个容器（约 50 GB 磁盘）；可以在容器服务器页面查看。')).toBeVisible()
    await fireEvent.update(within(remote).getByLabelText('空闲停止'), '120')
    await fireEvent.update(within(form).getByLabelText('仓库地址'), 'https://github.com/o/r')
    await fireEvent.click(within(form).getByRole('button', { name: '创建环境' }))
    expect(await within(tab).findByText(/已新增环境「远程仓库」。远程容器正在构建镜像/)).toBeVisible()
    const body = await writes(requests)[0]!.clone().json()
    expect(body).toMatchObject({ platform: 'claude', mode: 'dev', name: '远程仓库', sandbox: 'exec', network: false, location: 'remote', repo_url: 'https://github.com/o/r' })
    expect(body.remote_container_id).toBeUndefined()
    expect(body.new_container).toEqual({ name: '远程仓库 容器', host_id: HOST, image_kind: 'builtin', image: null, preset: 'large', runtime: 'gvisor', network: 'all', domains: [], secrets: [], idle_stop_minutes: 120 })
  })

  it('keeps Codex in the exec tier remotely, checks the image name and shows server-written errors', async () => {
    const requests = setup(r => (path(r) === `${base}/environments` && r.method === 'POST' ? Response.json({ error: { code: 'QUOTA_EXCEEDED', message: '远程开发容器的个数超过工作空间的上限（5）', request_id: 'x' } }, { status: 409 }) : undefined))
    const tab = await envTab()
    await fireEvent.click(await within(tab).findByRole('button', { name: '新增环境' }))
    const form = within(tab).getByRole('form', { name: '新增开发环境' })
    await fireEvent.click(within(form).getByRole('radio', { name: 'Codex' }))
    await fireEvent.update(within(form).getByLabelText(/^环境名称/), 'Codex 远程')
    await fireEvent.click(within(form).getByRole('radio', { name: /云端远程容器/ }))
    const sandbox = within(form).getByLabelText('访问权限')
    expect(sandbox).toHaveValue('exec')
    expect(within(sandbox).getByRole('option', { name: '只读' })).toBeDisabled()
    expect(within(sandbox).getByRole('option', { name: '可写工作区' })).toBeDisabled()
    expect(within(form).getByText(/远程容器里的 Codex 只有“可执行命令”这一档/)).toBeVisible()
    const remote = await within(form).findByRole('region', { name: '远程容器' })
    await waitFor(() => expect(within(remote).getByLabelText('容器服务器')).toHaveValue(HOST))
    await fireEvent.update(within(remote).getByLabelText('镜像'), 'custom')
    await fireEvent.click(within(form).getByRole('button', { name: '创建环境' }))
    expect(await within(tab).findByText('请填镜像名称。')).toBeVisible()
    expect(writes(requests)).toEqual([])
    await fireEvent.update(within(remote).getByLabelText('镜像名称'), 'python:3.12-slim')
    await fireEvent.update(within(remote).getByLabelText('网络'), 'models')
    await fireEvent.update(within(remote).getByLabelText('另加的域名'), 'pypi.org, *.example.com')
    await fireEvent.click(within(form).getByRole('button', { name: '创建环境' }))
    expect(await within(tab).findByText('远程开发容器的个数超过工作空间的上限（5）')).toBeVisible()
    const body = await writes(requests)[0]!.clone().json()
    expect(body.sandbox).toBe('exec')
    expect(body.new_container).toMatchObject({ image_kind: 'custom', image: 'python:3.12-slim', network: 'models', domains: ['pypi.org', '*.example.com'] })
  })

  it('uses an existing container and keeps the location read-only when editing', async () => {
    const requests = setup(async r => {
      if (path(r) === `${base}/environments` && r.method === 'POST') return Response.json({ id: 'be_new', ...(await r.clone().json()) }, { status: 201 })
      if (path(r) === `${base}/environments/${remoteEnv.id}` && r.method === 'PUT') return Response.json({ ...remoteEnv, ...(await r.clone().json()) })
      return undefined
    }, { envs: [remoteEnv], containers: [container()] })
    const tab = await envTab()
    await fireEvent.click(await within(tab).findByRole('button', { name: '新增环境' }))
    const form = within(tab).getByRole('form', { name: '新增开发环境' })
    await fireEvent.update(within(form).getByLabelText(/^环境名称/), '共用容器')
    await fireEvent.click(within(form).getByRole('radio', { name: /云端远程容器/ }))
    const remote = await within(form).findByRole('region', { name: '远程容器' })
    await waitFor(() => expect(within(remote).getByLabelText('容器')).toHaveValue('existing'))
    expect(within(remote).getByLabelText('已有的容器')).toHaveValue(CONTAINER)
    await fireEvent.click(within(form).getByRole('button', { name: '创建环境' }))
    await within(tab).findByText(/已新增环境「共用容器」/)
    const created = await writes(requests)[0]!.clone().json()
    expect(created).toMatchObject({ location: 'remote', remote_container_id: CONTAINER })
    expect(created.new_container).toBeUndefined()

    // 修改远程环境：运行位置只读，提交带着原来的容器
    await fireEvent.click(await within(tab).findByRole('button', { name: '环境设置：远程仓库' }))
    const edit = within(tab).getByRole('form', { name: '修改开发环境' })
    expect(within(edit).getByRole('radio', { name: /云端远程容器/ })).toBeChecked()
    expect(within(edit).getByRole('radio', { name: /云端远程容器/ })).toBeDisabled()
    expect(within(edit).getByText(/运行位置建好后不能改：远程容器「容器服务器 A · 开发机」/)).toBeVisible()
    await fireEvent.click(within(edit).getByRole('button', { name: '保存' }))
    await within(tab).findByText('已保存环境「远程仓库」。')
    expect(await writes(requests)[1]!.clone().json()).toMatchObject({ location: 'remote', remote_container_id: CONTAINER })
  })
})

describe('开发环境页：远程容器', () => {
  it('labels remote environments and manages the container', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup(r => {
      if (path(r) === `${base}/containers/${CONTAINER}/actions`) return Response.json(container({ state: 'stopped', version: 4 }))
      if (path(r) === `${base}/containers/${CONTAINER}` && r.method === 'DELETE') return Response.json({ error: { code: 'CONTAINER_IN_USE', message: '还有开发环境在用这个容器：远程仓库', request_id: 'x' } }, { status: 409 })
      if (path(r) === `${base}/containers/${CONTAINER}/platforms`) return Response.json({ platforms: { claude: { state: 'injected', detail: '', identity: null, can_login: false } } })
      return undefined
    }, { envs: [localEnv, remoteEnv], containers: [container({ versions_ok: false })] })
    const tab = await envTab()
    const list = await within(tab).findByRole('navigation', { name: '开发环境列表' })
    const item = await within(list).findByRole('button', { name: /远程仓库/ })
    await waitFor(() => expect(item).toHaveTextContent('云端 · 容器服务器 A'))
    expect(item).toHaveTextContent('运行中')
    expect(within(list).getByRole('button', { name: /本地仓库/ })).not.toHaveTextContent('云端')
    await fireEvent.click(item)
    const bar = await within(tab).findByRole('region', { name: '远程容器：开发机' })
    expect(bar).toHaveTextContent('2 核 · 4 GB 内存 · 20 GB 磁盘')
    expect(within(bar).getByRole('link', { name: '在容器服务器查看 →' })).toHaveAttribute('href', `/runner?environment_id=${RUNNER_ENV}`)
    expect(within(bar).getByText(/命令行版本和 MCP 管理器不一致/)).toBeVisible()
    expect(await within(bar).findByText('凭据随回合注入')).toBeVisible()
    // 停止要确认；用最新的 version
    await fireEvent.click(within(bar).getByRole('button', { name: '停止' }))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('正在跑的回合和后台作业会中断'))
    await within(bar).findByText(/已发出“停止”：开发机/)
    const stop = writes(requests).find(r => path(r).endsWith('/actions'))!
    expect(await stop.clone().json()).toEqual({ action: 'stop', version: 3 })
    // 删除容器：确认写明会失去什么；还有环境在用时显示后端的原因
    await fireEvent.click(within(bar).getByRole('button', { name: '删除容器' }))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('会话的工作目录和容器里的登录都会没了'))
    expect(await within(bar).findByText('还有开发环境在用这个容器：远程仓库')).toBeVisible()
  })

  it('edits the container network, extra domains and idle stop', async () => {
    const requests = setup(r => {
      if (path(r) === `${base}/containers/${CONTAINER}` && r.method === 'PATCH') return Response.json(container({ network: 'models', domains: ['git.example.com'], idle_stop_minutes: 120, version: 4 }))
      if (path(r) === `${base}/containers/${CONTAINER}/platforms`) return Response.json({ platforms: {} })
      return undefined
    }, { envs: [remoteEnv], containers: [container()] })
    const tab = await envTab()
    const list = await within(tab).findByRole('navigation', { name: '开发环境列表' })
    await fireEvent.click(await within(list).findByRole('button', { name: /远程仓库/ }))
    const bar = await within(tab).findByRole('region', { name: '远程容器：开发机' })
    await fireEvent.click(within(bar).getByRole('button', { name: '设置' }))
    const form = within(bar).getByRole('form', { name: '远程容器的设置' })
    expect(form).toHaveTextContent('资源（CPU、内存、磁盘）建好后不能改')
    await fireEvent.update(within(form).getByLabelText('网络'), 'models')
    await fireEvent.update(within(form).getByLabelText('另加的域名'), 'Git.Example.com\n')
    await fireEvent.update(within(form).getByLabelText('空闲停止'), '120')
    await fireEvent.click(within(form).getByRole('button', { name: '保存设置' }))
    await within(bar).findByText(/已保存「开发机」的设置/)
    const patch = writes(requests).find(r => r.method === 'PATCH')!
    expect(await patch.clone().json()).toEqual({ version: 3, network: 'models', domains: ['git.example.com'], idle_stop_minutes: 120 })
  })

  it('logs a platform in inside the container and hands over the authorization code', async () => {
    let started = false
    const requests = setup(async r => {
      const p = path(r)
      if (p === `${base}/containers/${CONTAINER}/platforms`) return Response.json({ platforms: { claude: { state: started ? 'ready' : 'needs_login', detail: '', identity: null, can_login: true } } })
      if (p === `${base}/containers/${CONTAINER}/login/claude` && r.method === 'POST') {
        const body = await r.clone().json()
        started = true
        if (body.action === 'start') return Response.json({ state: 'pending', verification_url: 'https://claude.ai/oauth/authorize?x=1', user_code: null, detail: '', steps: [], log: [], needs_code: true })
        return Response.json({ state: 'done', verification_url: null, user_code: null, detail: '账号一致', steps: [], log: [], needs_code: false })
      }
      if (p === `${base}/containers/${CONTAINER}/login/claude`) return Response.json({ state: 'pending', verification_url: 'https://claude.ai/oauth/authorize?x=1', user_code: null, detail: '', steps: [], log: [], needs_code: true })
      return undefined
    }, { envs: [remoteEnv], containers: [container()] })
    const tab = await envTab()
    const bar = await within(tab).findByRole('region', { name: '远程容器：开发机' })
    expect(await within(bar).findByText('要在容器里登录')).toBeVisible()
    await fireEvent.click(within(bar).getByRole('button', { name: '在容器里登录' }))
    const panel = await within(bar).findByRole('group', { name: '在远程容器里登录 Claude' })
    expect(await within(panel).findByRole('link', { name: 'https://claude.ai/oauth/authorize?x=1' })).toBeVisible()
    await fireEvent.update(within(panel).getByLabelText('授权码'), 'code#state')
    await fireEvent.click(within(panel).getByRole('button', { name: '提交授权码' }))
    expect(await within(panel).findByText(/Claude 已在远程容器里登录。账号一致/)).toBeVisible()
    const posted = writes(requests).filter(r => path(r).endsWith('/login/claude'))
    expect(await posted[0]!.clone().json()).toEqual({ action: 'start' })
    expect(await posted[1]!.clone().json()).toEqual({ action: 'code', code: 'code#state' })
  })
})

describe('容器服务器页：模块建的容器', () => {
  it('shows who owns it and leaves deleting to the LLM client page', async () => {
    const managed = runnerEnv({ owner_kind: 'llm_bridge', owner_label: 'MCP 管理器「主账号」· 远程容器「开发机」', owner_instance_id: null, lifetime: 'persistent', no_auto_archive: true })
    runnerSetup({ list: () => ({ items: [managed], total: 1, next_cursor: null }), detail: id => Response.json({ ...managed, id }) })
    await openApp(`/runner?environment_id=${managed.id}`)
    const tabs = await screen.findByRole('tablist', { name: '容器详情' })
    const panel = tabs.closest('.cs-detail') as HTMLElement
    expect(within(panel).getAllByText('MCP 管理器「主账号」· 远程容器「开发机」').length).toBeGreaterThan(0)
    await fireEvent.click(within(tabs).getByRole('tab', { name: '配置与回收' }))
    const actions = await screen.findByRole('region', { name: '生命周期操作' })
    expect(within(actions).getByRole('button', { name: '停止' })).toBeVisible()
    expect(within(actions).queryByRole('button', { name: '删除' })).toBeNull()
    expect(within(actions).queryByRole('button', { name: '归档' })).toBeNull()
    expect(within(actions).getByRole('link', { name: '大模型客户端 → 开发环境' })).toHaveAttribute('href', '/llm_bridge?view=manage')
    expect(screen.getByText('不自动归档（空闲时只停止）')).toBeVisible()
    expect(runnerPath).toBeTypeOf('function')
  })
})
