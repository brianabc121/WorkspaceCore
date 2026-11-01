import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

const at = '2026-11-01T04:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const instance = (over: Partial<Schema['AgentInstanceOut']> = {}): Schema['AgentInstanceOut'] => ({ id: 'inst-a', name: 'alpha', description: '机房 A', network_mode: 'public', scope: 'read', max_role: 'member', status: 'online', im_user_id: 'oc_a', im_nickname: '甲', im_ready: true, acting_user_id: 'user-a', last_seen_at: at, reported_version: '2026.9.36', plugin_version: '0.3.3', bundle_issued_at: at, disabled_at: null, version: 3, created_at: at, updated_at: at,
  credentials: [{ id: 'cred-1', kind: 'token', ingress: 'public', token_prefix: 'mwa_abcd', cert_fingerprint: null, created_at: at, expires_at: null, last_used_at: at, last_used_ip: '203.0.113.5', revoked_at: null }],
  secret_grants: [{ ref_id: 'openai_api_key', secret_id: 'sec-1', secret_name: 'OpenAI' }], ...over })
const secret: Schema['AgentSecretOut'] = { id: 'sec-1', name: 'OpenAI', kind: 'model_api_key', description: '', value_version: 2, last_rotated_at: at, grants: [{ instance_id: 'inst-a', instance_name: 'alpha', ref_id: 'openai_api_key' }], version: 5, created_at: at, updated_at: at }
const access: Schema['SecretAccessOut'] = { id: 'acc-1', at, instance_id: 'inst-a', instance_name: 'alpha', ref_id: 'openai_api_key', result: 'ok', secret_id: 'sec-1', source_ip: '203.0.113.5', value_version: 2 }

function setup(handler?: (r: Request) => Response | Promise<Response | undefined> | undefined, role: Schema['WorkspaceOut']['role'] = 'owner') {
  return mockApi(async r => await handler?.(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) :
    path(r) === '/api/v1/agents/settings' && r.method === 'GET' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 7 }) :
    path(r) === '/api/v1/agents/instances' && r.method === 'GET' ? Response.json({ items: [instance()], next_cursor: null, total_estimate: 1 }) :
    path(r) === '/api/v1/agents/secrets' && r.method === 'GET' ? Response.json({ items: [secret], next_cursor: null, total_estimate: 1 }) :
    path(r) === '/api/v1/agents/secrets/accesses' ? Response.json({ items: [access], next_cursor: null, total_estimate: 1 }) : undefined
  ))
}
afterEach(() => vi.restoreAllMocks())

