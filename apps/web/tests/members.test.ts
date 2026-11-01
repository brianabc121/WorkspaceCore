import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, fail, me, workspace } from './helpers'
import type { Schema } from '../src/services/api'

const at = '2026-11-01T04:00:00Z'
const path = (r: Request) => new URL(r.url).pathname
const MEMBERS = '/api/v1/workspaces/workspace-a/members'
const member = (over: Partial<Schema['MemberOut']>): Schema['MemberOut'] => ({ user_id: 'user-x', email: 'x@example.com', display_name: 'X', role: 'member', joined_at: at, you: false, ...over })
const owner = member({ user_id: 'user-a', email: 'test@example.com', display_name: '测试用户', role: 'owner', you: true })
const admin = member({ user_id: 'user-b', email: 'bob@example.com', display_name: 'Bob', role: 'admin' })
const viewer = member({ user_id: 'user-c', email: 'carol@example.com', display_name: 'Carol', role: 'viewer' })
function setup(role: Schema['WorkspaceOut']['role'], handler?: (r: Request) => Response | undefined, items = [owner, admin, viewer]) {
  return mockApi(r =>
    handler?.(r) ?? (
      path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) :
      path(r) === MEMBERS && r.method === 'GET' ? Response.json({ items, next_cursor: null, total_estimate: items.length }) : undefined))
}
const row = (name: string) => screen.findByRole('listitem', { name: `成员：${name}` })
afterEach(() => vi.restoreAllMocks())

describe('members', () => {
  it('lets the owner change roles, including admin, and remove members after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const requests = setup('owner', r =>
      r.method === 'PATCH' ? Response.json({ ...admin, role: 'member' }) :
      r.method === 'DELETE' ? new Response(null, { status: 204 }) : undefined)
    await openApp('/settings')
    const panel = await screen.findByRole('region', { name: '成员' })
    // The owner row (and your own row) has no controls.
    expect(within(await row('测试用户')).queryByLabelText('角色')).not.toBeInTheDocument()
    expect(within(await row('测试用户')).getByText('（你）')).toBeVisible()
    const bob = await row('Bob')
    expect(within(bob).getByRole('option', { name: 'admin' })).toBeInTheDocument()
    await fireEvent.update(within(bob).getByLabelText('角色'), 'member')
    await vi.waitFor(() => expect(requests.some(r => r.method === 'PATCH')).toBe(true))
    const patch = requests.find(r => r.method === 'PATCH')!
    expect(path(patch)).toBe(`${MEMBERS}/user-b`)
    expect(await patch.clone().json()).toEqual({ role: 'member' })
    expect(await within(panel).findByText('Bob 的角色已改为 member')).toBeVisible()
    await fireEvent.click(within(await row('Carol')).getByRole('button', { name: '移出' }))
    await vi.waitFor(() => expect(requests.some(r => r.method === 'DELETE' && path(r) === `${MEMBERS}/user-c`)).toBe(true))
    expect(window.confirm).toHaveBeenCalledOnce()
  })

  it('lets an admin manage member and viewer only', async () => {
    setup('admin')
    await openApp('/settings')
    await screen.findByRole('region', { name: '成员' })
    expect(within(await row('Bob')).queryByLabelText('角色')).not.toBeInTheDocument()
    expect(within(await row('Bob')).queryByRole('button', { name: '移出' })).not.toBeInTheDocument()
    const carol = await row('Carol')
    expect(within(carol).queryByRole('option', { name: 'admin' })).not.toBeInTheDocument()
    expect(within(screen.getByRole('form', { name: '添加成员' })).queryByRole('option', { name: 'admin' })).not.toBeInTheDocument()
  })

  it('adds existing accounts and creates new ones with an initial password', async () => {
    const requests = setup('owner', r => {
      if (r.method !== 'POST') return undefined
      return requests.filter(x => x.method === 'POST').length === 1 ? fail(404, 'USER_NOT_FOUND') : Response.json(member({ email: 'dave@example.com' }), { status: 201 })
    })
    await openApp('/settings')
    const form = await screen.findByRole('form', { name: '添加成员' })
    await fireEvent.update(within(form).getByLabelText('邮箱 *'), 'dave@example.com')
    await fireEvent.submit(form)
    expect(await within(form).findByText(/勾选“同时创建账号”/)).toBeVisible()
    await fireEvent.click(within(form).getByLabelText(/同时创建账号/))
    await fireEvent.update(within(form).getByLabelText('显示名 *'), 'Dave')
    await fireEvent.update(within(form).getByLabelText(/初始密码/), 'short')
    await fireEvent.submit(form)
    expect(await within(form).findByText('初始密码至少 12 个字符。')).toBeVisible()
    expect(requests.filter(r => r.method === 'POST')).toHaveLength(1)
    await fireEvent.update(within(form).getByLabelText(/初始密码/), 'dave initial pass')
    await fireEvent.submit(form)
    expect(await within(form).findByText(/已创建账号并加入：dave@example.com/)).toBeVisible()
    const posts = requests.filter(r => r.method === 'POST')
    expect(await posts[0].clone().json()).toEqual({ email: 'dave@example.com', role: 'member' })
    expect(await posts[1].clone().json()).toEqual({ email: 'dave@example.com', role: 'member', display_name: 'Dave', password: 'dave initial pass' })
    // The form is cleared, so the password does not stay on the page.
    expect(within(form).getByLabelText('邮箱 *')).toHaveValue('')
    expect(within(form).queryByLabelText(/初始密码/)).not.toBeInTheDocument()
  })

  it('is not shown to members and viewers', async () => {
    const requests = setup('member')
    await openApp('/settings')
    await screen.findByRole('form', { name: '工作空间设置' })
    expect(screen.queryByRole('region', { name: '成员' })).not.toBeInTheDocument()
    expect(requests.some(r => path(r) === MEMBERS)).toBe(false)
  })
})
