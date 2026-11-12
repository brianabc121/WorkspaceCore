// 设置 → 图片存储（T-0223）：每个工作空间在网页上选主存储、副本，填 R2 / OSS 的连接信息。
// owner 修改，admin 只读，member 看不到；保存前后端自检，不通过就不保存并显示各自的原因；
// 密钥只写不读：不回显、提交后清空、不进查询缓存；不填保持（null），清空传空字符串；版本冲突重新载入。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

type Out = Schema['StorageSettingsOut']
type In = Schema['StorageSettingsIn']
const path = (r: Request) => new URL(r.url).pathname
const blank: Out = {
  primary: 'local', replica: 'none',
  r2: { account_id: '', bucket: '', jurisdiction: '', access_key_id: '', secret_set: false },
  oss: { region: '', bucket: '', access_key_id: '', secret_set: false },
  in_use: [], version: 0, updated_at: null, checks: null,
}
const savedR2: Out = {
  ...blank, primary: 'r2', in_use: ['local', 'r2'], version: 3, updated_at: '2026-11-12T08:00:00Z',
  r2: { account_id: '0123456789abcdef', bucket: 'kb-images', jurisdiction: '', access_key_id: 'AKR2', secret_set: true },
}
const errorBody = (status: number, code: string, message: string, details: Record<string, unknown> = {}) =>
  Response.json({ error: { code, message, request_id: 'req-test', details } }, { status })

