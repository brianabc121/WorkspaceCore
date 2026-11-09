import { describe, it, expect } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, fail, session } from './helpers'
import type { Schema } from '../src/services/api'

// 安全设置改版（T-0193，效果图 04）：确认新密码只在前端核对；会话只显示接口给的字段；撤销按 ID、只更新列表
const pathOf = (r: Request) => new URL(r.url).pathname
const other: Schema['SessionOut'] = { id: '7f1c0000-0000-4000-8000-00000000b72d', current: false, created_at: '2026-11-08T12:10:00Z', expires_at: '2026-11-15T12:10:00Z', last_seen_at: '2026-11-09T01:10:00Z' }
const sessions = (items: Schema['SessionOut'][]) => (r: Request) => pathOf(r) === '/api/v1/auth/sessions' && r.method === 'GET' ? Response.json({ items, next_cursor: null, total_estimate: items.length }) : undefined

describe('security settings redesign', () => {
  it('does not send the request when the confirmation differs, and never sends the confirmation field', async () => {
    const list = sessions([session, other])
    const requests = mockApi((r) => list(r) ?? (pathOf(r) === '/api/v1/auth/password' ? new Response(null, { status: 204 }) : undefined))
    await openApp('/settings/security')
    const form = await screen.findByRole('form', { name: '修改密码' })
    await fireEvent.update(within(form).getByLabelText('当前密码'), 'old-password')
    await fireEvent.update(within(form).getByLabelText('新密码'), 'new-password-123')
    await fireEvent.update(within(form).getByLabelText('确认新密码'), 'new-password-124')
    await fireEvent.submit(form)
    expect(await within(form).findByText('两次输入的新密码不一致')).toBeVisible()
    expect(requests.some((r) => pathOf(r) === '/api/v1/auth/password')).toBe(false)
    await fireEvent.update(within(form).getByLabelText('确认新密码'), 'new-password-123')
    await fireEvent.submit(form)
    expect(await within(form).findByText('密码已更新，其他会话已撤销')).toBeVisible()
    const post = requests.find((r) => pathOf(r) === '/api/v1/auth/password')!
    expect(await post.clone().json()).toEqual({ current_password: 'old-password', new_password: 'new-password-123' })
    for (const label of ['当前密码', '新密码', '确认新密码']) expect(within(form).getByLabelText(label)).toHaveValue('')
  })

  it('reports a wrong current password without claiming success and clears the inputs', async () => {
    const list = sessions([session])
    mockApi((r) => list(r) ?? (pathOf(r) === '/api/v1/auth/password' ? fail(400, 'INVALID_PASSWORD') : undefined))
    await openApp('/settings/security')
    const form = await screen.findByRole('form', { name: '修改密码' })
    await fireEvent.update(within(form).getByLabelText('当前密码'), 'wrong-password')
    await fireEvent.update(within(form).getByLabelText('新密码'), 'new-password-123')
    await fireEvent.update(within(form).getByLabelText('确认新密码'), 'new-password-123')
    await fireEvent.submit(form)
    expect(await within(form).findByRole('alert')).toBeVisible()
    expect(within(form).queryByText('密码已更新，其他会话已撤销')).not.toBeInTheDocument()
    expect(within(form).getByLabelText('当前密码')).toHaveValue('')
  })

  it('shows only the fields the API provides, with counts of current and other sessions', async () => {
    mockApi(sessions([session, other]))
    await openApp('/settings/security')
    const panel = await screen.findByRole('region', { name: '登录会话' })
    expect(await within(panel).findByRole('heading', { name: '登录会话 · 2' })).toBeVisible()
    expect(within(panel).getByText('当前 1 · 其他 1')).toBeVisible()
    const current = within(panel).getByRole('listitem', { name: '当前会话' })
    expect(within(current).getByText('当前浏览器')).toBeVisible()
    const row = within(panel).getByRole('listitem', { name: '其他会话 B72D' })
    expect(within(row).getByText('创建时间')).toBeVisible()
    expect(within(row).getByText('最近活动')).toBeVisible()
    expect(within(row).getByText('到期时间')).toBeVisible()
    // 接口没有设备、系统、位置、IP：页面也不显示
    expect(panel.textContent).not.toMatch(/IP|城市|设备|Windows|macOS|Chrome|在线/)
  })

  it('revokes another session by its ID and only refreshes the list', async () => {
    const list = sessions([session, other])
    const requests = mockApi((r) => list(r) ?? (r.method === 'DELETE' ? new Response(null, { status: 204 }) : undefined))
    const { router } = await openApp('/settings/security')
    const row = await screen.findByRole('listitem', { name: '其他会话 B72D' })
    await fireEvent.click(within(row).getByRole('button', { name: '撤销会话' }))
    await fireEvent.click(screen.getByRole('button', { name: '确认撤销' }))
    expect(await screen.findByText('会话已撤销')).toBeVisible()
    const deletes = requests.filter((r) => r.method === 'DELETE')
    expect(deletes.map(pathOf)).toEqual([`/api/v1/auth/sessions/${other.id}`])
    expect(router.currentRoute.value.path).toBe('/settings/security')
  })
})
