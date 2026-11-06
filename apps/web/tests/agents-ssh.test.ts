import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// T-0090: "通过 SSH 自动部署" on the OpenClaw instance card.
const at = '2026-11-03T04:00:00Z'
const base = '/api/v1/agents/instances/inst-a/ssh'
const path = (r: Request) => new URL(r.url).pathname
const instance = (over: Partial<Schema['AgentInstanceOut']> = {}): Schema['AgentInstanceOut'] => ({ id: 'inst-a', name: 'alpha', description: '', network_mode: 'public', scope: 'read', max_role: 'member', status: 'pending', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: null, reported_version: null, plugin_version: null, bundle_issued_at: null, im_token_expires_at: null, disabled_at: null, project_admin: { enabled: false, granted_by: null, granted_at: null }, im_media: { enabled: false, granted_by: null, granted_at: null }, version: 3, created_at: at, updated_at: at, credentials: [], secret_grants: [], ...over })
const target = (over: Partial<Schema['AgentSshTargetOut']> = {}): Schema['AgentSshTargetOut'] => ({ host: 'oc.example.com', address: '203.0.113.10', port: 2222, username: 'deploy', host_key_algorithm: 'ssh-ed25519', host_key_fingerprint: 'SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG', host_key_confirmed: false, host_key_confirmed_at: null, client_key_installed: false, client_key_fingerprint: null, install_dir: null, installed_at: null, version: 1, created_at: at, updated_at: at, ...over })
const STEPS = ['connect', 'inspect', 'authorize_key', 'docker', 'reconnect', 'upload', 'pull', 'plugin', 'start', 'schedule', 'online'] as const
const job = (status: Schema['AgentSshJobOut']['status'], over: Partial<Schema['AgentSshJobOut']> = {}): Schema['AgentSshJobOut'] => ({ id: 'job-1', kind: 'install', status, log: '$ docker compose pull\npassword=***', error_code: null, error_message: null, started_at: at, finished_at: status === 'running' ? null : at, updated_at: at,
  steps: STEPS.map((key, index) => ({ key, status: status === 'succeeded' ? 'done' : index < 2 ? 'done' : index === 2 ? 'running' : 'pending', detail: key === 'online' && status === 'succeeded' ? '实例已连上工作台' : null })), ...over })
const state = (t: Schema['AgentSshTargetOut'] | null, j: Schema['AgentSshJobOut'] | null = null, warning: string | null = null): Schema['AgentSshState'] => ({ target: t, job: j, warning })
const fail = (status: number, code: string, details: Record<string, unknown> = {}, headers: Record<string, string> = {}) => Response.json({ error: { code, message: 'x', request_id: 'req', details } }, { status, headers })

function setup(handler: (r: Request) => Response | Promise<Response | undefined> | undefined, inst: Schema['AgentInstanceOut'] = instance()) {
  return mockApi(async r => await handler(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'owner' }] }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/instances' ? Response.json({ items: [inst], next_cursor: null, total_estimate: 1 }) :
    path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
    path(r) === '/api/v1/agents/host-summary' ? Response.json({ items: [] }) :
    path(r).endsWith('/ops') && r.method === 'GET' ? Response.json({ available: false, reason: 'not_deployed', running: null, items: [] }) : undefined
  ))
}
const json = async (requests: Request[], method: string, p: string) => {
  await vi.waitFor(() => expect(requests.some(r => r.method === method && path(r) === p)).toBe(true))
  return await requests.filter(r => r.method === method && path(r) === p).at(-1)!.clone().json() as unknown
}
async function panel() {
  await openApp('/agents')
  const card = await screen.findByRole('article', { name: '实例：alpha' })
  return within(card).getByRole('region', { name: 'SSH 自动部署' })
}
afterEach(() => vi.restoreAllMocks())