describe('openclaw instances', () => {
  it('is only for the owner', async () => {
    setup(undefined, 'admin')
    const { router } = await openApp('/agents')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/forbidden'))
    expect(screen.queryByRole('link', { name: 'OpenClaw 实例' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '密钥库' })).not.toBeInTheDocument()
  })
  it('registers an instance and disables one with its version after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/instances' && r.method === 'POST') return Response.json(instance({ id: 'inst-b', name: 'beta' }), { status: 201 })
      if (path(r).endsWith('/disable')) return Response.json(instance({ status: 'disabled' }))
    })
    await openApp('/agents')
    const card = await screen.findByRole('article', { name: '实例：alpha' })
    expect(within(card).getByText('在线')).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '登记实例' }))
    const dialog = await screen.findByRole('dialog', { name: '登记 OpenClaw 实例' })
    await fireEvent.update(within(dialog).getByLabelText('名称 *'), 'beta')
    await fireEvent.click(within(dialog).getByLabelText(/^内网/))
    await fireEvent.click(within(dialog).getByRole('button', { name: '登记' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'POST' && path(r) === '/api/v1/agents/instances')).toBe(true))
    expect(await requests.find(r => r.method === 'POST' && path(r) === '/api/v1/agents/instances')!.clone().json()).toEqual({ name: 'beta', description: '', scope: 'read', max_role: 'member', im_nickname: null, network_mode: 'intranet' })
    await fireEvent.click(within(card).getByRole('button', { name: '停用' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/disable'))).toBe(true))
    expect(await requests.find(r => path(r).endsWith('/disable'))!.clone().json()).toEqual({ version: 3 })
  })
  it('downloads the bundle as bytes with the CSRF token and the chosen handling of old credentials', async () => {
    const created = vi.fn(() => 'blob:bundle'), click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: created, revokeObjectURL: () => undefined }))
    document.cookie = 'ws_csrf=csrf-test'
    const requests = setup(r => path(r).endsWith('/bundle') ? new Response(new Blob([new Uint8Array([80, 75, 3, 4])]), { headers: { 'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="alpha.zip"' } }) : undefined)
    await openApp('/agents')
    const card = await screen.findByRole('article', { name: '实例：alpha' })
    await fireEvent.update(within(card).getByLabelText('旧凭证'), 'revoke_now')
    await fireEvent.click(within(card).getByRole('button', { name: '生成并下载接入包' }))
    expect(await screen.findByText(/接入包已生成并下载/)).toBeVisible()
    const post = requests.find(r => path(r).endsWith('/bundle'))!
    expect(post.headers.get('X-CSRF-Token')).toBe('csrf-test')
    expect(post.headers.get('X-Workspace-Id')).toBe('workspace-a')
    expect(await post.clone().json()).toEqual({ version: 3, previous: 'revoke_now' })
    expect(created).toHaveBeenCalledOnce(); expect(click).toHaveBeenCalledOnce()
  })
  it('edits secret grants and refuses invalid ref ids', async () => {
    const requests = setup(r => path(r).endsWith('/secret-grants') ? Response.json(instance()) : undefined)
    await openApp('/agents')
    const grants = within(await screen.findByRole('article', { name: '实例：alpha' })).getByRole('region', { name: '密钥授权' })
    await fireEvent.click(within(grants).getByRole('button', { name: '编辑授权' }))
    await fireEvent.click(within(grants).getByRole('button', { name: '添加授权' }))
    const refs = within(grants).getAllByLabelText('ref_id')
    await fireEvent.update(refs[1], 'Bad-Ref')
    await fireEvent.update(within(grants).getAllByLabelText('密钥')[1], 'sec-1')
    expect(within(grants).getByRole('button', { name: '保存授权' })).toBeDisabled()
    await fireEvent.update(refs[1], 'backup_key')
    await fireEvent.click(within(grants).getByRole('button', { name: '保存授权' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/secret-grants'))).toBe(true))
    expect(await requests.find(r => path(r).endsWith('/secret-grants'))!.clone().json()).toEqual({ grants: [{ secret_id: 'sec-1', ref_id: 'openai_api_key' }, { secret_id: 'sec-1', ref_id: 'backup_key' }], version: 3 })
  })
})

describe('secret vault', () => {
  it('adds a secret without ever showing the value, rotates with the version and filters accesses', async () => {
    const requests = setup(r => {
      if (path(r) === '/api/v1/agents/secrets' && r.method === 'POST') return Response.json(secret, { status: 201 })
      if (path(r).endsWith('/rotate')) return Response.json({ ...secret, value_version: 3, version: 6 })
    })
    await openApp('/agents/secrets')
    expect(await screen.findByRole('article', { name: '密钥：OpenAI' })).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '添加密钥' }))
    let dialog = await screen.findByRole('dialog', { name: '添加密钥' })
    await fireEvent.update(within(dialog).getByLabelText(/^名称/), 'Backup key')
    const value = within(dialog).getByLabelText('值 *')
    expect(value).toHaveAttribute('type', 'password')
    await fireEvent.update(value, 'sk-secret-value')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'POST' && path(r) === '/api/v1/agents/secrets')).toBe(true))
    expect(await requests.find(r => r.method === 'POST' && path(r) === '/api/v1/agents/secrets')!.clone().json()).toEqual({ name: 'Backup key', kind: 'model_api_key', description: '', value: 'sk-secret-value' })
    expect(screen.queryByText('sk-secret-value')).not.toBeInTheDocument()
    await fireEvent.click(within(screen.getByRole('article', { name: '密钥：OpenAI' })).getByRole('button', { name: '轮换' }))
    dialog = await screen.findByRole('dialog', { name: '轮换：OpenAI' })
    await fireEvent.update(within(dialog).getByLabelText('新值 *'), 'sk-new')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))
    await vi.waitFor(() => expect(requests.some(r => path(r).endsWith('/rotate'))).toBe(true))
    expect(await requests.find(r => path(r).endsWith('/rotate'))!.clone().json()).toEqual({ value: 'sk-new', version: 5 })
    const log = screen.getByRole('region', { name: '取用记录' })
    expect(within(log).getByRole('cell', { name: 'openai_api_key' })).toBeVisible()
    await fireEvent.update(within(log).getByLabelText('实例'), 'inst-a')
    await vi.waitFor(() => expect(requests.some(r => path(r) === '/api/v1/agents/secrets/accesses' && new URL(r.url).searchParams.get('instance_id') === 'inst-a')).toBe(true))
  })
})