function setup(options: { role?: Schema['WorkspaceOut']['role']; initial?: Out; put?: (body: In) => Response | undefined } = {}) {
  const state = { settings: { ...(options.initial ?? blank) }, bodies: [] as In[] }
  const requests = mockApi(async r => {
    const p = path(r)
    if (p === '/api/v1/me' && r.method === 'GET') return Response.json({ ...me, workspaces: [{ ...workspace, role: options.role ?? 'owner' }] })
    if (p === '/api/v1/settings/storage' && r.method === 'GET') return Response.json(state.settings)
    if (p === '/api/v1/settings/storage' && r.method === 'PUT') {
      const body = await r.clone().json() as In
      state.bodies.push(body)
      const custom = options.put?.(body)
      if (custom) return custom
      if (body.version !== state.settings.version) return errorBody(409, 'VERSION_CONFLICT', '存储设置已被其他人修改，请刷新后重试')
      const r2 = body.r2!, oss = body.oss!
      state.settings = {
        ...state.settings, primary: body.primary, replica: body.replica ?? 'none', version: state.settings.version + 1, updated_at: '2026-11-12T09:00:00Z',
        r2: { account_id: r2.account_id ?? '', bucket: r2.bucket ?? '', jurisdiction: r2.jurisdiction ?? '', access_key_id: r2.access_key_id ?? '', secret_set: r2.secret_access_key === null ? state.settings.r2.secret_set : !!r2.secret_access_key },
        oss: { region: oss.region ?? '', bucket: oss.bucket ?? '', access_key_id: oss.access_key_id ?? '', secret_set: oss.access_key_secret === null ? state.settings.oss.secret_set : !!oss.access_key_secret },
      }
      const checks = Object.fromEntries([body.primary, body.replica].filter(n => n && n !== 'none').map(n => [n, { ok: true, reason: null, checked_at: '2026-11-12T09:00:00Z' }]))
      return Response.json({ ...state.settings, checks })
    }
    return undefined
  })
  return { requests, state }
}
const puts = (requests: Request[]) => requests.filter(r => r.method === 'PUT' && path(r) === '/api/v1/settings/storage')
const field = (name: string) => screen.getByLabelText(name) as HTMLInputElement
async function type(name: string, value: string) { await fireEvent.update(field(name), value) }

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('storage settings page', () => {
  it('lets the owner pick R2, checks before saving and never keeps the secret', async () => {
    const { requests, state } = setup()
    const { queryClient } = await openApp('/settings/storage')
    expect(await screen.findByRole('heading', { level: 1, name: '图片存储' })).toBeVisible()
    expect(screen.getByRole('navigation', { name: /主导航|侧栏/ }).querySelector('a[href="/settings/storage"]')).not.toBeNull()
    expect(screen.getByRole('link', { name: /查看存储状态/ })).toHaveAttribute('href', '/operations')
    expect(await screen.findByRole('radio', { name: /本机/ })).toBeChecked()
    expect(screen.getByText('没有副本：图片只存在主存储里。')).toBeVisible()
    // 没选、也没保存过的云存储不显示连接信息
    expect(screen.queryByRole('region', { name: 'Cloudflare R2 连接' })).toBeNull()
    expect(screen.getByRole('button', { name: '检查并保存' })).toBeDisabled()

    // 填了一半又换回本机：填过的连接信息不能从页面上消失
    await fireEvent.click(screen.getByRole('radio', { name: /Cloudflare R2/ }))
    await type('桶名', 'kb-images')
    await fireEvent.click(screen.getByRole('radio', { name: /本机/ }))
    expect(screen.getByRole('region', { name: 'Cloudflare R2 连接' })).toBeVisible()
    expect(field('桶名').value).toBe('kb-images')
    await fireEvent.click(screen.getByRole('radio', { name: /Cloudflare R2/ }))
    const r2 = await screen.findByRole('region', { name: 'Cloudflare R2 连接' })
    await type('账户 ID', '0123456789ABCDEF')
    await type('桶名', 'kb-images')
    await type('辖区（可不填）', 'eu')
    await type('Access Key ID', 'AKR2')
    await type('Secret Access Key', 'r2-secret-value')
    // 地址只能是 R2 的官方域名，由账户 ID 与辖区拼出
    expect(within(r2).getByText('https://0123456789abcdef.eu.r2.cloudflarestorage.com/kb-images')).toBeVisible()
    expect(field('Secret Access Key')).toHaveAttribute('type', 'password')

    await fireEvent.click(screen.getByRole('button', { name: '检查并保存' }))
    const results = await screen.findByRole('region', { name: '自检结果' })
    expect(within(results).getByText('Cloudflare R2')).toBeVisible()
    expect(within(results).getByText('自检通过')).toBeVisible()
    expect(await screen.findByText(/已保存：自检通过/)).toBeVisible()
    expect(state.bodies[0]).toEqual({
      version: 0, primary: 'r2', replica: 'none',
      r2: { account_id: '0123456789ABCDEF', bucket: 'kb-images', jurisdiction: 'eu', access_key_id: 'AKR2', secret_access_key: 'r2-secret-value' },
      oss: { region: '', bucket: '', access_key_id: '', access_key_secret: null },
    })
    expect(puts(requests)).toHaveLength(1)
    // 密钥不回显、不进缓存；保存后显示“已设置”
    expect(field('Secret Access Key').value).toBe('')
    await vi.waitFor(() => expect(field('Secret Access Key')).toHaveAttribute('placeholder', '已设置，不改就留空'))
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map(q => q.state.data))).not.toContain('r2-secret-value')
  })

  it('shows each failed check with a hint and saves nothing', async () => {
    const { state } = setup({
      put: () => errorBody(422, 'STORAGE_CHECK_FAILED', '存储自检没有通过，设置没有保存', {
        checks: { r2: { ok: false, reason: 'SignatureDoesNotMatch', checked_at: '2026-11-12T09:00:00Z' }, oss: { ok: true, reason: null, checked_at: '2026-11-12T09:00:00Z' } },
      }),
    })
    await openApp('/settings/storage')
    await fireEvent.click(await screen.findByRole('radio', { name: /Cloudflare R2/ }))
    await fireEvent.update(screen.getByLabelText('副本'), 'oss')
    await screen.findByRole('region', { name: '阿里云 OSS 连接' })
    await type('账户 ID', 'acct')
    await type('Secret Access Key', 'wrong-secret')
    await type('AccessKey Secret', 'oss-secret')
    await fireEvent.click(screen.getByRole('button', { name: '检查并保存' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('存储自检没有通过，设置没有保存')
    const results = screen.getByRole('region', { name: '自检结果' })
    expect(within(results).getByText('自检失败：SignatureDoesNotMatch（Secret 填错了）')).toBeVisible()
    expect(within(results).getByText('自检通过')).toBeVisible()
    expect(state.settings.version).toBe(0)
    // 提交过就清空密钥输入
    expect(field('Secret Access Key').value).toBe('')
    expect(field('AccessKey Secret').value).toBe('')
  })

  it('keeps a saved secret when left blank and guards a store that still holds files', async () => {
    const { state } = setup({ initial: savedR2 })
    await openApp('/settings/storage')
    const r2 = await screen.findByRole('region', { name: 'Cloudflare R2 连接' })
    expect(field('Secret Access Key')).toHaveAttribute('placeholder', '已设置，不改就留空')
    expect(field('Secret Access Key').value).toBe('')
    expect(screen.getByText(/还存着本工作空间图片或快照：本机、Cloudflare R2/)).toBeVisible()
    expect(within(r2).getByRole('button', { name: '清空 R2 设置' })).toBeDisabled()
    expect(screen.getByText(/上次修改：/)).toBeVisible()

    // 换回本机：R2 仍显示（还存着文件），密钥不填 = 保持
    await fireEvent.click(screen.getByRole('radio', { name: /本机/ }))
    await type('桶名', 'kb-images-2')
    await fireEvent.click(screen.getByRole('button', { name: '检查并保存' }))
    await screen.findByText(/已保存：自检通过/)
    expect(state.bodies[0].version).toBe(3)
    expect(state.bodies[0].primary).toBe('local')
    expect(state.bodies[0].r2!.secret_access_key).toBeNull()
    expect(state.bodies[0].r2!.bucket).toBe('kb-images-2')
  })

  it('clears an unused cloud store by sending an empty secret', async () => {
    const { state } = setup({ initial: { ...savedR2, primary: 'local', in_use: ['local'] } })
    await openApp('/settings/storage')
    const r2 = await screen.findByRole('region', { name: 'Cloudflare R2 连接' })
    await fireEvent.click(within(r2).getByRole('button', { name: '清空 R2 设置' }))
    expect(within(r2).getByText(/保存后清空 R2 的连接信息与密钥/)).toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '检查并保存' }))
    await screen.findByText(/已保存：自检通过/)
    expect(state.bodies[0].r2).toEqual({ account_id: '', bucket: '', jurisdiction: '', access_key_id: '', secret_access_key: '' })
  })

  it('reloads after a version conflict and focuses the field the server rejected', async () => {
    let first = true
    const { requests, state } = setup({
      initial: savedR2,
      put: body => {
        if (first) { first = false; state.settings = { ...state.settings, version: 4 }; return errorBody(409, 'VERSION_CONFLICT', '存储设置已被其他人修改，请刷新后重试') }
        if (body.r2!.bucket === 'Bad') return errorBody(422, 'VALIDATION_FAILED', '桶名是 3 ~ 63 个小写字母、数字或短横线，首尾是字母或数字', { errors: [{ loc: ['body', 'r2', 'bucket'], msg: 'x' }] })
        return undefined
      },
    })
    await openApp('/settings/storage')
    await screen.findByRole('region', { name: 'Cloudflare R2 连接' })
    await type('桶名', 'kb-other')
    await fireEvent.click(screen.getByRole('button', { name: '检查并保存' }))
    expect(await screen.findByText('图片存储设置刚被其他人修改：已载入最新设置，请核对后再保存。')).toBeVisible()
    await vi.waitFor(() => expect(requests.filter(r => r.method === 'GET' && path(r) === '/api/v1/settings/storage').length).toBeGreaterThanOrEqual(2))
    await vi.waitFor(() => expect(field('桶名').value).toBe('kb-images'))

    await type('桶名', 'Bad')
    await fireEvent.click(screen.getByRole('button', { name: '检查并保存' }))
    expect(await screen.findByText('桶名是 3 ~ 63 个小写字母、数字或短横线，首尾是字母或数字')).toBeVisible()
    expect(state.bodies[1].version).toBe(4)
    await vi.waitFor(() => expect(document.activeElement).toBe(field('桶名')))
  })

  it('is read-only for admin and hidden from member', async () => {
    const { requests } = setup({ role: 'admin', initial: savedR2 })
    await openApp('/settings/storage')
    expect(await screen.findByText('只有 owner 能修改图片存储设置；你可以查看当前设置。')).toBeVisible()
    expect(field('桶名')).toBeDisabled()
    expect(field('桶名').value).toBe('kb-images')
    expect(screen.getByRole('radio', { name: /Cloudflare R2/ })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '检查并保存' })).toBeNull()
    expect(screen.queryByRole('button', { name: '清空 R2 设置' })).toBeNull()
    expect(puts(requests)).toHaveLength(0)
  })

  it('does not show the page or request settings for a member', async () => {
    const { requests } = setup({ role: 'member' })
    await openApp('/settings/storage')
    await vi.waitFor(() => expect(screen.queryByRole('heading', { level: 1, name: '图片存储' })).toBeNull())
    expect(requests.some(r => path(r) === '/api/v1/settings/storage')).toBe(false)
  })
})