describe('ssh automatic deployment', () => {
  it('probes the server, confirms the fingerprint, sends the password once and follows the job to online', async () => {
    let current = state(null), polls = 0
    const requests = setup(async r => {
      if (path(r) === base && r.method === 'GET') { polls++; return Response.json(current) }
      if (path(r) === base && r.method === 'PUT') { current = state(target()); return Response.json(current) }
      if (path(r) === `${base}/confirm`) { current = state(target({ host_key_confirmed: true, host_key_confirmed_at: at, version: 2 })); return Response.json(current) }
      if (path(r) === `${base}/install`) {
        const running = state(target({ host_key_confirmed: true, host_key_confirmed_at: at, client_key_installed: true, version: 3 }), job('running'))
        // The next poll sees the finished job.
        current = state(target({ host_key_confirmed: true, host_key_confirmed_at: at, client_key_installed: true, client_key_fingerprint: 'SHA256:client', install_dir: '/home/deploy/myworkspace-openclaw', installed_at: at, version: 4 }), job('succeeded'))
        return Response.json(running, { status: 202 })
      }
    })
    const ssh = await panel()
    const form = await within(ssh).findByRole('form', { name: '目标服务器' })
    const probe = within(form).getByRole('button', { name: '读取主机公钥' })
    expect(probe).toBeDisabled()
    await fireEvent.update(within(form).getByLabelText('服务器 IP 或域名 *'), ' oc.example.com ')
    await fireEvent.update(within(form).getByLabelText('SSH 端口 *'), '2222')
    await fireEvent.update(within(form).getByLabelText('SSH 账号 *'), 'bad user')
    expect(probe).toBeDisabled()
    await fireEvent.update(within(form).getByLabelText('SSH 账号 *'), 'deploy')
    await fireEvent.click(probe)
    expect(await json(requests, 'PUT', base)).toEqual({ host: 'oc.example.com', port: 2222, username: 'deploy' })

    const check = await within(ssh).findByRole('group', { name: '核对主机公钥' })
    expect(within(check).getByText('SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG')).toBeVisible()
    expect(within(check).getByText('ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub')).toBeVisible()
    const confirm = within(check).getByRole('button', { name: '确认指纹' })
    expect(confirm).toBeDisabled()
    await fireEvent.click(within(check).getByLabelText(/我已在服务器上核对/))
    await fireEvent.click(confirm)
    expect(await json(requests, 'POST', `${base}/confirm`)).toEqual({ version: 1, fingerprint: 'SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG' })

    const deploy = await within(ssh).findByRole('form', { name: '部署' })
    const password = within(deploy).getByLabelText('登录密码 *') as HTMLInputElement
    expect(password).toHaveAttribute('type', 'password')
    expect(within(deploy).getByRole('button', { name: '开始部署' })).toBeDisabled()
    await fireEvent.update(password, 'hunter2-secret')
    await fireEvent.click(within(deploy).getByRole('button', { name: '开始部署' }))
    expect(password.value).toBe('')
    expect(await json(requests, 'POST', `${base}/install`)).toEqual({ version: 2, password: 'hunter2-secret' })

    const progress = await within(ssh).findByRole('region', { name: '部署进度' })
    expect(await within(progress).findByText('部署完成，实例已连上工作台。')).toBeVisible()
    expect(within(progress).getByText('等待实例连上工作台：实例已连上工作台')).toBeVisible()
    expect(within(progress).getByText(/password=\*\*\*/)).toBeInTheDocument()
    expect(polls).toBeGreaterThanOrEqual(2)
    // The job finishing refreshes the instance list (its status and credentials changed).
    const lists = () => requests.filter(r => r.method === 'GET' && path(r) === '/api/v1/agents/instances').length
    await vi.waitFor(() => expect(lists()).toBeGreaterThanOrEqual(2))
    expect(within(ssh).getByText('/home/deploy/myworkspace-openclaw')).toBeVisible()
    // The password went out in exactly one request and is nowhere on the page.
    const bodies = await Promise.all(requests.filter(r => r.method !== 'GET').map(r => r.clone().text()))
    expect(bodies.filter(b => b.includes('hunter2-secret'))).toHaveLength(1)
    expect(document.body.innerHTML).not.toContain('hunter2-secret')
  })

  it('never carries a password typed for one server over to another', async () => {
    const a = target({ host_key_confirmed: true, host_key_confirmed_at: at, version: 2 })
    let current = state(a)
    const requests = setup(r => {
      if (path(r) === base && r.method === 'GET') return Response.json(current)
      if (path(r) === base && r.method === 'PUT') { current = state(target({ host: 'b.example.com', address: '198.51.100.7', host_key_fingerprint: 'SHA256:server-b', version: 3 })); return Response.json(current) }
      if (path(r) === `${base}/confirm`) { current = state(target({ host: 'b.example.com', address: '198.51.100.7', host_key_fingerprint: 'SHA256:server-b', host_key_confirmed: true, host_key_confirmed_at: at, version: 4 })); return Response.json(current) }
    })
    const ssh = await panel()
    let deploy = await within(ssh).findByRole('form', { name: '部署' })
    await fireEvent.update(within(deploy).getByLabelText('登录密码 *'), 'password-for-a')
    await fireEvent.click(within(deploy).getByRole('button', { name: '更换服务器' }))
    const form = within(ssh).getByRole('form', { name: '目标服务器' })
    await fireEvent.update(within(form).getByLabelText('服务器 IP 或域名 *'), 'b.example.com')
    await fireEvent.click(within(form).getByRole('button', { name: '读取主机公钥' }))
    const check = await within(ssh).findByRole('group', { name: '核对主机公钥' })
    await fireEvent.click(within(check).getByLabelText(/我已在服务器上核对/))
    await fireEvent.click(within(check).getByRole('button', { name: '确认指纹' }))
    deploy = await within(ssh).findByRole('form', { name: '部署' })
    expect((within(deploy).getByLabelText('登录密码 *') as HTMLInputElement).value).toBe('')
    expect(within(deploy).getByRole('button', { name: '开始部署' })).toBeDisabled()
    const bodies = await Promise.all(requests.filter(r => r.method !== 'GET').map(r => r.clone().text()))
    expect(bodies.some(b => b.includes('password-for-a'))).toBe(false)
  })

  it('polls a running job until it fails and shows the reason and the redacted log', async () => {
    let calls = 0
    const confirmed = target({ host_key_confirmed: true, host_key_confirmed_at: at, client_key_installed: true, version: 3 })
    setup(r => {
      if (path(r) === base && r.method === 'GET') {
        calls++
        return Response.json(calls === 1 ? state(confirmed, job('running')) : state(confirmed, job('failed', { error_code: 'pull_failed', error_message: '拉取镜像失败，详见日志', steps: job('running').steps.map(s => s.key === 'authorize_key' ? { ...s, status: 'failed' } : s) })))
      }
    })
    const ssh = await panel()
    const progress = await within(ssh).findByRole('region', { name: '部署进度' })
    expect(within(progress).getByText('部署：进行中')).toBeVisible()
    expect(within(ssh).queryByRole('form', { name: '部署' })).not.toBeInTheDocument()
    expect(await within(progress).findByText(/拉取镜像失败，详见日志/, {}, { timeout: 4000 })).toBeVisible()
    expect(within(progress).getByText(/pull_failed/)).toBeVisible()
    expect(within(ssh).getByRole('form', { name: '部署' })).toBeVisible()
  })

  it('redeploys without a password once the workbench key is on the server', async () => {
    const installed = target({ host_key_confirmed: true, host_key_confirmed_at: at, client_key_installed: true, installed_at: at, install_dir: '/home/deploy/myworkspace-openclaw', version: 5 })
    const requests = setup(r => {
      if (path(r) === base && r.method === 'GET') return Response.json(state(installed, job('succeeded')))
      if (path(r) === `${base}/install`) return Response.json(state(installed, job('running')), { status: 202 })
    })
    const ssh = await panel()
    const deploy = await within(ssh).findByRole('form', { name: '部署' })
    expect(within(deploy).getByLabelText('登录密码（可选）')).toBeVisible()
    expect(within(deploy).queryByRole('button', { name: '更换服务器' })).not.toBeInTheDocument()
    await fireEvent.click(within(deploy).getByRole('button', { name: '重新部署' }))
    expect(await json(requests, 'POST', `${base}/install`)).toEqual({ version: 5 })
  })

  it('uninstalls with removal of the files, and forces a revoke when the server cannot be reached', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const installed = target({ host_key_confirmed: true, host_key_confirmed_at: at, client_key_installed: true, installed_at: at, version: 6 })
    const requests = setup(r => {
      if (path(r) === base && r.method === 'GET') return Response.json(state(installed))
      if (path(r) === `${base}/uninstall`) return Response.json(state(installed, job('running', { kind: 'uninstall', steps: [{ key: 'connect', status: 'running', detail: null }, { key: 'stop', status: 'pending', detail: null }] })), { status: 202 })
      if (path(r) === `${base}/revoke`) {
        return r.clone().json().then((body: { force: boolean }) => body.force
          ? Response.json(state(target({ host_key_confirmed: true, version: 7 }), null, '没能连上服务器（timeout），只删除了工作台保存的私钥；请在服务器的 ~/.ssh/authorized_keys 里手动删除这一行：ssh-ed25519 AAAA myworkspace'))
          : fail(503, 'AGENT_SSH_UNREACHABLE', { reason: 'timeout' }))
      }
    })
    const ssh = await panel()
    const manage = await within(ssh).findByRole('group', { name: '卸载与吊销' })
    await fireEvent.click(within(manage).getByLabelText(/卸载时同时删除接入包目录/))
    await fireEvent.click(within(manage).getByRole('button', { name: '卸载' }))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('不可恢复'))
    expect(await json(requests, 'POST', `${base}/uninstall`)).toEqual({ version: 6, remove_files: true })

    // The running uninstall hides the buttons until the next state arrives.
    await vi.waitFor(() => expect(within(ssh).queryByRole('region', { name: '卸载进度' })).not.toBeInTheDocument())
    await fireEvent.click(within(await within(ssh).findByRole('group', { name: '卸载与吊销' })).getByRole('button', { name: '吊销专用密钥' }))
    expect(await json(requests, 'POST', `${base}/revoke`)).toEqual({ version: 6, force: false })
    expect(await within(ssh).findByText(/连接超时：检查 IP 与端口/)).toBeVisible()
    await fireEvent.click(within(ssh).getByRole('button', { name: /强制吊销/ }))
    await vi.waitFor(() => expect(requests.filter(r => path(r) === `${base}/revoke`)).toHaveLength(2))
    expect(await requests.filter(r => path(r) === `${base}/revoke`)[1].clone().json()).toEqual({ version: 6, force: true })
    expect(await within(ssh).findByText(/手动删除这一行：ssh-ed25519 AAAA myworkspace/)).toBeVisible()
  })

  it('explains probe failures and the rate limit', async () => {
    let attempt = 0
    setup(r => {
      if (path(r) === base && r.method === 'GET') return Response.json(state(null))
      if (path(r) === base && r.method === 'PUT') return ++attempt === 1 ? fail(503, 'AGENT_SSH_UNREACHABLE', { reason: 'refused' }) : attempt === 2 ? fail(422, 'AGENT_SSH_ADDRESS_NOT_ALLOWED') : fail(429, 'RATE_LIMITED', {}, { 'Retry-After': '42' })
    })
    const ssh = await panel()
    const form = await within(ssh).findByRole('form', { name: '目标服务器' })
    await fireEvent.update(within(form).getByLabelText('服务器 IP 或域名 *'), '203.0.113.10')
    await fireEvent.update(within(form).getByLabelText('SSH 账号 *'), 'root')
    await fireEvent.click(within(form).getByRole('button', { name: '读取主机公钥' }))
    expect(await within(ssh).findByText(/连接被拒绝：这个端口上没有 SSH 服务/)).toBeVisible()
    await fireEvent.click(within(form).getByRole('button', { name: '读取主机公钥' }))
    expect(await within(ssh).findByText(/不能连接这个地址/)).toBeVisible()
    await fireEvent.click(within(form).getByRole('button', { name: '读取主机公钥' }))
    expect(await within(ssh).findByText(/请 42 秒后重试/)).toBeVisible()
  })

  it('is not offered for intranet instances and never asks for their server', async () => {
    const requests = setup(() => undefined, instance({ network_mode: 'intranet' }))
    const ssh = await panel()
    expect(within(ssh).getByText(/只支持外网模式的实例/)).toBeVisible()
    expect(within(ssh).queryByRole('form')).not.toBeInTheDocument()
    expect(requests.some(r => path(r) === base)).toBe(false)
  })
})
