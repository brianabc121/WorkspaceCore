// Vikunja / n8n 登录账号卡片（T-0080）：只给管理员看，密码默认遮住，显示 / 复制时才请求明文。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace, noLogins } from './helpers'
import { automations, connection } from './sprint5-fixtures'
import type { Schema } from '../src/services/api'

const PASSWORD = 'fake shown password'
const pathOf = (r: Request) => new URL(r.url).pathname
const saved: Schema['AppLoginList'] = { items: [
  { app: 'vikunja', configured: true, username: 'workspace', password_set: true, login_url: 'https://vikunja.example.test/', updated_at: '2026-11-02T00:00:00Z', version: 3 },
  { app: 'n8n', configured: true, username: 'ops@example.test', password_set: false, login_url: 'https://n8n.example.test/', updated_at: '2026-11-02T00:00:00Z', version: 1 },
] }
function setup(logins: Schema['AppLoginList'], handler?: (r: Request) => Response | undefined) {
  return mockApi(async r => {
    const result = handler?.(r)
    if (result) return result
    const path = pathOf(r)
    if (path === '/api/v1/app-logins' && r.method === 'GET') return Response.json(logins)
    if (path === '/api/v1/app-logins/vikunja/reveal') return Response.json({ app: 'vikunja', username: 'workspace', password: PASSWORD })
    if (path === '/api/v1/automations') return Response.json(automations)
    if (path === '/api/v1/automations/connection') return Response.json(connection)
    return undefined
  })
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('app logins', () => {
  it('keeps the Vikunja password hidden until asked and copies it on request', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    const requests = setup(saved)
    await openApp('/projects')
    const card = await screen.findByRole('region', { name: 'Vikunja 登录' })
    await within(card).findByText('workspace')
    expect(card).not.toHaveTextContent(PASSWORD)
    expect(requests.filter(r => pathOf(r).endsWith('/reveal'))).toHaveLength(0)
    expect(within(card).getByRole('link', { name: /打开 Vikunja 登录页/ })).toHaveAttribute('href', 'https://vikunja.example.test/')

    await fireEvent.click(within(card).getByRole('button', { name: '显示密码' }))
    expect(await within(card).findByText(PASSWORD)).toBeVisible()
    const reveal = requests.find(r => pathOf(r).endsWith('/reveal'))!
    expect(reveal.method).toBe('POST')
    expect(reveal.headers.get('X-Workspace-Id')).toBe('workspace-a')
    await fireEvent.click(within(card).getByRole('button', { name: '隐藏密码' }))
    expect(card).not.toHaveTextContent(PASSWORD)

    await fireEvent.click(within(card).getByRole('button', { name: '复制密码' }))
    await within(card).findByText('已复制密码')
    expect(writeText).toHaveBeenCalledWith(PASSWORD)
    await fireEvent.click(within(card).getByRole('button', { name: '复制用户名' }))
    expect(writeText).toHaveBeenLastCalledWith('workspace')
  })

  it('fills in a missing account and edits only what changed', async () => {
    let logins = noLogins
    const requests = setup(logins, r => {
      if (r.method !== 'PUT') return undefined
      logins = saved
      return Response.json(saved.items[0])
    })
    await openApp('/projects')
    const card = await screen.findByRole('region', { name: 'Vikunja 登录' })
    await within(card).findByText(/还没有保存 Vikunja 的登录账号/)
    await fireEvent.click(within(card).getByRole('button', { name: '填写 Vikunja 账号' }))
    const dialog = await screen.findByRole('dialog', { name: 'Vikunja 登录账号' })
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存账号' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('请填写用户名')
    await fireEvent.update(within(dialog).getByLabelText('用户名'), ' admin ')
    await fireEvent.update(within(dialog).getByLabelText('密码'), 'typed password')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存账号' }))
    await screen.findByText('账号已保存')
    const put = requests.find(r => r.method === 'PUT')!
    expect(pathOf(put)).toBe('/api/v1/app-logins/vikunja')
    expect(await put.clone().json()).toEqual({ username: 'admin', password: 'typed password' })
    expect(document.body).not.toHaveTextContent('typed password')
  })

  it('sends the version and only the changed field when editing', async () => {
    const requests = setup(saved, r => r.method === 'PUT' ? Response.json(saved.items[0]) : undefined)
    await openApp('/projects')
    const card = await screen.findByRole('region', { name: 'Vikunja 登录' })
    await fireEvent.click(await within(card).findByRole('button', { name: '修改 Vikunja 账号' }))
    const dialog = await screen.findByRole('dialog', { name: 'Vikunja 登录账号' })
    expect(within(dialog).getByLabelText('用户名')).toHaveValue('workspace')
    expect(within(dialog).getByLabelText('密码')).toHaveValue('')
    await fireEvent.update(within(dialog).getByLabelText('密码'), 'new password')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存账号' }))
    await screen.findByText('账号已保存')
    expect(await requests.find(r => r.method === 'PUT')!.clone().json()).toEqual({ version: 3, password: 'new password' })
  })

  it('shows the n8n owner on the automations page', async () => {
    setup(saved)
    await openApp('/automations')
    const card = await screen.findByRole('region', { name: 'n8n 登录' })
    await within(card).findByText('ops@example.test')
    expect(within(card).getByText('没有保存密码')).toBeVisible()
    expect(within(card).queryByRole('button', { name: '显示密码' })).not.toBeInTheDocument()
    expect(within(card).getByRole('button', { name: '复制登录邮箱' })).toBeVisible()
  })

  it('never shows or requests logins for members and viewers', async () => {
    for (const role of ['member', 'viewer'] as const) {
      const requests = setup(saved, r => pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) : undefined)
      const { unmount } = await openApp('/projects')
      await screen.findByText(/Vikunja ·/)
      expect(screen.queryByRole('region', { name: 'Vikunja 登录' })).not.toBeInTheDocument()
      expect(requests.filter(r => pathOf(r).startsWith('/api/v1/app-logins'))).toHaveLength(0)
      unmount()
    }
  })
})
