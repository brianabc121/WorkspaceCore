// 手机底部导航（T-0086）：只在窄屏显示；owner / admin 第五项是消息（带未读数），其他角色是知识库
import { screen, within } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { me, mockApi, openApp, workspace } from './helpers'

const pathOf = (r: Request) => new URL(r.url).pathname
// 窄屏：所有 min-width 查询都不匹配
function phone() {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: !query.includes('min-width'), media: query, addEventListener: () => {}, removeEventListener: () => {} }))
}
// 按可访问名称逐个确认，并确认一共 5 项（角标数字是 aria-hidden 的）
function expectLinks(nav: HTMLElement, labels: string[]) {
  expect(within(nav).getAllByRole('link')).toHaveLength(labels.length)
  for (const name of labels) expect(within(nav).getByRole('link', { name })).toBeVisible()
}

describe('手机底部导航', () => {
  it('shows the five main pages with messages and unread count for an owner', async () => {
    phone()
    mockApi((r) => (pathOf(r) === '/api/v1/im/badges' ? Response.json({ im_unread: 4, pending_approvals: 0 }) : undefined))
    await openApp('/tasks')
    const nav = await screen.findByRole('navigation', { name: '底部导航' })
    await vi.waitFor(() => expectLinks(nav, ['总览', '任务', '项目', '日历', '消息，4 条未读']))
    expect(within(nav).getByRole('link', { name: '任务' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: '消息，4 条未读' })).toHaveAttribute('href', '/im')
  })

  it('offers the knowledge base instead of messages to members', async () => {
    phone()
    mockApi((r) => (pathOf(r) === '/api/v1/me' ? Response.json({ ...me, workspaces: [{ ...workspace, role: 'member' }] }) : undefined))
    await openApp('/')
    const nav = await screen.findByRole('navigation', { name: '底部导航' })
    expectLinks(nav, ['总览', '任务', '项目', '日历', '知识库'])
    expect(within(nav).getByRole('link', { name: '总览' })).toHaveAttribute('aria-current', 'page')
  })

  it('is not shown on wide screens', async () => {
    mockApi()
    await openApp('/')
    await screen.findByRole('navigation', { name: '主导航' })
    expect(screen.queryByRole('navigation', { name: '底部导航' })).not.toBeInTheDocument()
  })
})
