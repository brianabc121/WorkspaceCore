import { fireEvent, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { mockApi, openApp, emptyPage, fail, me, workspace } from './helpers'
import { bookmark, calendarEvent, source } from './sprint4-fixtures'
const path = (r: Request) => new URL(r.url).pathname
describe('本地资源写入恢复', () => {
  it.each([403, 404])('hides cached bookmarks and closes the editor after a denied write (%s)', async (status) => {
    let denied = false, readStatus = status
    const requests = mockApi((r) => {
      if (r.method === 'PATCH') { denied = true; return fail(status, status === 403 ? 'FORBIDDEN' : 'NOT_FOUND') }
      if (path(r) === '/api/v1/knowledge/sources') return Response.json({ ...emptyPage, items: [source] })
      if (path(r) === '/api/v1/knowledge/resources') return denied ? fail(readStatus, 'FORBIDDEN') : Response.json({ ...emptyPage, items: [bookmark] })
    })
    await openApp('/knowledge'); await screen.findByText(bookmark.title)
    await fireEvent.click(screen.getByRole('button', { name: `编辑收藏 ${bookmark.title}` }))
    await fireEvent.update(screen.getByLabelText('收藏标题'), '失去权限后的修改')
    await fireEvent.click(screen.getByRole('button', { name: '保存收藏' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByText(bookmark.title)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新增收藏' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '管理知识来源' })).not.toBeInTheDocument()
    readStatus = 503; await fireEvent.click(screen.getByRole('button', { name: '重新加载知识' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '重新加载知识' })).toBeEnabled())
    expect(screen.queryByText(bookmark.title)).not.toBeInTheDocument()
    denied = false; await fireEvent.click(screen.getByRole('button', { name: '重新加载知识' }))
    expect(await screen.findByText(bookmark.title)).toBeVisible()
    expect(screen.getByRole('button', { name: '新增收藏' })).toBeEnabled()
    expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(1)
  })
  it.each([403, 404])('hides cached calendar data and closes the editor after a denied write (%s)', async (status) => {
    let denied = false, readStatus = status
    const requests = mockApi((r) => {
      if (r.method === 'PATCH') { denied = true; return fail(status, status === 403 ? 'FORBIDDEN' : 'NOT_FOUND') }
      if (path(r) === '/api/v1/calendar/events') return denied ? fail(readStatus, 'FORBIDDEN') : Response.json({ ...emptyPage, items: [calendarEvent] })
    })
    await openApp('/calendar'); await fireEvent.click(await screen.findByRole('button', { name: calendarEvent.title }))
    await fireEvent.update(screen.getByLabelText('日程标题'), '失去权限后的修改')
    await fireEvent.click(screen.getByRole('button', { name: '保存日程' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: calendarEvent.title })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新建日程' })).not.toBeInTheDocument()
    readStatus = 503; await fireEvent.click(screen.getByRole('button', { name: '重新加载日历' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '重新加载日历' })).toBeEnabled())
    expect(screen.queryByRole('button', { name: calendarEvent.title })).not.toBeInTheDocument()
    denied = false; await fireEvent.click(screen.getByRole('button', { name: '重新加载日历' }))
    expect(await screen.findByRole('button', { name: calendarEvent.title })).toBeVisible()
    expect(screen.getByRole('button', { name: '新建日程' })).toBeEnabled()
    expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(1)
  })
  it('does not repeat a committed creation after refresh fails, including a failed recovery read', async () => {
    let committed = false, broken = true
    const requests = mockApi((r) => {
      if (path(r) !== '/api/v1/calendar/events') return
      if (r.method === 'POST') { committed = true; return Response.json(calendarEvent, { status: 201 }) }
      return committed && broken ? fail(503, 'HTTP_ERROR') : Response.json({ ...emptyPage, items: [calendarEvent] })
    })
    await openApp('/calendar'); await screen.findByRole('button', { name: '发布评审' })
    await fireEvent.click(screen.getByRole('button', { name: '新建日程' })); await fireEvent.update(screen.getByLabelText('日程标题'), '一次写入')
    await fireEvent.click(screen.getByRole('button', { name: '保存日程' }))
    expect(await screen.findByText(/操作已完成，但刷新失败/)).toBeVisible()
    expect(screen.getByRole('button', { name: '保存日程' })).toBeDisabled()
    await fireEvent.click(screen.getByRole('button', { name: '重新加载日历' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '重新加载日历' })).toBeEnabled())
    expect(screen.getByRole('button', { name: '保存日程' })).toBeDisabled()
    broken = false; await fireEvent.click(screen.getByRole('button', { name: '重新加载日历' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(requests.filter((r) => r.method === 'POST')).toHaveLength(1)
  })
  it('preserves sub-minute timestamps when editing only the event title', async () => {
    const event = { ...calendarEvent, start_at: '2026-10-30T01:30:45Z', end_at: '2026-10-30T02:30:45Z' }
    const requests = mockApi((r) => path(r) === '/api/v1/calendar/events' ? Response.json({ ...emptyPage, items: [event] }) : r.method === 'PATCH' ? Response.json(event) : undefined)
    await openApp('/calendar'); await fireEvent.click(await screen.findByRole('button', { name: '发布评审' }))
    await fireEvent.update(screen.getByLabelText('日程标题'), '只改标题'); await fireEvent.click(screen.getByRole('button', { name: '保存日程' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'PATCH')).toBe(true))
    expect(await requests.find((r) => r.method === 'PATCH')!.clone().json()).toEqual({ version: 3, title: '只改标题' })
  })
  it('does not surface a late calendar write or editor in another workspace', async () => {
    let finish: ((r: Response) => void) | undefined
    const requests = mockApi((r) => {
      if (path(r) === '/api/v1/me') return Response.json({ ...me, workspaces: [workspace, { ...workspace, id: 'workspace-b', name: 'B空间', role: 'viewer' }] })
      if (r.method === 'POST') return new Promise<Response>((resolve) => { finish = resolve })
      if (path(r) === '/api/v1/calendar/events') return Response.json({ ...emptyPage, items: [{ ...calendarEvent, title: r.headers.get('X-Workspace-Id') === 'workspace-b' ? 'B日程' : 'A日程' }] })
    })
    await openApp('/calendar'); await screen.findByRole('button', { name: 'A日程' })
    await fireEvent.click(screen.getByRole('button', { name: '新建日程' })); await fireEvent.update(screen.getByLabelText('日程标题'), 'A创建')
    await fireEvent.click(screen.getByRole('button', { name: '保存日程' })); await vi.waitFor(() => expect(finish).toBeDefined())
    await fireEvent.update(screen.getByLabelText('当前工作空间'), 'workspace-b'); await screen.findByRole('button', { name: 'B日程' })
    finish!(Response.json(calendarEvent, { status: 201 }))
    await vi.waitFor(() => expect(screen.queryByText('日程已保存')).not.toBeInTheDocument())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新建日程' })).not.toBeInTheDocument()
    expect(requests.find((r) => r.method === 'POST')!.headers.get('X-Workspace-Id')).toBe('workspace-a')
  })
  it('creates a configured knowledge source from an initially empty workspace', async () => {
    let configured = false
    const requests = mockApi((r) => {
      if (path(r) !== '/api/v1/knowledge/sources') return
      if (r.method === 'POST') { configured = true; return Response.json(source, { status: 201 }) }
      return Response.json({ ...emptyPage, items: configured ? [source] : [] })
    })
    await openApp('/knowledge'); await screen.findByText(/尚未配置知识来源/)
    await fireEvent.click(screen.getByRole('button', { name: '管理知识来源' }))
    await fireEvent.update(screen.getByLabelText('AFFiNE 外部地址'), 'https://docs.example.com')
    await fireEvent.click(screen.getByRole('button', { name: '保存知识来源' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '新增收藏' })).toBeEnabled()
    expect(await requests.find((r) => r.method === 'POST')!.clone().json()).toEqual({ provider: 'affine', name: 'AFFiNE', base_url: 'https://docs.example.com' })
  })
})
