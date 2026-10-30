import { screen, fireEvent } from '@testing-library/vue'
import { beforeEach, describe, it, expect, vi } from 'vitest'
import { mockApi, openApp } from './helpers'

const pages = [
  ['/', '总览'], ['/tasks', '任务'], ['/projects', '项目'], ['/knowledge', '知识库'],
  ['/calendar', '日历'], ['/developer', '开发者'], ['/operations', '运维'],
  ['/automations', '自动化'], ['/ai', 'AI 助手'], ['/settings', '设置'],
  ['/settings/integrations', '集成设置'], ['/settings/security', '安全设置'],
]
async function open(path = '/') {
  return openApp(path)
}
beforeEach(() => { mockApi() })
describe('工程骨架', () => {
  it.each(pages)('renders the %s page and navigation', async (path, label) => {
    await open(path)
    expect(screen.getByRole('heading', { level: 1, name: label })).toBeVisible()
    const nav = screen.getByRole('navigation', { name: '主导航' })
    for (const [href, name] of pages) {
      expect(nav.querySelector(`a[href="${href}"]`)).toHaveTextContent(name!)
    }
    expect(screen.getByText('Sprint 3 · 任务与集成')).toBeVisible()
  })
  it('navigates without reloading and closes the mobile menu', async () => {
    const { router } = await open()
    const toggle = screen.getByRole('button', { name: '切换导航' })
    await fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await fireEvent.click(screen.getByRole('link', { name: '任务' }))
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/tasks'))
    await vi.waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'false'))
    expect(screen.getByRole('heading', { name: '任务', level: 1 })).toBeVisible()
  })
  it('closes navigation on Escape and returns focus to its toggle', async () => {
    await open()
    const toggle = screen.getByRole('button', { name: '切换导航' })
    await fireEvent.click(toggle)
    const link = screen.getByRole('link', { name: '任务' })
    link.focus()
    await fireEvent.keyDown(link, { key: 'Escape' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveFocus()
  })
  it('shows an explicit not-found page', async () => {
    await open('/missing')
    expect(screen.getByRole('heading', { name: '页面未找到' })).toBeVisible()
    expect(screen.getByRole('link', { name: '返回总览' })).toHaveAttribute('href', '/')
  })
  it('toggles and persists the theme', async () => {
    await open()
    await fireEvent.click(screen.getByRole('button', { name: '切换到暗色主题' }))
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    expect(localStorage.getItem('workspace-theme')).toBe('dark')
    await fireEvent.click(screen.getByRole('button', { name: '切换到亮色主题' }))
    expect(document.documentElement).toHaveAttribute('data-theme', 'light')
  })
  it('restores a saved theme on startup', async () => {
    localStorage.setItem('workspace-theme', 'dark')
    await open()
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
  })
  it('keeps rendering and switching themes if storage is blocked', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    await open()
    await fireEvent.click(screen.getByRole('button', { name: '切换到暗色主题' }))
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
  })
})
