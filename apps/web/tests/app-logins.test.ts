// n8n 登录账号卡片（T-0080；T-0082 移除 Vikunja 后只有 n8n）：只给管理员看，密码默认遮住，显示 / 复制时才请求明文。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace, noLogins } from './helpers'
import { automations, connection } from './sprint5-fixtures'
import type { Schema } from '../src/services/api'

const PASSWORD = 'fake shown password'
const pathOf = (r: Request) => new URL(r.url).pathname
const owner = { app: 'n8n', configured: true, username: 'ops@example.test', password_set: true, login_url: 'https://n8n.example.test/', updated_at: '2026-11-02T00:00:00Z', version: 3 } as const
const saved: Schema['AppLoginList'] = { items: [owner] }
function setup(logins: Schema['AppLoginList'], handler?: (r: Request) => Response | undefined) {
  return mockApi(async r => {
    const result = handler?.(r)
    if (result) return result
    const path = pathOf(r)
    if (path === '/api/v1/app-logins' && r.method === 'GET') return Response.json(logins)
    if (path === '/api/v1/app-logins/n8n/reveal') return Response.json({ app: 'n8n', username: owner.username, password: PASSWORD })
    if (path === '/api/v1/automations') return Response.json(automations)
    if (path === '/api/v1/automations/connection') return Response.json(connection)
    return undefined
  })
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('app logins', () => {
  it('keeps the n8n password hidden until asked and copies it on request', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    const requests = setup(saved)
    await openApp('/automations')
    const card = await screen.findByRole('region', { name: 'n8n 登录' })
    await within(card).findByText(owner.username)
    expect(card).not.toHaveTextContent(PASSWORD)
    expect(requests.filter(r => pathOf(r).endsWith('/reveal'))).toHaveLength(0)
    expect(within(card).getByRole('link', { name: /打开 n8n 登录页/ })).toHaveAttribute('href', owner.login_url)

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
    await fireEvent.click(within(card).getByRole('button', { name: '复制登录邮箱' }))
    expect(writeText).toHaveBeenLastCalledWith(owner.username)
  })

  it('fills in a missing account and edits only what changed', async () => {
    let logins = noLogins
    const requests = setup(logins, r => {
      if (r.method !== 'PUT') return undefined
      logins = saved
      return Response.json(owner)
    })
    await openApp('/automations')
    const card = await screen.findByRole('region', { name: 'n8n 登录' })
    await within(card).findByText(/还没有保存 n8n 的登录账号/)
    await fireEvent.click(within(card).getByRole('button', { name: '填写 n8n 账号' }))
    const dialog = await screen.findByRole('dialog', { name: 'n8n 登录账号' })
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存账号' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('请填写登录邮箱')
    await fireEvent.update(within(dialog).getByLabelText('登录邮箱'), ' admin@example.test ')
    await fireEvent.update(within(dialog).getByLabelText('密码'), 'typed password')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存账号' }))
    await screen.findByText('账号已保存')
    const put = requests.find(r => r.method === 'PUT')!
    expect(pathOf(put)).toBe('/api/v1/app-logins/n8n')
    expect(await put.clone().json()).toEqual({ username: 'admin@example.test', password: 'typed password' })
    expect(document.body).not.toHaveTextContent('typed password')
  })

  it('sends the version and only the changed field when editing', async () => {
    const requests = setup(saved, r => r.method === 'PUT' ? Response.json(owner) : undefined)
    await openApp('/automations')
    const card = await screen.findByRole('region', { name: 'n8n 登录' })
    await fireEvent.click(await within(card).findByRole('button', { name: '修改 n8n 账号' }))
    const dialog = await screen.findByRole('dialog', { name: 'n8n 登录账号' })
    expect(within(dialog).getByLabelText('登录邮箱')).toHaveValue(owner.username)
    expect(within(dialog).getByLabelText('密码')).toHaveValue('')
    await fireEvent.update(within(dialog).getByLabelText('密码'), 'new password')
    await fireEvent.click(within(dialog).getByRole('button', { name: '保存账号' }))
    await screen.findByText('账号已保存')
    expect(await requests.find(r => r.method === 'PUT')!.clone().json()).toEqual({ version: 3, password: 'new password' })
  })

  it('shows an owner saved without a password', async () => {
    setup({ items: [{ ...owner, password_set: false, version: 1 }] })
    await openApp('/automations')
    const card = await screen.findByRole('region', { name: 'n8n 登录' })
    await within(card).findByText(owner.username)
    expect(within(card).getByText('没有保存密码')).toBeVisible()
    expect(within(card).queryByRole('button', { name: '显示密码' })).not.toBeInTheDocument()
    expect(within(card).getByRole('button', { name: '复制登录邮箱' })).toBeVisible()
  })

  it('is no longer shown on the projects page (T-0082)', async () => {
    const requests = setup(saved)
    await openApp('/projects')
    await screen.findByRole('heading', { name: '项目' })
    expect(screen.queryByRole('region', { name: /登录$/ })).not.toBeInTheDocument()
    expect(requests.filter(r => pathOf(r).startsWith('/api/v1/app-logins'))).toHaveLength(0)
  })

  it('never shows or requests logins for members and viewers', async () => {
    for (const role of ['member', 'viewer'] as const) {
      const requests = setup(saved, r => pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) : undefined)
      const { unmount } = await openApp('/automations')
      await screen.findByRole('heading', { name: '自动化' })
      expect(screen.queryByRole('region', { name: 'n8n 登录' })).not.toBeInTheDocument()
      expect(requests.filter(r => pathOf(r).startsWith('/api/v1/app-logins'))).toHaveLength(0)
      unmount()
    }
  })
})
