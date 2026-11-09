import { describe, it, expect, afterEach, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

// 设置页改版（T-0189，效果图 02）：资料与工作空间各自保存、成员表与角色规则、快捷入口只链接已有页面
const at = '2026-11-01T04:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const MEMBERS = '/api/v1/workspaces/workspace-a/members'
const member = (over: Partial<Schema['MemberOut']>): Schema['MemberOut'] => ({ user_id: 'user-x', email: 'x@example.com', display_name: 'X', role: 'member', joined_at: at, you: false, ...over })
const items = [
  member({ user_id: 'user-a', email: 'test@example.com', display_name: '测试用户', role: 'owner', you: true }),
  member({ user_id: 'user-b', email: 'bob@example.com', display_name: 'Bob', role: 'admin' }),
  member({ user_id: 'user-c', email: 'carol@example.com', display_name: 'Carol', role: 'viewer' }),
]
function setup(role: Schema['WorkspaceOut']['role'], handler?: (r: Request) => Response | undefined) {
  return mockApi(r =>
    handler?.(r) ?? (
      path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) :
      path(r) === MEMBERS && r.method === 'GET' ? Response.json({ items, next_cursor: null, total_estimate: items.length }) : undefined))
}
afterEach(() => vi.restoreAllMocks())

describe('settings page redesign', () => {
  it('shows the workspace and role, the read-only login email and a member count from the server list', async () => {
    setup('owner')
    await openApp('/settings')
    expect(await screen.findByRole('heading', { level: 1, name: '设置' })).toBeVisible()
    expect(screen.getByText(`${workspace.name} · 所有者`)).toBeVisible()
    const profile = screen.getByRole('form', { name: '个人资料' })
    expect(within(profile).getByText(me.user.email)).toBeVisible()
    expect(within(profile).getByText('只读')).toBeVisible()
    expect(within(profile).queryByRole('textbox', { name: '登录邮箱' })).not.toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '成员与角色 · 3' })).toBeVisible()
    // owner 那一行没有控件，写明不可修改
    const ownerRow = await screen.findByRole('row', { name: '成员：测试用户' })
    expect(within(ownerRow).getByText('不可修改')).toBeVisible()
    expect(within(ownerRow).queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('saves the profile alone without touching the workspace, and the workspace alone without touching the profile', async () => {
    const requests = setup('owner', r =>
      r.method === 'PATCH' && path(r) === '/api/v1/me' ? Response.json({ ...me.user, display_name: '新名字' }) :
      r.method === 'PATCH' && path(r) === '/api/v1/workspaces/workspace-a' ? Response.json({ ...workspace, name: '新空间', role: 'owner' }) : undefined)
    await openApp('/settings')
    const profile = await screen.findByRole('form', { name: '个人资料' })
    await fireEvent.update(within(profile).getByLabelText('显示名'), '新名字')
    await fireEvent.submit(profile)
    expect(await within(profile).findByText('个人资料已保存')).toBeVisible()
    expect(requests.filter(r => r.method === 'PATCH').map(path)).toEqual(['/api/v1/me'])
    const ws = screen.getByRole('form', { name: '工作空间设置' })
    await fireEvent.update(within(ws).getByLabelText('工作空间名称'), '新空间')
    await fireEvent.submit(ws)
    expect(await within(ws).findByText('工作空间已保存')).toBeVisible()
    expect(requests.filter(r => r.method === 'PATCH').map(path)).toEqual(['/api/v1/me', '/api/v1/workspaces/workspace-a'])
  })

  it('keeps a half-typed new member when the member list is refreshed', async () => {
    const requests = setup('owner')
    await openApp('/settings')
    const form = await screen.findByRole('form', { name: '添加成员' })
    await screen.findByRole('row', { name: '成员：Bob' })
    await fireEvent.update(within(form).getByLabelText('邮箱 *'), 'dave@example.com')
    const panel = screen.getByRole('region', { name: '成员' })
    await fireEvent.click(within(panel).getByRole('button', { name: '刷新' }))
    await vi.waitFor(() => expect(requests.filter(r => path(r) === MEMBERS).length).toBeGreaterThanOrEqual(2))
    expect(within(form).getByLabelText('邮箱 *')).toHaveValue('dave@example.com')
    // 添加的是已有账号，不说“已发送邀请”
    expect(screen.queryByText(/邀请已发送|已发送邀请/)).not.toBeInTheDocument()
  })

  it('links the shortcuts to the existing pages and hides admin-only ones from members', async () => {
    setup('owner')
    await openApp('/settings')
    const aside = await screen.findByRole('complementary', { name: '快捷设置与角色说明' })
    expect(within(aside).getByRole('link', { name: /安全设置/ })).toHaveAttribute('href', '/settings/security')
    expect(within(aside).getByRole('link', { name: /侧栏与页面/ })).toHaveAttribute('href', '/settings/navigation')
    expect(within(aside).getByRole('link', { name: /应用中心/ })).toHaveAttribute('href', '/settings/modules')
    expect(within(aside).getByRole('region', { name: '角色说明' })).toBeVisible()
  })

  it('shows members only the profile, the read-only workspace name and the non-admin shortcuts', async () => {
    const requests = setup('member')
    await openApp('/settings')
    const ws = await screen.findByRole('form', { name: '工作空间设置' })
    expect(within(ws).getByLabelText('工作空间名称')).toHaveAttribute('readonly')
    expect(within(ws).queryByRole('button', { name: '保存工作空间' })).not.toBeInTheDocument()
    const aside = screen.getByRole('complementary', { name: '快捷设置与角色说明' })
    expect(within(aside).queryByRole('link', { name: /应用中心/ })).not.toBeInTheDocument()
    expect(requests.some(r => path(r) === MEMBERS)).toBe(false)
  })
})
