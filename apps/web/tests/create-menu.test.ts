import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mockApi, openApp, me, workspace } from './helpers'
import { source } from './sprint4-fixtures'
import type { Schema } from '../src/services/api'

// 规范 §8.1 顶栏的全局“创建”菜单（T-0067）
const path = (r: Request) => new URL(r.url).pathname
type Role = Schema['WorkspaceOut']['role']
function setup(role: Role = 'owner', handler?: (r: Request) => Response | undefined) {
  return mockApi(r => handler?.(r) ?? (
    path(r) === '/api/v1/me' && r.method === 'GET' ? Response.json({ ...me, workspaces: [{ ...workspace, role }] }) :
    path(r) === '/api/v1/im/status' ? Response.json({ available: true, account_ready: true, im_user_id: 'ws_owner' }) :
    path(r) === '/api/v1/im/contacts' ? Response.json({ items: [{ im_user_id: 'oc_a', nickname: '甲', instance_id: 'inst-a', conversation_id: 'si_a', status: 'online', contact_group_ids: [] }] }) :
    path(r) === '/api/v1/im/contact-groups' ? Response.json({ items: [] }) :
    path(r) === '/api/v1/im/conversations' && r.method === 'GET' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) :
    path(r) === '/api/v1/agents/settings' ? Response.json({ agents_enabled: true, updated_at: null, updated_by: null, version: 1 }) :
    path(r) === '/api/v1/agents/instances' || path(r) === '/api/v1/agents/secrets' ? Response.json({ items: [], next_cursor: null, total_estimate: 0 }) : undefined))
}
async function openMenu() {
  await fireEvent.keyDown(await screen.findByRole('button', { name: '创建' }), { key: 'Enter' })
  return screen.findByRole('menu')
}
async function choose(label: string) {
  const menu = await openMenu()
  await fireEvent.click(within(menu).getByRole('menuitem', { name: new RegExp(`^${label}`) }))
}
const items = (menu: HTMLElement) => within(menu).getAllByRole('menuitem').map(item => item.textContent?.trim())
afterEach(() => vi.restoreAllMocks())

describe('global create menu', () => {
  it('lists what each role can create', async () => {
    setup('owner')
    await openApp('/')
    expect(items(await openMenu())).toEqual(['新建任务Vikunja', '新建日程日历', '收藏文档AFFiNE 链接', '新建群聊与 OpenClaw 实例', '添加成员工作空间', '登记 OpenClaw 实例接入包'])
  })

  it('leaves out admin-only and owner-only entries for other roles', async () => {
    setup('admin')
    const first = await openApp('/')
    expect(items(await openMenu())).not.toContain('登记 OpenClaw 实例接入包')
    first.unmount()
    setup('member')
    await openApp('/')
    expect(items(await openMenu())).toEqual(['新建任务Vikunja', '新建日程日历', '收藏文档AFFiNE 链接'])
  })

  it('is hidden for viewers, who cannot create anything', async () => {
    setup('viewer')
    await openApp('/')
    await screen.findByRole('button', { name: 'AI 助手' })
    expect(screen.queryByRole('button', { name: '创建' })).not.toBeInTheDocument()
  })

  it('opens the new task form in the Task Drawer from any page and cleans the address', async () => {
    setup()
    const { router } = await openApp('/')
    await choose('新建任务')
    const detail = await screen.findByRole('dialog', { name: '新建任务' })
    expect(within(detail).getByLabelText('任务标题')).toHaveValue('')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/tasks'))
    // Already on the page: choosing it again still opens the form.
    await fireEvent.click(within(detail).getByRole('button', { name: '关闭对话框' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog', { name: '新建任务' })).not.toBeInTheDocument())
    // Focus returns to the create button.
    expect(screen.getByRole('button', { name: '创建' })).toHaveFocus()
    await choose('新建任务')
    expect(await screen.findByRole('dialog', { name: '新建任务' })).toBeVisible()
  })

  it('opens the calendar event form', async () => {
    setup()
    const { router } = await openApp('/')
    await choose('新建日程')
    expect(await screen.findByRole('dialog', { name: '新建日程' })).toBeVisible()
    await vi.waitFor(() => expect(router.currentRoute.value.query.create).toBeUndefined())
  })

  it('opens the bookmark form, or the source settings for an admin when no source exists yet', async () => {
    setup('owner', r => path(r) === '/api/v1/knowledge/sources' ? Response.json({ items: [source], next_cursor: null, total_estimate: 1 }) : undefined)
    const first = await openApp('/')
    await choose('收藏文档')
    expect(await screen.findByRole('dialog', { name: '新增收藏' })).toBeVisible()
    first.unmount()

    setup('owner')
    await openApp('/')
    await choose('收藏文档')
    expect(await screen.findByRole('dialog', { name: '配置知识来源' })).toBeVisible()
  })

  it('only removes the parameter when the page cannot create right now', async () => {
    setup('member')
    const { router } = await openApp('/knowledge?create=1')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/knowledge'))
    expect(await screen.findByText('尚未配置知识来源，请管理员配置 AFFiNE 外部地址。')).toBeVisible()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens the group form, the member form and the instance form for admins and the owner', async () => {
    setup()
    await openApp('/')
    await choose('新建群聊')
    expect(await screen.findByRole('dialog', { name: '建群' })).toBeVisible()
    await fireEvent.click(within(screen.getByRole('dialog', { name: '建群' })).getByRole('button', { name: '关闭对话框' }))

    await choose('添加成员')
    const form = await screen.findByRole('form', { name: '添加成员' })
    await vi.waitFor(() => expect(within(form).getByLabelText('邮箱 *')).toHaveFocus())

    await choose('登记 OpenClaw 实例')
    expect(await screen.findByRole('dialog', { name: '登记 OpenClaw 实例' })).toBeVisible()
  })
})
